"""
[v12] NPC行动智能推演引擎
借鉴 MiroFish 的群体智能推演思路，实现小说NPC的批量状态推演。

核心流程：
1. 检索所有主要NPC的最后已知状态
2. 对每个NPC独立推演从最后露面到当前时间的变化
3. 交叉校验一致性（检查矛盾）
4. 生成结构化报告，玩家可选确认应用
"""
from __future__ import annotations
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .graph_rag import GraphRAG
    from .npc_registry import NpcRegistry
    from .character_state import CharacterStateManager

logger = logging.getLogger("chronoverse.npc_prediction")


@dataclass
class NpcSnapshot:
    npc_id: str
    name: str
    age: int = 20
    role: str = ""
    personality: str = ""
    tags: list[str] = field(default_factory=list)
    location: str = ""
    last_seen_day: int = 0
    last_seen_chapter: str = ""
    favor: int = 50
    relation_type: str = "陌生人"
    health: int = 100
    recent_actions: list[dict] = field(default_factory=list)
    source: str = "unknown"
    mbti_type: str = ""


@dataclass
class NpcPrediction:
    npc_id: str
    name: str
    from_day: int = 0
    to_day: int = 0
    events: list[dict] = field(default_factory=list)
    new_state: dict = field(default_factory=dict)
    narrative: str = ""
    confidence: float = 0.8


@dataclass
class PredictionReport:
    title: str = ""
    total_npcs: int = 0
    predictions: list[NpcPrediction] = field(default_factory=list)
    cross_validation_notes: str = ""
    cross_validation_score: float = 1.0
    summary: str = ""
    from_day: int = 0
    to_day: int = 0
    generated_at: str = ""
    source_mode: str = "auto"

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "total_npcs": self.total_npcs,
            "predictions": [asdict(p) for p in self.predictions],
            "cross_validation_notes": self.cross_validation_notes,
            "cross_validation_score": self.cross_validation_score,
            "summary": self.summary,
            "from_day": self.from_day,
            "to_day": self.to_day,
            "generated_at": self.generated_at,
            "source_mode": self.source_mode,
        }


SINGLE_NPC_PREDICTION_PROMPT = """你是一个小说世界的NPC推演器。请根据以下信息，推演{npc_name}从第{from_day}天到第{to_day}天的生活变化。

【NPC基础设定】
姓名: {npc_name} | 年龄: {age} | 身份: {role}
性格: {personality} | 标签: {tags}
MBTI: {mbti_type}

【第{from_day}天时的状态（最后已知状态）】
位置: {location} | 职业: {role}
与主角关系: {relation_type}（好感{favor}）
生命值: {health}/100
近期行为: {recent_actions}

【世界背景】
{world_context}（第{from_day}天 → 第{to_day}天，共{delta_days}天）

【约束规则 - 必须遵守】
1. 核心身份标签不可改变（妻子/丈夫/父亲/母亲/主角之妻/主角之夫等）
2. 已婚NPC不能与他人结婚，妻子/丈夫角色不能变
3. 已故NPC只能写祭奠相关内容
4. 已退休NPC不能再升职
5. 事件需符合年龄、性格、职业逻辑
6. 所有地点必须使用中文名称
7. 变化要合理，不要太戏剧化
8. 100字以内描述推演结果

【可触发的事件类型】
marriage(结婚), first_child(第一个孩子), child_birth(孩子出生),
career_advance(升职), start_business(创业), relocate(搬家),
retire(退休), illness(生病), accident(意外),
death_illness(病逝), death_old_age(寿终正寝),
imprisonment(入狱), wealth_change(财富变化),
leave_home(离家), join_faction(加入势力)

【输出JSON格式】
{{
    "events": [
        {{"type": "事件类型", "day": 天数, "description": "10字以内描述"}}
    ],
    "new_state": {{
        "location": "当前位置（中文）",
        "role": "当前职业（如有变化）",
        "tags": ["当前所有标签"],
        "mood": "当前心情",
        "health": 生命值,
        "favor": 好感度变化后的值
    }},
    "narrative": "100字以内的推演叙事，描述这段时间发生了什么"
}}

只输出JSON，不要其他内容。"""

