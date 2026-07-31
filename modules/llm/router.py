from __future__ import annotations
import json
import logging
import time
from typing import Optional

from .base_llm import BaseLLM, LLMUsageStats
from .mimo_llm import MimoLLM
# [v1.7 P3-A] 路由层耗时埋点（区分路由开销与底层 LLM 调用）
from ..core.timing import timed

logger = logging.getLogger("chronoverse.llm.router")

# 路由层总等待时间。底层单模型 30 秒超时，路由层最多尝试 60 秒后降级兜底。
# [v1.7 P2-4] 这两个常量保留作为默认值，LLMRouter 实例化时可被 router_cfg 覆盖
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

    [BudgetGuard] 预算控制（v1.3+）：
      - 每日 USD 预算：累计成本超支时自动降级到 cheap 模型
      - 每回合调用上限：防止单回合雪崩式调用
      - 熔断保护：连续失败时暂停 LLM 调用一段时间
      由 configure_budget() 配置，check_budget() 检查。
    """

    def __init__(self, main_llm: BaseLLM,
                 cheap_llm: Optional[BaseLLM] = None,
                 dialogue_llm: Optional[BaseLLM] = None,
                 router_cfg: "dict | None" = None):
        super().__init__()
        # [v1.7 P2-4] 路由层参数从 config 注入，无 config 时回退到默认常量
        rcfg = router_cfg or {}
        self._total_timeout = float(rcfg.get("total_timeout", ROUTER_TOTAL_TIMEOUT))
        self._async_single_timeout = float(rcfg.get("async_single_timeout", ASYNC_SINGLE_TIMEOUT))
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
        # [I] 路由维度聚合统计：按 task_type 累积调用次数
        self._task_call_counts: dict[str, int] = {}
        # [I] 兜底调用计数（所有真实模型失败后走 RuleBasedFallbackLLM）
        self._fallback_calls: int = 0
        # [I] 价格表（USD per 1K tokens），可在 config.json 中通过
        # llm.pricing 配置，未配置的模型成本估算为 0
        # 格式: {"model_name": {"input_per_1k": 0.03, "output_per_1k": 0.06}}
        self._price_table: dict[str, dict[str, float]] = {}

        # [BudgetGuard] 预算控制状态
        # _daily_cost_usd：今日累计成本（USD）
        # _daily_cost_date：今日日期字符串（用于跨日重置）
        # _per_turn_calls：当前回合调用计数
        # _per_turn_reset_at：回合计数重置时间戳
        # _consecutive_failures：连续失败计数
        # _circuit_open_until：熔断打开时的恢复时间戳（0=未熔断）
        # _budget_cfg：预算配置 dict
        self._daily_cost_usd: float = 0.0
        self._daily_cost_date: str = ""
        self._per_turn_calls: int = 0
        self._per_turn_reset_at: float = 0.0
        self._consecutive_failures: int = 0
        self._circuit_open_until: float = 0.0
        self._budget_cfg: dict = {
            "daily_budget_usd": 0.0,         # 0 = 不限制
            "per_turn_limit": 0,             # 0 = 不限制
            "per_turn_window_sec": 30.0,     # 回合窗口：30秒内的调用算同一回合
            "circuit_failure_threshold": 5,  # 连续失败 5 次触发熔断
            "circuit_recovery_sec": 30.0,    # 熔断恢复时间 30 秒
            "enabled": True,                 # 总开关
        }
        # [BudgetGuard] 预算事件日志（最近 50 条，用于前端展示）
        self._budget_events: list[dict] = []

    def configure_budget(self, cfg: dict):
        """[BudgetGuard] 配置预算控制参数。
        cfg 字段（全部可选）：
            daily_budget_usd: float     每日 USD 预算上限（0=不限）
            per_turn_limit: int         每回合调用上限（0=不限）
            per_turn_window_sec: float  回合窗口时长（秒）
            circuit_failure_threshold: int  触发熔断的连续失败次数
            circuit_recovery_sec: float 熔断恢复时长（秒）
            enabled: bool               总开关
        """
        if not isinstance(cfg, dict):
            return
        for k, v in cfg.items():
            if k in self._budget_cfg:
                self._budget_cfg[k] = v
        logger.info("[BudgetGuard] 配置已更新: %s", self._budget_cfg)

    def _now_date(self) -> str:
        """返回当前日期字符串（YYYY-MM-DD，本地时区）"""
        import datetime
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def _record_cost(self, model_name: str,
                     prompt_tokens: int, completion_tokens: int):
        """[BudgetGuard] 累计今日成本"""
        cost = self._estimate_cost(model_name, prompt_tokens, completion_tokens)
        if cost <= 0:
            return
        today = self._now_date()
        if today != self._daily_cost_date:
            self._daily_cost_date = today
            self._daily_cost_usd = 0.0
        self._daily_cost_usd += cost

    def _log_budget_event(self, event_type: str, detail: str):
        """[BudgetGuard] 记录预算事件，供前端展示"""
        import time as _t
        self._budget_events.append({
            "ts": _t.time(),
            "type": event_type,
            "detail": detail,
        })
        # 只保留最近 50 条
        if len(self._budget_events) > 50:
            self._budget_events = self._budget_events[-50:]

    def _reset_per_turn_if_needed(self):
        """[BudgetGuard] 超过窗口则重置回合计数"""
        import time as _t
        if _t.time() - self._per_turn_reset_at > self._budget_cfg["per_turn_window_sec"]:
            self._per_turn_calls = 0
            self._per_turn_reset_at = _t.time()

    def _record_call_attempt(self):
        """[BudgetGuard] 记录一次调用尝试（用于回合上限）"""
        self._per_turn_calls += 1

    def _record_call_success(self, model_name: str,
                              prompt_tokens: int, completion_tokens: int):
        """[BudgetGuard] 记录一次成功调用：清零失败计数 + 累计成本"""
        self._consecutive_failures = 0
        self._record_cost(model_name, prompt_tokens, completion_tokens)

    def _record_call_failure(self):
        """[BudgetGuard] 记录一次失败：递增失败计数，必要时触发熔断"""
        self._consecutive_failures += 1
        threshold = self._budget_cfg["circuit_failure_threshold"]
        if threshold > 0 and self._consecutive_failures >= threshold:
            import time as _t
            recovery = self._budget_cfg["circuit_recovery_sec"]
            self._circuit_open_until = _t.time() + recovery
            self._log_budget_event(
                "circuit_open",
                f"连续失败 {self._consecutive_failures} 次，熔断 {recovery}s"
            )
            logger.warning(
                "[BudgetGuard] 熔断打开：连续失败 %d 次，暂停 %ss",
                self._consecutive_failures, recovery
            )

    def _is_circuit_open(self) -> bool:
        """[BudgetGuard] 熔断是否处于打开状态"""
        import time as _t
        if self._circuit_open_until <= 0:
            return False
        if _t.time() >= self._circuit_open_until:
            # 熔断恢复
            self._circuit_open_until = 0.0
            self._consecutive_failures = 0
            self._log_budget_event("circuit_recover", "熔断恢复，重置失败计数")
            logger.info("[BudgetGuard] 熔断恢复")
            return False
        return True

    def check_budget(self, task_type: str = "") -> dict:
        """[BudgetGuard] 检查是否允许调用 LLM。
        返回:
            {
              "allow": bool,           # 是否允许
              "degrade_to": str,       # "main"/"cheap"/"fallback" 建议使用的模型
              "reason": str,           # 拒绝/降级原因
              "daily_cost_usd": float, # 今日累计成本
              "per_turn_calls": int,   # 本回合调用次数
            }
        """
        cfg = self._budget_cfg
        if not cfg.get("enabled", True):
            return {"allow": True, "degrade_to": "main", "reason": "disabled",
                    "daily_cost_usd": self._daily_cost_usd,
                    "per_turn_calls": self._per_turn_calls}

        # 1. 熔断检查
        if self._is_circuit_open():
            return {"allow": False, "degrade_to": "fallback",
                    "reason": "circuit_open",
                    "daily_cost_usd": self._daily_cost_usd,
                    "per_turn_calls": self._per_turn_calls}

        # 2. 每日预算检查
        budget = cfg.get("daily_budget_usd", 0.0)
        if budget > 0 and self._daily_cost_usd >= budget:
            # 超支：降级到 cheap（若 cheap 不可用则 fallback）
            degrade = "cheap" if self.cheap else "fallback"
            return {"allow": True, "degrade_to": degrade,
                    "reason": "daily_budget_exceeded",
                    "daily_cost_usd": self._daily_cost_usd,
                    "per_turn_calls": self._per_turn_calls}

        # 3. 每回合上限检查
        limit = cfg.get("per_turn_limit", 0)
        if limit > 0 and self._per_turn_calls >= limit:
            # 超回合上限：降级到 cheap（仍允许调用，但用便宜模型）
            degrade = "cheap" if self.cheap else "fallback"
            return {"allow": True, "degrade_to": degrade,
                    "reason": "per_turn_limit_exceeded",
                    "daily_cost_usd": self._daily_cost_usd,
                    "per_turn_calls": self._per_turn_calls}

        return {"allow": True, "degrade_to": "main", "reason": "ok",
                "daily_cost_usd": self._daily_cost_usd,
                "per_turn_calls": self._per_turn_calls}

    def _apply_budget_to_chain(self, chain: list, task_type: str = "") -> list:
        """[BudgetGuard] 根据预算检查结果调整降级链。
        - 超预算/超回合：移除 main 和 dialogue，只保留 cheap 和 fallback
        - 熔断：只保留 fallback
        """
        check = self.check_budget(task_type)
        if not check["allow"]:
            # 熔断：只留 fallback
            return [self.fallback]
        if check["degrade_to"] == "cheap":
            # 降级到 cheap：只保留 cheap 和 fallback
            return [llm for llm in chain if llm is self.cheap or llm is self.fallback]
        return chain

    # [P4-A-2-C] BudgetGuard 统一覆盖助手：供 chat_structured / achat* / *_from_messages 复用
    def _budget_enter(self, task_type: str, prompt: str) -> list:
        """调用前置：记录回合计数 + 按预算调整降级链。返回待尝试的 LLM 列表。"""
        self._reset_per_turn_if_needed()
        self._record_call_attempt()
        return self._apply_budget_to_chain(
            self._build_llm_chain(task_type, prompt), task_type
        )

    def _budget_success(self, llm) -> None:
        """调用成功后：清零失败计数 + 累计成本。
        [P4-A-2-C] 规则兜底 fallback 不清零失败计数，否则连续失败无法触发熔断。"""
        if llm is self.fallback:
            return  # fallback 是规则兜底，不算 LLM 成功，不清零失败计数
        usage = getattr(llm, 'last_usage', None)
        self._record_call_success(
            getattr(llm, 'model_name', 'unknown'),
            usage.prompt_tokens if usage and hasattr(usage, 'prompt_tokens') else 0,
            usage.completion_tokens if usage and hasattr(usage, 'completion_tokens') else 0,
        )

    def _budget_failure(self) -> None:
        """调用失败后：递增失败计数，必要时触发熔断。"""
        self._record_call_failure()

    def get_budget_status(self) -> dict:
        """[BudgetGuard] 获取预算状态（供前端展示）"""
        return {
            "enabled": self._budget_cfg.get("enabled", True),
            "daily_budget_usd": self._budget_cfg.get("daily_budget_usd", 0.0),
            "daily_cost_usd": round(self._daily_cost_usd, 4),
            "daily_cost_date": self._daily_cost_date,
            "per_turn_limit": self._budget_cfg.get("per_turn_limit", 0),
            "per_turn_calls": self._per_turn_calls,
            "consecutive_failures": self._consecutive_failures,
            "circuit_open": self._is_circuit_open(),
            "circuit_open_until": self._circuit_open_until,
            "recent_events": list(self._budget_events[-10:]),
        }

    def reset_daily_budget(self):
        """[BudgetGuard] 手动重置今日成本（跨日未自动重置时用）"""
        self._daily_cost_usd = 0.0
        self._daily_cost_date = self._now_date()
        self._log_budget_event("manual_reset", "手动重置今日成本")
        logger.info("[BudgetGuard] 今日成本已手动重置")

    def reset_circuit(self):
        """[BudgetGuard] 手动关闭熔断（紧急恢复用）"""
        self._circuit_open_until = 0.0
        self._consecutive_failures = 0
        self._log_budget_event("manual_circuit_reset", "手动关闭熔断")
        logger.info("[BudgetGuard] 熔断已手动关闭")

    def _record_task_call(self, task_type: str):
        """[I] 记录一次任务路由调用（按 task_type 累积）"""
        self._task_call_counts[task_type] = (
            self._task_call_counts.get(task_type, 0) + 1
        )

    def _record_fallback_call(self):
        """[I] 记录一次兜底调用（所有真实模型失败后走 fallback）"""
        self._fallback_calls += 1

    def configure_pricing(self, price_table: dict[str, dict[str, float]]):
        """[I] 配置模型价格表，用于成本估算。
        price_table 格式：
            {
                "model_name_1": {"input_per_1k": 0.03, "output_per_1k": 0.06},
                "model_name_2": {"input_per_1k": 0.005, "output_per_1k": 0.015},
            }
        价格单位：USD / 1K tokens。未配置的模型成本估算为 0。
        """
        if isinstance(price_table, dict):
            self._price_table.update(price_table)

    def _estimate_cost(self, model_name: str,
                        prompt_tokens: int, completion_tokens: int) -> float:
        """[I] 估算单次/累计调用成本（USD）"""
        price = self._price_table.get(model_name)
        if not price:
            return 0.0
        return (prompt_tokens / 1000.0 * price.get("input_per_1k", 0.0)
                + completion_tokens / 1000.0 * price.get("output_per_1k", 0.0))

    def get_aggregate_stats(self) -> dict:
        """[I] 聚合三个子 LLM 的统计 + 路由维度 + 成本估算。
        total_calls 仅为三个真实模型调用之和（不含 fallback），
        fallback_calls 单独统计。"""
        total_calls = 0
        total_prompt = 0
        total_completion = 0
        total_failed = 0
        total_latency = 0.0
        total_cost = 0.0
        per_model: dict[str, dict] = {}

        for label, llm in [("main", self.main),
                            ("dialogue", self.dialogue),
                            ("cheap", self.cheap)]:
            if llm is None:
                continue
            s = llm.stats
            model_name = getattr(llm, "model_name", label)
            cost = self._estimate_cost(
                model_name, s.total_prompt_tokens, s.total_completion_tokens
            )
            per_model[label] = {
                "model_name": model_name,
                "calls": s.total_calls,
                "prompt_tokens": s.total_prompt_tokens,
                "completion_tokens": s.total_completion_tokens,
                "failed_calls": s.failed_calls,
                "avg_latency_ms": round(s.avg_latency_ms, 1),
                "est_cost_usd": round(cost, 4),
            }
            total_calls += s.total_calls
            total_prompt += s.total_prompt_tokens
            total_completion += s.total_completion_tokens
            total_failed += s.failed_calls
            total_latency += s.total_latency_ms
            total_cost += cost

        return {
            "total_calls": total_calls,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "failed_calls": total_failed,
            "fallback_calls": self._fallback_calls,
            "avg_latency_ms": round(total_latency / total_calls, 1) if total_calls else 0.0,
            "est_total_cost_usd": round(total_cost, 4),
            "per_model": per_model,
            "per_task": dict(self._task_call_counts),
        }

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

    @timed(category="llm", label="router_chat")
    def chat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 4096,
             task_type: str = TASK_NARRATIVE, retries: int = 2) -> str:
        import time
        self._record_task_call(task_type)  # [I] 记录路由调用
        # [BudgetGuard] 预算检查 + 回合计数
        self._reset_per_turn_if_needed()
        self._record_call_attempt()
        llm_to_try = self._apply_budget_to_chain(
            self._build_llm_chain(task_type, prompt), task_type
        )
        last_error = None
        start_time = time.time()
        total_timeout = self._total_timeout
        for llm in llm_to_try:
            try:
                elapsed = time.time() - start_time
                if elapsed >= total_timeout:
                    logger.warning("Total timeout exceeded (%ds), triggering fallback", total_timeout)
                    break
                result = llm.chat(prompt, temperature=temperature, max_tokens=max_tokens)
                if result and result.strip():
                    # [Bug P4-A-2-D] 必须用 _budget_success 而非 _record_call_success：
                    # 前者对 fallback 不清零失败计数，否则降级链末端的 RuleBasedFallbackLLM
                    # 永远成功会把 _consecutive_failures 清零，熔断永远触发不了
                    self._budget_success(llm)
                    return result
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("LLM %s failed after %.1fs, trying next: %s",
                               getattr(llm, 'model_name', 'unknown'), elapsed, e, exc_info=True)
                self._record_call_failure()  # [BudgetGuard] 记录失败
                continue
        logger.error("All LLMs failed: %s", last_error)
        self._record_fallback_call()  # [I] 记录兜底
        return self.fallback.chat(prompt, temperature, max_tokens)

    @timed(category="llm", label="router_chat_json")
    def chat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 4096,
                  task_type: str = TASK_JSON, retries: int = 2,
                  schema_hint: str = "") -> dict:
        self._record_task_call(task_type)  # [I] 记录路由调用
        # [BudgetGuard] 预算检查 + 回合计数
        self._reset_per_turn_if_needed()
        self._record_call_attempt()
        llm_to_try = self._apply_budget_to_chain(
            self._build_llm_chain(task_type, prompt), task_type
        )
        last_error = None
        start_time = time.time()
        total_timeout = self._total_timeout
        for llm in llm_to_try:
            try:
                elapsed = time.time() - start_time
                if elapsed >= total_timeout:
                    logger.warning("Total timeout exceeded (%ds), triggering fallback", total_timeout)
                    break
                # [v12] 转发 schema_hint 给底层 LLM
                if schema_hint:
                    result = llm.chat_json(prompt, temperature=temperature,
                                            max_tokens=max_tokens, schema_hint=schema_hint)
                else:
                    result = llm.chat_json(prompt, temperature=temperature, max_tokens=max_tokens)
                if isinstance(result, dict) and "error" not in result:
                    # [Bug P4-A-2-D] 用 _budget_success 而非 _record_call_success（见上）
                    self._budget_success(llm)
                    return result
                if isinstance(result, dict) and result.get("narrative"):
                    self._budget_success(llm)
                    return result
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("LLM %s chat_json failed after %.1fs, trying next: %s",
                               getattr(llm, 'model_name', 'unknown'), elapsed, e, exc_info=True)
                self._record_call_failure()
                continue
        logger.error("All LLMs chat_json failed: %s", last_error)
        self._record_fallback_call()  # [I] 记录兜底
        return self.fallback.chat_json(prompt, temperature, max_tokens)

    @timed(category="llm", label="router_chat_structured")
    def chat_structured(self, prompt: str, schema_name: str,
                        temperature: float = 0.7, max_tokens: int = 2048,
                        task_type: str = TASK_JSON, narrative_hint: str = "500-1000字") -> dict:
        """
        结构化输出路由：优先调用首选 LLM 的 chat_structured，
        失败则依次降级到其他模型、fallback。
        若目标 LLM 不支持 chat_structured，回退到 chat_json。
        """
        self._record_task_call(task_type)  # [I] 记录路由调用
        # [P4-A-2-C] BudgetGuard 覆盖（原缺失：不计数/不熔断/不累计成本）
        llm_to_try = self._budget_enter(task_type, prompt)
        last_error = None
        start_time = time.time()
        total_timeout = self._total_timeout
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
                    self._budget_success(llm)  # [P4-A-2-C]
                    return result
                if isinstance(result, dict) and result.get("narrative"):
                    self._budget_success(llm)  # [P4-A-2-C]
                    return result
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("LLM %s chat_structured failed after %.1fs, trying next: %s",
                               getattr(llm, 'model_name', 'unknown'), elapsed, e, exc_info=True)
                self._budget_failure()  # [P4-A-2-C]
                continue
        logger.error("All LLMs chat_structured failed: %s", last_error)
        self._record_fallback_call()  # [I] 记录兜底
        return self.fallback.chat_json(prompt, temperature, max_tokens)

    async def achat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 4096,
                    task_type: str = TASK_NARRATIVE, retries: int = 2) -> str:
        import asyncio
        self._record_task_call(task_type)  # [I] 记录路由调用
        # [P4-A-2-C] BudgetGuard 覆盖
        llm_to_try = self._budget_enter(task_type, prompt)
        last_error = None
        start_time = time.time()
        total_timeout = self._total_timeout
        for llm in llm_to_try:
            try:
                elapsed = time.time() - start_time
                if elapsed >= total_timeout:
                    logger.warning("Total timeout exceeded (%ds), triggering fallback", total_timeout)
                    break
                result = await asyncio.wait_for(
                    llm.achat(prompt, temperature=temperature, max_tokens=max_tokens),
                    timeout=self._async_single_timeout
                )
                if result and result.strip():
                    self._budget_success(llm)  # [P4-A-2-C]
                    return result
            except asyncio.TimeoutError:
                elapsed = time.time() - start_time
                logger.warning("LLM %s async timed out after %.1fs, trying next",
                               getattr(llm, 'model_name', 'unknown'), elapsed)
                self._budget_failure()  # [P4-A-2-C]
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("LLM %s async failed after %.1fs, trying next: %s",
                               getattr(llm, 'model_name', 'unknown'), elapsed, e, exc_info=True)
                self._budget_failure()  # [P4-A-2-C]
                continue
        logger.error("All async LLMs failed: %s", last_error)
        self._record_fallback_call()  # [I] 记录兜底
        return await self.fallback.achat(prompt, temperature, max_tokens)

    async def achat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 4096,
                         task_type: str = TASK_JSON, retries: int = 2,
                         schema_hint: str = "") -> dict:
        import asyncio
        self._record_task_call(task_type)  # [I] 记录路由调用
        # [P4-A-2-C] BudgetGuard 覆盖
        llm_to_try = self._budget_enter(task_type, prompt)
        last_error = None
        start_time = time.time()
        total_timeout = self._total_timeout
        for llm in llm_to_try:
            try:
                elapsed = time.time() - start_time
                if elapsed >= total_timeout:
                    logger.warning("Total timeout exceeded (%ds), triggering fallback", total_timeout)
                    break
                # [v12] 转发 schema_hint 给底层 LLM
                if schema_hint:
                    result = await asyncio.wait_for(
                        llm.achat_json(prompt, temperature=temperature,
                                       max_tokens=max_tokens, schema_hint=schema_hint),
                        timeout=self._async_single_timeout
                    )
                else:
                    result = await asyncio.wait_for(
                        llm.achat_json(prompt, temperature=temperature, max_tokens=max_tokens),
                        timeout=self._async_single_timeout
                    )
                if isinstance(result, dict) and "error" not in result:
                    self._budget_success(llm)  # [P4-A-2-C]
                    return result
                if isinstance(result, dict) and result.get("narrative"):
                    self._budget_success(llm)  # [P4-A-2-C]
                    return result
            except asyncio.TimeoutError:
                elapsed = time.time() - start_time
                logger.warning("LLM %s async_json timed out after %.1fs, trying next",
                               getattr(llm, 'model_name', 'unknown'), elapsed)
                self._budget_failure()  # [P4-A-2-C]
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("LLM %s async_json failed after %.1fs, trying next: %s",
                               getattr(llm, 'model_name', 'unknown'), elapsed, e, exc_info=True)
                self._budget_failure()  # [P4-A-2-C]
                continue
        logger.error("All async LLMs chat_json failed: %s", last_error)
        self._record_fallback_call()  # [I] 记录兜底
        return await self.fallback.achat_json(prompt, temperature, max_tokens)

    def chat_json_from_messages(self, messages: list[dict], temperature: float = 0.4,
                                max_tokens: int = 4096, task_type: str = TASK_DIALOGUE,
                                retries: int = 2, narrative_hint: str = "500-1000字") -> dict:
        """[v10.5+] 默认 task_type=TASK_DIALOGUE（游戏内对话走对话模型）"""
        self._record_task_call(task_type)  # [I] 记录路由调用
        # [P4-A-2-C] BudgetGuard 覆盖（用首条消息内容做链构建 + 预算调整）
        prompt_repr = messages[0].get("content", "") if messages else ""
        candidates = self._budget_enter(task_type, prompt_repr)
        # 过滤出支持 chat_json_from_messages 的 LLM
        candidates = [llm for llm in candidates if hasattr(llm, 'chat_json_from_messages')]
        if not candidates:
            candidates = [self.fallback]
        last_error = None
        start_time = time.time()
        total_timeout = self._total_timeout
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
                    self._budget_success(llm)  # [P4-A-2-C]
                    return result
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("chat_json_from_messages failed for %s after %.1fs: %s",
                               getattr(llm, 'model_name', 'unknown'), elapsed, e, exc_info=True)
                self._budget_failure()  # [P4-A-2-C]
                continue
        # [Bug#13] 回退时将消息列表拼接为可读 prompt，而非序列化为 JSON 字符串
        self._record_fallback_call()  # [I] 记录兜底
        fallback_prompt = "\n".join(
            f"[{m.get('role','user')}]: {m.get('content','')}" for m in messages
        )
        return self.chat_json(fallback_prompt, temperature, max_tokens, task_type)

    async def achat_json_from_messages(self, messages: list[dict], temperature: float = 0.4,
                                       max_tokens: int = 4096, task_type: str = TASK_DIALOGUE,
                                       retries: int = 2, narrative_hint: str = "500-1000字") -> dict:
        """[v10.5+] 默认 task_type=TASK_DIALOGUE（游戏内对话走对话模型）"""
        import asyncio
        self._record_task_call(task_type)  # [I] 记录路由调用
        # [P4-A-2-C] BudgetGuard 覆盖
        prompt_repr = messages[0].get("content", "") if messages else ""
        candidates = self._budget_enter(task_type, prompt_repr)
        candidates = [llm for llm in candidates if hasattr(llm, 'achat_json_from_messages')]
        if not candidates:
            candidates = [self.fallback]
        last_error = None
        start_time = time.time()
        total_timeout = self._total_timeout
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
                    timeout=self._async_single_timeout
                )
                if isinstance(result, dict) and ("error" not in result or result.get("narrative")):
                    self._budget_success(llm)  # [P4-A-2-C]
                    return result
            except asyncio.TimeoutError:
                elapsed = time.time() - start_time
                logger.warning("achat_json_from_messages timed out for %s after %.1fs",
                               getattr(llm, 'model_name', 'unknown'), elapsed)
                self._budget_failure()  # [P4-A-2-C]
            except Exception as e:
                last_error = e
                elapsed = time.time() - start_time
                logger.warning("achat_json_from_messages failed for %s after %.1fs: %s",
                               getattr(llm, 'model_name', 'unknown'), elapsed, e, exc_info=True)
                self._budget_failure()  # [P4-A-2-C]
                continue
        self._record_fallback_call()  # [I] 记录兜底
        return await self.achat_json(json.dumps(messages, ensure_ascii=False), temperature, max_tokens, task_type)

    def get_stats(self) -> dict:
        result = super().get_stats()
        result["main_model"] = self.main.get_stats() if self.main else {}
        result["dialogue_model"] = self.dialogue.get_stats() if self.dialogue else None
        result["cheap_model"] = self.cheap.get_stats() if self.cheap else None
        result["aggregate"] = self.get_aggregate_stats()  # [I] 聚合统计 + 成本估算
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
                    logger.debug("Failed to close LLM %s: %s", getattr(llm, 'model_name', 'unknown'), e, exc_info=True)

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
        # [v1.7 P2-2] 流式路径依赖底层 SDK 的 timeout=60s（mimo_llm.py）做超时保护。
        #   非流式 fallback 路径走 Router.chat，受 ROUTER_TOTAL_TIMEOUT=60s 保护。
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
                                   getattr(llm, 'model_name', 'unknown'), e, exc_info=True)
                    continue
        # 所有模型都失败，回退到非流式 chat（Router 内部有 ROUTER_TOTAL_TIMEOUT 保护）
        result = self._router.chat(prompt, temperature=temperature, max_tokens=max_tokens, task_type=task_type)
        def _fallback_gen():
            yield result
        return _fallback_gen()

    def get_stats(self) -> dict:
        return self._router.get_stats()

    def close(self):
        # 连接池由 router 统一管理，此处不重复关闭
        pass
