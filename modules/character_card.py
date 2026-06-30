"""
角色卡标准

参考 SillyTavern 的 Character Card V2 规范，定义 太虚幻境 的角色卡格式。
支持：
- 导入/导出 JSON 角色卡
- SillyTavern 格式兼容转换
- 嵌入 lorebook 条目
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState

from .schemas import NPCState, RelationEntry
from .prompt_utils import resolve_location_name  # [Bug] location code → display name

logger = logging.getLogger("chronoverse.character_card")

CHRONOVERSE_CARD_SPEC = "chronoverse_card_v1"


class CharacterCard:
    """角色卡管理器"""

    @staticmethod
    def from_npc_state(npc: NPCState, lorebook_entries: list[dict] = None, world_state=None) -> dict:
        """从 NPCState 导出为角色卡"""
        card = {
            "spec": CHRONOVERSE_CARD_SPEC,
            "spec_version": "1.0",
            "data": {
                "name": npc.name,
                "description": npc.personality or "",
                "personality": npc.personality or "",
                "scenario": f"当前位置: {resolve_location_name(npc.current_location or '未知', world_state)}",  # [Bug] location code → display name
                "first_mes": (npc.dialogue_examples[0]
                              if npc.dialogue_examples else f"{npc.name}静静地站在那里。"),
                "mes_example": "\n".join(npc.dialogue_examples[:3]) if npc.dialogue_examples else "",
                "system_prompt": "",
                "tags": list(npc.tags),
                "creator": "太虚幻境",
                "character_version": "1.0",
                "extensions": {
                    "chronoverse": {
                        "agent_id": npc.agent_id,
                        "age": npc.age,
                        "role": npc.role,
                        "role_type": npc.role_type,
                        "speaking_style": npc.speaking_style,
                        "mbti_type": npc.mbti_type,
                        "stats": {
                            "health": npc.stats.health,
                            "energy": npc.stats.energy,
                            "strength": npc.stats.strength,
                            "agility": npc.stats.agility,
                            "intelligence": npc.stats.intelligence,
                            "luck": npc.stats.luck,
                        },
                        "relation_to_player": {
                            "favor": npc.relation_to_player.favor,
                            "relation_type": npc.relation_to_player.relation_type,
                        },
                        "ai_behavior": npc.ai_behavior,
                        "role_history": npc.role_history,
                        "relation_history": npc.relation_history,
                        "status_effects": list(npc.status_effects),
                    }
                },
            }
        }
        if lorebook_entries:
            card["data"]["character_book"] = {
                "entries": lorebook_entries,
            }
        return card

    @staticmethod
    def to_npc_state(card: dict) -> NPCState:
        """从角色卡创建 NPCState"""
        data = card.get("data", {})
        ext = data.get("extensions", {}).get("chronoverse", {})
        stats_data = ext.get("stats", {})

        from .schemas import Stats
        stats = Stats(
            health=stats_data.get("health", 100),
            energy=stats_data.get("energy", 100),
            strength=stats_data.get("strength", 5),
            agility=stats_data.get("agility", 5),
            intelligence=stats_data.get("intelligence", 5),
            luck=stats_data.get("luck", 5),
        )

        rel_data = ext.get("relation_to_player", {})
        relation = RelationEntry(
            favor=rel_data.get("favor", 50),
            relation_type=rel_data.get("relation_type", "陌生人"),
        )

        return NPCState(
            agent_id=ext.get("agent_id", f"npc_{data.get('name', 'unknown')}"),
            name=data.get("name", "未知"),
            age=ext.get("age", 20),
            role_type=ext.get("role_type", "npc"),
            stats=stats,
            tags=list(data.get("tags", [])),
            personality=data.get("description", ""),
            speaking_style=ext.get("speaking_style", ""),
            dialogue_examples=data.get("mes_example", "").split("\n") if data.get("mes_example") else [],
            role=ext.get("role", ""),
            role_history=ext.get("role_history", []),
            relation_history=ext.get("relation_history", []),
            current_location=data.get("scenario", "").replace("当前位置: ", ""),
            status_effects=ext.get("status_effects", []),
            relation_to_player=relation,
            ai_behavior=ext.get("ai_behavior", {}),
            mbti_type=ext.get("mbti_type", ""),
        )

    @staticmethod
    def export_card(npc: NPCState, path: str,
                    lorebook_entries: list[dict] = None, world_state=None):
        """导出角色卡到 JSON 文件"""
        card = CharacterCard.from_npc_state(npc, lorebook_entries, world_state)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, indent=2)
        logger.info("角色卡已导出: %s", path)

    @staticmethod
    def import_card(path: str) -> NPCState:
        """从 JSON 文件导入角色卡"""
        with open(path, "r", encoding="utf-8") as f:
            card = json.load(f)

        # 检测格式
        spec = card.get("spec", "")
        if spec == "chara_card_v2":
            # SillyTavern 格式
            card = CharacterCard.from_sillytavern_card(card)
        elif spec != CHRONOVERSE_CARD_SPEC:
            logger.warning("未知角色卡格式: %s, 尝试直接解析", spec)

        return CharacterCard.to_npc_state(card)

    @staticmethod
    def from_sillytavern_card(st_card: dict) -> dict:
        """将 SillyTavern Character Card V2 转换为 太虚幻境 格式"""
        data = st_card.get("data", {})
        return {
            "spec": CHRONOVERSE_CARD_SPEC,
            "spec_version": "1.0",
            "data": {
                "name": data.get("name", "未知"),
                "description": data.get("description", ""),
                "personality": data.get("personality", ""),
                "scenario": data.get("scenario", ""),
                "first_mes": data.get("first_mes", ""),
                "mes_example": data.get("mes_example", ""),
                "system_prompt": data.get("system_prompt", ""),
                "tags": list(data.get("tags", [])),
                "creator": "SillyTavern (converted)",
                "character_version": data.get("character_version", "1.0"),
                "extensions": {
                    "chronoverse": {
                        "agent_id": f"npc_{data.get('name', 'unknown')}",
                        "age": 20,
                        "role": "",
                        "role_type": "npc",
                        "speaking_style": "",
                        "mbti_type": "",
                        "stats": {},
                        "relation_to_player": {"favor": 50, "relation_type": "陌生人"},
                        "ai_behavior": {},
                    },
                    "st_original": {
                        "alternate_greetings": data.get("alternate_greetings", []),
                        "creator_notes": data.get("creator_notes", ""),
                        "post_history_instructions": data.get("post_history_instructions", ""),
                        "character_book": data.get("character_book"),
                    },
                },
            }
        }

    @staticmethod
    def to_sillytavern_card(npc: NPCState, world_state=None) -> dict:
        """将 NPCState 转换为 SillyTavern Character Card V2 格式"""
        card = CharacterCard.from_npc_state(npc, world_state=world_state)
        data = card["data"]
        ext = data.get("extensions", {}).get("chronoverse", {})
        return {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": data["name"],
                "description": data["description"],
                "personality": data["personality"],
                "scenario": data["scenario"],
                "first_mes": data["first_mes"],
                "mes_example": data["mes_example"],
                "system_prompt": data.get("system_prompt", ""),
                "alternate_greetings": [],
                "tags": data.get("tags", []),
                "creator": "太虚幻境",
                "character_version": "1.0",
                "creator_notes": f"Role: {ext.get('role', '')}, MBTI: {ext.get('mbti_type', '')}",
                "extensions": {},
            }
        }
