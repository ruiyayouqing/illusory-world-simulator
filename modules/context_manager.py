from __future__ import annotations
import re
from .schemas import PlayerState, WorldState, NPCState
from .prompt_utils import resolve_location_name  # [Bug] location code → display name


class ContextManager:
    """管理AI调用时的上下文注入，提高一致性"""

    def __init__(self):
        self.world_summary_cache: str = ""
        self.npc_summary_cache: str = ""

    def build_full_context(self, state: PlayerState, player_input: str,
                           world_state: dict = None, day: int = 1,
                           npc_names: list[str] = None,
                           npc_states: dict = None,
                           narrative_history: list[dict] = None) -> str:
        """构建完整的上下文，包含NPC详情、世界设定、近期历史"""

        parts = []

        # 1. NPC详情注入（RAG检索相关NPC）
        npc_text = self._build_npc_context(player_input, npc_names, npc_states, world_state)  # [Bug] 传入 world_state 以解析 location code
        if npc_text:
            parts.append(npc_text)

        # 2. 世界设定注入
        world_text = self._build_world_context(world_state)
        if world_text:
            parts.append(world_text)

        # 3. 近期叙事摘要（带关键词检索）
        history_text = self._build_history_context(player_input, narrative_history)
        if history_text:
            parts.append(history_text)

        # 4. 玩家当前状态
        player_text = self._build_player_context(state, world_state)  # [Bug] 传入 world_state 以解析 location code
        parts.append(player_text)

        return "\n".join(parts)

    def _build_npc_context(self, player_input: str, npc_names: list[str] = None,
                           npc_states: dict = None, world_state=None) -> str:  # [Bug] 增加 world_state 参数
        """构建NPC上下文，包含完整身份信息"""
        if not npc_names or not npc_states:
            return ""

        lines = ["【已知人物及设定（必须严格遵守，不可篡改身份）】"]
        for nid, npc in npc_states.items():
            name = npc.name
            detail = f"- {name}"
            if npc.personality:
                detail += f"（{npc.personality[:60]}）"
            if npc.tags:
                detail += f" 标签:{', '.join(npc.tags[:3])}"
            if npc.current_location:
                detail += f" 位置:{resolve_location_name(npc.current_location, world_state)}"  # [Bug] location code → display name
            rel = npc.relation_to_player
            if rel:
                detail += f" 关系:{rel.relation_type} 好感:{rel.favor}"
            lines.append(detail)

        return "\n".join(lines)

    def _build_world_context(self, world_state: dict = None) -> str:
        """构建世界设定上下文"""
        if not world_state:
            return ""

        world_type = world_state.get("world_type", "custom")
        world_name = world_state.get("world_name", "未知世界")
        world_desc = world_state.get("description", "")[:300]
        era = world_state.get("era_name", "")
        era_year = world_state.get("era_year", "")

        world_type_names = {
            "historical": "历史世界（真实朝代，无魔法修仙，无现代科技）",
            "wuxia": "武侠世界（有内力武功，无魔法枪械）",
            "xianxia": "修仙世界（有灵气法宝，无现代科技）",
            "fantasy": "奇幻世界（有魔法种族，根据具体设定）",
            "scifi": "科幻世界（有高科技，根据具体时代）",
            "postapocalyptic": "末日世界（文明崩塌，资源稀缺）",
            "modern": "现代世界（当代社会）",
            "custom": "自定义世界",
        }

        return (
            f"【世界设定 - 极其重要，必须严格遵守】\n"
            f"世界名称: {world_name}\n"
            f"纪年: {era}{era_year}\n"
            f"世界类型: {world_type_names.get(world_type, '未知世界')}\n"
            f"世界背景: {world_desc}\n"
            f"你必须根据这个世界类型来判断玩家行为是否合理。"
            f"如果玩家的行为与世界类型矛盾，你必须在叙事中合理否定。"
        )

    def _build_history_context(self, player_input: str,
                               narrative_history: list[dict] = None) -> str:
        """构建近期叙事上下文，检索与当前输入相关的历史"""
        if not narrative_history:
            return ""

        # 提取玩家输入的关键词
        keywords = set(re.findall(r'[\u4e00-\u9fa5]{2,}', player_input))

        # 检索相关历史（最近5条 + 关键词匹配）
        relevant = []
        recent = narrative_history[-5:] if len(narrative_history) > 5 else narrative_history

        for entry in recent:
            text = entry.get("text", "")[:200]
            if not text:
                continue

            # 检查关键词匹配
            matched = any(kw in text for kw in keywords) if keywords else False
            if matched or len(relevant) < 3:
                relevant.append(text)

        if not relevant:
            return ""

        return (
            f"【近期叙事摘要】\n"
            + "\n".join([f"- {t[:150]}..." for t in relevant[-3:]])
            + "\n请保持与上述叙事的连贯性。"
        )

    def _build_player_context(self, state: PlayerState, world_state=None) -> str:  # [Bug] 增加 world_state 参数
        """构建玩家状态上下文"""
        return (
            f"【玩家信息】\n"
            f"姓名: {state.name}, 年龄: {state.age}\n"
            f"位置: {resolve_location_name(state.location, world_state)}\n"  # [Bug] location code → display name
            f"属性: 力量{state.stats.strength} 敏捷{state.stats.agility} "
            f"智力{state.stats.intelligence} 幸运{state.stats.luck}\n"
            f"生命: {state.stats.health}/{state.stats.max_health} "
            f"体力: {state.stats.energy}/{state.stats.max_energy}\n"
            f"金币: {state.social.gold} 声望: {state.social.reputation}\n"
            f"标签: {', '.join(state.tags)}\n"
            f"状态: {', '.join(state.status_effects) if state.status_effects else '正常'}\n"
            f"记忆: {'; '.join(state.memory.short_term[-10:])}\n"
        )

    def update_caches(self, world_state: WorldState = None,
                      npc_states: dict = None):
        """更新缓存（当世界状态或NPC变化时调用）"""
        if world_state:
            self.world_summary_cache = f"{world_state.world_name} - {world_state.world_type}"
        if npc_states:
            self.npc_summary_cache = ", ".join([npc.name for npc in npc_states.values()])
