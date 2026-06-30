"""
分支思维规划器提示词

参考 AIvilization 的 Branch-Thinking Planner (BTP)，
将目标分解为并行分支，选择最优先分支，生成行动序列。
"""

# ── 目标分解：将总体目标分解为 4 个并行分支 ──────────────
DECOMPOSE_GOAL_PROMPT = """你是目标分解器。根据以下NPC的总体目标和当前状态，将其分解为4个并行的行动分支。

【NPC信息】
姓名: {npc_name}
年龄: {npc_age}
性格: {personality}
当前目标: {current_goal}
长期目标: {long_term_goal}
当前位置: {location}

【当前状态】
生命: {health}/{max_health} 体力: {energy}/{max_energy}
金币: {gold} 声望: {reputation}
标签: {tags}
关系: {relations}

【世界状态】
日期: 第{day}天 {time}
季节: {season} 天气: {weather}
危机等级: {crisis_level}/10

【分支规则】
1. survival（生存）：维持生命、恢复体力、获取食物/水
2. social（社交）：与他人互动、维护关系、获取信息
3. career（职业）：工作赚钱、提升技能、追求地位
4. exploration（探索）：探索新地点、寻找机遇、了解世界

【输出JSON格式】
{{
    "branches": [
        {{"type": "survival", "objective": "具体目标描述", "priority": 0.0-1.0, "sub_tasks": ["子任务1", "子任务2"]}},
        {{"type": "social", "objective": "...", "priority": 0.0-1.0, "sub_tasks": ["..."]}},
        {{"type": "career", "objective": "...", "priority": 0.0-1.0, "sub_tasks": ["..."]}},
        {{"type": "exploration", "objective": "...", "priority": 0.0-1.0, "sub_tasks": ["..."]}}
    ]
}}
只输出JSON。"""

# ── 分支优先级排序：根据当前状态选择最优先分支 ──────────
PRIORITIZE_BRANCH_PROMPT = """你是优先级评估器。根据NPC当前状态，对以下分支进行优先级排序。

【NPC当前状态】
生命: {health}/{max_health} 体力: {energy}/{max_energy}
金币: {gold}
当前位置: {location}
状态效果: {status_effects}

【候选分支】
{branches_text}

【评估规则】
- 生命/体力低于30%时，survival分支优先级最高
- 金币低于生存所需时，career分支优先级提升
- 连续多轮没有社交时，social分支优先级提升
- 危机等级高时，survival分支权重增加
- 已有明确短期目标时，对应分支优先级提升

【输出JSON格式】
{{"selected_branch": "分支类型", "reason": "选择原因", "urgency": "high/medium/low"}}
只输出JSON。"""

# ── 行动序列生成：将分支目标转化为具体行动 ──────────────
GENERATE_ACTIONS_PROMPT = """你是行动规划器。根据以下分支目标，生成2-4个具体的可执行行动。

【分支信息】
类型: {branch_type}
目标: {branch_objective}
子任务: {sub_tasks}

【NPC信息】
姓名: {npc_name} 年龄: {npc_age}
性格: {personality}
能力: 力量{strength} 敏捷{agility} 智力{intelligence}
位置: {location}

【可用行动类型】
- work: 工作赚钱（消耗体力，获得金币）
- rest: 休息恢复（恢复体力和生命）
- social: 社交互动（与附近NPC对话）
- travel: 移动（前往其他地点，消耗体力）
- explore: 探索（搜索当前位置的隐藏信息）
- trade: 交易（买卖物品）
- study: 学习（提升技能/知识）
- craft: 制作（制作物品）

【输出JSON格式】
{{
    "actions": [
        {{"type": "行动类型", "target": "目标/对象", "detail": "具体描述", "est_duration": "预估时长", "energy_cost": 0-100, "priority": 0.0-1.0}},
        ...
    ],
    "fallback": "如果首选行动失败，退回到这个行动类型"
}}
只输出JSON。"""

# ── 执行前模拟：检测行动序列中的约束违反 ──────────────────
SIMULATE_EXECUTION_PROMPT = """你是行动模拟器。预演以下行动序列，检测可能的约束违反。

【NPC当前状态】
生命: {health}/{max_health} 体力: {energy}/{max_energy}
金币: {gold}
位置: {location}
库存: {inventory}

【待执行行动序列】
{actions_text}

【约束规则】
1. 体力不能低于0（否则行动中断）
2. 生命不能低于0
3. 金币不能为负（购买时需要足够金币）
4. 移动需要体力消耗（距离越远消耗越多）
5. 某些行动需要特定条件（如需要工具、需要在特定地点）

【输出JSON格式】
{{
    "feasible": true/false,
    "issues": [
        {{"action_index": 0, "issue": "问题描述", "severity": "critical/warning/suggestion", "fix": "修复建议"}}
    ],
    "adjusted_actions": []  // 如果有修复，放调整后的行动序列；否则为空
}}
只输出JSON。"""
