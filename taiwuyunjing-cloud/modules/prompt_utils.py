from __future__ import annotations
import re


def substitute_params(template: str, **kwargs) -> str:
    """统一变量替换系统：{{var}} → value"""
    if not template:
        return ""
    result = template
    for key, value in kwargs.items():
        placeholder = "{{" + key + "}}"
        result = result.replace(placeholder, str(value) if value is not None else "")
    leftover = re.findall(r'\{\{(\w+)\}\}', result)
    for key in leftover:
        result = result.replace("{{" + key + "}}", "")
    return result


def build_npc_detail(npc, include_examples: bool = True, world_state=None) -> str:
    """构建单个 NPC 的完整注入文本"""
    parts = [npc.name]
    if npc.role:
        parts.append(f"职业={npc.role}")
    if npc.relation_to_player.relation_type and npc.relation_to_player.relation_type != "陌生人":
        parts.append(f"关系={npc.relation_to_player.relation_type}")
    if npc.personality:
        parts.append(f"性格={npc.personality[:60]}")
    if npc.speaking_style:
        parts.append(f"说话={npc.speaking_style[:40]}")
    if npc.current_location:
        parts.append(f"位置={resolve_location_name(npc.current_location, world_state)}")  # [Bug] location code → display name
    if npc.relation_to_player.favor:
        parts.append(f"好感={npc.relation_to_player.favor}")

    # [v10.1] 人物卡闭环：注入NPC对玩家的印象
    imp = getattr(npc, 'impression_of_player', {})
    if imp and imp.get('interaction_count', 0) > 0:
        trust = imp.get('trust_level', 50)
        trust_desc = "信任" if trust >= 70 else "友善" if trust >= 50 else "警惕" if trust >= 30 else "敌对"
        parts.append(f"对玩家态度={trust_desc}({trust})")
        if imp.get('summary'):
            parts.append(f"对玩家印象={imp['summary'][:80]}")
        if imp.get('known_traits'):
            parts.append(f"观察到玩家特质={','.join(imp['known_traits'][:3])}")

    if npc.role_history:
        last = npc.role_history[-1]
        parts.append(f"(第{last['day']}天从{last['from']}变为{npc.role})")

    line = " | ".join(parts)

    if include_examples and hasattr(npc, 'dialogue_examples') and npc.dialogue_examples:
        examples = npc.dialogue_examples[:3]
        line += "\n  对话示例:\n" + "\n".join([f'  "{ex}"' for ex in examples])

    return line


def build_npc_context(npc_states: dict, player_input: str = "", world_state=None) -> str:
    """构建完整的 NPC 注入上下文"""
    if not npc_states:
        return ""

    lines = ["【已知人物身份档案 - 绝对权威，不可篡改】"]
    for nid, npc in npc_states.items():
        detail = build_npc_detail(npc, include_examples=True, world_state=world_state)
        lines.append(f"- {detail}")

    lines.append("（以上为各人物当前真实身份。如有身份变更历史，已标注。不得无理由修改。）")
    return "\n".join(lines)


def build_world_context(world_state: dict) -> str:
    """构建世界设定上下文"""
    if not world_state:
        return ""

    world_type = world_state.get("world_type", "custom")
    world_name = world_state.get("world_name", "未知世界")
    world_desc = world_state.get("description", "")[:300]
    era = world_state.get("era_name", "")
    era_year = world_state.get("era_year", "")

    world_type_names = {
        "historical": "历史世界（真实朝代，无魔法修仙，无现代科技）",
        "wuxia": "武侠世界（有内力武功，无魔法枪械）",
        "xianxia": "修仙世界（有灵气法宝，无现代科技）",
        "fantasy": "奇幻世界（有魔法种族，根据具体设定）",
        "scifi": "科幻世界（有高科技，根据具体时代）",
        "postapocalyptic": "末日世界（文明崩塌，资源稀缺）",
        "modern": "现代世界（当代社会）",
        "custom": "自定义世界",
    }

    # [v9] 构建地点名称对照表，确保LLM使用中文名称
    locations = world_state.get("locations", {})
    loc_map_text = ""
    if locations:
        loc_pairs = []
        for loc_code, loc_data in locations.items():
            if isinstance(loc_data, dict):
                loc_name = loc_data.get("location_name", loc_data.get("name", loc_code))
            else:
                loc_name = str(loc_data)
            if loc_name and loc_name != loc_code:
                loc_pairs.append(f"{loc_code}→{loc_name}")
        if loc_pairs:
            loc_map_text = f"\n【重要】地点名称对照（必须使用地点的原始名称）: {'; '.join(loc_pairs[:20])}"

    return (
        f"【世界设定 - 极其重要】\n"
        f"世界: {world_name} | 类型: {world_type_names.get(world_type, '未知')}\n"
        f"纪年: {era}{era_year}\n"
        f"背景: {world_desc}\n"
        f"玩家行为必须符合此世界类型，矛盾则否定。"
        f"{loc_map_text}"
    )


