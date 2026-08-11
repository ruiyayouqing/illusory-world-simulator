"""
[v1.6] NPC-NPC 对话编排器 — 让虚拟世界里的 NPC 之间能够自由对话。

设计目标：
  - 突破"玩家中心化"对话模式，NPC 之间也能自由交流
  - 单次 LLM 调用生成多轮对话，避免 LLM 调用爆炸
  - 玩家不在场时也产生对话，整理为"江湖见闻"供玩家感知
  - 与现有 npc_reaction / group_chat / social_network 协同工作

触发流程：
  WorldManager.on_new_day / advance_time
    → NpcDialogueManager.maybe_trigger_dialogues()
        → 筛选同场景 + 关系强度达标 + 双方非 sleeping 的 NPC 对
        → 按场景类型（偶遇/议事/冲突/闲聊/密谋）模板组装上下文
        → 单次 LLM 调用生成完整多轮对话（JSON 数组）
        → 写入双方记忆 + 派发 EventBus 事件 + 返回见闻摘要

数据流：
  dialogue_session → 双方 episodic_memory + social_network 关系更新
                   → EventBus.emit("on_npc_dialogue", session)
                   → WorldManager 收集 → 前端「江湖见闻」面板
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .schemas import NPCState, WorldState, PlayerState
    from .npc_perception import NPCPerceptionSystem
    from .social_network import SocialNetwork

logger = logging.getLogger("chronoverse.npc_dialogue")


# ── 对话场景类型 ──────────────────────────────────────────────
DIALOGUE_SCENES = {
    "encounter": {
        "name": "偶遇闲谈",
        "description": "路遇熟人，寒暄近况",
        "min_relation": 20,
        "max_turns": 4,
        "weight": 30,
    },
    "discuss": {
        "name": "议事商谈",
        "description": "商讨正事、交换情报",
        "min_relation": 40,
        "max_turns": 6,
        "weight": 25,
    },
    "conflict": {
        "name": "争执冲突",
        "description": "立场对立，言语交锋",
        "min_relation": -100,  # 仇人也可触发
        "max_turns": 5,
        "weight": 10,
    },
    "smalltalk": {
        "name": "市井闲聊",
        "description": "茶馆酒肆的闲言碎语",
        "min_relation": 10,
        "max_turns": 3,
        "weight": 25,
    },
    "confide": {
        "name": "密谈私语",
        "description": "私密谈话，交换秘密",
        "min_relation": 60,
        "max_turns": 5,
        "weight": 10,
    },
}


# ── 对话生成 prompt ─────────────────────────────────────────────
NPC_DIALOGUE_PROMPT = """你是虚拟世界中的多角色对话编排器。请根据以下两个 NPC 的人设和场景，生成一段自然的多轮对话。

【场景类型】{scene_name}（{scene_description}）
【发生地点】{location}（第{day}天 {time}，{weather}）

【角色 A】
姓名：{npc_a_name}
年龄：{npc_a_age}
身份：{npc_a_role}
性格：{npc_a_personality}
说话风格：{npc_a_speaking}
近期心事：{npc_a_concern}
对 B 的好感：{npc_a_favor}/100

【角色 B】
姓名：{npc_b_name}
年龄：{npc_b_age}
身份：{npc_b_role}
性格：{npc_b_personality}
说话风格：{npc_b_speaking}
近期心事：{npc_b_concern}
对 A 的好感：{npc_b_favor}/100

【两人关系】
{relation_desc}

【最近江湖大事】（可作为话题）
{world_events}

【输出要求】
1. 生成 {max_turns} 轮以内的对话（每人说一次算一轮）
2. 严格保持双方说话风格和性格
3. 对话内容要符合场景类型和两人关系
4. 可包含动作神态描写（用括号标注）
5. 信息密度高，避免空话

