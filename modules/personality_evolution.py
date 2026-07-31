"""
[v1.3] NPC 性格演化轨迹（全模式通用）

独立于 character_state.py，专门负责"稳定性格"的演化。
character_state.py 负责"动态状态"（心情/压力/伤势），
本模块负责"性格转折点"（亲人死亡/被背叛/重大得失触发的性格重塑）。

设计原则：
- 模式无关：小说扮演和普通模式都启用
- 惰性触发：不是每回合都检测，由 TurnProcessor 按 check_interval_turns 周期调用
- LLM 驱动：检测到候选事件后，由 LLM 生成新的性格描述和叙事
- 持久化：写入 NPCState.personality_history，存档自动保存
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState
    from .llm.base_llm import BaseLLM

logger = logging.getLogger("chronoverse.personality")


# 触发性格转折的关键事件模式（正则）
_TRAUMA_PATTERNS = [
    (re.compile(r"(亲人|妻子|丈夫|父亲|母亲|儿子|女儿|挚友|爱人|兄弟|姐妹).{0,8}(死|丧|亡|逝|遇难|身亡|被杀)"), "亲人死亡"),
    (re.compile(r"(被背叛|被出卖|背信弃义|欺骗了我|出卖了我)"), "被背叛"),
    (re.compile(r"(被诬陷|被冤枉|蒙冤|含冤|诬告)"), "被诬陷"),
    (re.compile(r"(家破人亡|灭门|惨遭|满门|血洗)"), "家破人亡"),
    (re.compile(r"(功成名就|心愿得偿|夙愿得偿|终成眷属|大仇得报)"), "心愿达成"),
    (re.compile(r"(彻底失败|一败涂地|功亏一篑|万劫不复|身败名裂)"), "彻底失败"),
    (re.compile(r"(被救|救命之恩|救了我一命|救了.{1,6}一命)"), "被救命"),
    (re.compile(r"(重伤|残疾|毁容|断臂|断腿|失明|失聪)"), "重伤残疾"),
    (re.compile(r"(获得|觉醒|传承).{0,15}(神功|宝物|秘籍|血脉|天赋|力量)"), "得遇机缘"),
    (re.compile(r"(失去|被夺).{0,15}(神功|宝物|秘籍|修为|功力)"), "失去重宝"),
]

# 不触发转折的弱事件（避免误判）
_WEAK_KEYWORDS = ["轻微", "小事", "稍微", "一点点", "日常"]


def detect_trauma_events(narrative: str, npc_name: str) -> list[dict]:
    """从叙事文本中检测该 NPC 经历的创伤/转折事件。
    返回 [{"event_type": str, "matched_text": str, "snippet": str}, ...]
    """
    if not narrative or not npc_name:
        return []

    # 检查 NPC 是否在叙事中出现
    if npc_name not in narrative:
        return []

    events = []
    for pattern, event_type in _TRAUMA_PATTERNS:
        matches = pattern.findall(narrative)
        if not matches:
            continue
        # 过滤弱事件
        for match in matches:
            match_str = match if isinstance(match, str) else (match[0] if match else "")
            if any(w in match_str for w in _WEAK_KEYWORDS):
                continue
            # 提取上下文片段（前后30字）
            idx = narrative.find(match_str)
            if idx == -1:
                continue
            start = max(0, idx - 30)
            end = min(len(narrative), idx + len(match_str) + 30)
            snippet = narrative[start:end].replace("\n", " ")
            events.append({
                "event_type": event_type,
                "matched_text": match_str,
                "snippet": snippet,
            })
    return events


def _has_recent_shift(npc: "NPCState", current_day: int, min_gap_days: int = 7) -> bool:
    """检查 NPC 最近是否已经发生过性格转折（避免短时间内重复触发）"""
    history = getattr(npc, 'personality_history', []) or []
    if not history:
        return False
    last = history[-1]
    last_day = last.get("day", 0)
    return (current_day - last_day) < min_gap_days


def check_and_apply_personality_shift(
    npc: "NPCState",
    narrative: str,
    turn: int,
    day: int,
    llm: "BaseLLM" = None,
    world_context: str = "",
) -> dict | None:
    """检测并应用一次性格转折。

    参数：
        npc: NPCState 实例
        narrative: 本回合叙事文本
        turn: 当前回合数
        day: 当前游戏天数
        llm: LLM 实例（用于生成新性格描述，可选）
        world_context: 世界背景信息（可选，用于让 LLM 生成更贴合的叙事）

    返回：
        若发生转折，返回转折详情 dict；否则返回 None
    """
    if not npc or not narrative:
        return None

    # 最近 7 天内不重复触发
    if _has_recent_shift(npc, day):
        return None

    # 检测创伤事件
    events = detect_trauma_events(narrative, npc.name)
    if not events:
        return None

    # 取最严重的事件
    primary_event = events[0]
    event_type = primary_event["event_type"]
    snippet = primary_event["snippet"]

    # 旧性格（用于对比）
    old_personality = npc.personality or ""
    old_traits = list(npc.tags or [])[:5]

    # 由 LLM 生成新性格
    new_personality = old_personality
    new_traits = old_traits
    shift_narrative = ""

    if llm:
        try:
            prompt = f"""你是性格演化引擎。一位 NPC 经历了重大事件，请生成他的性格转折。

