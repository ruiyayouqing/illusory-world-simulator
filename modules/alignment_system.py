"""
[v1.5 第二期] 立场名誉系统 — 玩家行为 → NPC 立场反应（纯规则，不调 LLM）

设计要点：
  1. 7 级立场：仁善/刚正/中庸/无畏/桀骜/狂邪/唯我
  2. 玩家行为分类：killed_npc/robbed/helped/insulted/gifted/...
  3. NPC 立场决定反应方向和强度（仁善 NPC 看不惯杀人；狂邪 NPC 反而欣赏）
  4. 立场差异 → 关系基础修正（玩家刚正 + NPC 桀骜 → 好感基础 -10）
  5. 名誉传播：高名誉 NPC 被陌生人初次见面好感 +10

调用点：TurnProcessorV2 在玩家回合处理后调用 apply_player_action_to_npcs()
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState, PlayerState

logger = logging.getLogger("chronoverse.alignment")


# 7 级立场（按"善→恶"排序）
ALIGNMENTS = ("仁善", "刚正", "中庸", "无畏", "桀骜", "狂邪", "唯我")

# 立场之间的基础张力（差异越大，关系修正越负）
# 计算方式：取两个立场的索引差，差值越大关系基础越低
_ALIGNMENT_INDEX = {a: i for i, a in enumerate(ALIGNMENTS)}


# ===== 玩家行为 → NPC 反应规则 =====
# 行为类型 → {立场: (好感变化, 信任变化, 描述)}
ACTION_REACTION_RULES = {
    "killed_npc": {
        # 杀人
        "仁善":  (-30, -20, "不忍杀生"),
        "刚正":  (-15, -10, "不齿此举"),
        "中庸":  (-5,   0, "默然不语"),
        "无畏":  (0,    0, "见怪不怪"),
        "桀骜":  (5,    0, "颇为欣赏"),
        "狂邪":  (10,   5, "正该如此"),
        "唯我":  (5,    0, "与我何干"),
    },
    "robbed": {
        # 掠夺
        "仁善":  (-20, -15, "深恶痛绝"),
        "刚正":  (-15, -10, "鄙视此举"),
        "中庸":  (-5,   0, "略感不妥"),
        "无畏":  (0,    0, "见怪不怪"),
        "桀骜":  (3,    0, "略感兴趣"),
        "狂邪":  (8,    5, "颇为欣赏"),
        "唯我":  (3,    0, "与我何干"),
    },
    "helped": {
        # 助人
        "仁善":  (20,  15, "由衷感激"),
        "刚正":  (15,  10, "颇有好感"),
        "中庸":  (8,    5, "略有好感"),
        "无畏":  (3,    0, "略感意外"),
        "桀骜":  (-3,   0, "不以为然"),
        "狂邪":  (-8,  -3, "嗤之以鼻"),
        "唯我":  (-3,   0, "多管闲事"),
    },
    "gifted": {
        # 送礼
        "仁善":  (10,  10, "心领神会"),
        "刚正":  (8,    5, "略有感激"),
        "中庸":  (10,   5, "心存感激"),
        "无畏":  (5,    0, "略有好感"),
        "桀骜":  (5,    0, "勉强收下"),
        "狂邪":  (5,   -3, "却之不恭"),
        "唯我":  (8,    0, "照单全收"),
    },
    "insulted": {
        # 侮辱
        "仁善":  (-15, -10, "伤心失望"),
        "刚正":  (-20, -15, "怒不可遏"),
        "中庸":  (-15, -10, "心生芥蒂"),
        "无畏":  (-15, -10, "记恨在心"),
        "桀骜":  (-25, -15, "誓要报复"),
        "狂邪":  (-30, -20, "不死不休"),
        "唯我":  (-25, -15, "誓要报复"),
    },
    "saved": {
        # 救命
        "仁善":  (40,  30, "感激涕零"),
        "刚正":  (35,  25, "铭记在心"),
        "中庸":  (30,  20, "深感厚恩"),
        "无畏":  (25,  15, "承你这份情"),
        "桀骜":  (20,  10, "勉强承情"),
        "狂邪":  (15,   5, "权当欠你一次"),
        "唯我":  (15,   5, "记账上"),
    },
}


# ===== 玩家行为关键词识别（从 player_input 推断行为类型）=====

PLAYER_ACTION_KEYWORDS = {
    "killed_npc": [("杀",), ("击杀",), ("斩",), ("毒杀",), ("灭口",), ("处死",)],
    "robbed":     [("抢",), ("夺",), ("偷",), ("掠夺",), ("搜刮",)],
    "helped":     [("救",), ("助",), ("帮",), ("援手",), ("解围",)],
    "gifted":     [("送", "礼"), ("赠",), ("献",), ("赏赐",)],
    "insulted":   [("辱",), ("骂",), ("羞辱",), ("嘲讽",), ("讥讽",)],
    "saved":      [("救", "命"), ("救命",), ("救下",), ("保住", "命")],
}


def detect_player_action(player_input: str) -> list[str]:
    """从玩家输入文本识别行为类型

    简单关键词匹配，返回所有匹配到的行为类型
    """
    if not player_input:
        return []
    actions = []
    for action_type, keyword_groups in PLAYER_ACTION_KEYWORDS.items():
        for keywords in keyword_groups:
            if all(k in player_input for k in keywords):
                actions.append(action_type)
                break  # 一种行为类型只匹配一次
    return actions


# ===== 立场差异修正 =====

def alignment_diff_modifier(player_alignment: str, npc_alignment: str) -> tuple[int, str]:
    """计算玩家与 NPC 立场差异带来的关系基础修正

    Returns:
        (favor_delta, description)
    """
    p_idx = _ALIGNMENT_INDEX.get(player_alignment, 2)  # 默认中庸
    n_idx = _ALIGNMENT_INDEX.get(npc_alignment, 2)
    diff = abs(p_idx - n_idx)
    if diff == 0:
        return 5, "志同道合"
    elif diff == 1:
        return 0, ""  # 相邻立场，无修正
    elif diff == 2:
        return -5, "略有分歧"
    elif diff == 3:
        return -10, "立场迥异"
    else:  # diff >= 4
        return -15, "道不同不相为谋"


# ===== 名誉传播 =====

def reputation_first_impression_bonus(npc_personal_reputation: int) -> int:
    """NPC 个人名誉对陌生人初次见面好感的加成

    名誉 ≥ 50 → +5~+15
    名誉 ≤ -50 → -5~-15
    """
    if npc_personal_reputation >= 80:
        return 15
    elif npc_personal_reputation >= 50:
        return 10
    elif npc_personal_reputation >= 20:
        return 5
    elif npc_personal_reputation <= -80:
        return -15
    elif npc_personal_reputation <= -50:
        return -10
    elif npc_personal_reputation <= -20:
        return -5
    return 0


# ===== 主入口：玩家行为 → NPC 立场反应 =====

def apply_player_action_to_npcs(player_input: str, npcs: dict,
                                 player_state, exclude_npc_id: str = None) -> list[dict]:
    """根据玩家输入识别行为，对所有 NPC 应用立场反应

    Args:
        player_input: 玩家输入文本
        npcs: {npc_id: NPCState} 字典
        player_state: PlayerState（用于读取玩家立场，但当前玩家无 alignment 字段，暂用 social.reputation 推断）
        exclude_npc_id: 排除的 NPC id（如行为目标 NPC 本身，避免重复计算）

    Returns:
        反应记录列表 [{npc_id, action_type, favor_delta, trust_delta, description}, ...]
    """
    actions = detect_player_action(player_input)
    if not actions:
        return []

    # 玩家立场：暂用 player.social.reputation 推断（无 alignment 字段）
    # reputation >= 50 → 刚正；<= -50 → 狂邪；其他 → 中庸
    player_rep = getattr(getattr(player_state, "social", None), "reputation", 0)
    if player_rep >= 50:
        player_alignment = "刚正"
    elif player_rep <= -50:
        player_alignment = "狂邪"
    else:
        player_alignment = "中庸"

    records = []
    for npc_id, npc in npcs.items():
        if exclude_npc_id and npc_id == exclude_npc_id:
            continue
        # 跳过休眠/垂死/昏迷的 NPC（无法感知玩家行为）
        npc_status = getattr(npc, "status_effects", []) or []
        if any(s in npc_status for s in ("休眠", "垂死", "昏迷", "失踪", "已故")):
            continue
        # 跳过已死亡的 NPC
        if "已故" in (getattr(npc, "tags", []) or []):
            continue

        npc_alignment = getattr(npc, "alignment", "中庸") or "中庸"
        rel = getattr(npc, "relation_to_player", None)
        if not rel:
            continue

        for action_type in actions:
            rules = ACTION_REACTION_RULES.get(action_type, {})
            rule = rules.get(npc_alignment)
            if not rule:
                continue
            favor_delta, trust_delta, description = rule

            # 应用好感变化
            old_favor = getattr(rel, "favor", 50)
            new_favor = max(-100, min(100, old_favor + favor_delta))
            # [Bug] RelationEntry 字段名是 favor
            if hasattr(rel, "favor"):
                rel.favor = new_favor

            # 应用信任变化（在 impression_of_player.trust_level）
            impression = getattr(npc, "impression_of_player", None)
            if impression and isinstance(impression, dict):
                old_trust = impression.get("trust_level", 50)
                impression["trust_level"] = max(0, min(100, old_trust + trust_delta))

            # NPC 个人名誉小幅调整（做了好事/坏事被人看到）
            if action_type in ("killed_npc", "robbed", "insulted"):
                # 玩家做坏事 → 玩家名誉下降（由其他模块处理）
                pass

            records.append({
                "npc_id": npc_id,
                "npc_name": getattr(npc, "name", npc_id),
                "action_type": action_type,
                "favor_delta": favor_delta,
                "trust_delta": trust_delta,
                "description": description,
                "npc_alignment": npc_alignment,
                "new_favor": new_favor,
            })

    return records


# ===== 战争站队 =====

def npc_pick_side_in_war(npc, faction_a: str, faction_b: str) -> str | None:
    """NPC 在战争中站队（基于 faction_id 和 alignment）

    Returns:
        "a" / "b" / None（保持中立）
    """
    npc_faction = getattr(npc, "faction_id", None)
    if npc_faction == faction_a:
        return "a"
    if npc_faction == faction_b:
        return "b"

    # 无 faction 时按 alignment 倾向
    npc_alignment = getattr(npc, "alignment", "中庸") or "中庸"
    if npc_alignment in ("仁善", "刚正"):
        # 仁善/刚正倾向帮弱势方（这里随机选一方表示"伸张正义"）
        # 实际实现需要外部传入两方实力对比
        return None
    if npc_alignment in ("狂邪", "唯我"):
        # 狂邪/唯我不参与（除非有利益）
        return None
    # 中庸/无畏/桀骜：保持中立
    return None
