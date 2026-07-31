from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from typing import Generator, Tuple

from openai import OpenAI, AsyncOpenAI
from .base_llm import BaseLLM
# [v1.7 P3-A] LLM 调用耗时埋点（进 TimingCollector，/api/timing 可查）
from ..core.timing import timed, TimingCollectorInstance

logger = logging.getLogger("chronoverse.llm")


class MimoLLM(BaseLLM):
    def __init__(self, api_key: str, base_url: str, model_name: str = "mimo-V2.5-Pro",
                 default_max_tokens: int = 0, preflight_check: bool = True,
                 runtime_cfg: "dict | None" = None):
        super().__init__()
        # [v1.7 P2-4] 运行时参数从 config 注入，无 config 时回退到原硬编码默认值
        cfg = runtime_cfg or {}
        self._timeout = float(cfg.get("timeout", 60.0))
        self._max_retries = int(cfg.get("max_retries", 0))
        self._preflight_timeout = float(cfg.get("preflight_timeout", 15.0))
        self._retry_sleep_sec = float(cfg.get("retry_sleep_sec", 0.5))
        self._fallback_max_tokens = int(cfg.get("default_max_tokens", 8192))
        self._max_tokens_cap = int(cfg.get("max_tokens_cap", 32768))
        self._stream_default_max_tokens = int(cfg.get("stream_default_max_tokens", 16384))
        self._world_gen_timeout = float(cfg.get("world_gen_timeout", 180.0))
        self._structured_timeout = float(cfg.get("structured_timeout", 60.0))
        # 单次接口调用不要等太久；路由层会负责切换备用模型。
        # 关闭 SDK 自动重试，避免 SDK 重试 + 项目重试叠加导致卡顿一分钟以上。
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=self._timeout, max_retries=self._max_retries)
        self.async_client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=self._timeout, max_retries=self._max_retries)
        self.model_name = model_name
        self.default_max_tokens = default_max_tokens  # 0 = 不限制，使用API默认值
        # [v1.4] 双重缓存治理：_cache 字段保留作为向后兼容占位（外部直接访问时不会 AttributeError），
        # 但所有缓存读写都优先委托给 external_cache（LLMCache 实例）。
        # 若未注入 external_cache，则不进行任何缓存（避免与 LLMCache 不一致）。
        self._cache: dict[str, tuple[float, str]] = {}
        self._cache_ttl = 300
        self._cache_lock = threading.Lock()  # [Bug#24] 保护缓存的并发读写
        self._external_cache = None  # type: ignore[var-annotated]  # LLMCache 实例（由 GameEngine 注入）
        self._cache_disabled_locally = False  # 仅在 LLMCache 内部出错时退回无缓存模式
        # [Bug] DeepSeek等API不支持response_format，根据模型名自动判断
        _no_structured = ["deepseek", "qwen", "glm", "yi"]
        self._structured_supported = not any(n in model_name.lower() for n in _no_structured)
        # [v1.4 P1-6] json_object 模式比 json_schema 更通用，大多数 OpenAI 兼容 API 都支持
        # 只有明确不支持的模型才关闭（首次调用失败后自动降级）
        self._json_object_supported = True
        self._api_reachable = True  # API是否可达

        # 预检测API能力（启动时一次性检测，避免首次调用超时）
        if preflight_check:
            self._preflight_check()

    def set_external_cache(self, cache):
        """[v1.4] 注入外部 LLMCache 实例，统一缓存层。
        注入后 MimoLLM 内部 _cache 不再被读写，所有缓存走 LLMCache。"""
        self._external_cache = cache
        # 清空旧 _cache，避免历史数据混淆
        with self._cache_lock:
            self._cache.clear()
        logger.info("MimoLLM external cache injected: %s", type(cache).__name__ if cache else "None")

    def _preflight_check(self):
        """预检测API能力：发送轻量级请求验证API可达性和结构化输出支持"""
        try:
            # 发送一个极简请求测试API是否可达
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
                timeout=self._preflight_timeout,
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
                logger.warning("API预检测: 连接失败, model=%s, error=%s", self.model_name, e, exc_info=True)
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
            logger.debug("Failed to close sync LLM client: %s", e, exc_info=True)
        # 异步客户端：尝试同步关闭，若在运行中的事件循环内则跳过（由 GC 回收）
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_running():
                loop.run_until_complete(self.async_client.close())
            else:
                # 在运行中的事件循环内，无法同步关闭，调度异步关闭
                loop.create_task(self.async_client.close())
        except Exception as e:
            logger.debug("Failed to close async LLM client: %s", e, exc_info=True)

    def _cache_key(self, prompt: str, temperature: float, max_tokens: int,
                   response_format: dict | None = None) -> str:
        # [v1.4 P1-6] response_format 纳入 cache_key，避免不同模式缓存冲突
        rf = json.dumps(response_format, sort_keys=True) if response_format else ""
        raw = f"{prompt}|{temperature}|{max_tokens}|{rf}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _get_cached(self, key: str) -> str | None:
        """[v1.4] 优先委托给 external_cache（LLMCache）；未注入时不缓存。
        [P4-A-2-D] 修复命中率统计：命中/未命中时更新 LLMCache.hit_count/miss_count。"""
        if self._external_cache is not None and not self._cache_disabled_locally:
            try:
                # LLMCache 用 (prompt, temperature) 作 key，这里用 cache_key 反查 prompt 不可行
                # 改为：直接用 key 在 LLMCache.cache 中查找（key 格式不同，但 _store 存了原始 prompt）
                # 实际方案：在 _set_cache 时同步写入 LLMCache，_get_cached 也走 LLMCache
                # LLMCache 的 key 是 _make_key(prompt, temperature)，与我们这里的 key 不同
                # 因此这里维持一个 key → prompt 的本地小映射（<50条），命中后查 LLMCache
                # 但更简单的做法：让 LLMCache 暴露 raw get/set，直接用我们的 key
                cache_dict = self._external_cache.cache
                entry = cache_dict.get(key)
                if entry is not None:
                    # TTL 检查（与 LLMCache._is_expired 一致）
                    if self._external_cache.ttl > 0:
                        age = time.time() - entry.get("timestamp", 0)
                        if age > self._external_cache.ttl:
                            try:
                                del cache_dict[key]
                                self._external_cache.expired_count += 1
                                # [P4-A-2-D] 过期计入 miss
                                self._external_cache.miss_count += 1
                            except Exception:
                                pass
                            return None
                    # [P4-A-2-D] 命中计入 hit
                    self._external_cache.hit_count += 1
                    return entry.get("response")
                # [P4-A-2-D] 未命中计入 miss
                self._external_cache.miss_count += 1
                return None
            except Exception as e:
                logger.debug("external_cache get failed, disable locally: %s", e, exc_info=True)
                self._cache_disabled_locally = True
                return None
        # 未注入 external_cache：不缓存（避免双重缓存不一致）
        return None

    def _set_cache(self, key: str, val: str):
        """[v1.4] 写入 external_cache（LLMCache）；未注入时不缓存。"""
        if self._external_cache is not None and not self._cache_disabled_locally:
            try:
                # 直接复用 LLMCache 的存储结构，绕过 _make_key
                cache = self._external_cache.cache
                # LRU 淘汰
                if len(cache) >= self._external_cache.max_size:
                    oldest = min(cache.keys(), key=lambda k: cache[k].get("timestamp", 0))
                    try:
                        del cache[oldest]
                    except Exception:
                        pass
                cache[key] = {
                    "prompt": "",  # 不再存储 prompt（key 已是哈希），节省内存
                    "response": val,
                    "temperature": 0.0,  # 未知，用 0 占位
                    "timestamp": time.time(),
                }
            except Exception as e:
                logger.debug("external_cache set failed, disable locally: %s", e, exc_info=True)
                self._cache_disabled_locally = True

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
        """[v1.4] 输入级缓存（语义模糊匹配），委托给 LLMCache._find_semantic_match"""
        if self._external_cache is not None and not self._cache_disabled_locally:
            try:
                # 使用 LLMCache 的语义匹配能力
                sem_key = self._external_cache._find_semantic_match(prompt, 0.8)
                if sem_key:
                    entry = self._external_cache.cache.get(sem_key)
                    if entry:
                        return entry.get("response")
                return None
            except Exception as e:
                logger.debug("external_cache semantic get failed: %s", e, exc_info=True)
                return None
        return None

    def _set_cache_by_input(self, prompt: str, val: str):
        """[v1.4] 写入语义缓存（用 LLMCache._store）"""
        if self._external_cache is not None and not self._cache_disabled_locally:
            try:
                # 用 prompt 直接作为 key 的一部分（避免与精确缓存冲突）
                key = "input_" + hashlib.md5(prompt[:500].encode()).hexdigest()
                self._set_cache(key, val)
            except Exception as e:
                logger.debug("external_cache semantic set failed: %s", e, exc_info=True)

    def _invalidate_cache(self, key: str):
        """[v1.4] 失效缓存条目（替代直接 self._cache.pop 调用）。"""
        if self._external_cache is not None and not self._cache_disabled_locally:
            try:
                self._external_cache.cache.pop(key, None)
            except Exception:
                pass
        # 也清理本地占位 dict（向后兼容）
        with self._cache_lock:
            self._cache.pop(key, None)

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

    @timed(category="llm", label="mimo_chat")
    def chat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 0,
             retries: int = 1, response_format: dict | None = None) -> str:
        # API不可达时直接返回空，避免超时等待
        if not self._api_reachable:
            logger.warning("API不可达，跳过调用: model=%s", self.model_name)
            return ""

        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if max_tokens <= 0:
            max_tokens = self._fallback_max_tokens
        key = self._cache_key(prompt, temperature, max_tokens, response_format)
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
                # [v1.4 P1-6] 支持 response_format 参数（json_object / json_schema）
                if response_format is not None:
                    api_params["response_format"] = response_format
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
                            current_max_tokens = self._fallback_max_tokens
                        else:
                            current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                        logger.info("finish_reason=length，增大 max_tokens 到 %d 重试", current_max_tokens)
                    time.sleep(self._retry_sleep_sec)
                    continue
                if finish_reason == "length" and result and needs_json:
                    if current_max_tokens <= 0:
                        current_max_tokens = self._fallback_max_tokens
                    else:
                        current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                    logger.info("finish_reason=length（JSON任务），增大 max_tokens 到 %d 重试", current_max_tokens)
                    time.sleep(self._retry_sleep_sec)
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
                logger.warning("LLM调用失败，重试 %d/%d: %s", attempt + 1, retries, e, exc_info=True)
                time.sleep(self._retry_sleep_sec)

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
            max_tokens = self._fallback_max_tokens
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
                            current_max_tokens = self._fallback_max_tokens
                        else:
                            current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                        logger.info("finish_reason=length，增大 max_tokens 到 %d 重试", current_max_tokens)
                    await asyncio.sleep(self._retry_sleep_sec)
                    continue
                if finish_reason == "length" and result and needs_json:
                    if current_max_tokens <= 0:
                        current_max_tokens = self._fallback_max_tokens
                    else:
                        current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                    logger.info("async finish_reason=length（JSON任务），增大 max_tokens 到 %d 重试", current_max_tokens)
                    await asyncio.sleep(self._retry_sleep_sec)
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
                logger.warning("LLM async调用失败，重试 %d/%d: %s", attempt + 1, retries, e, exc_info=True)
                await asyncio.sleep(self._retry_sleep_sec)

        latency = (time.time() - start_time) * 1000
        self.stats.record_call(0, 0, 0, latency, failed=True)
        logger.error("LLM async调用失败，已重试%d次: %s", retries, last_error)
        raise last_error if last_error else RuntimeError("LLM async返回空内容")

    @timed(category="llm", label="mimo_chat_json")
    def chat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 0,
                  retries: int = 2, narrative_hint: str = "500-1000字",
                  schema_hint: str = "") -> dict:
        # [v12修复] schema_hint 允许调用方指定JSON格式，
        # 避免硬编码游戏叙事格式导致 GraphRAG 提取实体时格式不匹配
        if schema_hint:
            json_prompt = (
                prompt
                + "\n\n【极其重要】你必须输出一个JSON对象，格式如下：\n"
                + schema_hint
                + "\n\n【绝对禁止】不要输出任何JSON以外的文字！不要省略任何字段！"
            )
        else:
            # 默认：游戏叙事格式（保持向后兼容）
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
            max_tokens = self._fallback_max_tokens
        current_max_tokens = max_tokens
        # [v1.4 P1-6] 使用 response_format={"type":"json_object"} 原生 JSON 模式
        # 让 API 保证输出有效 JSON，减少解析失败率
        rf = {"type": "json_object"} if self._json_object_supported else None
        for attempt in range(retries):
            try:
                raw = self.chat(json_prompt, temperature=temperature,
                                max_tokens=current_max_tokens, retries=1,
                                response_format=rf)
                result = self._parse_json(raw)
                if "error" not in result:
                    return result
                self._invalidate_cache(self._cache_key(json_prompt, temperature, current_max_tokens, rf))
                # [v1.4 P1-6] 如果 json_object 模式失败，可能是 API 不支持，降级
                if rf is not None and attempt == 0:
                    self._json_object_supported = False
                    rf = None
                    logger.info("json_object 模式失败，降级为纯 prompt 模式")
                    # 失效旧缓存（带 rf 的 key）
                    self._invalidate_cache(self._cache_key(json_prompt, temperature, current_max_tokens, {"type": "json_object"}))
                is_truncated = self._is_likely_truncated(raw)
                if is_truncated:
                    if current_max_tokens <= 0:
                        current_max_tokens = self._fallback_max_tokens
                    else:
                        current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                    logger.warning("JSON疑似被截断，增大 max_tokens 重试: → %d", current_max_tokens)
                else:
                    logger.warning("JSON解析失败，重试 %d/%d: %s", attempt + 1, retries, result.get("error", ""))
                    if current_max_tokens <= 0:
                        current_max_tokens = self._fallback_max_tokens
                    else:
                        current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                time.sleep(self._retry_sleep_sec)
            except Exception as e:
                last_error = e
                logger.warning("chat_json失败，重试 %d/%d: %s", attempt + 1, retries, e, exc_info=True)
                # [v1.4 P1-6] 异常时也尝试降级 json_object
                if rf is not None and attempt == 0:
                    err_str = str(e).lower()
                    if "response_format" in err_str or "json" in err_str or "unavailable" in err_str:
                        self._json_object_supported = False
                        rf = None
                        logger.info("API不支持json_object模式，降级为纯 prompt 模式")
                if current_max_tokens <= 0:
                    current_max_tokens = self._fallback_max_tokens
                else:
                    current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                time.sleep(self._retry_sleep_sec)

        logger.error("chat_json最终失败: %s", last_error)
        return {"error": str(last_error), "narrative": "", "options": []}

    async def achat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 0,
                         retries: int = 2, narrative_hint: str = "500-1000字",
                         schema_hint: str = "") -> dict:
        # [v12修复] 同 chat_json：支持 schema_hint 自定义JSON格式
        if schema_hint:
            json_prompt = (
                prompt
                + "\n\n【极其重要】你必须输出一个JSON对象，格式如下：\n"
                + schema_hint
                + "\n\n【绝对禁止】不要输出任何JSON以外的文字！不要省略任何字段！"
            )
        else:
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
            max_tokens = self._fallback_max_tokens
        current_max_tokens = max_tokens
        for attempt in range(retries):
            try:
                raw = await self.achat(json_prompt, temperature=temperature, max_tokens=current_max_tokens, retries=1)
                result = self._parse_json(raw)
                if "error" not in result:
                    return result
                self._invalidate_cache(self._cache_key(json_prompt, temperature, current_max_tokens, None))
                is_truncated = self._is_likely_truncated(raw)
                if is_truncated:
                    if current_max_tokens <= 0:
                        current_max_tokens = self._fallback_max_tokens
                    else:
                        current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                    logger.warning("async JSON疑似被截断，增大 max_tokens 重试: → %d", current_max_tokens)
                else:
                    logger.warning("async JSON解析失败，重试 %d/%d: %s", attempt + 1, retries, result.get("error", ""))
                    if current_max_tokens <= 0:
                        current_max_tokens = self._fallback_max_tokens
                    else:
                        current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                await asyncio.sleep(self._retry_sleep_sec)
            except Exception as e:
                last_error = e
                logger.warning("achat_json失败，重试 %d/%d: %s", attempt + 1, retries, e, exc_info=True)
                if current_max_tokens <= 0:
                    current_max_tokens = self._fallback_max_tokens
                else:
                    current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                await asyncio.sleep(self._retry_sleep_sec)

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
            max_tokens = self._fallback_max_tokens
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
                            current_max_tokens = self._fallback_max_tokens
                        else:
                            current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                        logger.info("finish_reason=length，增大 max_tokens 到 %d 重试", current_max_tokens)
                    time.sleep(self._retry_sleep_sec)
                    continue
                latency = (time.time() - start_time) * 1000
                p_tok, c_tok, ch_tok = self._extract_usage(response)
                self.stats.record_call(p_tok, c_tok, ch_tok, latency)
                result = self._parse_json(raw)
                if "error" in result or not result.get("narrative"):
                    if self._is_likely_truncated(raw):
                        current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                        logger.info("JSON 截断检测，增大 max_tokens 到 %d 重试", current_max_tokens)
                    last_error = ValueError(result.get("error", "解析失败或缺少narrative"))
                    time.sleep(self._retry_sleep_sec)
                    continue
                return result
            except Exception as e:
                last_error = e
                logger.warning("chat_json_from_messages调用失败，重试 %d/%d: %s", attempt + 1, retries, e, exc_info=True)
                time.sleep(self._retry_sleep_sec)

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
            max_tokens = self._fallback_max_tokens
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
                            current_max_tokens = self._fallback_max_tokens
                        else:
                            current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                        logger.info("finish_reason=length，增大 max_tokens 到 %d 重试", current_max_tokens)
                    await asyncio.sleep(self._retry_sleep_sec)
                    continue
                latency = (time.time() - start_time) * 1000
                p_tok, c_tok, ch_tok = self._extract_usage(response)
                self.stats.record_call(p_tok, c_tok, ch_tok, latency)
                result = self._parse_json(raw)
                if "error" in result or not result.get("narrative"):
                    if self._is_likely_truncated(raw):
                        current_max_tokens = min(current_max_tokens * 2, self._max_tokens_cap)
                        logger.info("JSON 截断检测，增大 max_tokens 到 %d 重试", current_max_tokens)
                    last_error = ValueError(result.get("error", "解析失败或缺少narrative"))
                    await asyncio.sleep(self._retry_sleep_sec)
                    continue
                return result
            except Exception as e:
                last_error = e
                logger.warning("achat_json_from_messages调用失败，重试 %d/%d: %s", attempt + 1, retries, e, exc_info=True)
                await asyncio.sleep(self._retry_sleep_sec)

        latency = (time.time() - start_time) * 1000
        self.stats.record_call(0, 0, 0, latency, failed=True)
        logger.error("achat_json_from_messages最终失败: %s", last_error)
        return {"error": str(last_error) if last_error else "achat_json_from_messages失败", "narrative": "", "options": []}

    @timed(category="llm", label="mimo_chat_structured")
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
            max_tokens = self._fallback_max_tokens

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
                    logger.info("API不支持structured output，后续调用跳过response_format", exc_info=True)
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
            logger.error("Structured output completely failed: %s", e, exc_info=True)
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
            logger.error("LLM stream chat failed: %s", e, exc_info=True)
            return

        if token_count == 0:
            logger.warning("chat_stream返回空流，model=%s", self.model_name)
            return

        logger.info(
            "chat_stream 完成: %d tokens, %d chars, finish_reason=%s",
            token_count, total_chars_sent, finish_reason,
        )

        # [P4-A-2-E] 记录 stats（流式 API 无 usage，用估算值）
        # prompt_tokens 估算：中文约 1.5 字/token，取 3 字/token 折中
        prompt_content = prompt if isinstance(prompt, str) else json.dumps(prompt, ensure_ascii=False)
        est_prompt_tokens = len(prompt_content) // 3
        self.stats.record_call(
            prompt_tokens=est_prompt_tokens,
            completion_tokens=token_count,
            latency_ms=0.0,
        )
        self.last_usage.prompt_tokens = est_prompt_tokens
        self.last_usage.completion_tokens = token_count
        self.last_usage.model = self.model_name

        # ── 正常结束：无需额外操作 ──
        if finish_reason != "length":
            return

        # ── 截断了：用 2x max_tokens 非流式重试，只 yield 增量部分 ──
        retry_max = min(max_tokens * 2, self._max_tokens_cap)
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
            logger.error("chat_stream 截断重试失败: %s", e, exc_info=True)

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
        # [v12容错] 修复 JSON 字符串值内的未转义换行（LLM 常见 bug）
        # 现象：description 字段值包含真实换行符，导致 JSON 解析失败
        # 修复：将 "..." 之间的换行替换为 \n 转义
        try:
            fixed = re.sub(
                r'("(?:[^"\\]|\\.)*")|(\n)',
                lambda m: m.group(1) if m.group(1) else '\\n',
                cleaned
            )
            return json.loads(fixed)
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
