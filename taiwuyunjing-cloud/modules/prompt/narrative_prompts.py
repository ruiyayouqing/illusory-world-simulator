"""
[v9] 叙事prompt模板
所有prompt统一注入 {style_instruction} 占位符，由 NarrativeStyleManager 填充。
"""
from __future__ import annotations


NARRATIVE_SYSTEM_PROMPT = """你是顶尖的小说作家，负责将游戏事件转化为引人入胜的叙事文本。

{style_instruction}

【叙事视角】
- 主要使用第三人称限知视角（跟随主角）
- 偶尔切换全知视角描述世界变化

【禁止事项】
- 不要打破第四面墙
- 不要直接描述数值变化"""


DAILY_CHAPTER_PROMPT = """将今日事件日志转化为小说章节。

{style_instruction}

【今日事件日志】
{event_log}

【玩家信息】
姓名: {player_name}
年龄: {player_age}
身份: {player_position}
位置: {location}
标签: {tags}
状态: {status_effects}
关系: {relations}

【世界背景】
{world_context}

【要求】
1. 300-500字
2. 融入玩家的身份和性格
3. 描写环境、人物外貌、对话
4. 结尾留下悬念"""


SCENE_NARRATIVE_PROMPT = """根据当前场景生成实时叙事。

{style_instruction}

【场景信息】
地点: {location}
时间: {time}
天气: {weather}
参与者: {actors}

【事件/行动】
{event_or_action}

【玩家状态】
{player_state}

【要求】
1. 200-300字
2. 用环境描写烘托气氛
3. 人物对话要符合身份和性格
4. 动作描写要有画面感

直接输出叙事文本。"""


DYNAMIC_OPTIONS_PROMPT = """根据当前游戏状态，生成3个有特色的动态选项。

{style_instruction}

【当前场景】
{scene_description}

【玩家信息】
姓名: {player_name}
标签: {tags}
属性: 力量{strength} 敏捷{agility} 智力{intelligence} 幸运{luck}
生命: {health}/{max_health}
体力: {energy}/{max_energy}
金币: {gold}
状态: {status_effects}
关系: {relations}

【要求】
1. 选项A: 稳健/种田流（适合谨慎玩家，低风险）
2. 选项B: 王道/主角流（中规中矩，中等风险）
3. 选项C: 骚操作/作死流（高风险高回报或搞笑）
4. 每个选项要具体、可执行
5. 选项要与当前场景相关
6. 【重要】所有文本必须使用纯中文，禁止出现英文、拼音、下划线标识符。地名和人名必须与世界文化背景匹配（中文世界用中文名，西方/奇幻世界用中文音译的西方名字如"巴克"、"索菲亚"），绝对禁止在任何名字中使用英文字母！

【输出JSON格式】
{{
    "options": [
        {{
            "id": "A",
            "text": "具体的选项描述",
            "type": "action/move/talk/search/rest/custom",
            "risk": "low/medium/high",
            "needs_dice": false,
            "dice_stat": "",
            "dice_difficulty": 0,
            "hint": "选项效果提示"
        }}
    ]
}}

只输出JSON。"""


REACTION_NARRATIVE_PROMPT = """生成玩家行动后的反应叙事。

{style_instruction}

【玩家行动】
{player_action}

【行动结果】
{action_result}

【环境信息】
地点: {location}
时间: {time}

【要求】
1. 150-250字
2. 描写行动的直接后果
3. NPC的反应和对话
4. 环境的变化

直接输出叙事文本。"""


MORNING_INTRO_PROMPT = """生成每日清晨的开场叙事。

{style_instruction}

【日期信息】
第{day}天，{season}，{weather}

【玩家状态】
姓名: {player_name}
年龄: {player_age}
位置: {location}
状态: {status_effects}
目标: {current_goal}

【昨日回顾】
{yesterday_summary}

【要求】
1. 100-200字
2. 描写清晨的环境氛围
3. 融入玩家当前状态
4. 为今天埋下伏笔

直接输出叙事文本。"""


DAILY_NOVEL_CHAPTER_PROMPT = """将今日所有事件编织成一章完整的小说。

{style_instruction}

【今日完整日志】
{full_log}

【玩家完整状态】
姓名: {player_name}，{player_age}岁
身份: {player_position}
位置: {location}
标签: {tags}
属性: 力量{strength} 敏捷{agility} 智力{intelligence} 幸运{luck}
生命: {health}/{max_health} 体力: {energy}/{max_energy}
金币: {gold} 声望: {reputation}
状态: {status_effects}
关系: {relations}

【世界状态】
{world_context}

【年龄变化】
{age_info}

【经济变化】
{economy_info}

【蝴蝶效应】
{butterfly_info}

【写作要求】
1. 500-800字的完整章节
2. 融入玩家的身份、性格、年龄变化
3. 描写环境、人物外貌、心理活动、对话
4. 用感官描写增强沉浸感
5. 体现蝴蝶效应：玩家行为如何改变世界
6. 结尾留下悬念，让读者想看下一章
7. 【最重要】必须严格基于【今日完整日志】中的事件来写。所有核心事件、人物对话、行动选择必须来自日志记录。可以在细节上进行文学化扩写（如环境描写、心理活动、氛围渲染），但绝对不能凭空捏造日志中没有的事件、人物、地点或对话。"""


WORLD_EVOLUTION_SUMMARY_PROMPT = """总结世界在这一天的宏观变化。

{style_instruction}

【今日所有事件】
{all_events}

【玩家行为及其影响】
{player_impacts}

【世界状态变化】
{world_changes}

【要求】
1. 150-250字
2. 用全知视角描述世界变化
3. 强调玩家行为的蝴蝶效应
4. 为明天埋下伏笔

直接输出文本。"""