CROSS_VALIDATION_PROMPT = """你是世界一致性审计员。以下是{count}个NPC从第{from_day}天到第{to_day}天的推演结果。
请检查是否存在逻辑矛盾：

【所有NPC推演结果】
{predictions_json}

【检查项】
1. 位置矛盾：A说在北方，B说在南方遇到了A
2. 时间矛盾：事件发生时间超出推演范围
3. 关系矛盾：A杀了B但B还活着且无解释
4. 身份矛盾：核心身份标签被错误修改
5. 因果矛盾：因A事件导致B结果，但A事件并未发生
6. 逻辑矛盾：NPC做了一个与性格/设定完全矛盾的事

【输出JSON格式】
{{
    "issues": [
        {{"npc_id": "npc_id", "issue": "矛盾描述", "suggestion": "修正建议"}}
    ],
    "overall_consistency": 0.85,
    "summary": "整体一致性评价，50字以内"
}}
只输出JSON。"""


class NpcPredictionEngine:
    """[v12] NPC群体快照推演引擎"""

    def __init__(self, llm: "BaseLLM" = None,
                 graph_rag: "GraphRAG" = None,
                 npc_registry: "NpcRegistry" = None,
                 character_state_manager: "CharacterStateManager" = None):
        self.llm = llm
        self.graph_rag = graph_rag
        self.npc_registry = npc_registry
        self.character_state_manager = character_state_manager
        self._current_report: Optional[PredictionReport] = None
        self._progress_callback = None

    def set_progress_callback(self, callback):
        self._progress_callback = callback

    def _report_progress(self, current: int, total: int, msg: str):
        if self._progress_callback:
            try:
                self._progress_callback(current, total, msg)
            except Exception:
                pass

    def predict_all_npcs(self, source_mode: str = "auto",
                         max_npcs: int = 50,
                         engine=None) -> PredictionReport:
        start_time = time.time()
        logger.info("[Prediction] 开始推演, source=%s, max=%d", source_mode, max_npcs)

        from_day = 1
        to_day = 1
        world_context = ""
        if engine and engine.world_state:
            to_day = engine.world_state.current_day
            world_context = (
                f"{engine.world_state.world_name}，"
                f"第{to_day}天，{engine.world_state.season}，{engine.world_state.weather}"
            )

        snapshots = self._collect_npc_snapshots(source_mode, max_npcs, engine)
        if not snapshots:
            logger.warning("[Prediction] 未找到可推演的NPC")
            return PredictionReport(
                title="NPC行动推演报告",
                total_npcs=0,
                summary="未找到可推演的NPC",
                from_day=from_day,
                to_day=to_day,
                generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                source_mode=source_mode,
            )

        earliest_day = min(s.last_seen_day for s in snapshots) if snapshots else 1
        from_day = max(earliest_day, 1)

        logger.info("[Prediction] 找到 %d 个NPC, 推演范围: 第%d天 ~ 第%d天",
                     len(snapshots), from_day, to_day)

        predictions = []
        total = len(snapshots)
        for i, snap in enumerate(snapshots):
            self._report_progress(i + 1, total, f"正在推演: {snap.name}")
            pred = self._predict_single_npc(snap, from_day, to_day, world_context, engine)
            if pred:
                predictions.append(pred)
            if i < total - 1:
                time.sleep(0.3)

        logger.info("[Prediction] 独立推演完成, %d/%d 成功", len(predictions), total)

        self._report_progress(total + 1, total + 2, "正在执行一致性校验...")
        validation_notes, validation_score = self._cross_validate(predictions)

        report = self._generate_report(
            predictions, from_day, to_day, world_context,
            validation_notes, validation_score, source_mode
        )
        self._current_report = report

        elapsed = time.time() - start_time
        logger.info("[Prediction] 推演完成, 耗时 %.1f秒, NPC=%d, 一致性=%.0f%%",
                     elapsed, len(predictions), validation_score * 100)

        return report

    def _collect_npc_snapshots(self, source_mode: str, max_npcs: int,
                                engine=None) -> list[NpcSnapshot]:
        snapshots = []

        if source_mode in ("auto", "game") and engine:
            snapshots = self._collect_from_game_state(engine, max_npcs)

        if not snapshots and source_mode in ("auto", "novel"):
            snapshots = self._collect_from_registry(max_npcs)

        snapshots.sort(key=lambda s: s.last_seen_day, reverse=True)
        return snapshots[:max_npcs]

    def _collect_from_game_state(self, engine, max_npcs: int) -> list[NpcSnapshot]:
        snapshots = []
        if not engine.npc_states:
            return snapshots

        for npc_id, npc in engine.npc_states.items():
            if "已故" in npc.tags:
                continue
            snap = NpcSnapshot(
                npc_id=npc_id,
                name=npc.name,
                age=npc.age,
                role=npc.role,
                personality=npc.personality,
                tags=list(npc.tags),
                location=npc.current_location,
                last_seen_day=npc.last_action_day or 1,
                favor=npc.relation_to_player.favor,
                relation_type=npc.relation_to_player.relation_type,
                health=npc.stats.health,
                recent_actions=npc.recent_actions[-3:] if npc.recent_actions else [],
                source="game_state",
                mbti_type=npc.mbti_type,
            )
            snapshots.append(snap)

        return snapshots

    def _collect_from_registry(self, max_npcs: int) -> list[NpcSnapshot]:
        snapshots = []
        if not self.npc_registry:
            return snapshots

        for npc_id, entry in self.npc_registry.world_npcs.items():
            if not entry.alive:
                continue
            snap = NpcSnapshot(
                npc_id=npc_id,
                name=entry.name,
                age=entry.age,
                role=entry.position_in_faction or entry.title,
                personality=entry.personality,
                tags=list(entry.tags),
                location=entry.location,
                last_seen_day=entry.last_met_day or 1,
                favor=entry.relation_to_player.get("favor", 50),
                relation_type=entry.relation_to_player.get("relation_type", "陌生人"),
                health=100,
                recent_actions=[],
                source="registry",
            )
            snapshots.append(snap)

        return snapshots

    def _predict_single_npc(self, snap: NpcSnapshot, from_day: int, to_day: int,
                             world_context: str, engine=None) -> Optional[NpcPrediction]:
        if not self.llm:
            return None

        delta_days = max(0, to_day - snap.last_seen_day)
        if delta_days == 0:
            return NpcPrediction(
                npc_id=snap.npc_id,
                name=snap.name,
                from_day=snap.last_seen_day,
                to_day=to_day,
                events=[],
                new_state={"location": snap.location, "role": snap.role},
                narrative=f"{snap.name}今天没有发生变化。",
                confidence=1.0,
            )

        recent_text = "无"
        if snap.recent_actions:
            recent_text = "；".join(
                f"第{a.get('day', '?')}天: {a.get('action', '')} - {a.get('detail', '')}"
                for a in snap.recent_actions[:3]
            )

        prompt = SINGLE_NPC_PREDICTION_PROMPT.format(
            npc_name=snap.name,
            from_day=snap.last_seen_day,
            to_day=to_day,
            age=snap.age,
            role=snap.role or "普通居民",
            personality=snap.personality or "普通",
            tags=", ".join(snap.tags) if snap.tags else "无",
            mbti_type=snap.mbti_type or "未知",
            location=snap.location or "未知",
            relation_type=snap.relation_type,
            favor=snap.favor,
            health=snap.health,
            recent_actions=recent_text,
            world_context=world_context or "未知世界",
            delta_days=delta_days,
        )

        try:
            response = self.llm.chat_json(prompt, temperature=0.7)
            events = response.get("events", [])
            new_state = response.get("new_state", {})
            narrative = response.get("narrative", "")

            return NpcPrediction(
                npc_id=snap.npc_id,
                name=snap.name,
                from_day=snap.last_seen_day,
                to_day=to_day,
                events=events,
                new_state=new_state,
                narrative=narrative,
                confidence=0.8,
            )
        except Exception as e:
            logger.warning("[Prediction] NPC %s 推演失败: %s", snap.name, e)
            return NpcPrediction(
                npc_id=snap.npc_id,
                name=snap.name,
                from_day=snap.last_seen_day,
                to_day=to_day,
                events=[],
                new_state={"location": snap.location, "role": snap.role},
                narrative=f"{snap.name}的推演失败，状态保持不变。",
                confidence=0.0,
            )

    def _cross_validate(self, predictions: list[NpcPrediction]) -> tuple[str, float]:
        if not self.llm or len(predictions) < 2:
            return "", 1.0

        pred_summary = []
        for p in predictions:
            events_text = "; ".join(
                f"{e.get('type', '?')}(第{e.get('day', '?')}天): {e.get('description', '')}"
                for e in (p.events or [])
            ) or "无事件"
            pred_summary.append({
                "npc_id": p.npc_id,
                "name": p.name,
                "from_day": p.from_day,
                "to_day": p.to_day,
                "events": events_text,
                "new_state": p.new_state,
            })

        prompt = CROSS_VALIDATION_PROMPT.format(
            count=len(predictions),
            from_day=predictions[0].from_day if predictions else 0,
            to_day=predictions[0].to_day if predictions else 0,
            predictions_json=json.dumps(pred_summary, ensure_ascii=False, indent=2),
        )

        try:
            response = self.llm.chat_json(prompt, temperature=0.3)
            issues = response.get("issues", [])
            score = response.get("overall_consistency", 0.8)
            summary = response.get("summary", "")

            notes_parts = []
            if issues:
                notes_parts.append(f"发现{len(issues)}个一致性问题:")
                for issue in issues:
                    notes_parts.append(f"  - {issue.get('npc_id', '?')}: {issue.get('issue', '')} -> {issue.get('suggestion', '')}")
            if summary:
                notes_parts.append(f"评价: {summary}")

            return "\n".join(notes_parts), min(max(score, 0.0), 1.0)
        except Exception as e:
            logger.warning("[Prediction] 一致性校验失败: %s", e)
            return "一致性校验失败", 0.5

    def _generate_report(self, predictions: list[NpcPrediction],
                          from_day: int, to_day: int,
                          world_context: str,
                          validation_notes: str, validation_score: float,
                          source_mode: str) -> PredictionReport:
        event_counts = {}
        for p in predictions:
            for e in (p.events or []):
                etype = e.get("type", "unknown")
                event_counts[etype] = event_counts.get(etype, 0) + 1

        summary_parts = [f"推演范围: 第{from_day}天 → 第{to_day}天"]
        if event_counts:
            summary_parts.append("主要变化:")
            EVENT_CN = {
                "marriage": "结婚", "first_child": "初为人父/母",
                "child_birth": "孩子出生", "career_advance": "升职",
                "start_business": "创业", "relocate": "搬家",
                "retire": "退休", "illness": "生病", "accident": "意外",
                "death_illness": "病逝", "death_old_age": "寿终正寝",
                "imprisonment": "入狱", "wealth_change": "财富变化",
                "leave_home": "离家", "join_faction": "加入势力",
            }
            for etype, count in sorted(event_counts.items(), key=lambda x: -x[1]):
                cn = EVENT_CN.get(etype, etype)
                summary_parts.append(f"  {cn}: {count}人")

        report = PredictionReport(
            title=f"NPC行动推演报告 — {world_context or '第{0}天~第{1}天'.format(from_day, to_day)}",
            total_npcs=len(predictions),
            predictions=predictions,
            cross_validation_notes=validation_notes,
            cross_validation_score=validation_score,
            summary="\n".join(summary_parts),
            from_day=from_day,
            to_day=to_day,
            generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            source_mode=source_mode,
        )
        return report

    def apply_predictions(self, npc_ids: list[str] = None,
                           engine=None) -> dict:
        if not self._current_report or not engine:
            return {"error": "无推演报告或引擎不可用"}

        applied = 0
        skipped = 0

        for pred in self._current_report.predictions:
            if npc_ids and pred.npc_id not in npc_ids:
                continue

            npc = engine.npc_states.get(pred.npc_id)
            if not npc:
                skipped += 1
                continue

            new_state = pred.new_state or {}
            if "location" in new_state and new_state["location"]:
                npc.current_location = new_state["location"]
            if "role" in new_state and new_state["role"]:
                npc.record_role_change(
                    new_state["role"],
                    "NPC推演自动更新",
                    engine.world_state.current_day if engine.world_state else 1,
                )
            if "tags" in new_state:
                for tag in new_state["tags"]:
                    if tag not in npc.tags:
                        npc.tags.append(tag)
            if "health" in new_state and isinstance(new_state["health"], (int, float)):
                npc.stats.health = max(0, min(npc.stats.max_health, int(new_state["health"])))
            if "favor" in new_state and isinstance(new_state["favor"], (int, float)):
                npc.relation_to_player.favor = max(0, min(100, int(new_state["favor"])))

            if pred.narrative:
                npc.recent_actions.append({
                    "day": engine.world_state.current_day if engine.world_state else 0,
                    "action": "prediction",
                    "detail": pred.narrative,
                    "location": npc.current_location,
                })
                if len(npc.recent_actions) > 10:
                    npc.recent_actions = npc.recent_actions[-10:]

            applied += 1

        if applied > 0 and engine.meta:
            engine.save_manager.save_state(
                engine.current_world_id,
                engine.meta,
                engine.world_state,
                engine.player_state,
                engine.npc_states,
            )

        return {"applied": applied, "skipped": skipped}

    def get_current_report(self) -> Optional[PredictionReport]:
        return self._current_report

    def to_dict(self) -> dict:
        if self._current_report:
            return self._current_report.to_dict()
        return {}
