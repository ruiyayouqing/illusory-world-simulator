"""
小说导入器

参考 BookWorld 的小说导入功能，支持从小说文本中自动提取：
- 角色信息（姓名/性格/说话风格/身份）
- 世界观设定（世界类型/时代/力量体系）
- 地理信息（地点/描述/类型）
- 角色关系（关系类型/好感度）
"""
from __future__ import annotations
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM

from .prompt.importer_prompts import (
    EXTRACT_CHARACTERS_PROMPT, EXTRACT_WORLD_PROMPT,
    EXTRACT_LOCATIONS_PROMPT, EXTRACT_RELATIONS_PROMPT,
)

logger = logging.getLogger("chronoverse.novel_importer")


class NovelImporter:
    """小说导入器：从文本自动提取世界和角色信息"""

    def __init__(self, llm: BaseLLM):
        self.llm = llm

    def import_from_text(self, text: str, world_type: str = "auto") -> dict:
        """
        从小说文本自动提取角色/世界观/地理/关系。
        
        Args:
            text: 小说文本（建议 2000-10000 字）
            world_type: 世界类型，"auto" 时自动推断
            
        Returns:
            完整的世界创建数据，可直接传给 GameEngine.generate_world_from_description
        """
        # 截取前 8000 字以控制 token 消耗
        excerpt = text[:8000]

        # Step 1: 提取角色
        characters = self._extract_characters(excerpt)
        logger.info("提取到 %d 个角色", len(characters))

        # Step 2: 提取世界观
        world_info = self._extract_world(excerpt, world_type)
        logger.info("世界类型: %s, 名称: %s", world_info.get("world_type"), world_info.get("world_name"))

        # Step 3: 提取地点
        locations = self._extract_locations(excerpt)
        logger.info("提取到 %d 个地点", len(locations))

        # Step 4: 提取关系
        relations = self._extract_relations(excerpt, characters)
        logger.info("提取到 %d 条关系", len(relations))

        # Step 5: 构建完整的世界数据
        world_data = self._build_world_data(
            world_info, characters, locations, relations, text
        )
        return world_data

    def _extract_characters(self, text: str) -> list[dict]:
        """提取角色信息"""
        try:
            prompt = EXTRACT_CHARACTERS_PROMPT.format(novel_text=text)
            result = self.llm.chat_json(prompt, temperature=0.3, max_tokens=0)
            return result.get("characters", [])
        except Exception as e:
            logger.warning("角色提取失败: %s", e)
            return []

    def _extract_world(self, text: str, world_type: str = "auto") -> dict:
        """提取世界观"""
        try:
            prompt = EXTRACT_WORLD_PROMPT.format(novel_text=text)
            result = self.llm.chat_json(prompt, temperature=0.3, max_tokens=0)
            if world_type != "auto":
                result["world_type"] = world_type
            return result
        except Exception as e:
            logger.warning("世界观提取失败: %s", e)
            return {"world_type": world_type if world_type != "auto" else "custom",
                    "world_name": "未知世界", "description": text[:200]}

    def _extract_locations(self, text: str) -> list[dict]:
        """提取地点信息"""
        try:
            prompt = EXTRACT_LOCATIONS_PROMPT.format(novel_text=text)
            result = self.llm.chat_json(prompt, temperature=0.3, max_tokens=0)
            return result.get("locations", [])
        except Exception as e:
            logger.warning("地点提取失败: %s", e)
            return []

    def _extract_relations(self, text: str, characters: list[dict]) -> list[dict]:
        """提取角色关系"""
        if len(characters) < 2:
            return []
        characters_text = "\n".join([
            f"- {c['name']}: {c.get('role', '未知')}, {c.get('personality', '')[:30]}"
            for c in characters
        ])
        try:
            prompt = EXTRACT_RELATIONS_PROMPT.format(
                characters_text=characters_text, novel_text=text
            )
            result = self.llm.chat_json(prompt, temperature=0.3, max_tokens=0)
            return result.get("relations", [])
        except Exception as e:
            logger.warning("关系提取失败: %s", e)
            return []

    def _build_world_data(self, world_info: dict, characters: list[dict],
                          locations: list[dict], relations: list[dict],
                          original_text: str) -> dict:
        """构建完整的世界创建数据"""
        # 构建NPC数据
        npcs = {}
        for i, char in enumerate(characters):
            npc_id = f"npc_{char['name']}"
            # 找到该角色的关系
            char_relations = [r for r in relations
                              if r.get("from") == char["name"] or r.get("to") == char["name"]]
            best_relation = char_relations[0] if char_relations else {}
            relation_type = best_relation.get("relation_type", "陌生人")
            favor = best_relation.get("favor", 50)

            npcs[npc_id] = {
                "name": char["name"],
                "age": char.get("age", 25),
                "personality": char.get("personality", ""),
                "speaking_style": char.get("speaking_style", ""),
                "tags": char.get("tags", []),
                "initial_location": (locations[0]["code"] if locations else "village"),
                "relation_to_player": {
                    "favor": favor,
                    "relation_type": relation_type,
                },
                "goals": char.get("background", ""),
            }

        # 构建地点数据
        loc_dict = {}
        for loc in locations:
            loc_dict[loc.get("code", loc["name"])] = {
                "name": loc["name"],
                "description": loc.get("description", ""),
                "type": loc.get("location_type", "other"),
                "special_actions": loc.get("special_actions", []),
            }

        # 构建玩家起始数据
        first_char = characters[0] if characters else {}
        player_start = {
            "name": "穿越者",
            "age": 18,
            "background": f"穿越到了{world_info.get('world_name', '未知世界')}",
            "starting_location": locations[0]["code"] if locations else "village",
            "stats": {"health": 100, "energy": 100, "strength": 5,
                      "agility": 5, "intelligence": 8, "luck": 5},
            "tags": ["穿越者"],
            "starting_gold": 100,
        }

        return {
            "world_name": world_info.get("world_name", "未知世界"),
            "world_type": world_info.get("world_type", "custom"),
            "description": world_info.get("description", original_text[:300]),
            "era_name": world_info.get("era_name", ""),
            "era_year": world_info.get("era_year", 1),
            "initial_event": f"你穿越到了{world_info.get('world_name', '这个世界')}。{world_info.get('description', '')[:200]}",
            "npcs": npcs,
            "locations": loc_dict,
            "player_start": player_start,
            "power_system": world_info.get("power_system", ""),
            "social_structure": world_info.get("social_structure", ""),
            "core_conflict": world_info.get("core_conflict", ""),
        }

    def import_from_file(self, file_path: str, world_type: str = "auto") -> dict:
        """从文件导入小说"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
            return self.import_from_text(text, world_type)
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="gbk") as f:
                text = f.read()
            return self.import_from_text(text, world_type)