【输出 JSON 格式】
{{
    "dialogue": [
        {{
            "speaker": "角色名",
            "content": "台词（可含动作描写）",
            "emotion": "情绪/态度关键词",
            "action": "可选，伴随的动作"
        }}
    ],
    "summary": "20字以内的见闻摘要（用于玩家听闻）",
    "relation_change": {{
        "a_to_b": -5到5的整数,
        "b_to_a": -5到5的整数
    }},
    "topic_tags": ["话题标签1", "话题标签2"]
}}
只输出 JSON。"""


class NpcDialogueManager:
    """NPC-NPC 对话编排器

    用法：
        mgr = NpcDialogueManager(llm, perception, social_network)
        sessions = mgr.maybe_trigger_dialogues(all_npcs, world_state, player)
        # sessions 即可派发到 EventBus + 写入「江湖见闻」
    """

    # 每天最多产生多少场 NPC 对话（防止 LLM 调用爆炸）
    MAX_DIALOGUES_PER_DAY = 3
    # 触发概率（每天评估每对 NPC 时 roll 一次）
    TRIGGER_PROBABILITY = 0.15
    # 同一对 NPC 两次对话间隔的最小天数
    MIN_INTERVAL_DAYS = 2

    def __init__(
        self,
        llm: "BaseLLM | None" = None,
        perception: "NPCPerceptionSystem | None" = None,
        social_network: "SocialNetwork | None" = None,
    ):
        self.llm = llm
        self.perception = perception
        self.social_network = social_network
        # 历史记录：{(npc_a_id, npc_b_id): last_day}
        self._dialogue_history: dict[tuple[str, str], int] = {}
        # 当日已产生的对话数
        self._today_count: dict[int, int] = {}
        # 最近 N 场对话会话（供前端拉取）
        self.recent_sessions: list[dict] = []
        self.MAX_RECENT_SESSIONS = 30

    # ── 主入口 ───────────────────────────────────────────────

    def maybe_trigger_dialogues(
        self,
        all_npcs: "dict[str, NPCState]",
        world_state: "WorldState",
        player: "PlayerState | None" = None,
    ) -> list[dict]:
        """评估并可能触发 NPC 之间的对话。

        在 WorldManager.on_new_day 或 advance_time 中调用。

        Returns:
            对话会话列表（每场对话一个 dict），供调用方派发事件 / 写入见闻流
        """
        if not self.llm or not all_npcs:
            return []

        day = world_state.current_day
        # 当日配额检查
        if self._today_count.get(day, 0) >= self.MAX_DIALOGUES_PER_DAY:
            return []

        # 收集活跃 NPC 候选（非休眠、非垂死、非昏迷）
        candidates = []
        for npc_id, npc in all_npcs.items():
            if npc_id == "player":
                continue
            if getattr(npc, "is_dormant", False) or getattr(npc, "hidden", False):
                continue
            if any(s in (npc.status_effects or [])
                   for s in ("昏迷", "垂死", "囚禁", "已故")):
                continue
            candidates.append(npc)

        if len(candidates) < 2:
            return []

        # 按 location 分组，只对同场景 NPC 配对
        by_location: dict[str, list["NPCState"]] = {}
        for npc in candidates:
            loc = npc.current_location or "unknown"
            by_location.setdefault(loc, []).append(npc)

        sessions: list[dict] = []
        for loc, group in by_location.items():
            if len(group) < 2:
                continue
            # 在同场景中找合适的一对
            pair = self._pick_pair(group, day)
            if pair is None:
                continue
            npc_a, npc_b, scene_type = pair

            # 当日配额再次检查
            if self._today_count.get(day, 0) >= self.MAX_DIALOGUES_PER_DAY:
                break

            # 生成对话
            session = self._generate_dialogue(
                npc_a, npc_b, scene_type, loc, world_state, player,
            )
            if session:
                sessions.append(session)
                self._today_count[day] = self._today_count.get(day, 0) + 1
                # 更新历史
                pair_key = self._pair_key(npc_a.agent_id, npc_b.agent_id)
                self._dialogue_history[pair_key] = day
                # 缓存到 recent_sessions
                self.recent_sessions.append(session)
                if len(self.recent_sessions) > self.MAX_RECENT_SESSIONS:
                    self.recent_sessions = self.recent_sessions[-self.MAX_RECENT_SESSIONS:]

        # 清理过期历史（保留 30 天）
        cutoff = day - 30
        for k in list(self._dialogue_history.keys()):
            if self._dialogue_history[k] < cutoff:
                del self._dialogue_history[k]
        # 清理旧日配额
        for d in list(self._today_count.keys()):
            if d < cutoff:
                del self._today_count[d]

        if sessions:
            logger.info("[NpcDialogue] day=%d 触发 %d 场对话", day, len(sessions))
        return sessions

    # ── 内部方法 ─────────────────────────────────────────────

    def _pick_pair(
        self, group: "list[NPCState]", day: int,
    ) -> "tuple[NPCState, NPCState, str] | None":
        """从同场景 NPC 列表中挑出一对合适的对话者。

        选择策略：
        1. 遍历所有可能的两两组合，过滤掉冷却期内的
        2. 按 trigger_probability roll
        3. 按 scene 类型权重抽取
        """
        import itertools

        valid_pairs = []
        for a, b in itertools.combinations(group, 2):
            pair_key = self._pair_key(a.agent_id, b.agent_id)
            last_day = self._dialogue_history.get(pair_key, -999)
            if day - last_day < self.MIN_INTERVAL_DAYS:
                continue
            # roll 触发概率
            if random.random() > self.TRIGGER_PROBABILITY:
                continue

            # 决定 scene 类型
            scene_type = self._decide_scene_type(a, b)
            if scene_type is None:
                continue

            weight = DIALOGUE_SCENES[scene_type]["weight"]
            valid_pairs.append((a, b, scene_type, weight))

        if not valid_pairs:
            return None

        # 加权随机选一对
        total_w = sum(p[3] for p in valid_pairs)
        r = random.uniform(0, total_w)
        acc = 0
        for a, b, st, w in valid_pairs:
            acc += w
            if r <= acc:
                return a, b, st
        return valid_pairs[-1][:3]

    def _decide_scene_type(self, a: "NPCState", b: "NPCState") -> "str | None":
        """根据两人关系决定对话场景类型。

        Returns:
            scene_type 字符串；None 表示不触发对话
        """
        # 双方互相对对方的 favor（取 NPC impression_of_player 不适用，
        # 这里用 social_network 的关系强度；若没有则用默认 50）
        favor_ab = self._get_favor(a, b)
        favor_ba = self._get_favor(b, a)
        avg_favor = (favor_ab + favor_ba) / 2

        # 性格关键词
        p_a = (a.personality or "").lower()
        p_b = (b.personality or "").lower()

        # 仇人配对：双方 favor 很低 → 可能触发冲突
        if avg_favor < 20:
            return "conflict"

        # 密谈：双方好感高且至少一方性格"狡黠/谨慎/深沉"
        if avg_favor >= 60 and any(
            k in p_a + p_b for k in ("狡", "慎", "深", "谋", "隐")
        ):
            return "confide"

        # 议事：双方好感中等以上且都有 role（职业身份）
        if avg_favor >= 40 and (a.role or b.role):
            return "discuss"

        # 偶遇：好感 20+ 的熟人
        if avg_favor >= 20:
            return "encounter"

        # 闲聊
        if avg_favor >= 10:
            return "smalltalk"

        return None

    def _get_favor(self, a: "NPCState", b: "NPCState") -> int:
        """获取 a 对 b 的好感度。

        优先用 social_network.get_relation_strength，回退到默认 50。
        """
        if self.social_network:
            try:
                strength = self.social_network.get_relation_strength(a.agent_id, b.agent_id)
                if strength > 0:
                    return strength
            except Exception:
                pass
        return 50

    def _pair_key(self, a_id: str, b_id: str) -> tuple[str, str]:
        """生成无序的 pair key（a-b 和 b-a 视为同一对）"""
        return tuple(sorted([a_id, b_id]))  # type: ignore[return-value]

    def _generate_dialogue(
        self,
        npc_a: "NPCState",
        npc_b: "NPCState",
        scene_type: str,
        location: str,
        world_state: "WorldState",
        player: "PlayerState | None",
    ) -> "dict | None":
        """调 LLM 生成一场对话。"""
        scene = DIALOGUE_SCENES[scene_type]
        # 地点显示名
        loc_name = self._resolve_location_name(location, world_state)
        # 两人关系描述
        relation_desc = self._describe_relation(npc_a, npc_b)
        # 世界大事摘要
        world_events = self._get_world_events_brief(world_state)
        # 各自近期心事
        concern_a = self._extract_concern(npc_a)
        concern_b = self._extract_concern(npc_b)
        favor_ab = self._get_favor(npc_a, npc_b)
        favor_ba = self._get_favor(npc_b, npc_a)

        prompt = NPC_DIALOGUE_PROMPT.format(
            scene_name=scene["name"],
            scene_description=scene["description"],
            location=loc_name,
            day=world_state.current_day,
            time=world_state.current_time,
            weather=world_state.weather,
            npc_a_name=npc_a.name,
            npc_a_age=npc_a.age,
            npc_a_role=npc_a.role or "无",
            npc_a_personality=npc_a.personality or "普通",
            npc_a_speaking=npc_a.speaking_style or "正常",
            npc_a_concern=concern_a,
            npc_a_favor=favor_ab,
            npc_b_name=npc_b.name,
            npc_b_age=npc_b.age,
            npc_b_role=npc_b.role or "无",
            npc_b_personality=npc_b.personality or "普通",
            npc_b_speaking=npc_b.speaking_style or "正常",
            npc_b_concern=concern_b,
            npc_b_favor=favor_ba,
            relation_desc=relation_desc,
            world_events=world_events,
            max_turns=scene["max_turns"],
        )

        try:
            result = self.llm.chat_json(prompt, temperature=0.7, max_tokens=0)
        except Exception as e:
            logger.warning("[NpcDialogue] LLM 调用失败 (%s vs %s): %s",
                          npc_a.name, npc_b.name, e)
            return None

        if not result or "dialogue" not in result:
            logger.debug("[NpcDialogue] LLM 返回空对话")
            return None

        # 构建对话会话对象
        session = {
            "session_id": f"dlg_{world_state.current_day}_{npc_a.agent_id}_{npc_b.agent_id}",
            "day": world_state.current_day,
            "time": world_state.current_time,
            "location": location,
            "location_name": loc_name,
            "scene_type": scene_type,
            "scene_name": scene["name"],
            "participants": [
                {"npc_id": npc_a.agent_id, "name": npc_a.name},
                {"npc_id": npc_b.agent_id, "name": npc_b.name},
            ],
            "dialogue": result.get("dialogue", []),
            "summary": result.get("summary", f"{npc_a.name}与{npc_b.name}在{loc_name}{scene['name']}"),
            "relation_change": result.get("relation_change", {}),
            "topic_tags": result.get("topic_tags", []),
            "player_witnessed": self._is_player_witnessed(location, player),
        }

        # 写入双方记忆 + 更新关系
        self._apply_dialogue_effects(npc_a, npc_b, session, world_state.current_day)

        return session

    # ── 副作用：写入记忆 + 关系更新 ─────────────────────────

    def _apply_dialogue_effects(
        self,
        npc_a: "NPCState",
        npc_b: "NPCState",
        session: dict,
        day: int,
    ):
        """把对话写入双方记忆，更新关系。"""
        summary = session.get("summary", "")
        scene_name = session.get("scene_name", "对话")
        loc_name = session.get("location_name", "")
        topic_tags = session.get("topic_tags", [])

        # A 的记忆
        npc_a.recent_actions.append({
            "day": day,
            "action": "dialogue",
            "target": npc_b.name,
            "detail": f"在{loc_name}与{npc_b.name}{scene_name}。{summary}",
            "topic_tags": topic_tags,
        })
        if len(npc_a.recent_actions) > 10:
            npc_a.recent_actions = npc_a.recent_actions[-10:]

        # B 的记忆
        npc_b.recent_actions.append({
            "day": day,
            "action": "dialogue",
            "target": npc_a.name,
            "detail": f"在{loc_name}与{npc_a.name}{scene_name}。{summary}",
            "topic_tags": topic_tags,
        })
        if len(npc_b.recent_actions) > 10:
            npc_b.recent_actions = npc_b.recent_actions[-10:]

        # 关系变化：用 add_link 直接覆盖更新 strength
        rel_change = session.get("relation_change", {}) or {}
        a_to_b = int(rel_change.get("a_to_b", 0) or 0)
        b_to_a = int(rel_change.get("b_to_a", 0) or 0)
        if self.social_network:
            try:
                if a_to_b != 0:
                    cur = self.social_network.get_relation_strength(
                        npc_a.agent_id, npc_b.agent_id
                    )
                    new_strength = max(0, min(100, cur + a_to_b)) if cur > 0 else max(0, min(100, 50 + a_to_b))
                    self.social_network.add_link(
                        npc_a.agent_id, npc_b.agent_id,
                        "熟人" if new_strength >= 30 else "点头之交",
                        strength=new_strength,
                    )
                if b_to_a != 0:
                    cur = self.social_network.get_relation_strength(
                        npc_b.agent_id, npc_a.agent_id
                    )
                    new_strength = max(0, min(100, cur + b_to_a)) if cur > 0 else max(0, min(100, 50 + b_to_a))
                    self.social_network.add_link(
                        npc_b.agent_id, npc_a.agent_id,
                        "熟人" if new_strength >= 30 else "点头之交",
                        strength=new_strength,
                    )
            except Exception as e:
                logger.debug("[NpcDialogue] 关系更新失败: %s", e)

    # ── 工具方法 ─────────────────────────────────────────────

    def _resolve_location_name(self, location: str, world_state: "WorldState") -> str:
        """location code → 显示名"""
        if not location:
            return "未知之地"
        locs = getattr(world_state, "locations", None) or {}
        if location in locs:
            loc_obj = locs[location]
            if isinstance(loc_obj, dict):
                return loc_obj.get("location_name") or loc_obj.get("name") or location
            if hasattr(loc_obj, "location_name"):
                return loc_obj.location_name or location
            if hasattr(loc_obj, "name"):
                return loc_obj.name or location
        return location

    def _describe_relation(self, a: "NPCState", b: "NPCState") -> str:
        fab = self._get_favor(a, b)
        fba = self._get_favor(b, a)
        if fab < 20 or fba < 20:
            return f"两人有嫌隙（{a.name}好感{fab}，{b.name}好感{fba}）"
        if fab >= 60 and fba >= 60:
            return f"两人交情深厚（{a.name}好感{fab}，{b.name}好感{fba}）"
        if fab >= 40 or fba >= 40:
            return f"两人是相识的同行（{a.name}好感{fab}，{b.name}好感{fba}）"
        return f"两人略有点头之交（{a.name}好感{fab}，{b.name}好感{fba}）"

    def _get_world_events_brief(self, world_state: "WorldState") -> str:
        """最近世界大事摘要，作为对话话题素材。"""
        summary = getattr(world_state, "event_history_summary", "") or ""
        if not summary:
            return "近期无特殊事件"
        return summary[-300:]

    def _extract_concern(self, npc: "NPCState") -> str:
        """从 NPC 状态中提取近期心事。"""
        # 优先用 current_goal
        goal = npc.ai_behavior.get("current_goal", "") if npc.ai_behavior else ""
        long_term = npc.ai_behavior.get("long_term_goal", "") if npc.ai_behavior else ""
        # 取最近一条 recent_action
        recent = ""
        if npc.recent_actions:
            last = npc.recent_actions[-1]
            recent = last.get("detail", "") if isinstance(last, dict) else str(last)
            recent = recent[:60]
        parts = []
        if goal:
            parts.append(f"当前目标：{goal}")
        if long_term:
            parts.append(f"长远志向：{long_term}")
        if recent:
            parts.append(f"最近事项：{recent}")
        return "；".join(parts) if parts else "无特别心事"

    def _is_player_witnessed(self, location: str, player: "PlayerState | None") -> bool:
        """玩家是否目击了这场对话（同场景）"""
        if not player:
            return False
        return (player.location or "") == (location or "")

    # ── 外部 API ─────────────────────────────────────────────

    def get_recent_sessions(self, limit: int = 10) -> list[dict]:
        """获取最近的对话会话（供前端「江湖见闻」面板使用）。"""
        return list(reversed(self.recent_sessions[-limit:]))

    def get_rumor_feed(self, limit: int = 10, player_location: str = "") -> list[dict]:
        """生成玩家可听闻的传闻流。

        玩家目击的对话可见完整内容，远处对话降级为传闻摘要。
        """
        rumors = []
        for s in reversed(self.recent_sessions[-limit * 2:]):
            if s.get("player_witnessed"):
                rumors.append({
                    "type": "witnessed",
                    "day": s["day"],
                    "time": s.get("time", ""),
                    "location": s.get("location_name", ""),
                    "scene_name": s.get("scene_name", ""),
                    "participants": [p["name"] for p in s.get("participants", [])],
                    "summary": s.get("summary", ""),
                    "dialogue": s.get("dialogue", []),
                    "topic_tags": s.get("topic_tags", []),
                })
            else:
                # 远处传闻：只暴露模糊信息
                participants = [p["name"] for p in s.get("participants", [])]
                # 概率性模糊化姓名
                if random.random() < 0.4 and len(participants) >= 2:
                    participants = [f"某{random.choice(['客', '人', '者'])}"] + participants[1:]
                rumors.append({
                    "type": "rumor",
                    "day": s["day"],
                    "location": s.get("location_name", ""),
                    "scene_name": s.get("scene_name", ""),
                    "summary": s.get("summary", ""),
                    "participants": participants,
                    "topic_tags": s.get("topic_tags", [])[:2],
                })
        return rumors[:limit]


# ── 全局单例 ──────────────────────────────────────────────────
_global_manager: "NpcDialogueManager | None" = None


def get_npc_dialogue_manager() -> "NpcDialogueManager | None":
    global _global_manager
    return _global_manager


def set_npc_dialogue_manager(mgr: "NpcDialogueManager | None"):
    global _global_manager
    _global_manager = mgr
