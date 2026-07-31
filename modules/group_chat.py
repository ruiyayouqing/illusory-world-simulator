"""
群聊/多NPC对话管理器

参考 SillyTavern 的群聊设计，支持：
- Swap 模式：每次只有1个NPC回复（轮流）
- Join 模式：多个NPC同时在prompt中
- 自然发言顺序：基于性格的 Talkativeness 因子
- 玩家可以选择指名NPC
[v1.6] 新增纯 NPC 群聊场景（玩家不在场）：
- start_npc_only_scene() 生成 3+ NPC 在某地的群聊
- 用于茶馆议事、市井闲聊、密室商讨等场景
- 单次 LLM 调用生成完整多轮对话（参考 NPCDialogueManager 思路）
"""
from __future__ import annotations
import random
import logging
from typing import TYPE_CHECKING
from .prompt_utils import resolve_location_name, sanitize_player_input  # [Bug] location code → display name | [v1.4 P2-10] Prompt injection 防护

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .schemas import NPCState, PlayerState, WorldState

from .prompt.group_prompts import (
    GROUP_SCENE_PROMPT, GROUP_NPC_REPLY_PROMPT, GROUP_NARRATIVE_PROMPT,
)

logger = logging.getLogger("chronoverse.group_chat")


# [v1.6] 纯 NPC 群聊 prompt（玩家不在场）
NPC_ONLY_GROUP_PROMPT = """你是虚拟世界中多角色对话的编排器。请根据场景和参与者人设，生成一段自然的多人群聊。

【场景类型】{scene_type}
【发生地点】{location}（第{day}天 {time}，{weather}）

【参与者列表】
{participants_text}

【场景背景】
{scene_context}

【最近江湖大事】
{world_events}

【输出要求】
1. 生成 {max_turns} 轮以内的群聊（一人发言一次算一轮）
2. 严格保持每个角色的说话风格和性格
3. 群聊要自然流动，可包含插嘴、附和、争论
4. 信息密度高，避免空话
5. 可包含动作神态描写（用括号标注）

【输出 JSON 格式】
{{
    "scene_narrative": "30字以内的场景氛围描写",
    "dialogue": [
        {{
            "speaker": "角色名",
            "content": "台词",
            "emotion": "情绪/态度",
            "action": "可选，伴随动作"
        }}
    ],
    "summary": "20字以内的见闻摘要",
    "topic_tags": ["话题标签"]
}}
只输出 JSON。"""


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
                # [v1.4 P2-10] Prompt injection 防护
                player_input=sanitize_player_input(player_input) if player_input else "(场景开始)",
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
                # [v1.4 P2-10] Prompt injection 防护
                latest_message=sanitize_player_input(latest_message, max_len=300),
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

    # ── [v1.6] 纯 NPC 群聊（玩家不在场） ────────────────────────

    def start_npc_only_scene(
        self,
        npcs: list["NPCState"],
        world_state: "WorldState",
        location: str,
        scene_type: str = "市井闲聊",
        max_turns: int = 6,
    ) -> dict:
        """[v1.6] 生成纯 NPC 群聊场景（玩家不在场）。

        与 start_group_scene 的区别：
        - 不需要 player_input
        - 单次 LLM 调用生成完整多轮对话（避免逐轮调用）
        - 返回结构包含 summary 和 topic_tags，便于派发到「江湖见闻」流

        Args:
            npcs: 参与的 NPC 列表（建议 3-5 个）
            world_state: 世界状态
            location: 场景地点 code
            scene_type: 场景类型（市井闲聊/茶馆议事/密室商讨等）
            max_turns: 最大对话轮数

        Returns:
            {scene_narrative, dialogue, summary, topic_tags, participants}
            LLM 失败时返回回退结构
        """
        if not npcs or len(npcs) < 2:
            return {
                "scene_narrative": "",
                "dialogue": [],
                "summary": "",
                "topic_tags": [],
                "participants": [],
            }

        # 限制参与人数（避免 prompt 过长）
        participants_npcs = npcs[:5]

        # 构建参与者文本
        participants_text = "\n".join([
            f"- {n.name}：{n.personality[:40] if n.personality else '普通'}，"
            f"说话风格={n.speaking_style[:30] if n.speaking_style else '正常'}，"
            f"身份={n.role or '无名'}"
            for n in participants_npcs
        ])

        # 地点显示名
        loc_name = location
        if world_state and hasattr(world_state, "locations") and location in world_state.locations:
            loc_obj = world_state.locations[location]
            if isinstance(loc_obj, dict):
                loc_name = loc_obj.get("location_name") or loc_obj.get("name") or location
            elif hasattr(loc_obj, "location_name"):
                loc_name = loc_obj.location_name or location
            elif hasattr(loc_obj, "name"):
                loc_name = loc_obj.name or location

        # 场景背景
        scene_context = f"第{world_state.current_day}天 {world_state.current_time}，{world_state.weather}"
        world_events = (world_state.event_history_summary or "近期无特殊事件")[-300:]

        try:
            prompt = NPC_ONLY_GROUP_PROMPT.format(
                scene_type=scene_type,
                location=loc_name,
                day=world_state.current_day,
                time=world_state.current_time,
                weather=world_state.weather,
                participants_text=participants_text,
                scene_context=scene_context,
                world_events=world_events,
                max_turns=max_turns,
            )
            result = self.llm.chat_json(prompt, temperature=0.7, max_tokens=0)
            if not result:
                raise ValueError("LLM 返回空")

            return {
                "scene_narrative": result.get("scene_narrative", ""),
                "dialogue": result.get("dialogue", []),
                "summary": result.get("summary", ""),
                "topic_tags": result.get("topic_tags", []),
                "participants": [
                    {"npc_id": n.agent_id, "name": n.name} for n in participants_npcs
                ],
                "location": location,
                "location_name": loc_name,
                "scene_type": scene_type,
                "day": world_state.current_day,
            }
        except Exception as e:
            logger.warning("[GroupChat] NPC-only scene failed: %s", e)
            # 回退：返回空结构，调用方降级处理
            return {
                "scene_narrative": f"在{loc_name}，{len(participants_npcs)}人聚在一起{scene_type}。",
                "dialogue": [],
                "summary": f"{participants_npcs[0].name}等人在{loc_name}{scene_type}",
                "topic_tags": [],
                "participants": [
                    {"npc_id": n.agent_id, "name": n.name} for n in participants_npcs
                ],
                "location": location,
                "location_name": loc_name,
                "scene_type": scene_type,
                "day": world_state.current_day,
            }

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
