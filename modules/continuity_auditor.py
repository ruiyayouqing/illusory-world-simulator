"""
[v10+] 多维度连续性审计器 — 借鉴 InkOS 的 33 维审计

异步执行，每 N 回顾一次，不阻塞主游戏循环。

审计维度（5 个核心维度）：
  1. 角色身份一致性 — 角色职业/地位是否前后矛盾
  2. 资源连续性     — 丢失的物品不能再出现，金币变化是否合理
  3. 时间线一致性   — 事件顺序是否合理，已死角色不能复活
  4. 性格漂移检测   — 角色性格是否突变（善良→邪恶需要剧情铺垫）
  5. 伏笔偿还检查   — 配合 ForeshadowLifecycle，检查伏笔是否被遗忘

输出：每个维度 pass / warning / critical + 修复建议
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from .prompt_utils import resolve_location_name  # [Bug] location code → display name

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .schemas import PlayerState, WorldState, NPCState
    from .foreshadow_lifecycle import ForeshadowLifecycle

logger = logging.getLogger("chronoverse.continuity_auditor")


class AuditSeverity:
    PASS = "pass"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class DimensionResult:
    """单个审计维度的结果"""
    dimension: str
    severity: str          # pass / warning / critical
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "severity": self.severity,
            "issues": self.issues,
            "suggestions": self.suggestions,
        }


@dataclass
class AuditReport:
    """完整的审计报告"""
    turn: int
    day: int
    dimensions: list[DimensionResult] = field(default_factory=list)
    overall_severity: str = AuditSeverity.PASS
    critical_count: int = 0
    warning_count: int = 0

    def to_dict(self) -> dict:
        return {
            "turn": self.turn,
            "day": self.day,
            "overall_severity": self.overall_severity,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "dimensions": [d.to_dict() for d in self.dimensions],
        }


class ContinuityAuditor:
    """
    [v10+] 多维度连续性审计器

    异步运行，每 N 回合检查一次叙事连续性。
    不在每个回合的流水线中执行，避免拖慢响应速度。
    """

    def __init__(self, llm: "BaseLLM", audit_interval: int = 5):
        self.llm = llm
        self.audit_interval = audit_interval
        self._last_audit_turn = 0
        self._audit_history: list[AuditReport] = []

    def should_audit(self, current_turn: int) -> bool:
        return current_turn - self._last_audit_turn >= self.audit_interval

    def audit(self, recent_narratives: list[dict],
              player_state: "PlayerState",
              world_state: "WorldState",
              npc_states: dict[str, "NPCState"],
              foreshadow: "ForeshadowLifecycle" = None,
              current_turn: int = 0,
              current_day: int = 0) -> AuditReport:
        """
        执行一次多维度连续性审计。

        使用单次 LLM 调用完成所有维度的检查，节省成本。
        """
        self._last_audit_turn = current_turn
        report = AuditReport(turn=current_turn, day=current_day)

        # 构建审计上下文
        narrative_text = "\n".join([
            f"[第{n.get('day', '?')}天] {n.get('text', '')[:300]}"
            for n in recent_narratives[-5:]
        ])

        npc_info = self._build_npc_info(npc_states, world_state)  # [Bug] 传入 world_state 以解析 location code
        player_info = self._build_player_info(player_state, world_state)  # [Bug] 传入 world_state 以解析 location code
        resource_info = self._build_resource_info(player_state, world_state)

        # 伏笔健康信息
        foreshadow_info = ""
        if foreshadow:
            health = foreshadow.get_health_report(current_day)
            foreshadow_info = (
                f"活跃伏笔: {health['active']}个, "
                f"已解决: {health['resolved']}个, "
                f"过期: {health['stale']}个"
            )
            if health.get("stale_hooks"):
                stale_list = "\n".join([
                    f"  - {h['content'][:60]}（第{h['inserted_day']}天埋下）"
                    for h in health["stale_hooks"][:3]
                ])
                foreshadow_info += f"\n过期伏笔:\n{stale_list}"

        prompt = f"""你是叙事连续性审计员。请检查以下最近叙事是否存在连续性问题。

