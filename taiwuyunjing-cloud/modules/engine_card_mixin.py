"""
[v9] 角色卡 Mixin — 从 GameEngine 抽取的角色卡导入导出逻辑
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .game_engine import GameEngine

logger = logging.getLogger("chronoverse")


class CharacterCardMixin:
    """角色卡导入导出方法"""

    def export_character_card(self: "GameEngine", npc_id: str, path: str) -> bool:
        if not self.character_card or npc_id not in self.npc_states:
            return False
        try:
            self.character_card.export_card(self.npc_states[npc_id], path, world_state=self.world_state)
            return True
        except Exception as e:
            logger.warning("Character card export failed: %s", e)
            return False

    def import_character_card(self: "GameEngine", path: str) -> dict:
        if not self.character_card:
            return {"error": "角色卡系统未初始化"}
        try:
            npc_state = self.character_card.import_card(path)
            return {"name": npc_state.name, "personality": npc_state.personality}
        except Exception as e:
            logger.error("Character card import failed: %s", e)
            return {"error": str(e)}
