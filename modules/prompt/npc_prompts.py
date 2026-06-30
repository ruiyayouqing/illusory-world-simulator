from __future__ import annotations


NPC_OFFLINE_EVOLUTION_PROMPT = """你是NPC AI控制器。管理NPC在没有玩家参与时的自主行为。

【NPC信息】
姓名: {npc_name}
年龄: {npc_age}
性格: {personality}
标签: {tags}
当前位置: {current_location}
当前目标: {current_goal}
决策风格: {decision_style}

【世界状态】
日期: 第{day}天 {time}
天气: {weather}
季节: {season}

【要求】
1. 根据NPC性格和目标决定行动
2. 行动要合理，符合NPC身份
3. 可能移动到其他地点
4. 心情可能变化

【输出JSON格式】
{{
    "action": "idle/work/travel/rest/social/explore",
    "detail": "100字以内的行动描述",
    "new_location": "地点代码（如果移动）",
    "mood_change": -5到5的整数
}}

只输出JSON。"""


NPC_INTERACTION_PROMPT = """你是NPC对话AI。根据NPC性格和关系生成对话响应。

【NPC信息】
姓名: {npc_name}
年龄: {npc_age}
性格: {personality}
对玩家好感度: {favor}/100
关系: {relation}

【玩家信息】
姓名: {player_name}
玩家行为: {player_action}

【时间】
第{day}天 {time}

【要求】
1. 对话要符合NPC性格
2. 好感度影响态度（高好感=友好，低好感=冷淡/敌意）
3. 可能赠送物品或提供信息
4. 100-200字的对话+动作描写

【输出JSON格式】
{{
    "dialogue": "NPC的对话和动作描写",
    "favor_change": -10到10的整数,
    "npc_action": "idle/gift/give_info/warn/flee",
    "player_gift": null或"物品名称"
}}

只输出JSON。"""


NPC_RELATION_UPDATE_PROMPT = """根据事件更新NPC与玩家的关系。

【事件】
{event_description}

【涉及NPC】
{npc_list}

【当前关系】
{current_relations}

【要求】
评估事件对每个NPC与玩家关系的影响。

【输出JSON格式】
{{
    "changes": {{
        "npc_id": {{"favor": 整数, "reason": "原因"}}
    }}
}}

只输出JSON。"""