【最近叙事】
{narrative_text[:1500]}

【玩家信息】
{player_info}

【NPC信息】
{npc_info}

【资源状态】
{resource_info}

【伏笔状态】
{foreshadow_info or "无伏笔信息"}

【审计维度】
请从以下 5 个维度逐一检查：

1. 角色身份一致性：NPC 的职业/地位是否与之前一致？有没有突然变成另一个身份？
2. 资源连续性：玩家的物品/金币变化是否合理？有没有"凭空出现"或"丢失后又出现"的物品？
3. 时间线一致性：事件顺序是否合理？已死角色是否复活？时间跳跃是否有铺垫？
4. 性格漂移检测：角色性格是否突变？如果从善良变残忍，是否有足够的剧情铺垫？
5. 伏笔偿还检查：是否有长期未解决的伏笔？是否有伏笔被遗忘？

【输出JSON格式】
{{
    "dimensions": [
        {{
            "dimension": "character_identity",
            "severity": "pass/warning/critical",
            "issues": ["问题描述"],
            "suggestions": ["修复建议"]
        }},
        {{
            "dimension": "resource_continuity",
            "severity": "pass/warning/critical",
            "issues": [],
            "suggestions": []
        }},
        {{
            "dimension": "timeline_consistency",
            "severity": "pass/warning/critical",
            "issues": [],
            "suggestions": []
        }},
        {{
            "dimension": "personality_drift",
            "severity": "pass/warning/critical",
            "issues": [],
            "suggestions": []
        }},
        {{
            "dimension": "foreshadow_payoff",
            "severity": "pass/warning/critical",
            "issues": [],
            "suggestions": []
        }}
    ]
}}

