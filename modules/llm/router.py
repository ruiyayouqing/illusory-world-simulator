from __future__ import annotations
import json
import logging
import time
from typing import Optional

from .base_llm import BaseLLM, LLMUsageStats
from .mimo_llm import MimoLLM

logger = logging.getLogger("chronoverse.llm.router")

# 路由层总等待时间。底层单模型 30 秒超时，路由层最多尝试 60 秒后降级兜底。
ROUTER_TOTAL_TIMEOUT = 60
ASYNC_SINGLE_TIMEOUT = 30

# [v10.5+] 任务类型常量
#   NARRATIVE / JSON：默认走主力模型（世界生成、角色卡等重活）
#   DIALOGUE：走对话模型（游戏内叙事/NPC对话/选项生成），未配置则回退主力
#   CLASSIFY / SCORE / SIMPLE：走备用模型（蝴蝶评估/记忆整理/审计等），未配置则回退主力
TASK_NARRATIVE = "narrative"
TASK_JSON = "json"
TASK_CLASSIFY = "classify"
TASK_SCORE = "score"
TASK_SIMPLE = "simple"
TASK_DIALOGUE = "dialogue"


class RuleBasedFallbackLLM(BaseLLM):
    """规则兜底LLM，完全不调用API，处理最简单的任务"""

    def __init__(self):
        super().__init__()
        self.model_name = "rule-based-fallback"

    def chat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 4096) -> str:
        return self._fallback_response()

    def chat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 4096) -> dict:
        return json.loads(self._fallback_response())

    async def achat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 4096) -> str:
        return self._fallback_response()

    async def achat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 4096) -> dict:
        return json.loads(self._fallback_response())

    def _fallback_response(self) -> str:
        return json.dumps({
            "narrative": "你感到一阵恍惚，世界仿佛停滞了片刻。\n\n（AI服务暂时繁忙，请稍候片刻再继续，你可以输入'环顾四周'重新探索）",
            "state_changes": {},
            "options": [
                {"text": "休息片刻", "action": "rest"},
                {"text": "环顾四周", "action": "look"},
                {"text": "查看状态", "action": "status"},
            ]
        }, ensure_ascii=False)


