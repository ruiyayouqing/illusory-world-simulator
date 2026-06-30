"""
群聊/多NPC对话管理器

参考 SillyTavern 的群聊设计，支持：
- Swap 模式：每次只有1个NPC回复（轮流）
- Join 模式：多个NPC同时在prompt中
- 自然发言顺序：基于性格的 Talkativeness 因子
- 玩家可以选择指名NPC
"""
from __future__ import annotations
import random
import logging
from typing import TYPE_CHECKING
from .prompt_utils import resolve_location_name  # [Bug] location code → display name

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .schemas import NPCState, PlayerState, WorldState

from .prompt.group_prompts import (
    GROUP_SCENE_PROMPT, GROUP_NPC_REPLY_PROMPT, GROUP_NARRATIVE_PROMPT,
)

logger = logging.getLogger("chronoverse.group_chat")


class GroupChatManager:
    """群聊/多NPC对话管理器"""

    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.active_groups: dict[str, list[str]] = {}  # scene_id -> [npc_ids]
        self.dialogue_log: list[dict] = []  # 当前场景的对话记录

    def start_group_scene(self, npcs: list[NPCState], player: PlayerState,
                          world_state: WorldState,
                          player_input: str = "") -> dict:
        """
        开始群聊场景。
        
        Returns:
            包含 scene_narrative, reply_order, participants
        """
        self.dialogue_log = []
        if player_input:
            self.dialogue_log.append({
                "speaker": player.name,
                "content": player_input,
                "type": "player",
            })

        participants = self._build_participants(npcs)
        participants_text = "\n".join([
            f"- {p['name']}: {p['personality'][:40]}, 说话风格={p['speaking_style'][:30]}, "
            f"好感={p['favor']}, 位置={p['location']}"
            for p in participants
        ])

        try:
            # [Bug] 使用 location_name（如"汴京城"）而非 location code（如"bianjing"）
            loc_name = player.location
            if world_state and hasattr(world_state, 'locations') and player.location in world_state.locations:
                loc_obj = world_state.locations[player.location]
                if isinstance(loc_obj, dict):
                    loc_name = loc_obj.get('location_name') or loc_obj.get('name') or player.location
                elif hasattr(loc_obj, 'location_name'):
                    loc_name = loc_obj.location_name or player.location
                elif hasattr(loc_obj, 'name'):
                    loc_name = loc_obj.name or player.location
            prompt = GROUP_SCENE_PROMPT.format(
                participants_text=participants_text,
                location=loc_name,
                time=world_state.current_time,
                weather=world_state.weather,
                event_context=world_state.event_history_summary[:200] if world_state.event_history_summary else "无特殊事件",
                group_history=self._format_dialogue_log(),
                player_input=player_input or "(场景开始)",
            )
            result = self.llm.chat_json(prompt, temperature=0.6, max_tokens=0)
            return {
                "scene_narrative": result.get("scene_narrative", ""),
                "reply_order": result.get("reply_order", []),
                "participants": participants,
            }
        except Exception as e:
            logger.warning("群聊场景生成失败: %s", e)
            # 回退：随机选2个NPC
            reply_order = [{"npc_id": n.agent_id, "reason": "随机参与"}
                          for n in random.sample(npcs, min(2, len(npcs)))]
            return {
                "scene_narrative": f"在{resolve_location_name(player.location, world_state)}，众人聚在一起交谈。",  # [Bug] location code → display name
                "reply_order": reply_order,
                "participants": participants,
            }

    def generate_npc_reply(self, npc: NPCState, player: PlayerState,
                           world_state: WorldState,
                           latest_message: str,
                           speaker_name: str,
                           other_npcs: list[NPCState] = None) -> dict:
        """
        生成单个NPC在群聊中的回复（Swap模式）。
        
        Returns:
            包含 dialogue, mood_change, favor_change
        """
        other_text = "无其他人" if not other_npcs else "; ".join([
            f"{n.name}({n.personality[:20]})" for n in other_npcs[:4]
        ])

        try:
            prompt = GROUP_NPC_REPLY_PROMPT.format(
                npc_name=npc.name,
                npc_age=npc.age,
                personality=npc.personality or "普通",
                speaking_style=npc.speaking_style or "正常",
                mood="正常",
                relation_type=npc.relation_to_player.relation_type,
                favor=npc.relation_to_player.favor,
                group_history=self._format_dialogue_log(),
                speaker=speaker_name,
                latest_message=latest_message[:300],
                other_participants=other_text,
            )
            reply = self.llm.chat(prompt, temperature=0.7, max_tokens=1024)
            reply = reply.strip()

            # 记录到对话日志
            self.dialogue_log.append({
                "speaker": npc.name,
                "content": reply,
                "type": "npc",
            })

            return {
                "dialogue": reply,
                "npc_name": npc.name,
                "npc_id": npc.agent_id,
            }
        except Exception as e:
            logger.warning("NPC群聊回复生成失败: %s", e)
            fallback = f"{npc.name}沉默不语，似乎在思考什么。"
            self.dialogue_log.append({
                "speaker": npc.name, "content": fallback, "type": "npc",
            })
            return {"dialogue": fallback, "npc_name": npc.name,
                    "npc_id": npc.agent_id}

    def decide_reply_order(self, npcs: list[NPCState],
                           player_input: str,
                           strategy: str = "natural") -> list[NPCState]:
        """
        决定NPC发言顺序。
        
        Args:
            npcs: 可参与的NPC列表
            player_input: 玩家输入
            strategy: "natural" / "random" / "talkativeness"
        """
        if not npcs:
            return []

        # 检查玩家是否指名了某个NPC
        for npc in npcs:
            if npc.name in player_input:
                # 被指名的NPC排第一
                others = [n for n in npcs if n != npc]
                return [npc] + self._sort_by_talkativeness(others)

        if strategy == "random":
            return random.sample(npcs, min(3, len(npcs)))
        elif strategy == "talkativeness":
            return self._sort_by_talkativeness(npcs)[:3]
        else:  # natural
            # 混合策略：性格外向的优先，但有随机性
            sorted_npcs = self._sort_by_talkativeness(npcs)
            result = []
            for npc in sorted_npcs[:3]:
                # 每个NPC有基于talkativeness的发言概率
                talk_score = self._get_talkativeness(npc)
                if random.random() < talk_score or not result:
                    result.append(npc)
            return result if result else [sorted_npcs[0]]

    def generate_group_narrative(self, player: PlayerState,
                                  world_state: WorldState) -> str:
        """将群聊对话日志生成为小说体叙事"""
        if len(self.dialogue_log) < 2:
            return ""
        participants = list(set(d["speaker"] for d in self.dialogue_log))
        dialogue_text = "\n".join([
            f"{d['speaker']}: {d['content'][:200]}" for d in self.dialogue_log
        ])
        try:
            # [Bug] 使用 location_name（如"汴京城"）而非 location code（如"bianjing"）
            loc_name = player.location
            if world_state and hasattr(world_state, 'locations') and player.location in world_state.locations:
                loc_obj = world_state.locations[player.location]
                if isinstance(loc_obj, dict):
                    loc_name = loc_obj.get('location_name') or loc_obj.get('name') or player.location
                elif hasattr(loc_obj, 'location_name'):
                    loc_name = loc_obj.location_name or player.location
                elif hasattr(loc_obj, 'name'):
                    loc_name = loc_obj.name or player.location
            prompt = GROUP_NARRATIVE_PROMPT.format(
                location=loc_name,
                time=world_state.current_time,
                participants=", ".join(participants),
                dialogue_log=dialogue_text,
            )
            return self.llm.chat(prompt, temperature=0.7, max_tokens=1024)
        except Exception as e:
            logger.warning("Group chat reply generation failed: %s", e)
            return ""

    def get_dialogue_log(self) -> list[dict]:
        """获取当前场景的对话日志"""
        return list(self.dialogue_log)

    def clear_dialogue_log(self):
        """清空对话日志"""
        self.dialogue_log = []

    # ── 内部方法 ──────────────────────────────────────────

    def _build_participants(self, npcs: list[NPCState]) -> list[dict]:
        return [
            {"id": npc.agent_id, "name": npc.name,
             "personality": npc.personality or "普通",
             "speaking_style": npc.speaking_style or "正常",
             "favor": npc.relation_to_player.favor,
             "location": npc.current_location or "未知",
             "talkativeness": self._get_talkativeness(npc)}
            for npc in npcs
        ]

    def _sort_by_talkativeness(self, npcs: list[NPCState]) -> list[NPCState]:
        """按健谈度排序（基于性格关键词）"""
        return sorted(npcs, key=lambda n: self._get_talkativeness(n), reverse=True)

    def _get_talkativeness(self, npc: NPCState) -> float:
        """计算NPC的健谈度（0.0-1.0），基于性格和MBTI"""
        score = 0.5  # 基础值
        personality = (npc.personality or "").lower()
        # 外向性格加分
        extrovert_keywords = ["热情", "开朗", "健谈", "活泼", "豪爽", "爱说", "幽默"]
        introvert_keywords = ["沉默", "内向", "安静", "害羞", "寡言", "冷淡"]
        for kw in extrovert_keywords:
            if kw in personality:
                score += 0.15
        for kw in introvert_keywords:
            if kw in personality:
                score -= 0.15
        # MBTI 修正
        if npc.mbti_type:
            from .mbti_styles import get_mbti_profile
            profile = get_mbti_profile(npc.mbti_type)
            if profile:
                score = score * 0.5 + profile.social_frequency * 0.5
        # 好感度影响：好感越高越愿意说话
        favor = npc.relation_to_player.favor
        score += (favor - 50) * 0.002
        return max(0.1, min(1.0, score))

    def _format_dialogue_log(self) -> str:
        """格式化对话日志"""
        if not self.dialogue_log:
            return "（对话刚开始）"
        return "\n".join([
            f"{d['speaker']}: {d['content'][:200]}" for d in self.dialogue_log[-10:]
        ])