只输出JSON。没有问题的维度 severity 填 "pass"，issues 和 suggestions 填空数组。"""

        try:
            # [v10] 优先使用结构化输出（审计 schema），失败回退到 chat_json
            if hasattr(self.llm, "chat_structured"):
                result = self.llm.chat_structured(prompt, "audit", temperature=0.2, max_tokens=0)
            else:
                result = self.llm.chat_json(prompt, temperature=0.2, max_tokens=0)
        except Exception as e:
            logger.warning("Continuity audit LLM failed: %s", e)
            return report

        # 解析结果
        dimension_names = [
            "character_identity", "resource_continuity",
            "timeline_consistency", "personality_drift", "foreshadow_payoff",
        ]

        for dim_data in result.get("dimensions", []):
            # 兼容 schema 的 name 字段与原 prompt 的 dimension 字段
            dim_name = dim_data.get("dimension") or dim_data.get("name", "")
            if dim_name not in dimension_names:
                continue
            dimension_names.remove(dim_name)

            dr = DimensionResult(
                dimension=dim_name,
                severity=dim_data.get("severity", AuditSeverity.PASS),
                issues=dim_data.get("issues", []),
                suggestions=dim_data.get("suggestions", []),
            )
            report.dimensions.append(dr)

            if dr.severity == AuditSeverity.CRITICAL:
                report.critical_count += 1
            elif dr.severity == AuditSeverity.WARNING:
                report.warning_count += 1

        # 未返回的维度默认 pass
        for dim_name in dimension_names:
            report.dimensions.append(DimensionResult(
                dimension=dim_name, severity=AuditSeverity.PASS
            ))

        # 计算总体严重度
        if report.critical_count > 0:
            report.overall_severity = AuditSeverity.CRITICAL
        elif report.warning_count > 0:
            report.overall_severity = AuditSeverity.WARNING
        else:
            report.overall_severity = AuditSeverity.PASS

        self._audit_history.append(report)
        if len(self._audit_history) > 30:
            self._audit_history = self._audit_history[-30:]

        logger.info("Continuity audit: %s (critical=%d, warning=%d)",
                     report.overall_severity, report.critical_count, report.warning_count)

        return report

    def get_latest_report(self) -> dict | None:
        if not self._audit_history:
            return None
        return self._audit_history[-1].to_dict()

    def get_audit_trend(self) -> dict:
        """返回审计趋势"""
        if not self._audit_history:
            return {"trend": "no_data", "history": []}

        history = []
        for report in self._audit_history[-10:]:
            history.append({
                "turn": report.turn,
                "severity": report.overall_severity,
                "critical": report.critical_count,
                "warning": report.warning_count,
            })

        # 趋势判断
        recent = self._audit_history[-5:]
        older = self._audit_history[-10:-5]
        recent_critical = sum(1 for r in recent
                              if r.overall_severity == AuditSeverity.CRITICAL)
        older_critical = sum(1 for r in older
                             if r.overall_severity == AuditSeverity.CRITICAL)

        if recent_critical > older_critical:
            trend = "declining"
        elif recent_critical < older_critical:
            trend = "improving"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "total_audits": len(self._audit_history),
            "recent_critical_rate": round(recent_critical / max(1, len(recent)), 2),
            "older_critical_rate": round(older_critical / max(1, len(older)), 2),
            "history": history,
        }

    # ── 辅助方法 ──────────────────────────────────────────

    @staticmethod
    def _build_npc_info(npc_states: dict, world_state=None) -> str:  # [Bug] 增加 world_state 参数
        if not npc_states:
            return "无NPC信息"
        parts = []
        for npc in list(npc_states.values())[:8]:
            role_change = ""
            if npc.role_history:
                last = npc.role_history[-1]
                role_change = f"（第{last.get('day', '?')}天从{last.get('from', '?')}变为{last.get('to', '?')}）"
            parts.append(
                f"- {npc.name}: 角色={npc.role}{role_change}, "
                f"好感={npc.relation_to_player.favor}, "
                f"位置={resolve_location_name(npc.current_location, world_state)}"  # [Bug] location code → display name
            )
        return "\n".join(parts)

    @staticmethod
    def _build_player_info(player_state, world_state=None) -> str:  # [Bug] 增加 world_state 参数
        if not player_state:
            return "无玩家信息"
        return (
            f"姓名={player_state.name}, 年龄={player_state.age}, "
            f"位置={resolve_location_name(player_state.location, world_state)}, "  # [Bug] location code → display name
            f"标签={', '.join(player_state.tags[:6])}"
        )

    @staticmethod
    def _build_resource_info(player_state, world_state) -> str:
        parts = []
        if player_state:
            parts.append(f"金币: {player_state.social.gold}")
            if player_state.inventory.items:
                items = [f"{i.name}x{i.quantity}" for i in player_state.inventory.items[:5]]
                parts.append(f"物品: {', '.join(items)}")
        if world_state and world_state.economy:
            parts.append(f"通胀率: {world_state.economy.inflation_rate:.2f}")
        return "; ".join(parts) if parts else "无资源信息"

    def to_dict(self) -> dict:
        return {
            "last_audit_turn": self._last_audit_turn,
            "audit_history": [r.to_dict() for r in self._audit_history[-15:]],
        }

    def from_dict(self, data: dict):
        self._last_audit_turn = data.get("last_audit_turn", 0)
        self._audit_history = []
        for rd in data.get("audit_history", []):
            report = AuditReport(
                turn=rd.get("turn", 0),
                day=rd.get("day", 0),
                overall_severity=rd.get("overall_severity", AuditSeverity.PASS),
                critical_count=rd.get("critical_count", 0),
                warning_count=rd.get("warning_count", 0),
            )
            for dd in rd.get("dimensions", []):
                report.dimensions.append(DimensionResult(
                    dimension=dd.get("dimension", ""),
                    severity=dd.get("severity", AuditSeverity.PASS),
                    issues=dd.get("issues", []),
                    suggestions=dd.get("suggestions", []),
                ))
            self._audit_history.append(report)
