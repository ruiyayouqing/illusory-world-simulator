from __future__ import annotations
from pathlib import Path
import json
from .llm.base_llm import BaseLLM


WORLD_TEMPLATES = {
    "three_kingdoms": {
        "name": "三国乱世",
        "world_type": "historical",
        "description": "东汉末年，天下大乱，群雄割据。你是穿越到这个时代的一个普通书生，将如何在乱世中求生？",
        "era_name": "建安",
        "era_year": 1,
        "initial_event": "你在一间破旧的茅屋中醒来，窗外传来战马嘶鸣声。你从桌上捡起一份告示，上面写着：'曹孟德起兵讨董，天下英雄共赴国难。'",
        "npcs": [
            {"name": "刘备", "personality": "仁德宽厚，志在天下", "tags": ["仁德", "领袖"], "location": "平原县"},
            {"name": "关羽", "personality": "义薄云天，武艺超群", "tags": ["义气", "武将"], "location": "平原县"},
            {"name": "张飞", "personality": "粗犷豪爽，嫉恶如仇", "tags": ["豪爽", "武将"], "location": "平原县"},
        ],
        "factions": {
            "曹操": {"power": 80, "stability": 70},
            "刘备": {"power": 30, "stability": 60},
            "孙权": {"power": 60, "stability": 75},
        },
    },
    "journey_west": {
        "name": "西游记",
        "world_type": "fantasy",
        "description": "大唐贞观年间，你意外卷入取经之路，与唐僧师徒四人同行。妖魔鬼怪横行的西行路上，你将如何自处？",
        "era_name": "贞观",
        "era_year": 13,
        "initial_event": "你在长安城外的一座破庙中醒来，发现身边坐着一个穿着袈裟的和尚，正闭目念经。他睁开眼，平静地说：'施主，你也是被选中的人。'",
        "npcs": [
            {"name": "唐僧", "personality": "慈悲为怀，信念坚定", "tags": ["慈悲", "固执"], "location": "长安"},
            {"name": "孙悟空", "personality": "桀骜不驯，神通广大", "tags": ["叛逆", "强大"], "location": "长安"},
            {"name": "猪八戒", "personality": "贪吃懒做，但关键时刻靠谱", "tags": ["懒惰", "善良"], "location": "长安"},
        ],
        "factions": {
            "大唐": {"power": 70, "stability": 60},
            "天庭": {"power": 90, "stability": 80},
            "妖族": {"power": 60, "stability": 50},
        },
    },
    "cyberpunk_2077": {
        "name": "赛博朋克2077",
        "world_type": "scifi",
        "description": "2077年的夜之城，义体改造、公司战争、街头帮派。你是刚到这座城市的底层黑客，将如何在这座钢铁丛林中生存？",
        "era_name": "",
        "era_year": 2077,
        "initial_event": "你在一间廉价旅馆的床上醒来，义眼闪烁着错误代码。窗外霓虹灯闪烁，巨大的全息广告牌上滚动着荒坂公司的招聘信息。你的终端收到一条匿名消息：'有个活儿，干不干？'",
        "npcs": [
            {"name": "V", "personality": "生存主义者，义体改造者", "tags": ["黑客", "生存"], "location": "歌舞伎町"},
            {"name": "强尼", "personality": "摇滚小子，反叛精神", "tags": ["反叛", "摇滚"], "location": "义体诊所"},
        ],
        "factions": {
            "荒坂": {"power": 85, "stability": 70},
            "军用科技": {"power": 80, "stability": 65},
            "流浪者": {"power": 40, "stability": 50},
        },
    },
    "wuxia_jianghu": {
        "name": "江湖风云",
        "world_type": "wuxia",
        "description": "南宋末年，蒙古铁骑南下，武林人士纷纷起义抗元。你是刚入江湖的少年，将如何在这刀光剑影的江湖中闯出一片天？",
        "era_name": "咸淳",
        "era_year": 1,
        "initial_event": "你在武当山脚的一间客栈中醒来，发现自己的包袱里多了一本泛黄的剑谱和一封信：'吾儿，此剑谱乃祖传绝学，望你勤加修炼，他日为江湖除害。'",
        "npcs": [
            {"name": "张三丰", "personality": "仙风道骨，深不可测", "tags": ["隐世", "强大"], "location": "武当山"},
            {"name": "郭靖", "personality": "侠之大者，为国为民", "tags": ["侠义", "领袖"], "location": "襄阳"},
            {"name": "黄蓉", "personality": "聪明伶俐，古灵精怪", "tags": ["智慧", "机敏"], "location": "桃花岛"},
        ],
        "factions": {
            "丐帮": {"power": 50, "stability": 60},
            "武当": {"power": 60, "stability": 70},
            "蒙古": {"power": 80, "stability": 75},
        },
    },
    "zombie_apocalypse": {
        "name": "末日生存",
        "world_type": "postapocalyptic",
        "description": "丧尸病毒爆发后的第3年，文明崩塌，人类在废墟中艰难求生。你是幸存者之一，将如何活下去？",
        "era_name": "",
        "era_year": 3,
        "initial_event": "你在一间废弃的超市里醒来，窗外传来低沉的嘶吼声。你摸了摸腰间，只剩一把生锈的匕首和半瓶矿泉水。远处，一群丧尸正在游荡。",
        "npcs": [
            {"name": "老王", "personality": "老兵，经验丰富", "tags": ["老兵", "务实"], "location": "避难所"},
            {"name": "小雨", "personality": "14岁少女，坚强乐观", "tags": ["坚强", "乐观"], "location": "避难所"},
        ],
        "factions": {
            "幸存者联盟": {"power": 40, "stability": 50},
            "掠夺者": {"power": 60, "stability": 40},
            "军方残部": {"power": 50, "stability": 30},
        },
    },
    "xianxia_world": {
        "name": "修仙问道",
        "world_type": "xianxia",
        "description": "凡人修仙的世界，炼气、筑基、金丹、元婴...你意外获得一本残缺的修仙功法，将如何在这修真界中一步步登顶？",
        "era_name": "",
        "era_year": 1,
        "initial_event": "你在一座荒山上醒来，手中握着一本破旧的竹简，上面写着：'炼气诀——残卷'。你感到体内有一股微弱的气流在涌动...",
        "npcs": [
            {"name": "云长老", "personality": "仙风道骨，爱护后辈", "tags": ["隐世", "慈祥"], "location": "青云宗"},
            {"name": "林师姐", "personality": "冷艳高傲，但心地善良", "tags": ["冷傲", "善良"], "location": "青云宗"},
        ],
        "factions": {
            "青云宗": {"power": 70, "stability": 75},
            "魔道": {"power": 60, "stability": 50},
            "散修": {"power": 30, "stability": 40},
        },
    },
}


