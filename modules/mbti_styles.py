"""
MBTI 决策风格系统

16种 MBTI 类型影响 NPC 的规划、社交、探索、风险偏好等决策参数。
参考 AIvilization 的 MBTI 初始化方式，为每个 NPC 分配性格类型。
"""
from __future__ import annotations
import random
from dataclasses import dataclass


@dataclass
class MBTIProfile:
    """MBTI 类型的决策参数"""
    code: str                # 如 "INTJ"
    label: str               # 如 "建筑师"
    planning_horizon: str    # "short" / "medium" / "long"
    risk_tolerance: float    # 0.0-1.0，风险偏好
    social_frequency: float  # 0.0-1.0，社交频率
    exploration_drive: float # 0.0-1.0，探索欲望
    work_ethic: float        # 0.0-1.0，工作勤奋度
    emotional_reactivity: float  # 0.0-1.0，情绪反应强度
    decision_speed: str      # "fast" / "moderate" / "deliberate"
    description: str         # 一句话描述


# ── 16 种 MBTI 类型定义 ─────────────────────────────────

MBTI_PROFILES: dict[str, MBTIProfile] = {
    # 分析师 (NT)
    "INTJ": MBTIProfile("INTJ", "建筑师", "long", 0.5, 0.2, 0.7, 0.9, 0.2, "deliberate",
        "独立思考的战略家，善于长期规划，偏好独处"),
    "INTP": MBTIProfile("INTP", "逻辑学家", "long", 0.4, 0.25, 0.8, 0.6, 0.15, "deliberate",
        "好奇的思考者，热爱探索知识，行动力较弱"),
    "ENTJ": MBTIProfile("ENTJ", "指挥官", "long", 0.7, 0.6, 0.6, 0.95, 0.3, "fast",
        "天生的领导者，果断高效，追求权力和成就"),
    "ENTP": MBTIProfile("ENTP", "辩论家", "medium", 0.8, 0.7, 0.9, 0.5, 0.4, "fast",
        "机智的辩手，喜欢挑战和创新，容易厌倦"),

    # 外交官 (NF)
    "INFJ": MBTIProfile("INFJ", "提倡者", "long", 0.3, 0.4, 0.6, 0.8, 0.7, "deliberate",
        "安静的理想主义者，有强烈的使命感和直觉"),
    "INFP": MBTIProfile("INFP", "调停者", "medium", 0.2, 0.35, 0.7, 0.5, 0.8, "moderate",
        "诗意的理想主义者，重视内心价值和和谐"),
    "ENFJ": MBTIProfile("ENFJ", "主人公", "medium", 0.4, 0.9, 0.5, 0.85, 0.7, "fast",
        "富有魅力的领袖，善于激励他人，关心他人福祉"),
    "ENFP": MBTIProfile("ENFP", "竞选者", "short", 0.7, 0.85, 0.9, 0.4, 0.9, "fast",
        "热情洋溢的自由灵魂，充满创意和感染力"),

    # 守卫者 (SJ)
    "ISTJ": MBTIProfile("ISTJ", "物流师", "long", 0.2, 0.3, 0.2, 0.95, 0.2, "moderate",
        "可靠的传统主义者，注重规则和责任"),
    "ISFJ": MBTIProfile("ISFJ", "守卫者", "medium", 0.15, 0.5, 0.2, 0.9, 0.5, "moderate",
        "温暖的保护者，默默奉献，重视稳定"),
    "ESTJ": MBTIProfile("ESTJ", "总经理", "long", 0.4, 0.6, 0.3, 0.9, 0.3, "fast",
        "高效的组织者，重视秩序和传统，善于管理"),
    "ESFJ": MBTIProfile("ESFJ", "执政官", "medium", 0.2, 0.9, 0.3, 0.8, 0.6, "fast",
        "热心的社交达人，重视和谐与他人认可"),

    # 探险家 (SP)
    "ISTP": MBTIProfile("ISTP", "鉴赏家", "short", 0.8, 0.2, 0.8, 0.4, 0.2, "fast",
        "冷静的实用主义者，善于解决实际问题，喜欢冒险"),
    "ISFP": MBTIProfile("ISFP", "探险家", "short", 0.5, 0.4, 0.8, 0.3, 0.6, "moderate",
        "灵活的艺术家，享受当下，重视美感和自由"),
    "ESTP": MBTIProfile("ESTP", "企业家", "short", 0.9, 0.7, 0.85, 0.5, 0.5, "fast",
        "大胆的行动派，热爱刺激和冒险，善于抓住机会"),
    "ESFP": MBTIProfile("ESFP", "表演者", "short", 0.8, 0.9, 0.7, 0.3, 0.9, "fast",
        "热情的娱乐家，活在当下，社交能力极强"),
}