class LLMRouter(BaseLLM):
    """
    [v10.5+] LLM 路由器 — 三模型分层：
      - main       主力模型：世界生成、角色卡、多智能体关键剧情等重活
      - dialogue   对话模型：游戏内叙事/NPC对话/选项生成（玩家直接感知的内容）
      - cheap      备用模型：蝴蝶评估/记忆整理/身份审计等辅助任务
    任一模型未配置时回退到主力模型；全部失败则降级到规则模板。

    模型选择规则：
      - task_type=TASK_DIALOGUE → dialogue（未配置则 main）
      - task_type=TASK_CLASSIFY/SCORE/SIMPLE → cheap（未配置则 main）
      - task_type=TASK_NARRATIVE/JSON（默认） → main
      - 主模型失败时自动降级：main → dialogue → cheap → fallback
    """

    def __init__(self, main_llm: BaseLLM,
                 cheap_llm: Optional[BaseLLM] = None,
                 dialogue_llm: Optional[BaseLLM] = None):
        super().__init__()
        self.main = main_llm
        self.cheap = cheap_llm
        self.dialogue = dialogue_llm
        self.fallback = RuleBasedFallbackLLM()
        parts = [main_llm.model_name]
        if dialogue_llm:
            parts.append(f"dlg={dialogue_llm.model_name}")
        if cheap_llm:
            parts.append(f"cheap={cheap_llm.model_name}")
        self.model_name = f"router({'+'.join(parts)})"
        self.use_cheap_for_simple_tasks = True
        self.auto_fallback_enabled = True

    def _pick_primary(self, task_type: str = TASK_NARRATIVE, prompt: str = "") -> Optional[BaseLLM]:
        """根据任务类型选择首选 LLM（未配置则回退到主力）。"""
        # 对话任务 → 对话模型
        if task_type == TASK_DIALOGUE:
            return self.dialogue or self.main
        # 简单任务 → 备用模型
        if task_type in {TASK_CLASSIFY, TASK_SCORE, TASK_SIMPLE}:
            return self.cheap or self.main
        # 短 prompt + 关键词 → 备用模型
        if self.cheap and len(prompt) < 500 and any(
            kw in prompt for kw in ["分类", "评分", "判断", "是否", "选择"]
        ):
            return self.cheap
        # 默认 → 主力模型
        return self.main

    def _build_llm_chain(self, task_type: str = TASK_NARRATIVE, prompt: str = "") -> list[BaseLLM]:
        """构建降级链：首选 → 其他可用模型 → fallback。"""
        primary = self._pick_primary(task_type, prompt)
        chain: list[BaseLLM] = [primary]
        if self.auto_fallback_enabled:
            # 按优先级补入其他模型（去重）
            for llm in [self.main, self.dialogue, self.cheap]:
                if llm is not None and llm not in chain:
                    chain.append(llm)
            chain.append(self.fallback)
        # 过滤 None
        return [llm for llm in chain if llm is not None]

    def chat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 4096,
             task_type: str = TASK_NARRATIVE, retries: int = 2) -> str:
        import time
        llm_to_try = self._build_llm_chain(task_type, prompt)
        last_error = None
        start_time = time.time()
        total_timeout = ROUTER_TOTAL_TIMEOUT
        for llm in llm_to_try:
            try:
                elapsed = time.time() - start_time
                if elapsed >= total_timeout:
                    logger.warning("Total timeout exceeded (%ds), triggering fallback", total_timeout)
                    break
                result = llm.chat(prompt, temperature=temperature, max_tokens=max_tokens)
                if result and result.strip():
                    return result
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("LLM %s failed after %.1fs, trying next: %s", 
                               getattr(llm, 'model_name', 'unknown'), elapsed, e)
                continue
        logger.error("All LLMs failed: %s", last_error)
        return self.fallback.chat(prompt, temperature, max_tokens)

    def chat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 4096,
                  task_type: str = TASK_JSON, retries: int = 2) -> dict:
        llm_to_try = self._build_llm_chain(task_type, prompt)
        last_error = None
        start_time = time.time()
        total_timeout = ROUTER_TOTAL_TIMEOUT
        for llm in llm_to_try:
            try:
                elapsed = time.time() - start_time
                if elapsed >= total_timeout:
                    logger.warning("Total timeout exceeded (%ds), triggering fallback", total_timeout)
                    break
                result = llm.chat_json(prompt, temperature=temperature, max_tokens=max_tokens)
                if isinstance(result, dict) and "error" not in result:
                    return result
                if isinstance(result, dict) and result.get("narrative"):
                    return result
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("LLM %s chat_json failed after %.1fs, trying next: %s", 
                               getattr(llm, 'model_name', 'unknown'), elapsed, e)
                continue
        logger.error("All LLMs chat_json failed: %s", last_error)
        return self.fallback.chat_json(prompt, temperature, max_tokens)

    def chat_structured(self, prompt: str, schema_name: str,
                        temperature: float = 0.7, max_tokens: int = 2048,
                        task_type: str = TASK_JSON, narrative_hint: str = "500-1000字") -> dict:
        """
        结构化输出路由：优先调用首选 LLM 的 chat_structured，
        失败则依次降级到其他模型、fallback。
        若目标 LLM 不支持 chat_structured，回退到 chat_json。
        """
        llm_to_try = self._build_llm_chain(task_type, prompt)
        last_error = None
        start_time = time.time()
        total_timeout = ROUTER_TOTAL_TIMEOUT
        for llm in llm_to_try:
            try:
                elapsed = time.time() - start_time
                if elapsed >= total_timeout:
                    logger.warning("Total timeout exceeded (%ds), triggering fallback", total_timeout)
                    break
                if hasattr(llm, "chat_structured"):
                    result = llm.chat_structured(prompt, schema_name,
                                                 temperature=temperature,
                                                 max_tokens=max_tokens,
                                                 narrative_hint=narrative_hint)
                else:
                    result = llm.chat_json(prompt, temperature=temperature,
                                           max_tokens=max_tokens)
                if isinstance(result, dict) and "error" not in result:
                    return result
                if isinstance(result, dict) and result.get("narrative"):
                    return result
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("LLM %s chat_structured failed after %.1fs, trying next: %s",
                               getattr(llm, 'model_name', 'unknown'), elapsed, e)
                continue
        logger.error("All LLMs chat_structured failed: %s", last_error)
        return self.fallback.chat_json(prompt, temperature, max_tokens)

    async def achat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 4096,
                    task_type: str = TASK_NARRATIVE, retries: int = 2) -> str:
        import asyncio
        llm_to_try = self._build_llm_chain(task_type, prompt)
        last_error = None
        start_time = time.time()
        total_timeout = ROUTER_TOTAL_TIMEOUT
        for llm in llm_to_try:
            try:
                elapsed = time.time() - start_time
                if elapsed >= total_timeout:
                    logger.warning("Total timeout exceeded (%ds), triggering fallback", total_timeout)
                    break
                result = await asyncio.wait_for(
                    llm.achat(prompt, temperature=temperature, max_tokens=max_tokens),
                    timeout=ASYNC_SINGLE_TIMEOUT
                )
                if result and result.strip():
                    return result
            except asyncio.TimeoutError:
                elapsed = time.time() - start_time
                logger.warning("LLM %s async timed out after %.1fs, trying next", 
                               getattr(llm, 'model_name', 'unknown'), elapsed)
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("LLM %s async failed after %.1fs, trying next: %s", 
                               getattr(llm, 'model_name', 'unknown'), elapsed, e)
                continue
        logger.error("All async LLMs failed: %s", last_error)
        return await self.fallback.achat(prompt, temperature, max_tokens)

    async def achat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 4096,
                         task_type: str = TASK_JSON, retries: int = 2) -> dict:
        import asyncio
        llm_to_try = self._build_llm_chain(task_type, prompt)
        last_error = None
        start_time = time.time()
        total_timeout = ROUTER_TOTAL_TIMEOUT
        for llm in llm_to_try:
            try:
                elapsed = time.time() - start_time
                if elapsed >= total_timeout:
                    logger.warning("Total timeout exceeded (%ds), triggering fallback", total_timeout)
                    break
                result = await asyncio.wait_for(
                    llm.achat_json(prompt, temperature=temperature, max_tokens=max_tokens),
                    timeout=ASYNC_SINGLE_TIMEOUT
                )
                if isinstance(result, dict) and "error" not in result:
                    return result
                if isinstance(result, dict) and result.get("narrative"):
                    return result
            except asyncio.TimeoutError:
                elapsed = time.time() - start_time
                logger.warning("LLM %s async_json timed out after %.1fs, trying next", 
                               getattr(llm, 'model_name', 'unknown'), elapsed)
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("LLM %s async_json failed after %.1fs, trying next: %s", 
                               getattr(llm, 'model_name', 'unknown'), elapsed, e)
                continue
        logger.error("All async LLMs chat_json failed: %s", last_error)
        return await self.fallback.achat_json(prompt, temperature, max_tokens)

    def chat_json_from_messages(self, messages: list[dict], temperature: float = 0.4,
                                max_tokens: int = 4096, task_type: str = TASK_DIALOGUE,
                                retries: int = 2, narrative_hint: str = "500-1000字") -> dict:
        """[v10.5+] 默认 task_type=TASK_DIALOGUE（游戏内对话走对话模型）"""
        primary = self._pick_primary(task_type, "")
        candidates = [primary]
        if self.auto_fallback_enabled:
            for llm in [self.main, self.dialogue, self.cheap]:
                if llm is not None and llm not in candidates and hasattr(llm, 'chat_json_from_messages'):
                    candidates.append(llm)
        last_error = None
        start_time = time.time()
        total_timeout = ROUTER_TOTAL_TIMEOUT
        for llm in candidates:
            if not hasattr(llm, 'chat_json_from_messages'):
                continue
            try:
                elapsed = time.time() - start_time
                if elapsed >= total_timeout:
                    logger.warning("Total timeout exceeded (%ds), triggering fallback", total_timeout)
                    break
                result = llm.chat_json_from_messages(messages, temperature=temperature, max_tokens=max_tokens,
                                                     retries=retries, narrative_hint=narrative_hint)
                if isinstance(result, dict) and ("error" not in result or result.get("narrative")):
                    return result
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("chat_json_from_messages failed for %s after %.1fs: %s", 
                               getattr(llm, 'model_name', 'unknown'), elapsed, e)
                continue
        # [Bug#13] 回退时将消息列表拼接为可读 prompt，而非序列化为 JSON 字符串
        fallback_prompt = "\n".join(
            f"[{m.get('role','user')}]: {m.get('content','')}" for m in messages
        )
        return self.chat_json(fallback_prompt, temperature, max_tokens, task_type)

    async def achat_json_from_messages(self, messages: list[dict], temperature: float = 0.4,
                                       max_tokens: int = 4096, task_type: str = TASK_DIALOGUE,
                                       retries: int = 2, narrative_hint: str = "500-1000字") -> dict:
        """[v10.5+] 默认 task_type=TASK_DIALOGUE（游戏内对话走对话模型）"""
        import asyncio
        primary = self._pick_primary(task_type, "")
        candidates = [primary]
        if self.auto_fallback_enabled:
            for llm in [self.main, self.dialogue, self.cheap]:
                if llm is not None and llm not in candidates and hasattr(llm, 'achat_json_from_messages'):
                    candidates.append(llm)
        last_error = None
        start_time = time.time()
        total_timeout = ROUTER_TOTAL_TIMEOUT
        for llm in candidates:
            if not hasattr(llm, 'achat_json_from_messages'):
                continue
            try:
                elapsed = time.time() - start_time
                if elapsed >= total_timeout:
                    logger.warning("Total timeout exceeded (%ds), triggering fallback", total_timeout)
                    break
                result = await asyncio.wait_for(
                    llm.achat_json_from_messages(messages, temperature=temperature, max_tokens=max_tokens,
                                                  retries=retries, narrative_hint=narrative_hint),
                    timeout=ASYNC_SINGLE_TIMEOUT
                )
                if isinstance(result, dict) and ("error" not in result or result.get("narrative")):
                    return result
            except asyncio.TimeoutError:
                elapsed = time.time() - start_time
                logger.warning("achat_json_from_messages timed out for %s after %.1fs", 
                               getattr(llm, 'model_name', 'unknown'), elapsed)
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("achat_json_from_messages failed for %s after %.1fs: %s", 
                               getattr(llm, 'model_name', 'unknown'), elapsed, e)
                continue
        return await self.achat_json(json.dumps(messages, ensure_ascii=False), temperature, max_tokens, task_type)

    def get_stats(self) -> dict:
        result = super().get_stats()
        result["main_model"] = self.main.get_stats() if self.main else {}
        result["dialogue_model"] = self.dialogue.get_stats() if self.dialogue else None
        result["cheap_model"] = self.cheap.get_stats() if self.cheap else None
        return result

    def configure(self, use_cheap_for_simple: bool = None, auto_fallback: bool = None):
        if use_cheap_for_simple is not None:
            self.use_cheap_for_simple_tasks = use_cheap_for_simple
        if auto_fallback is not None:
            self.auto_fallback_enabled = auto_fallback

    def close(self):
        """[Bug] 关闭所有子 LLM 的连接池"""
        for llm in [self.main, self.dialogue, self.cheap, self.fallback]:
            if llm is not None:
                try:
                    llm.close()
                except Exception as e:
                    logger.debug("Failed to close LLM %s: %s", getattr(llm, 'model_name', 'unknown'), e)

    def bind_task_type(self, default_task_type: str) -> "TaskBoundLLM":
        """[v10.5+] 返回一个绑定了默认 task_type 的代理视图。
        子系统通过此视图调用时无需显式传 task_type，自动路由到对应模型。
        用于让对话类子系统走对话模型、辅助类子系统走备用模型。"""
        return TaskBoundLLM(self, default_task_type)


