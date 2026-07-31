"""
[v1.3] NPC 私密档案生成器（全模式通用）

为 NPC 生成 3-5 条私密事实（秘密、过往、好恶、未言之心愿）。

设计原则：
- 模式无关：小说模式用原著素材，普通模式用 LLM 即兴生成
- 惰性触发：NPC 首次与玩家接触时才生成（避免一次性批量调用 LLM）
- 开关可控：普通模式由 config.features.npc_private_facts.enabled 控制
            小说模式默认启用（dormant NPC 登场时填充）
- 持久化：写入 NPCState.private_facts，存档自动保存
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState
    from .llm.base_llm import BaseLLM

logger = logging.getLogger("chronoverse.private_facts")


def generate_private_facts(
    npc: "NPCState",
    llm: "BaseLLM" = None,
    world_context: str = "",
    novel_source: str = "",
    max_facts: int = 5,
) -> list[dict]:
    """为 NPC 生成私密档案。

    参数：
        npc: NPCState 实例
        llm: LLM 实例（必需，否则返回空）
        world_context: 世界背景信息（可选）
        novel_source: 小说原著中关于此角色的素材（小说模式用，可选）
        max_facts: 最大事实数（默认5）

    返回：
        [{"fact": str, "type": "secret/past/preference/wish", "created_day": int}, ...]
        失败时返回空列表
    """
    if not npc or not llm:
        return []

    # 已生成过则跳过
    if getattr(npc, 'private_facts_generated', False):
        return list(getattr(npc, 'private_facts', []) or [])

    # 构造 prompt
    npc_info = f"""姓名：{npc.name}
身份：{npc.role or '未明确'}
性格：{npc.personality or '（无描述）'}
标签：{', '.join(npc.tags) if npc.tags else '无'}
位置：{npc.current_location or '未知'}
说话风格：{npc.speaking_style or '（无）'}"""

    source_section = ""
    if novel_source:
        source_section = f"""
【小说原著素材】
{novel_source[:800]}
"""

    prompt = f"""你是 NPC 私密档案生成器。请为以下 NPC 生成 {max_facts} 条私密事实。

【NPC 信息】
{npc_info}

【世界背景】
{world_context or '（无）'}
{source_section}
【任务】
生成该 NPC 的私密档案，每条事实必须属于以下类型之一：
- secret：秘密（不能让别人知道的事，如隐疾、隐藏身份、犯罪记录）
- past：过往（重要的人生经历，影响性格形成）
- preference：好恶（强烈的喜好或厌恶，可被玩家利用）
- wish：未言之心愿（藏在心底的愿望，玩家可帮助实现或利用）

返回 JSON：
{{
    "facts": [
        {{"fact": "事实描述（30-80字）", "type": "secret"}},
        {{"fact": "...", "type": "past"}},
        ...
    ]
}}

要求：
1. 事实要符合 NPC 的身份和世界观
2. 每条事实都可被玩家通过对话/行动触发或发现
3. 不要生成重复或泛泛的事实
4. 只返回 JSON，不要其他文字"""

    try:
        result = llm.chat_json(prompt, temperature=0.7)
        if not result or "error" in result:
            logger.warning("[PrivateFacts] LLM 返回异常: %s", result)
            return []

        facts_raw = result.get("facts", [])
        if not isinstance(facts_raw, list):
            return []

        # 规范化
        facts = []
        valid_types = {"secret", "past", "preference", "wish"}
        for f in facts_raw[:max_facts]:
            if not isinstance(f, dict):
                continue
            fact_text = (f.get("fact") or "").strip()
            fact_type = (f.get("type") or "").strip()
            if not fact_text:
                continue
            if fact_type not in valid_types:
                fact_type = "past"
            facts.append({
                "fact": fact_text[:200],
                "type": fact_type,
                "created_day": 0,  # 调用方会覆写
            })

        if not facts:
            return []

        # 写入 NPCState
        npc.private_facts = facts
        npc.private_facts_generated = True

        logger.info(
            "[PrivateFacts] %s 生成 %d 条私密档案",
            npc.name, len(facts)
        )
        return facts

    except Exception as e:
        logger.warning("[PrivateFacts] 生成失败 (%s): %s", npc.name, e)
        return []


def get_private_facts_for_prompt(npc: "NPCState") -> str:
    """获取 NPC 私密档案的 prompt 片段（供对话/叙事使用）"""
    facts = getattr(npc, 'private_facts', []) or []
    if not facts:
        return ""

    type_labels = {
        "secret": "秘密",
        "past": "过往",
        "preference": "好恶",
        "wish": "心愿",
    }
    lines = []
    for f in facts:
        label = type_labels.get(f.get("type", ""), "其他")
        lines.append(f"  - [{label}] {f.get('fact', '')}")

    return f"【{npc.name}的私密档案】\n" + "\n".join(lines)


def get_private_facts_summary(npc: "NPCState") -> dict:
    """获取 NPC 私密档案摘要（供前端展示）"""
    facts = list(getattr(npc, 'private_facts', []) or [])
    return {
        "name": npc.name,
        "generated": getattr(npc, 'private_facts_generated', False),
        "count": len(facts),
        "facts": facts,
    }


def is_feature_enabled(config: dict) -> bool:
    """检查 NPC 私密档案功能是否启用（普通模式）"""
    if not config:
        return False
    return bool(config.get("features", {}).get("npc_private_facts", {}).get("enabled", False))


def get_max_facts(config: dict) -> int:
    """获取每个 NPC 最多生成多少条事实"""
    if not config:
        return 5
    try:
        return int(config.get("features", {}).get("npc_private_facts", {}).get("max_facts_per_npc", 5))
    except Exception:
        return 5