ALL_MBTI_CODES = list(MBTI_PROFILES.keys())


def get_random_mbti() -> str:
    """随机分配一个 MBTI 类型"""
    return random.choice(ALL_MBTI_CODES)


def get_mbti_profile(code: str) -> MBTIProfile | None:
    """获取 MBTI 类型的决策参数"""
    return MBTI_PROFILES.get(code)


def assign_mbti_to_npc(personality: str = "", tags: list[str] = None) -> str:
    """根据 NPC 的性格描述和标签，智能分配最匹配的 MBTI 类型"""
    if not personality:
        return get_random_mbti()

    personality = personality.lower()
    tags = [t.lower() for t in (tags or [])]
    text = personality + " " + " ".join(tags)

    # 关键词匹配
    keyword_map = {
        "INTJ": ["战略", "谋略", "独立", "深谋", "冷静", "理性", "策划"],
        "INTP": ["学者", "研究", "好奇", "分析", "思考", "知识", "书"],
        "ENTJ": ["领导", "统帅", "果断", "野心", "权谋", "帝王", "将"],
        "ENTP": ["辩论", "创新", "机智", "聪明", "狡猾", "灵活"],
        "INFJ": ["理想", "使命", "直觉", "神秘", "预言", "先知"],
        "INFP": ["诗意", "善良", "温柔", "内心", "幻想", "浪漫"],
        "ENFJ": ["魅力", "鼓舞", "领袖", "仁慈", "关怀", "教导"],
        "ENFP": ["热情", "自由", "创意", "乐观", "活泼", "开朗"],
        "ISTJ": ["严谨", "守序", "传统", "忠诚", "可靠", "规矩"],
        "ISFJ": ["守护", "奉献", "温暖", "体贴", "默默", "照顾"],
        "ESTJ": ["管理", "组织", "效率", "纪律", "严格", "公正"],
        "ESFJ": ["社交", "热心", "和谐", "体贴", "照顾", "人缘"],
        "ISTP": ["实用", "冷静", "冒险", "技巧", "工匠", "武者"],
        "ISFP": ["艺术", "美感", "自由", "随和", "安静", "敏感"],
        "ESTP": ["大胆", "冒险", "行动", "机会", "刺激", "勇猛"],
        "ESFP": ["表演", "热情", "乐观", "社交", "享乐", "感染"],
    }

    scores = {code: 0 for code in ALL_MBTI_CODES}
    for code, keywords in keyword_map.items():
        for kw in keywords:
            if kw in text:
                scores[code] += 1

    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
    return get_random_mbti()


def get_decision_style_prompt(mbti_code: str) -> str:
    """生成 MBTI 决策风格的 prompt 片段，注入 NPC 行为 prompt"""
    profile = MBTI_PROFILES.get(mbti_code)
    if not profile:
        return ""
    return (
        f"【决策风格: {profile.code} {profile.label}】\n"
        f"- 规划视野: {profile.planning_horizon}\n"
        f"- 风险偏好: {profile.risk_tolerance:.0%}\n"
        f"- 社交倾向: {profile.social_frequency:.0%}\n"
        f"- 探索欲望: {profile.exploration_drive:.0%}\n"
        f"- 工作勤奋: {profile.work_ethic:.0%}\n"
        f"- 情绪反应: {profile.emotional_reactivity:.0%}\n"
        f"- 决策速度: {profile.decision_speed}\n"
        f"- 性格概述: {profile.description}\n"
        f"请根据以上决策风格调整你的行为。"
    )


def modify_social_chance(base_chance: float, mbti_code: str) -> float:
    """根据 MBTI 类型调整社交概率"""
    profile = MBTI_PROFILES.get(mbti_code)
    if not profile:
        return base_chance
    return min(1.0, base_chance * (0.5 + profile.social_frequency))


def modify_exploration_chance(base_chance: float, mbti_code: str) -> float:
    """根据 MBTI 类型调整探索概率"""
    profile = MBTI_PROFILES.get(mbti_code)
    if not profile:
        return base_chance
    return base_chance * (0.5 + profile.exploration_drive)