def resolve_location_name(location_code: str, world_state=None) -> str:
    """
    [v9] 将地点ID转换为中文显示名。
    优先从 world_state.locations 查表，失败则做常见模式替换。
    [Bug] 支持 WorldState 对象和 dict 两种传入类型。
    """
    if not location_code:
        return "未知地点"

    # 查表 — 兼容 WorldState 对象和 dict
    locations = None
    if world_state:
        if isinstance(world_state, dict):
            locations = world_state.get("locations", {})
        elif hasattr(world_state, 'locations'):
            locations = world_state.locations
        # locations 可能是 dict[str, dict] 或 dict[str, LocationDef]
        if locations and location_code in locations:
            loc_data = locations[location_code]
            if isinstance(loc_data, dict):
                return loc_data.get("location_name", loc_data.get("name", location_code))
            elif isinstance(loc_data, str):
                return loc_data
            elif hasattr(loc_data, 'location_name'):
                return loc_data.location_name or location_code
            elif hasattr(loc_data, 'name'):
                return loc_data.name or location_code

    # 常见英文模式替换
    replacements = {
        "_manor": "府",
        "_house": "宅",
        "_inn": "客栈",
        "_shop": "店铺",
        "_temple": "寺庙",
        "_market": "集市",
        "_gate": "城门",
        "_street": "街",
        "_bridge": "桥",
        "_hill": "山",
        "_mountain": "山",
        "_river": "河",
        "_lake": "湖",
        "_forest": "林",
        "_village": "村",
        "_town": "镇",
        "_city": "城",
        "_palace": "宫殿",
        "_garden": "花园",
        "_study": "书房",
        "_bedroom": "卧室",
        "_kitchen": "厨房",
        "_hall": "厅",
        "_tower": "塔",
        "_cave": "洞",
        "_valley": "谷",
    }
    result = location_code
    for eng, chn in replacements.items():
        result = result.replace(eng, chn)
    # 如果还有下划线，用空格替代
    result = result.replace("_", "")
    return result


def build_player_context(state, world_state: dict = None) -> str:
    """构建玩家状态上下文"""
    # [v9] 将location code转为中文名
    location_display = resolve_location_name(state.location, world_state)
    return (
        f"【玩家身份 - 绝对不可篡改】\n"
        f"姓名: {state.name} | 身份: {state.social.position} | 年龄: {state.age}岁\n"
        f"【玩家属性】\n"
        f"位置: {location_display}\n"
        f"力量{state.stats.strength} 敏捷{state.stats.agility} "
        f"智力{state.stats.intelligence} 幸运{state.stats.luck}\n"
        f"生命: {state.stats.health}/{state.stats.max_health} "
        f"体力: {state.stats.energy}/{state.stats.max_energy}\n"
        f"金币: {state.social.gold} 声望: {state.social.reputation}\n"
        f"标签: {', '.join(state.tags)}\n"
        f"状态: {', '.join(state.status_effects) if state.status_effects else '正常'}\n"
        f"记忆: {'; '.join(state.memory.short_term[-10:])}\n"
    )


def build_history_context(player_input: str, narrative_history: list[dict],
                          max_history_tokens: int = 4000) -> str:
    """
    [v10.1] 分层记忆召回构建上下文
    - 摘要（更早的历史，压缩版）
    - 最近N条（完整细节）
    - 关键词匹配相关条目
    
    Args:
        max_history_tokens: 历史上下文的最大token预算
    """
    if not narrative_history:
        return ""

    keywords = set(re.findall(r'[\u4e00-\u9fa5]{2,}', player_input))

    summaries = []
    recent_entries = []
    for entry in narrative_history:
        if entry.get("type") == "summary":
            summaries.append(entry.get("text", ""))
        else:
            recent_entries.append(entry)

    # 动态计算 recent 窗口：根据历史长度和token预算调整
    # 短历史（<20条）：取更多条目以保证连贯性
    # 长历史（>50条）：适当减少，但至少保留最近5条
    base_window = 8
    if len(recent_entries) < 20:
        base_window = min(len(recent_entries), 12)
    elif len(recent_entries) > 50:
        base_window = max(5, min(10, len(recent_entries) // 5))
    
    recent = recent_entries[-base_window:] if len(recent_entries) > base_window else recent_entries
    relevant = []

    if summaries:
        relevant.append("【前期剧情摘要】\n" + "\n\n".join(summaries[-3:]))

    # 估算每个条目的平均token数（中文约1.5字/token）
    avg_entry_tokens = 200  # 约300字/条
    max_entries = max_history_tokens // avg_entry_tokens
    
    for entry in recent:
        text = entry.get("text", "")[:600]
        pi = entry.get("player_input", "")[:200]
        if not text:
            continue
        matched = any(kw in text or kw in pi for kw in keywords) if keywords else False
        if matched or len(relevant) < max_entries:
            relevant.append(f"【第{entry.get('day', '?')}天】玩家: {pi}\n叙事: {text[:500]}")

    if len(relevant) <= 1 and not summaries:
        return ""

    return (
        f"【剧情上下文（必须保持连贯）】\n"
        + "\n---\n".join(relevant)
        + "\n---\n请保持与上述叙事的连贯性，紧接上文继续写，不要跳转场景。"
        + "\n【强制规则】使用上文中出现过的所有人物名字，绝对不允许更改或编造新名字！"
        + "\n【强制规则】上文中出现的'系统'、'面板'、'属性'等概念必须保持完全一致的表述！"
        + "\n【强制规则】在开始写新叙事前，先检查上文中所有角色的名字，确保使用完全相同的名字！"
    )