【NPC 信息】
姓名：{npc.name}
身份：{npc.role or '未明确'}
原性格描述：{old_personality or '（无明确描述）'}
原性格标签：{', '.join(old_traits) if old_traits else '无'}

【触发事件】
事件类型：{event_type}
事件片段：{snippet}

【世界背景】
{world_context or '（无）'}

【任务】
请基于此事件，生成该 NPC 的性格转折。返回 JSON：
{{
    "new_personality": "新的性格描述（50-150字，体现转折后的性格特征）",
    "new_traits": ["新标签1", "新标签2", "新标签3"],
    "narrative": "性格转折的叙事描写（80-200字，第二人称或第三人称，文学化，描述他如何因此事而改变）",
    "intensity": "转折强度（low/medium/high）"
}}

要求：
1. 性格变化要合理、有因果（被背叛→多疑；亲人死→沉默寡言；得机缘→自信）
2. 保留原有性格的核心，只调整受影响的部分
3. 只返回 JSON，不要其他文字"""

            result = llm.chat_json(prompt, temperature=0.7)
            if result and "error" not in result:
                new_personality = result.get("new_personality", old_personality) or old_personality
                new_traits = result.get("new_traits", old_traits) or old_traits
                shift_narrative = result.get("narrative", "") or ""
        except Exception as e:
            logger.warning("[PersonalityEvolution] LLM 生成失败 (%s): %s", npc.name, e)
            # LLM 失败时使用规则化兜底
            new_traits = _rule_based_traits_shift(event_type, old_traits)
            shift_narrative = f"自那以后，{npc.name}的性格似乎有了些许变化。"
    else:
        # 无 LLM 时使用规则化兜底
        new_traits = _rule_based_traits_shift(event_type, old_traits)
        shift_narrative = f"自那以后，{npc.name}的性格似乎有了些许变化。"

    # 应用变化
    npc.personality = new_personality
    if isinstance(new_traits, list):
        # 合并新标签（保留原有未冲突的标签）
        merged = list(old_traits)
        for t in new_traits:
            if t and t not in merged:
                merged.append(t)
        # 限制标签数量
        npc.tags = merged[:10]

    # 记录到 personality_history
    shift_record = {
        "npc_name": npc.name,
        "turn": turn,
        "day": day,
        "trauma": event_type,
        "trigger_event": snippet[:200],
        "from_personality": old_personality[:200],
        "to_personality": new_personality[:200],
        "from_traits": old_traits,
        "to_traits": list(npc.tags),
        "narrative": shift_narrative,
    }
    if not hasattr(npc, 'personality_history') or npc.personality_history is None:
        npc.personality_history = []
    npc.personality_history.append(shift_record)

    # 限制历史长度
    if len(npc.personality_history) > 30:
        npc.personality_history = npc.personality_history[-30:]

    logger.info(
        "[PersonalityEvolution] %s 性格转折: %s (turn=%d, day=%d)",
        npc.name, event_type, turn, day
    )

    return shift_record


def _rule_based_traits_shift(event_type: str, old_traits: list) -> list:
    """无 LLM 时的规则化性格标签调整"""
    shifts = {
        "亲人死亡": ["沉默寡言", "多疑"],
        "被背叛": ["多疑", "冷漠"],
        "被诬陷": ["愤世嫉俗", "谨慎"],
        "家破人亡": ["孤僻", "复仇心切"],
        "心愿达成": ["豁达", "从容"],
        "彻底失败": ["颓废", "自卑"],
        "被救命": ["忠诚", "感恩"],
        "重伤残疾": ["坚韧", "隐忍"],
        "得遇机缘": ["自信", "雄心勃勃"],
        "失去重宝": ["焦虑", "执着"],
    }
    return shifts.get(event_type, [])


def batch_check_npcs(
    npcs: dict,
    narrative: str,
    turn: int,
    day: int,
    llm: "BaseLLM" = None,
    world_context: str = "",
) -> list[dict]:
    """批量检测所有 NPC 的性格转折。
    npcs: {agent_id: NPCState} 或 [NPCState]
    返回：[shift_record, ...]
    """
    if not narrative:
        return []

    # 统一为迭代器
    if isinstance(npcs, dict):
        npc_iter = npcs.values()
    else:
        npc_iter = npcs

    shifts = []
    for npc in npc_iter:
        # 跳过休眠 NPC
        if getattr(npc, 'is_dormant', False):
            continue
        try:
            shift = check_and_apply_personality_shift(
                npc, narrative, turn, day, llm, world_context
            )
            if shift:
                shifts.append(shift)
        except Exception as e:
            logger.warning(
                "[PersonalityEvolution] 检测失败 (%s): %s",
                getattr(npc, 'name', '?'), e
            )
    return shifts


def get_personality_history(npc: "NPCState") -> list[dict]:
    """获取 NPC 的性格演化历史（供前端展示）"""
    return list(getattr(npc, 'personality_history', []) or [])


def get_personality_summary(npc: "NPCState") -> dict:
    """获取 NPC 性格演化摘要"""
    history = get_personality_history(npc)
    return {
        "name": npc.name,
        "current_personality": npc.personality,
        "current_traits": list(npc.tags or []),
        "shift_count": len(history),
        "last_shift": history[-1] if history else None,
        "history": history,
    }