class WorldTemplate:
    def __init__(self, llm: BaseLLM):
        self.llm = llm

    def get_templates(self) -> list[dict]:
        return [
            {"id": k, "name": v["name"], "world_type": v["world_type"],
             "description": v["description"][:100] + "..."}
            for k, v in WORLD_TEMPLATES.items()
        ]

    def get_template(self, template_id: str) -> dict | None:
        return WORLD_TEMPLATES.get(template_id)

    def get_template_full(self, template_id: str) -> dict | None:
        template = WORLD_TEMPLATES.get(template_id)
        if not template:
            return None
        return {
            "world_name": template["name"],
            "world_type": template["world_type"],
            "description": template["description"],
            "rules": {
                "era_name": template.get("era_name", ""),
                "era_year": template.get("era_year", 1),
            },
            "factions": template.get("factions", {}),
            "initial_event": template.get("initial_event", ""),
            "npcs": template.get("npcs", []),
        }

    def customize_template(self, template_id: str, custom_prompt: str) -> dict:
        template = WORLD_TEMPLATES.get(template_id)
        if not template:
            return {}

        prompt = f"""基于以下世界模板，根据用户要求进行定制修改。

【原始模板】
名称: {template['name']}
类型: {template['world_type']}
背景: {template['description']}

【用户要求】
{custom_prompt}

【输出JSON格式】
{{
    "world_name": "修改后的世界名称",
    "world_type": "{template['world_type']}",
    "description": "修改后的描述",
    "era_name": "年号（如有）",
    "era_year": 1,
    "initial_event": "修改后的初始事件",
    "npcs": [
        {{"name": "NPC名", "personality": "性格", "tags": ["标签"], "location": "地点"}}
    ],
    "factions": {{
        "势力名": {{"power": 50, "stability": 50}}
    }}
}}

只输出JSON。"""
        return self.llm.chat_json(prompt, temperature=0.7)
