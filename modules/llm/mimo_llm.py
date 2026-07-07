from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from typing import Generator, Callable, Tuple

from openai import OpenAI, AsyncOpenAI
from .base_llm import BaseLLM

logger = logging.getLogger("chronoverse.llm")


class MimoLLM(BaseLLM):
    def __init__(self, api_key: str, base_url: str, model_name: str = "mimo-V2.5-Pro",
                 default_max_tokens: int = 0, preflight_check: bool = True):
        super().__init__()
        # 单次接口调用不要等太久；路由层会负责切换备用模型。
        # 关闭 SDK 自动重试，避免 SDK 重试 + 项目重试叠加导致卡顿一分钟以上。
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=0)
        self.async_client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=0)
        self.model_name = model_name
        self.default_max_tokens = default_max_tokens  # 0 = 不限制，使用API默认值
        self._cache: dict[str, tuple[float, str]] = {}
        self._cache_ttl = 300
        self._cache_lock = threading.Lock()  # [Bug#24] 保护缓存的并发读写
        # [Bug] DeepSeek等API不支持response_format，根据模型名自动判断
        _no_structured = ["deepseek", "qwen", "glm", "yi"]
        self._structured_supported = not any(n in model_name.lower() for n in _no_structured)
        self._api_reachable = True  # API是否可达

        # 预检测API能力（启动时一次性检测，避免首次调用超时）
        if preflight_check:
            self._preflight_check()

    def _preflight_check(self):
        """预检测API能力：发送轻量级请求验证API可达性和结构化输出支持"""
        try:
            # 发送一个极简请求测试API是否可达
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
                timeout=15.0,
            )
            if response and response.choices:
                self._api_reachable = True
                logger.info("API预检测通过: model=%s, api_reachable=True", self.model_name)
            else:
                self._api_reachable = False
                logger.warning("API预检测: 空响应, model=%s", self.model_name)
        except Exception as e:
            err_str = str(e).lower()
            # 区分API不可达和模型不存在
            if "timeout" in err_str or "connect" in err_str:
                self._api_reachable = False
                logger.warning("API预检测: 连接失败, model=%s, error=%s", self.model_name, e)
            elif "model" in err_str and ("not found" in err_str or "does not exist" in err_str):
                self._api_reachable = False
                logger.warning("API预检测: 模型不存在, model=%s", self.model_name)
            else:
                # 其他错误（如认证失败），API本身是可达的
                self._api_reachable = True
                logger.info("API预检测: API可达但有错误, model=%s, error=%s", self.model_name, e)

    def set_default_max_tokens(self, max_tokens: int):
        """[Bug] 运行时更新 default_max_tokens，设置修改后立即生效"""
        self.default_max_tokens = max_tokens
        logger.info("MimoLLM default_max_tokens updated to %d", max_tokens)

    def close(self):
        """[Bug] 关闭 httpx 连接池，防止连接泄漏"""
        # 同步客户端直接关闭
        try:
            self.client.close()
        except Exception as e:
            logger.debug("Failed to close sync LLM client: %s", e)
        # 异步客户端：尝试同步关闭，若在运行中的事件循环内则跳过（由 GC 回收）
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_running():
                loop.run_until_complete(self.async_client.close())
            else:
                # 在运行中的事件循环内，无法同步关闭，调度异步关闭
                loop.create_task(self.async_client.close())
        except Exception as e:
            logger.debug("Failed to close async LLM client: %s", e)

    def _cache_key(self, prompt: str, temperature: float, max_tokens: int) -> str:
        raw = f"{prompt}|{temperature}|{max_tokens}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _get_cached(self, key: str) -> str | None:
        with self._cache_lock:
            if key in self._cache:
                ts, val = self._cache[key]
                if time.time() - ts < self._cache_ttl:
                    return val
                del self._cache[key]
            return None

    def _set_cache(self, key: str, val: str):
        with self._cache_lock:
            self._cache[key] = (time.time(), val)
            if len(self._cache) > 500:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest]

    def _get_input_only_key(self, prompt: str) -> str:
        import re
        player_action_match = re.search(r'【玩家输入[^】]*】\s*(.+?)(?:\n\n|$)', prompt, re.DOTALL)
        if player_action_match:
            core_input = player_action_match.group(1).strip()[:500]
        else:
            lines = prompt.split('\n')
            last_lines = [l for l in lines if l.strip()][-5:]
            core_input = '\n'.join(last_lines)[:500]
        return hashlib.md5(core_input.encode()).hexdigest()

    def _get_cached_by_input(self, prompt: str) -> str | None:
        input_key = self._get_input_only_key(prompt)
        if input_key in self._cache:
            ts, val = self._cache[input_key]
            if time.time() - ts < self._cache_ttl * 2:
                return val
            del self._cache[input_key]
        return None

    def _set_cache_by_input(self, prompt: str, val: str):
        input_key = self._get_input_only_key(prompt)
        self._cache[input_key] = (time.time(), val)
        if len(self._cache) > 1000:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]

    def _extract_usage(self, response) -> Tuple[int, int, int]:
        """从response中提取prompt_tokens, completion_tokens, cache_hit_tokens"""
        usage = getattr(response, "usage", None)
        if not usage:
            return 0, 0, 0
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        cache_hit = 0

        try:
            extra = getattr(usage, "model_extra", None) or {}
            if "prompt_cache_hit_tokens" in extra:
                cache_hit = int(extra["prompt_cache_hit_tokens"])
            details = getattr(usage, "prompt_tokens_details", None)
            if details and hasattr(details, "cached_tokens"):
                cache_hit = max(cache_hit, getattr(details, "cached_tokens", 0) or 0)
        except Exception:
            pass

        return prompt_tokens, completion_tokens, cache_hit

    def chat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 0,
             retries: int = 1) -> str:
        # API不可达时直接返回空，避免超时等待
        if not self._api_reachable:
            logger.warning("API不可达，跳过调用: model=%s", self.model_name)
            return ""

        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if max_tokens <= 0:
            max_tokens = 8192
        key = self._cache_key(prompt, temperature, max_tokens)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        start_time = time.time()
        last_error = None
        current_max_tokens = max_tokens
        needs_json = any(kw in prompt for kw in ["JSON", "json", "选项", "narrative"])
        for attempt in range(retries):
            try:
                api_params = {
                    "model": self.model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "max_tokens": current_max_tokens,
                }
                response = self.client.chat.completions.create(**api_params)
                choice = response.choices[0] if response.choices else None
                finish_reason = choice.finish_reason if choice else "no_choices"
                result = choice.message.content if choice and choice.message else None
                if not result or not result.strip():
                    p_tok, c_tok, ch_tok = self._extract_usage(response)
                    logger.warning("LLM返回空内容，finish_reason=%s, model=%s, max_tokens=%s, completion_tokens=%d",
                                   finish_reason, self.model_name, current_max_tokens or "API默认", c_tok)
                    last_error = ValueError(f"LLM返回空内容 (finish_reason={finish_reason})")
                    if finish_reason == "length":
                        if current_max_tokens <= 0:
                            current_max_tokens = 8192
                        else:
                            current_max_tokens = min(current_max_tokens * 2, 32768)
                        logger.info("finish_reason=length，增大 max_tokens 到 %d 重试", current_max_tokens)
                    time.sleep(0.5)
                    continue
                if finish_reason == "length" and result and needs_json:
                    if current_max_tokens <= 0:
                        current_max_tokens = 8192
                    else:
                        current_max_tokens = min(current_max_tokens * 2, 32768)
                    logger.info("finish_reason=length（JSON任务），增大 max_tokens 到 %d 重试", current_max_tokens)
                    time.sleep(0.5)
                    # 如果是最后一次重试，返回已有内容而非抛异常
                    if attempt >= retries - 1:
                        latency = (time.time() - start_time) * 1000
                        p_tok, c_tok, ch_tok = self._extract_usage(response)
                        self.stats.record_call(p_tok, c_tok, ch_tok, latency)
                        self.last_usage = type(self.last_usage)(
                            prompt_tokens=p_tok, completion_tokens=c_tok,
                            cache_hit_tokens=ch_tok, latency_ms=latency, model=self.model_name,
                        )
                        logger.info("chat 返回截断结果，共 %d tokens", c_tok)
                        return result
                    continue
                latency = (time.time() - start_time) * 1000
                p_tok, c_tok, ch_tok = self._extract_usage(response)
                self.stats.record_call(p_tok, c_tok, ch_tok, latency)
                self.last_usage = type(self.last_usage)(
                    prompt_tokens=p_tok, completion_tokens=c_tok,
                    cache_hit_tokens=ch_tok, latency_ms=latency, model=self.model_name
                )
                self._set_cache(key, result)
                return result
            except Exception as e:
                last_error = e
                logger.warning("LLM调用失败，重试 %d/%d: %s", attempt + 1, retries, e)
                time.sleep(0.5)

        latency = (time.time() - start_time) * 1000
        self.stats.record_call(0, 0, 0, latency, failed=True)
        logger.error("LLM调用失败，已重试%d次: %s", retries, last_error)
        raise last_error if last_error else RuntimeError("LLM返回空内容")

    async def achat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 0,
                    retries: int = 1) -> str:
        # API不可达时直接返回空，避免超时等待
        if not self._api_reachable:
            logger.warning("API不可达，跳过异步调用: model=%s", self.model_name)
            return ""

        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if max_tokens <= 0:
            max_tokens = 8192
        key = self._cache_key(prompt, temperature, max_tokens)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        start_time = time.time()
        last_error = None
        current_max_tokens = max_tokens
        needs_json = any(kw in prompt for kw in ["JSON", "json", "选项", "narrative"])
        for attempt in range(retries):
            try:
                api_params = {
                    "model": self.model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "max_tokens": current_max_tokens,
                }
                response = await self.async_client.chat.completions.create(**api_params)
                choice = response.choices[0] if response.choices else None
                finish_reason = choice.finish_reason if choice else "no_choices"
                result = choice.message.content if choice and choice.message else None
                if not result or not result.strip():
                    p_tok, c_tok, ch_tok = self._extract_usage(response)
                    logger.warning("LLM async返回空内容，finish_reason=%s, model=%s, max_tokens=%s, completion_tokens=%d",
                                   finish_reason, self.model_name, current_max_tokens or "API默认", c_tok)
                    last_error = ValueError(f"LLM返回空内容 (finish_reason={finish_reason})")
                    if finish_reason == "length":
                        if current_max_tokens <= 0:
                            current_max_tokens = 8192
                        else:
                            current_max_tokens = min(current_max_tokens * 2, 32768)
                        logger.info("finish_reason=length，增大 max_tokens 到 %d 重试", current_max_tokens)
                    await asyncio.sleep(0.5)
                    continue
                if finish_reason == "length" and result and needs_json:
                    if current_max_tokens <= 0:
                        current_max_tokens = 8192
                    else:
                        current_max_tokens = min(current_max_tokens * 2, 32768)
                    logger.info("async finish_reason=length（JSON任务），增大 max_tokens 到 %d 重试", current_max_tokens)
                    await asyncio.sleep(0.5)
                    # 如果是最后一次重试，返回已有内容而非抛异常
                    if attempt >= retries - 1:
                        latency = (time.time() - start_time) * 1000
                        p_tok, c_tok, ch_tok = self._extract_usage(response)
                        self.stats.record_call(p_tok, c_tok, ch_tok, latency)
                        self.last_usage = type(self.last_usage)(
                            prompt_tokens=p_tok, completion_tokens=c_tok,
                            cache_hit_tokens=ch_tok, latency_ms=latency, model=self.model_name,
                        )
                        logger.info("async chat 返回截断结果，共 %d tokens", c_tok)
                        return result
                    continue
                latency = (time.time() - start_time) * 1000
                p_tok, c_tok, ch_tok = self._extract_usage(response)
                self.stats.record_call(p_tok, c_tok, ch_tok, latency)
                self.last_usage = type(self.last_usage)(
                    prompt_tokens=p_tok, completion_tokens=c_tok,
                    cache_hit_tokens=ch_tok, latency_ms=latency, model=self.model_name
                )
                self._set_cache(key, result)
                return result
            except Exception as e:
                last_error = e
                logger.warning("LLM async调用失败，重试 %d/%d: %s", attempt + 1, retries, e)
                await asyncio.sleep(0.5)

        latency = (time.time() - start_time) * 1000
        self.stats.record_call(0, 0, 0, latency, failed=True)
        logger.error("LLM async调用失败，已重试%d次: %s", retries, last_error)
        raise last_error if last_error else RuntimeError("LLM async返回空内容")

    def chat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 0,
                  retries: int = 2, narrative_hint: str = "500-1000字") -> dict:
        json_prompt = (
            prompt
            + "\n\n【极其重要】你必须输出一个JSON对象，格式如下："
            f'{{"narrative":"你的叙事内容（{narrative_hint}的小说体叙事，要有丰富的细节、心理描写和环境氛围）","options":[{{"id":"A","text":"选项","type":"action","risk":"low"}},{{"id":"B","text":"选项","type":"action","risk":"medium"}},{{"id":"C","text":"选项","type":"action","risk":"high"}}]}}'
            "\n【绝对禁止】不要省略narrative字段！不要省略options字段！不要输出任何JSON以外的文字！"
        )

        last_error = None
        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if max_tokens <= 0:
            max_tokens = 8192
        current_max_tokens = max_tokens
        for attempt in range(retries):
            try:
                raw = self.chat(json_prompt, temperature=temperature, max_tokens=current_max_tokens, retries=1)
                result = self._parse_json(raw)
                if "error" not in result:
                    return result
                self._cache.pop(self._cache_key(json_prompt, temperature, current_max_tokens), None)
                is_truncated = self._is_likely_truncated(raw)
                if is_truncated:
                    if current_max_tokens <= 0:
                        current_max_tokens = 8192
                    else:
                        current_max_tokens = min(current_max_tokens * 2, 32768)
                    logger.warning("JSON疑似被截断，增大 max_tokens 重试: → %d", current_max_tokens)
                else:
                    logger.warning("JSON解析失败，重试 %d/%d: %s", attempt + 1, retries, result.get("error", ""))
                    if current_max_tokens <= 0:
                        current_max_tokens = 8192
                    else:
                        current_max_tokens = min(current_max_tokens * 2, 32768)
                time.sleep(0.5)
            except Exception as e:
                last_error = e
                logger.warning("chat_json失败，重试 %d/%d: %s", attempt + 1, retries, e)
                if current_max_tokens <= 0:
                    current_max_tokens = 8192
                else:
                    current_max_tokens = min(current_max_tokens * 2, 32768)
                time.sleep(0.5)

        logger.error("chat_json最终失败: %s", last_error)
        return {"error": str(last_error), "narrative": "", "options": []}

    async def achat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 0,
                         retries: int = 2, narrative_hint: str = "500-1000字") -> dict:
        json_prompt = (
            prompt
            + "\n\n【极其重要】你必须输出一个JSON对象，格式如下："
            f'{{"narrative":"你的叙事内容（{narrative_hint}的小说体叙事，要有丰富的细节、心理描写和环境氛围）","options":[{{"id":"A","text":"选项","type":"action","risk":"low"}},{{"id":"B","text":"选项","type":"action","risk":"medium"}},{{"id":"C","text":"选项","type":"action","risk":"high"}}]}}'
            "\n【绝对禁止】不要省略narrative字段！不要省略options字段！不要输出任何JSON以外的文字！"
        )
        last_error = None
        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if max_tokens <= 0:
            max_tokens = 8192
        current_max_tokens = max_tokens
        for attempt in range(retries):
            try:
                raw = await self.achat(json_prompt, temperature=temperature, max_tokens=current_max_tokens, retries=1)
                result = self._parse_json(raw)
                if "error" not in result:
                    return result
                self._cache.pop(self._cache_key(json_prompt, temperature, current_max_tokens), None)
                is_truncated = self._is_likely_truncated(raw)
                if is_truncated:
                    if current_max_tokens <= 0:
                        current_max_tokens = 8192
                    else:
                        current_max_tokens = min(current_max_tokens * 2, 32768)
                    logger.warning("async JSON疑似被截断，增大 max_tokens 重试: → %d", current_max_tokens)
                else:
                    logger.warning("async JSON解析失败，重试 %d/%d: %s", attempt + 1, retries, result.get("error", ""))
                    if current_max_tokens <= 0:
                        current_max_tokens = 8192
                    else:
                        current_max_tokens = min(current_max_tokens * 2, 32768)
                await asyncio.sleep(0.5)
            except Exception as e:
                last_error = e
                logger.warning("achat_json失败，重试 %d/%d: %s", attempt + 1, retries, e)
                if current_max_tokens <= 0:
                    current_max_tokens = 8192
                else:
                    current_max_tokens = min(current_max_tokens * 2, 32768)
                await asyncio.sleep(0.5)

        logger.error("achat_json最终失败: %s", last_error)
        return {"error": str(last_error) if last_error else "achat_json失败", "narrative": "", "options": []}

    def chat_json_from_messages(self, messages: list[dict], temperature: float = 0.4, max_tokens: int = 0,
                                retries: int = 2, narrative_hint: str = "500-1000字") -> dict:
        # API不可达时直接返回错误，避免超时等待
        if not self._api_reachable:
            logger.warning("API不可达，跳过chat_json_from_messages: model=%s", self.model_name)
            return {"error": "API不可达", "narrative": "", "options": []}

        json_hint = (
            "\n\n【极其重要】你必须输出一个JSON对象，格式如下："
            f'{{"narrative":"你的叙事内容（{narrative_hint}的小说体叙事，要有丰富的细节、心理描写和环境氛围）","options":[{{"id":"A","text":"选项","type":"action","risk":"low"}},{{"id":"B","text":"选项","type":"action","risk":"medium"}},{{"id":"C","text":"选项","type":"action","risk":"high"}}]}}'
            "\n【绝对禁止】不要省略narrative字段！不要省略options字段！不要输出任何JSON以外的文字！"
        )
        final_messages = list(messages)
        if final_messages and final_messages[-1]["role"] == "user":
            final_messages[-1] = {"role": "user", "content": final_messages[-1]["content"] + json_hint}
        else:
            final_messages.append({"role": "user", "content": json_hint})

        start_time = time.time()
        last_error = None
        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if max_tokens <= 0:
            max_tokens = 8192
        current_max_tokens = max_tokens
        for attempt in range(retries):
            try:
                api_params = {
                    "model": self.model_name,
                    "messages": final_messages,
                    "temperature": temperature,
                    "max_tokens": current_max_tokens,
                }
                response = self.client.chat.completions.create(**api_params)
                choice = response.choices[0] if response.choices else None
                finish_reason = choice.finish_reason if choice else "no_choices"
                raw = choice.message.content if choice and choice.message else None
                if not raw or not raw.strip():
                    logger.warning("chat_json_from_messages返回空内容，finish_reason=%s, model=%s, max_tokens=%s",
                                   finish_reason, self.model_name, current_max_tokens or "API默认")
                    last_error = ValueError(f"LLM返回空内容 (finish_reason={finish_reason})")
                    if finish_reason == "length":
                        if current_max_tokens <= 0:
                            current_max_tokens = 8192
                        else:
                            current_max_tokens = min(current_max_tokens * 2, 32768)
                        logger.info("finish_reason=length，增大 max_tokens 到 %d 重试", current_max_tokens)
                    time.sleep(0.5)
                    continue
                latency = (time.time() - start_time) * 1000
                p_tok, c_tok, ch_tok = self._extract_usage(response)
                self.stats.record_call(p_tok, c_tok, ch_tok, latency)
                result = self._parse_json(raw)
                if "error" in result or not result.get("narrative"):
                    if self._is_likely_truncated(raw):
                        current_max_tokens = min(current_max_tokens * 2, 32768)
                        logger.info("JSON 截断检测，增大 max_tokens 到 %d 重试", current_max_tokens)
                    last_error = ValueError(result.get("error", "解析失败或缺少narrative"))
                    time.sleep(0.5)
                    continue
                return result
            except Exception as e:
                last_error = e
                logger.warning("chat_json_from_messages调用失败，重试 %d/%d: %s", attempt + 1, retries, e)
                time.sleep(0.5)

        latency = (time.time() - start_time) * 1000
        self.stats.record_call(0, 0, 0, latency, failed=True)
        logger.error("chat_json_from_messages最终失败: %s", last_error)
        return {"error": str(last_error), "narrative": "", "options": []}
    async def achat_json_from_messages(self, messages: list[dict], temperature: float = 0.4,
                                       max_tokens: int = 0,
                                       retries: int = 2, narrative_hint: str = "500-1000字") -> dict:
        # API不可达时直接返回错误，避免超时等待
        if not self._api_reachable:
            logger.warning("API不可达，跳过achat_json_from_messages: model=%s", self.model_name)
            return {"error": "API不可达", "narrative": "", "options": []}

        json_hint = (
            "\n\n【极其重要】你必须输出一个JSON对象，格式如下："
            f'{{"narrative":"你的叙事内容（{narrative_hint}的小说体叙事，要有丰富的细节、心理描写和环境氛围）","options":[{{"id":"A","text":"选项","type":"action","risk":"low"}},{{"id":"B","text":"选项","type":"action","risk":"medium"}},{{"id":"C","text":"选项","type":"action","risk":"high"}}]}}'
            "\n【绝对禁止】不要省略narrative字段！不要省略options字段！不要输出任何JSON以外的文字！"
        )
        final_messages = list(messages)
        if final_messages and final_messages[-1]["role"] == "user":
            final_messages[-1] = {"role": "user", "content": final_messages[-1]["content"] + json_hint}
        else:
            final_messages.append({"role": "user", "content": json_hint})

        start_time = time.time()
        last_error = None
        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if max_tokens <= 0:
            max_tokens = 8192
        current_max_tokens = max_tokens
        for attempt in range(retries):
            try:
                api_params = {
                    "model": self.model_name,
                    "messages": final_messages,
                    "temperature": temperature,
                    "max_tokens": current_max_tokens,
                }
                response = await self.async_client.chat.completions.create(**api_params)
                choice = response.choices[0] if response.choices else None
                finish_reason = choice.finish_reason if choice else "no_choices"
                raw = choice.message.content if choice and choice.message else None
                if not raw or not raw.strip():
                    logger.warning("achat_json_from_messages返回空内容，finish_reason=%s, model=%s, max_tokens=%s",
                                   finish_reason, self.model_name, current_max_tokens or "API默认")
                    last_error = ValueError(f"LLM返回空内容 (finish_reason={finish_reason})")
                    if finish_reason == "length":
                        if current_max_tokens <= 0:
                            current_max_tokens = 8192
                        else:
                            current_max_tokens = min(current_max_tokens * 2, 32768)
                        logger.info("finish_reason=length，增大 max_tokens 到 %d 重试", current_max_tokens)
                    await asyncio.sleep(0.5)
                    continue
                latency = (time.time() - start_time) * 1000
                p_tok, c_tok, ch_tok = self._extract_usage(response)
                self.stats.record_call(p_tok, c_tok, ch_tok, latency)
                result = self._parse_json(raw)
                if "error" in result or not result.get("narrative"):
                    if self._is_likely_truncated(raw):
                        current_max_tokens = min(current_max_tokens * 2, 32768)
                        logger.info("JSON 截断检测，增大 max_tokens 到 %d 重试", current_max_tokens)
                    last_error = ValueError(result.get("error", "解析失败或缺少narrative"))
                    await asyncio.sleep(0.5)
                    continue
                return result
            except Exception as e:
                last_error = e
                logger.warning("achat_json_from_messages调用失败，重试 %d/%d: %s", attempt + 1, retries, e)
                await asyncio.sleep(0.5)

        latency = (time.time() - start_time) * 1000
        self.stats.record_call(0, 0, 0, latency, failed=True)
        logger.error("achat_json_from_messages最终失败: %s", last_error)
        return {"error": str(last_error) if last_error else "achat_json_from_messages失败", "narrative": "", "options": []}

    def chat_structured(self, prompt: str, schema_name: str, temperature: float = 0.7, max_tokens: int = 0,
                        narrative_hint: str = "500-1000字") -> dict:
        """结构化输出：使用 JSON Schema 约束 LLM 输出。"""
        # API不可达时直接返回空，避免超时等待
        if not self._api_reachable:
            logger.warning("API不可达，跳过chat_structured: model=%s", self.model_name)
            return {}

        from .structured_output import StructuredOutputManager, get_narrative_schema
        request_timeout = 180.0 if schema_name == "world" else 60.0

        # [Bug#5] 规范化 max_tokens，避免传 0 给 API
        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if max_tokens <= 0:
            max_tokens = 8192

        # 构建 API 参数和结构化 prompt（叙事 schema 使用配置的字数）
        if schema_name == "narrative":
            from .structured_output import NARRATIVE_SCHEMA
            import copy
            try:
                hint_max = int(narrative_hint.split("-")[-1].replace("字", "")) if "-" in narrative_hint else int(narrative_hint.replace("字", ""))
            except (ValueError, IndexError):
                hint_max = 1000
            custom_schema = get_narrative_schema(hint_max)
            api_params = {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": custom_schema, "strict": False}
                }
            }
            structured_prompt = StructuredOutputManager.build_structured_prompt(prompt, schema_name)
            # 替换 schema 中的字数提示
            structured_prompt = structured_prompt.replace("500-1000字", narrative_hint)
        else:
            api_params = StructuredOutputManager.build_api_params(schema_name)
            structured_prompt = StructuredOutputManager.build_structured_prompt(prompt, schema_name)

        # [Bug] 如果API不支持response_format或API不可达，直接跳过，避免每次浪费调用
        if self._structured_supported and self._api_reachable:
            # 第一阶段：尝试使用 response_format 参数（如果 API 支持）
            try:
                start_time = time.time()
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": structured_prompt}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=request_timeout,
                    **api_params,
                )
                choice = response.choices[0] if response.choices else None
                raw = choice.message.content if choice and choice.message else None
                if raw and raw.strip():
                    latency = (time.time() - start_time) * 1000
                    p_tok, c_tok, ch_tok = self._extract_usage(response)
                    self.stats.record_call(p_tok, c_tok, ch_tok, latency)
                    self.last_usage = type(self.last_usage)(
                        prompt_tokens=p_tok, completion_tokens=c_tok,
                        cache_hit_tokens=ch_tok, latency_ms=latency, model=self.model_name
                    )
                    result = json.loads(raw)
                    valid, err = StructuredOutputManager.validate(result, schema_name)
                    if valid:
                        logger.debug("Structured output (response_format) succeeded: schema=%s", schema_name)
                        return result
                    logger.warning("Structured output validation failed: %s, retrying with prompt-only", err)
                else:
                    logger.warning("Structured API returned empty content, falling back to prompt-based")
            except Exception as e:
                err_str = str(e)
                if "response_format" in err_str or "unavailable" in err_str:
                    self._structured_supported = False
                    logger.info("API不支持structured output，后续调用跳过response_format")
                logger.warning("Structured API call failed: %s, falling back to prompt-based", e)

        # 第二阶段：回退到仅用 prompt 约束（chat + _parse_json）
        try:
            # 世界生成 JSON 很长，使用流式请求避免整体超时。
            # [Bugfix] 同步请求的 timeout 是整个生成的上限，32768 tokens 首次调用
            # (冷启动/排队) 容易超过 180s；流式只要持续有 token 返回就不会超时。
            if schema_name == "world":
                start_time = time.time()
                stream = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": structured_prompt}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    timeout=request_timeout,
                )
                raw_parts: list[str] = []
                finish_reason = None
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        raw_parts.append(chunk.choices[0].delta.content)
                    if chunk.choices and chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                raw = "".join(raw_parts)
                latency = (time.time() - start_time) * 1000
                # 流式响应无 usage，按字符数粗略估算 tokens
                est_tokens = len(raw) // 3
                self.stats.record_call(0, est_tokens, 0, latency)
                self.last_usage = type(self.last_usage)(
                    prompt_tokens=0, completion_tokens=est_tokens,
                    cache_hit_tokens=0, latency_ms=latency, model=self.model_name
                )
                logger.info("World gen stream done: %d chars, finish_reason=%s, %.1fs",
                            len(raw), finish_reason, latency / 1000)
                if not raw.strip():
                    logger.warning("World gen stream returned empty, finish_reason=%s", finish_reason)
            else:
                raw = self.chat(structured_prompt, temperature=temperature, max_tokens=max_tokens)
            result = self._parse_json(raw)
            if result and "error" not in result:
                valid, err = StructuredOutputManager.validate(result, schema_name)
                if valid:
                    logger.debug("Structured output (prompt fallback) succeeded: schema=%s", schema_name)
                    return result
                logger.warning("Fallback validation failed: %s", err)
                return result  # 返回即使不完美，避免完全失败
            return result or {"error": "解析失败"}
        except Exception as e:
            logger.error("Structured output completely failed: %s", e)
            return {"error": str(e)}

    def chat_stream(self, prompt: str | list[dict], temperature: float = 0.8,
                    max_tokens: int = 0) -> Generator[str, None, None]:
        """流式聊天：逐 token 实时生成，返回生成器。
        prompt 可以是字符串（兼容旧调用方）或 messages 列表（保留角色信息）。
        [v11-fix] 改为实时 yield：每个 token 立即传出，不再缓冲。
        截断时用 2x max_tokens 非流式重试，只 yield 增量部分。"""
        # API不可达时直接返回空，避免超时等待
        if not self._api_reachable:
            logger.warning("API不可达，跳过chat_stream: model=%s", self.model_name)
            yield ""
            return

        # [v10.6] 统一 max_tokens 逻辑
        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if max_tokens <= 0:
            max_tokens = 16384

        # 构建 messages
        if isinstance(prompt, list):
            messages = prompt
        else:
            messages = [{"role": "user", "content": prompt}]

        # ── 实时流式生成：逐 token yield，同时记录 finish_reason ──
        finish_reason = None
        total_chars_sent = 0
        token_count = 0
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    total_chars_sent += len(token)
                    token_count += 1
                    yield token  # 立即 yield，不缓冲
                if chunk.choices and chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
        except Exception as e:
            logger.error("LLM stream chat failed: %s", e)
            return

        if token_count == 0:
            logger.warning("chat_stream返回空流，model=%s", self.model_name)
            return

        logger.info(
            "chat_stream 完成: %d tokens, %d chars, finish_reason=%s",
            token_count, total_chars_sent, finish_reason,
        )

        # ── 正常结束：无需额外操作 ──
        if finish_reason != "length":
            return

        # ── 截断了：用 2x max_tokens 非流式重试，只 yield 增量部分 ──
        retry_max = min(max_tokens * 2, 32768)
        if retry_max <= max_tokens:
            logger.warning("chat_stream 截断但已达最大 token 上限: %d", max_tokens)
            return

        logger.warning(
            "chat_stream 截断 (finish_reason=length, %d tokens, %d chars sent)，"
            "重试增大到 %d tokens",
            max_tokens, total_chars_sent, retry_max,
        )
        try:
            retry_response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=retry_max,
                stream=False,
            )
            choice = retry_response.choices[0] if retry_response.choices else None
            if choice and choice.message and choice.message.content:
                full_text = choice.message.content
                # 只 yield 已发送内容之后的增量部分，避免前端重复
                if len(full_text) > total_chars_sent:
                    extra = full_text[total_chars_sent:]
                    logger.info(
                        "chat_stream 截断重试成功: 原文 %d 字 → 全文 %d 字，增量 %d 字",
                        total_chars_sent, len(full_text), len(extra),
                    )
                    yield extra
                else:
                    logger.info("chat_stream 截断重试: 全文长度 %d 未超过已发送 %d，无增量",
                                len(full_text), total_chars_sent)
            else:
                logger.warning("chat_stream 截断重试返回空内容")
        except Exception as e:
            logger.error("chat_stream 截断重试失败: %s", e)

    def _parse_json(self, raw: str) -> dict:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        cleaned = re.sub(r'```json\s*', '', cleaned)
        cleaned = re.sub(r'```\s*$', '', cleaned)
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
        # [Bug#23] 移除 /* */ 注释清理正则 — 该正则会破坏 JSON 字符串值内的内容
        # 如叙事中包含 "/* 战斗 */" 这类文本会被错误删除，导致 JSON 解析失败
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        match = re.search(r'(\{[\s\S]*\})', cleaned)
        if match:
            candidate = match.group(1)
            candidate = re.sub(r',\s*([}\]])', r'\1', candidate)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
            # [Bug] 尝试截取到第一个完整的顶层对象（深度归零处）
            depth = 0
            last_close = -1
            for i, ch in enumerate(candidate):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        last_close = i
                        break
            if last_close > 0:
                try:
                    return json.loads(candidate[:last_close + 1])
                except json.JSONDecodeError:
                    pass
            # [Bug] 移除"提取最大子对象"回退逻辑——该逻辑会返回截断的部分JSON，
            # 导致调用方收到结构不完整的数据（如只返回locations字典）。
            # 正确做法是返回error，让上层重试或降级处理。
        logger.warning("JSON parse failed, raw=%.200s", raw)
        return {"error": "JSON解析失败", "raw": raw}

    def _is_likely_truncated(self, raw: str) -> bool:
        """[Bug] 检测 JSON 是否因 finish_reason=length 被截断"""
        if not raw:
            return False
        stripped = raw.strip()
        # 常见截断特征：末尾是未闭合的字符串、逗号、冒号
        if stripped.endswith((',', ':')):
            return True
        # [Bug#22] 只计算字符串外部的括号，避免叙事内容中的 {} [] 导致误判
        depth = 0
        in_string = False
        escape = False
        for ch in stripped:
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in '{[':
                depth += 1
            elif ch in '}]':
                depth -= 1
        if depth > 0:
            return True
        # 末尾是引号且不在字符串内 = 未闭合的字符串值
        if stripped.endswith('"') and not in_string:
            # 正常结束的 JSON 最后一个字符应该是 }，不是 "
            # 但如果整个内容就是一个字符串，这种情况除外
            if stripped.count('{') > 0:
                return True
        return False
