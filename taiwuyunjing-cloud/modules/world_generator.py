from __future__ import annotations
import json
import uuid
import random
from .llm.base_llm import BaseLLM


WORLD_TYPE_TEMPLATES = {
    "historical": {
        "name": "历史穿越",
        "description": "穿越到真实历史朝代，体验古代生活",
        "example": "明朝、唐朝、宋朝、罗马帝国、维京时代",
        "style": "明清白话文/章回体",
        "economy_focus": "农业、商业、手工业",
    },
    "fantasy": {
        "name": "奇幻冒险",
        "description": "魔法、龙、精灵、矮人的奇幻世界",
        "example": "中世纪欧洲奇幻、东方修仙、异世界转生",
        "style": "史诗奇幻/翻译腔",
        "economy_focus": "魔法材料、神器、冒险公会",
    },
    "scifi": {
        "name": "科幻未来",
        "description": "太空探索、赛博朋克、AI社会",
        "example": "赛博朋克2077、太空歌剧、后人类时代",
        "style": "赛博朋克/硬科幻",
        "economy_focus": "芯片、义体、数据",
    },
    "postapocalyptic": {
        "name": "末日生存",
        "description": "文明崩塌后的生存挣扎",
        "example": "丧尸末日、核冬天、AI叛变后",
        "style": "废土文学/硬核生存",
        "economy_focus": "弹药、食物、净水、燃料",
    },
    "wuxia": {
        "name": "武侠江湖",
        "description": "刀光剑影、恩怨情仇的江湖世界",
        "example": "金庸式武侠、古龙式悬疑",
        "style": "古典武侠",
        "economy_focus": "镖局、酒楼、武林秘籍",
    },
    "xianxia": {
        "name": "修仙问道",
        "description": "炼气、筑基、金丹、元婴的修真世界",
        "example": "凡人修仙传、遮天",
        "style": "修真小说",
        "economy_focus": "灵石、丹药、法宝",
    },
    "modern": {
        "name": "都市生活",
        "description": "现代都市的日常与冒险",
        "example": "都市异能、校园、职场",
        "style": "现代都市小说",
        "economy_focus": "金钱、人脉、资源",
    },
    "custom": {
        "name": "自定义世界",
        "description": "由玩家自由描述的任意世界",
        "example": "任何你能想象的世界",
        "style": "由玩家定义",
        "economy_focus": "由玩家定义",
    },
}


