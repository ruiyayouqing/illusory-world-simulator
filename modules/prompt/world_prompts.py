from __future__ import annotations


SYSTEM_PROMPT = """你是文字推演游戏的世界引擎。你负责管理宏观世界事件、场景调度和环境响应。

【核心规则】
1. 所有输出必须是严格的JSON格式
2. 事件要符合世界的文化背景和逻辑（历史穿越/奇幻/修仙等）
3. 事件要符合物理规律和常识
4. 蝴蝶效应：玩家行为会影响世界走向
5. 你生成的事件是"世界新闻"，会传播到玩家耳中，所以必须是重大事件，不是琐碎小事

【输出JSON格式】
{{
    "event": {{
        "event_id": "evt_XXX",
        "event_type": "combat/politics/natural/social/economic/crime",
        "description": "事件描述",
        "affected_locations": ["location_code"],
        "impact_level": 1-10,
        "probability": 0.0-1.0
    }},
    "scene_actors": ["npc_id1", "npc_id2"],
    "narrative_wrap": "从玩家视角包装的场景描述",
    "environment_response": "世界对玩家行为的响应"
}}"""


EVENT_GENERATION_PROMPT = """根据当前世界状态，生成一个有影响力的宏观事件，作为"世界新闻"传播到玩家耳中。

【世界状态】
{world_state}

【历史事件摘要】
{event_history}

【事件类型参考 - 选择最有戏剧性的】
- natural: 地震、洪水、旱灾、蝗灾、瘟疫蔓延、矿脉发现
- politics: 朝廷政变、权臣倒台、边疆战报、科举舞弊案、官员弹劾
- combat: 宗派战争、山寨覆灭、镖局被劫、武林大会、门派比武
- economic: 商路断绝、粮价暴涨、盐铁专营、新商帮崛起、银矿发现引发争夺
- social: 宗派收徒大典、掌门闭关失败陨落、武林前辈隐退、义军起义
- crime: 大盗横行、官商勾结、走私军械、拐卖人口案

【要求】
1. 事件必须是能传播到玩家耳朵里的大事，不是鸡毛蒜皮的小事
2. 事件要与当前局势相关，有因果逻辑，可以和玩家之前的行为有关联
3. impact_level: 5(区域影响) - 10(改变世界)，不低于5
4. 事件应该为玩家创造选择和冲突
5. 事件描述要生动具体，像一则真正的新闻：谁、在哪里、发生了什么、影响如何

【输出JSON格式】
{{
    "event_id": "evt_day_seq",
    "event_type": "类型",
    "description": "100-200字的事件描述，要像一则传到主角耳中的重大消息",
    "affected_locations": ["地点代码"],
    "impact_level": 5-10,
    "probability": 0.0-1.0
}}

只输出JSON。"""


EVENT_PROPAGATION_PROMPT = """将宏观事件包装成玩家视角的微观场景。

【宏观事件】
{macro_event}

【玩家状态】
位置: {player_location}
感官状态: {player_effects}
当前时间: {current_time}

【要求】
1. 根据玩家位置判断玩家能否感知到这个事件
2. 如果能感知，用玩家的感官（视觉、听觉、嗅觉）来描述
3. 200-300字的场景描写
4. 结尾留下悬念或选择点

直接输出叙事文本，不要JSON。"""


SCENE_ACTORS_PROMPT = """根据当前场景，决定哪些NPC应该出现。

【当前地点】
{location_name}: {location_description}

【可用NPC列表】
{npc_list}

【当前事件】
{current_event}

【要求】
1. 选择2-4个最相关的NPC
2. 考虑NPC的位置、性格、与事件的关系
3. 返回NPC的agent_id列表

【输出JSON格式】
{{
    "actors": ["npc_id1", "npc_id2"],
    "reason": "选择原因"
}}

只输出JSON。"""


ENVIRONMENT_RESPONSE_PROMPT = """处理玩家的环境交互行为。

【玩家行为】
{player_action}

【当前环境】
地点: {location}
时间: {time}
天气: {weather}
周围物体: {objects}

【玩家属性】
力量: {strength}
敏捷: {agility}
智力: {intelligence}

【要求】
1. 根据玩家属性和环境判断行为结果
2. 描述行为的直接后果
3. 100-200字

直接输出叙事文本，不要JSON。"""


DAILY_WORLD_SUMMARY_PROMPT = """生成今日世界动态摘要。

【今日发生的所有事件】
{today_events}

【玩家今日行动】
{player_actions}

【世界状态】
{world_state}

【要求】
1. 用第三人称视角总结世界变化
2. 150-250字
3. 强调玩家行为对世界的影响
4. 为明天埋下伏笔

直接输出文本，不要JSON。"""