class TaskBoundLLM(BaseLLM):
    """[v10.5+] 绑定默认 task_type 的 LLM 代理。
    所有调用转发给内部的 LLMRouter，自动注入 default_task_type。
    子系统代码无需任何修改，只需在 registry 创建时用 router.bind_task_type() 包装。"""

    def __init__(self, router: LLMRouter, default_task_type: str):
        super().__init__()
        self._router = router
        self._default_task_type = default_task_type
        # 透传 model_name 和 stats，让上层代码透明
        self.model_name = f"{router.model_name}[{default_task_type}]"
        self.stats = router.stats
        self.last_usage = router.last_usage

    def chat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 4096,
             **kwargs) -> str:
        kwargs.setdefault("task_type", self._default_task_type)
        return self._router.chat(prompt, temperature=temperature, max_tokens=max_tokens, **kwargs)

    def chat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 4096,
                  **kwargs) -> dict:
        kwargs.setdefault("task_type", self._default_task_type)
        return self._router.chat_json(prompt, temperature=temperature, max_tokens=max_tokens, **kwargs)

    async def achat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 4096,
                    **kwargs) -> str:
        kwargs.setdefault("task_type", self._default_task_type)
        return await self._router.achat(prompt, temperature=temperature, max_tokens=max_tokens, **kwargs)

    async def achat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 4096,
                         **kwargs) -> dict:
        kwargs.setdefault("task_type", self._default_task_type)
        return await self._router.achat_json(prompt, temperature=temperature, max_tokens=max_tokens, **kwargs)

    def chat_json_from_messages(self, messages: list[dict], temperature: float = 0.4,
                                max_tokens: int = 4096, **kwargs) -> dict:
        kwargs.setdefault("task_type", self._default_task_type)
        return self._router.chat_json_from_messages(messages, temperature=temperature,
                                                    max_tokens=max_tokens, **kwargs)

    async def achat_json_from_messages(self, messages: list[dict], temperature: float = 0.4,
                                       max_tokens: int = 4096, **kwargs) -> dict:
        kwargs.setdefault("task_type", self._default_task_type)
        return await self._router.achat_json_from_messages(messages, temperature=temperature,
                                                           max_tokens=max_tokens, **kwargs)

    def chat_structured(self, prompt: str, schema_name: str,
                        temperature: float = 0.7, max_tokens: int = 2048,
                        **kwargs) -> dict:
        kwargs.setdefault("task_type", self._default_task_type)
        return self._router.chat_structured(prompt, schema_name, temperature=temperature,
                                            max_tokens=max_tokens, **kwargs)

    def chat_stream(self, prompt: str, temperature: float = 0.8,
                    max_tokens: int = 4096, **kwargs):
        # [Bug#15] 流式生成也需要 fallback 链：主模型失败时尝试其他模型
        kwargs.setdefault("task_type", self._default_task_type)
        task_type = kwargs.get("task_type", self._default_task_type)
        chain = self._router._build_llm_chain(task_type, prompt)
        for llm in chain:
            if hasattr(llm, "chat_stream"):
                try:
                    return llm.chat_stream(prompt, temperature=temperature, max_tokens=max_tokens)
                except NotImplementedError:
                    continue
                except Exception as e:
                    logger.warning("chat_stream failed for %s, trying next: %s",
                                   getattr(llm, 'model_name', 'unknown'), e)
                    continue
        # 所有模型都失败，回退到非流式 chat
        result = self._router.chat(prompt, temperature=temperature, max_tokens=max_tokens, task_type=task_type)
        def _fallback_gen():
            yield result
        return _fallback_gen()

    def get_stats(self) -> dict:
        return self._router.get_stats()

    def close(self):
        # 连接池由 router 统一管理，此处不重复关闭
        pass