class WorldGenerator:
    def __init__(self, llm: BaseLLM):
        self.llm = llm

    def get_world_types(self) -> list[dict]:
        return [{"id": k, **v} for k, v in WORLD_TYPE_TEMPLATES.items()]

    def generate_world(self, user_description: str, world_type: str = "custom") -> dict:
        template = WORLD_TYPE_TEMPLATES.get(world_type, WORLD_TYPE_TEMPLATES["custom"])

        prompt = f"""你是一个游戏世界设计大师。根据玩家描述，生成完整的游戏世界设定JSON。

【玩家描述】{user_description}
【世界类型】{template['name']}: {template['description']}

请生成以下JSON（只输出JSON，不要markdown）：

{{
    "world_name": "世界名称",
    "world_type": "{world_type}",
    "description": "200字世界背景故事",
    "power_system": {{
        "name": "力量体系名称",
        "levels": [{{"name": "等级1", "description": "描述"}}],
        "player_level": "玩家当前等级",
        "level_description": "玩家实力描述"
    }},
    "era_name": "纪年名称",
    "era_year": 9987,
    "factions": {{
        "势力名": {{
            "power": 50,
            "stability": 50,
            "description": "描述",
            "goals": "目标",
            "enemies": [],
            "allies": [],
            "faction_type": "宗门/帝国/魔教/商会/帮派/隐世/其他"
        }}
    }},
    "locations": {{
        "loc_code": {{
            "location_name": "地点名（必须中文，如'汴京'、'临安城'，禁止拼音/英文）",
            "description": "描述",
            "detail": "详细",
            "special_actions": [],
            "connected_to": [],
            "danger_level": 1,
            "controlling_faction": "控制该地点的势力（可选）"
        }}
    }},
    "npcs": {{
        "npc_id": {{
            "name": "符合世界文化背景的姓名",
            "title": "称号/职位",
            "age": 25,
            "gender": "男/女",
            "personality": "性格",
            "speaking_style": "说话风格",
            "background": "背景故事（100字左右）",
            "appearance": "外貌描述",
            "goals": "当前目标",
            "long_term_goal": "长期人生目标（一句话）",
            "short_term_goals": ["近期小目标1", "近期小目标2"],
            "power_level": "实力等级，对应力量体系中的等级名",
            "faction": "所属势力",
            "position_in_faction": "在势力中的职位，如 宗主/大长老/内门弟子/帮主",
            "importance": "world",
            "reputation_level": 10,
            "stats": {{"health": 100, "strength": 5, "agility": 5, "intelligence": 5, "magic": 0}},
            "tags": ["标签"],
            "initial_location": "初始地点",
            "alive": true,
            "secrets": "不为人知的秘密（对玩家隐藏）",
            "relation_to_player": {{"favor": 50, "relation_type": "素未谋面"}}
        }}
    }},
    "economy": {{"currency_name": "灵石", "base_prices": {{"丹药": 10}}, "supply_demand": {{"丹药": 1.0}}}},
    "initial_event": "根据玩家身份写一段150字以上的开场叙述",
    "world_intro": "300-500字世界观简介",
    "player_start": {{
        "name": "起一个符合世界文化背景的名字（禁止使用'无名氏'、'路人'等占位词）",
        "age": 20,
        "max_age": 800,
        "position": "身份地位",
        "background": "背景故事",
        "starting_location": "初始地点（必须使用中文地名，如'汴京'、'临安'，禁止使用拼音或英文）",
        "starting_items": [{{"name": "物品", "quantity": 1, "item_type": "misc"}}],
        "starting_gold": 100,
        "reputation": 0,
        "faction": "出身势力（如果有）",
        "power_level": "初始实力等级",
        "stats": {{"health": 300, "max_health": 300, "energy": 200, "max_energy": 200, "strength": 30, "agility": 25, "intelligence": 20, "magic": 60, "luck": 10}},
        "tags": ["标签"]
    }},
    "world_lore": ["传说1", "传说2", "传说3", "传说4", "传说5"],
    "map_distance": {{"地点A": {{"地点B": 50}}}}
}}

【关键要求 - NPC生成规则】
1. 需要生成约6-8个【影响世界走向的关键NPC】（importance="world"）
2. 按势力分配：每个主要势力至少要有：领袖1人、副手/核心人物1人
3. 主要势力数量：应该有3-5个主要势力（正道1个、魔道1个、朝堂/帝国1个、商会/散修1个）
4. 每个NPC必须有title称号和faction所属势力
5. reputation_level：名气值1-10，顶级大人物（宗主/皇帝）是10，长老级是7-9，核心弟子是5-6，重要人物是3-4
6. power_level必须对应power_system里定义的等级，顶级人物应该是世界顶尖战力
7. NPC之间要有关系网：谁和谁是仇敌、谁是谁的师父、谁暗恋谁，不要所有人都是独立的
8. 不要生成店小二、路人甲这种龙套，只生成能影响世界局势的大人物
9. 玩家一开始是小人物，所以大部分NPC和玩家初始关系都是"素未谋面"

【关键要求 - 命名规则 - 极其重要】
名字必须与世界类型和文化背景完全匹配：
1. 历史穿越/武侠/修仙：使用中文姓名（如"沈文"、"林清之"、"赵明诚"）
2. 奇幻冒险：使用中文音译的西方/奇幻风格名字（如"巴克"、"阿尔德里克"、"索菲亚"、"桑尼克"），绝对不能出现英文字母！
3. 科幻未来：使用中文音译的现代名字（如"亚历克斯"、"诺瓦"、"凯"），绝对不能出现英文字母！
4. 末日生存：使用中文音译的现代简短名字（如"铁锤"、"老猫"、"雷文"），绝对不能出现英文字母！
5. 都市异能：使用现代中文名（如"林清"、"周明"）
6. 自定义世界：根据世界描述中的文化背景来命名
绝对禁止在任何名字中使用英文字母！所有名字必须用中文汉字书写！
绝对禁止在中文世界中使用西方风格名字！

地点名称也必须与世界文化背景匹配：
- 中文世界用中文地名（如"汴京"、"临安城"）
- 西方世界用中文音译的西式地名（如"暴风城"、"暮色森林"）
- 禁止使用拼音（如"bianjing"）或英文字母！
- 所有地名必须用中文汉字书写！

记住：这是世界创建时的关键人物表，不是所有NPC。路人会在游戏过程中动态生成。"""

        import logging
        _logger = logging.getLogger("chronoverse")
        required = ["world_name", "description", "player_start", "npcs", "locations"]

        for attempt in range(2):
            temps = [0.7, 0.8]
            # 世界生成必须返回 world_name/player_start/npcs/locations 等世界字段。
            # 不能走通用 chat_json，否则部分模型会被追加的 narrative/options 模板带偏。
            response = self.llm.chat_structured(
                prompt,
                "world",
                temperature=temps[attempt],
                max_tokens=32768,
            )
            if "error" in response:
                _logger.warning("World gen attempt %d: LLM parse error: %s", attempt + 1, response.get("error"))
                continue
            missing = [k for k in required if k not in response or not response[k]]
            if not missing and response.get("world_name"):
                return response
            _logger.warning("World gen attempt %d: missing fields %s, keys=%s", attempt + 1, missing, list(response.keys()))

        if "error" not in response:
            return {"error": f"LLM返回不完整，缺少字段: {', '.join(missing)}"}
        return {"error": f"世界生成失败: {response.get('error', '未知错误')}"}

    def generate_world_lore(self, world_data: dict, topic: str = "") -> str:
        prompt = f"""为这个世界生成一段传说或历史故事。

【世界信息】
名称: {world_data.get('world_name', '未知')}
背景: {world_data.get('description', '')}
势力: {', '.join(world_data.get('factions', {}).keys())}

{"【指定主题】" + topic if topic else ""}

【要求】
1. 100-200字的传说/历史故事
2. 要有戏剧性和吸引力
3. 可能与游戏剧情相关

直接输出文本。"""
        return self.llm.chat(prompt, temperature=0.85)

    def generate_npc_dialogue(self, npc_data: dict, context: str,
                              player_action: str = "") -> dict:
        prompt = f"""你是NPC对话AI。根据NPC性格和当前情境生成对话。

【NPC信息】
姓名: {npc_data.get('name', '未知')}
年龄: {npc_data.get('age', 25)}
性格: {npc_data.get('personality', '普通')}
背景: {npc_data.get('background', '')}
目标: {npc_data.get('goals', '')}

【当前情境】
{context}

{"【玩家行为】" + player_action if player_action else ""}

【输出JSON格式】
{{
    "dialogue": "NPC的对话（包含动作描写）",
    "emotion": "NPC当前情绪",
    "hint": "NPC可能透露的信息/线索",
    "favor_change": 0
}}

只输出JSON。"""
        return self.llm.chat_json(prompt, temperature=0.7)

    def generate_faction_event(self, world_data: dict, faction_name: str) -> dict:
        factions = world_data.get("factions", {})
        faction = factions.get(faction_name, {})

        prompt = f"""为{faction_name}势力生成一个事件。

【势力信息】
名称: {faction_name}
实力: {faction.get('power', 50)}
稳定性: {faction.get('stability', 50)}
目标: {faction.get('goals', '')}
敌人: {', '.join(faction.get('enemies', []))}

【世界势力】
{json.dumps({k: v.get('power', 50) for k, v in factions.items()}, ensure_ascii=False)}

【输出JSON格式】
{{
    "event_type": "政治/军事/经济/宗教",
    "description": "事件描述",
    "affected_factions": ["受影响的势力"],
    "impact_level": 1到10,
    "consequences": ["可能的后果"]
}}

只输出JSON。"""
        return self.llm.chat_json(prompt, temperature=0.8)
