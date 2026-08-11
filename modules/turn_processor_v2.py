"""
[v10] 回合处理器 v2 — 整合世界规则引擎、意图分类、社会网络

核心改进：
  1. 规则引擎在LLM之前运行，输出确定性的状态变化
  2. 意图分类器选择生成策略，简单意图用模板
  3. 社会网络更新NPC关系
  4. 状态快照保存历史

v10 新增：
  5. 闭环学习：叙事回顾 + 教训注入
  6. NPC 程序性记忆：记录动作经验
  7. 世界任务板：自动分配和推进任务
  8. 记忆 Curator：定期整理记忆
  9. 蝴蝶效应审批门：高影响行为需玩家确认
  10. 分层记忆：带重要性评分的记忆存储
"""
from __future__ import annotations
import logging
import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .game_engine import GameEngine

from .world_rules import WorldRulesEngine
from .intent_classifier import IntentClassifier, IntentType, GenerationStrategy, TemplateGenerator
from .social_network import SocialNetwork
from .prompt_utils import resolve_location_name, sanitize_player_input  # [Bug] location code → display name | [v1.4 P2-10] Prompt injection 防护
from .narrative_timekeeper import parse_time_skip_from_text  # [Bug v9.1] 玩家输入时间跳跃预检测

logger = logging.getLogger("chronoverse.turn_processor")

SUICIDE_KEYWORDS = [
    "自杀", "自尽", "自刎", "自戕", "割腕", "服毒", "上吊", "投河",
    "跳崖", "抹脖子", "了断自己", "结束自己的生命", "寻死",
]
FORESHADOW_KEYWORDS = [
    "秘密", "阴谋", "伏笔", "暗中", "真相", "隐藏", "背叛", "神秘",
]
# [Bug] 伏笔解决关键词：叙事中出现这些词且伏笔被多次提及时，触发 resolve
FORESHADOW_RESOLUTION_KEYWORDS = [
    "真相大白", "揭晓", "水落石出", "原形毕露", "东窗事发", "破案",
    "解决", "完成", "兑现", "了结", "终结", "尘埃落定", "水到渠成",
    "发现", "找到", "揭开", "揭露", "曝光", "败露", "识破", "看穿",
]
# [v10+++] 关键剧情关键词：命中时触发多智能体分工叙事（Agents' Room 式）
CRITICAL_PLOT_KEYWORDS = [
    "杀", "死", "战", "背叛", "告白", "离别", "登基", "篡位", "复仇",
    "决斗", "刺杀", "营救", "牺牲", "背叛", "和解", "诀别", "生死",
    "突破", "渡劫", "觉醒", "陨落", "加冕", "废黜", "联姻", "结盟",
]
# 连续被动回合达到此阈值后，下一次主动行动视为转折点
PASSIVE_STREAK_THRESHOLD = 3


@dataclass
class TurnResult:
    """[v10.5] TurnProcessorV2.process() 的结构化输出契约"""
    # [v11] 已迁移至 modules/turn_result.py，此处保留旧引用供兼容
    # 新代码应: from .turn_result import TurnResult
    pass


# [v11] 导入独立版本的 TurnResult（覆盖上面的占位）
from .turn_result import TurnResult  # noqa: E402 F811

# 重新导出 TurnResult，保持向后兼容
__all__ = ["TurnProcessorV2", "TurnResult"]


class TurnProcessorV2:
    """回合处理器 v2：整合世界规则引擎的完整处理流程"""

    # 环境类型 → 不兼容的动作关键词（玩家在该环境输入这些动作时触发提醒）
    _ENV_ACTION_CONFLICTS = {
        "desert": {
            "actions": ["冲浪", "游泳", "潜水", "划船", "钓鱼", "洗澡", "跳水", "洗衣服"],
            "locations": ["海边", "海滩", "海岸", "沙滩", "大海", "湖泊", "河边", "泳池", "海里", "水中"],
            "hint": "玩家当前位于沙漠环境，但输入中包含了水域活动或海边地点，这在沙漠中不可能实现",
        },
        "ocean": {
            "actions": ["挖土", "种地", "种菜", "耕田", "劈柴", "烧火"],
            "locations": ["沙漠", "戈壁", "草原深处", "山洞里"],
            "hint": "玩家当前位于海洋环境，但输入中包含了陆地深处活动，这在海上不可能实现",
        },
        "mountain": {
            "actions": ["冲浪", "潜水", "划船", "游泳"],
            "locations": ["海底", "海里", "深海"],
            "hint": "玩家当前位于山区，但输入中包含了深海活动，这在山上不可能实现",
        },
        "city": {
            "actions": ["打猎", "采集草药", "砍柴", "捕鱼"],
            "locations": ["原始森林", "深山老林", "荒野"],
            "hint": "玩家当前位于城市，但输入中包含了野外生存活动，除非离开城市否则不合理",
        },
        "forest": {
            "actions": ["冲浪", "潜水", "划船"],
            "locations": ["海底", "海里", "大海中央"],
            "hint": "玩家当前位于森林，但输入中包含了海洋活动，这在森林中不可能实现",
        },
        "space": {
            "actions": ["游泳", "冲浪", "划船", "骑马", "开车"],
            "locations": ["海底", "海里", "河里", "湖里"],
            "hint": "玩家当前位于太空/飞船，但输入中包含了地面或水上活动，这在太空中不可能实现",
        },
    }

    # 通用瞬移检测：输入中提到的地点如果不在世界地图上，且不是当前地点
    _TELEPORT_KEYWORDS = [
        "瞬移", "传送", "穿越到", "突然出现在", "一下子到了",
        "眨眼间到了", "瞬间移动", " teleport",
    ]

    def __init__(self, engine: "GameEngine"):
        self.engine = engine
        self.rules_engine = WorldRulesEngine()
        self.intent_classifier = IntentClassifier()
        self.social_network = SocialNetwork()
        self._social_initialized = False
        # [v10+++] 多智能体分工叙事相关状态
        self._passive_streak: int = 0  # 连续被动回合计数（转折点检测）
        self._pre_impact: dict | None = None  # 蝴蝶效应预评估结果（供 Step 7 复用）

    def _bg(self, func, *args, **kwargs):
        """安全后台执行：有task_queue就异步，否则同步执行
        [v11] 增加异步错误告警日志，不再静默吞掉异常。"""
        func_name = getattr(func, '__name__', 'unknown')
        eng = self.engine
        if hasattr(eng, 'task_queue') and eng.task_queue is not None:
            try:
                eng.task_queue.post(func, *args, **kwargs)
                return
            except Exception as e:
                logger.warning("后台队列投递失败 [%s]: %s, 改为同步执行", func_name, e, exc_info=True)
        try:
            func(*args, **kwargs)
        except Exception as e:
            # [v11] 改为 warning 级别 + 函数名，不再静默吞掉
            logger.warning("后台任务执行失败 [%s]: %s", func_name, e, exc_info=True)

    # ── [v1.5 第一期] 闭关/隐居状态识别 ──────────────────────────
    _SECLUSION_KEYWORDS = ("闭关", "潜修", "静修", "苦修")
    _HIDEOUT_KEYWORDS = ("隐居", "隐遁", "归隐", "隐遁山林")
    _EMERGE_KEYWORDS = ("出关", "离开洞府", "结束闭关", "结束潜修", "结束静修", "出定")

    def _update_seclusion_status(self, player_input: str) -> None:
        """根据玩家输入更新 status_effects 中的闭关/隐居标记。
        - 识别"闭关/潜修/静修/苦修" → 添加 "闭关中"
        - 识别"隐居/归隐" → 添加 "隐居中"
        - 识别"出关/离开洞府/结束闭关" → 移除上述标记
        """
        eng = self.engine
        if not eng.player_state:
            return
        status = eng.player_state.status_effects or []

        # 出关优先（同时清除两个状态）
        if any(kw in player_input for kw in self._EMERGE_KEYWORDS):
            new_status = [s for s in status if s not in ("闭关中", "隐居中")]
            if len(new_status) != len(status):
                eng.player_state.status_effects = new_status
                logger.info("Seclusion ended by player input: %s", player_input[:30])
            return

        # 闭关（仅当当前不在闭关/隐居时添加）
        if any(kw in player_input for kw in self._SECLUSION_KEYWORDS):
            if "闭关中" not in status and "隐居中" not in status:
                status.append("闭关中")
                eng.player_state.status_effects = status
                logger.info("Seclusion started (闭关中) by player input: %s", player_input[:30])
            return

        # 隐居
        if any(kw in player_input for kw in self._HIDEOUT_KEYWORDS):
            if "隐居中" not in status and "闭关中" not in status:
                status.append("隐居中")
                eng.player_state.status_effects = status
                logger.info("Seclusion started (隐居中) by player input: %s", player_input[:30])

    def _check_spatial_consistency(self, player_input: str) -> str:
        """检测玩家输入是否与当前空间环境矛盾，返回提示文本（无矛盾则返回空）。"""
        eng = self.engine
        if not eng.player_state or not eng.world_state:
            return ""

        current_location = eng.player_state.location or ""
        current_loc_name = resolve_location_name(current_location, eng.world_state)

        # 1. 检测显式瞬移关键词
        for kw in self._TELEPORT_KEYWORDS:
            if kw in player_input:
                return (
                    f"【空间一致性提醒】玩家当前位于「{current_loc_name}」，"
                    f"但输入中包含「{kw}」类动作。在当前世界设定下，"
                    f"玩家不具备瞬移/传送能力。你需要在叙事中合理否定这种行为，"
                    f"例如：角色只是产生了幻觉、做了一场梦、或被同伴拉住。"
                )

        # 2. 从玩家位置推断当前环境类型
        env_type = self._detect_env_type(current_loc_name, eng.world_state)

        # 3. 检查环境-动作冲突
        if env_type and env_type in self._ENV_ACTION_CONFLICTS:
            conflicts = self._ENV_ACTION_CONFLICTS[env_type]
            # 检查动作冲突
            for action in conflicts["actions"]:
                if action in player_input:
                    return (
                        f"【空间一致性提醒】{conflicts['hint']}（玩家位于「{current_loc_name}」，"
                        f"输入中包含「{action}」）。你需要在叙事中合理否定这种行为，"
                        f"让玩家意识到这不可能实现。"
                    )
            # 检查地点冲突（玩家提到的地点与当前环境矛盾）
            for loc in conflicts["locations"]:
                if loc in player_input:
                    return (
                        f"【空间一致性提醒】{conflicts['hint']}（玩家位于「{current_loc_name}」，"
                        f"输入中提到「{loc}」）。你需要在叙事中合理否定这种空间跳跃。"
                    )

        # 4. 检查玩家提到的地点是否存在于世界地图中
        if eng.world_state and eng.world_state.locations:
            world_locs = eng.world_state.locations
            mentioned_locs = []
            for loc_key, loc_data in world_locs.items():
                loc_name = ""
                if isinstance(loc_data, dict):
                    loc_name = loc_data.get("location_name", "") or loc_data.get("name", "")
                elif hasattr(loc_data, "location_name"):
                    loc_name = loc_data.location_name or ""
                elif hasattr(loc_data, "name"):
                    loc_name = loc_data.name or ""
                if not loc_name or loc_name not in player_input:
                    continue
                # [Bug] 跳过玩家当前所在地点（code 或显示名称匹配）
                # 避免把"玩家位于落霞城，输入提到落霞城"误判为瞬移
                if loc_key == current_location:
                    continue
                # [Bug] 显示名称相同也跳过（玩家位置 code 与地图 key 可能不一致）
                if loc_name == current_loc_name:
                    continue
                # [Bug] 模糊匹配：处理"剑渊峰附近"vs"剑渊峰"等子串关系
                # 玩家当前位置包含地点名（或反之）时，视为同一地点
                if (loc_name in current_loc_name) or (current_loc_name in loc_name):
                    continue
                mentioned_locs.append(loc_name)

            if mentioned_locs:
                locs_str = "、".join(mentioned_locs[:3])
                return (
                    f"【空间一致性提醒】玩家当前位于「{current_loc_name}」，"
                    f"但输入中提到了「{locs_str}」。这些地点不在当前位置附近，"
                    f"玩家不具备瞬间移动的能力。你需要在叙事中合理处理："
                    f"可以写角色只是想去但还没动身，或者写角色意识到路途遥远需要准备。"
                    f"绝对不要让玩家凭空出现在另一个地点。"
                )

        return ""

    def _detect_env_type(self, location_name: str, world_state) -> str:
        """根据地点名称推断环境类型。"""
        name = location_name.lower()
        # 沙漠环境
        if any(kw in name for kw in ["沙漠", "戈壁", "沙丘", "荒漠", "沙海"]):
            return "desert"
        # 海洋环境
        if any(kw in name for kw in ["海", "岛", "湾", "港", "码头", "海岸"]):
            return "ocean"
        # 山区环境
        if any(kw in name for kw in ["山", "峰", "岭", "崖", "洞", "谷"]):
            return "mountain"
        # 森林环境
        if any(kw in name for kw in ["林", "森", "树林", "丛林", "密林"]):
            return "forest"
        # 城市环境
        if any(kw in name for kw in ["城", "镇", "村", "府", "宫", "殿", "街", "巷", "坊"]):
            return "city"
        # 太空环境
        if any(kw in name for kw in ["太空", "飞船", "空间站", "星球", "宇宙"]):
            return "space"
        return ""

    def _options_need_refresh(self, options: list, narrative: str) -> bool:
        """判断选项是否过于通用，需要按本轮正文重新生成。"""
        if not narrative or not options or len(options) < 3:
            return bool(narrative)
        generic_markers = [
            "仔细观察", "观察周围", "四处看看", "环顾四周", "搜集信息",
            "整理思绪", "制定应对策略", "制定下一步计划", "当前局势",
            "打破僵局", "主动出击", "出人意料", "大胆的举动",
            "找人聊天", "附近的人", "寻求建议",
            "去草坪", "去厨房", "转转", "看看那", "打打气",
            "找个地方歇息", "四处走走", "看看周围",
            "和附近的人交谈", "四处张望", "深吸一口气",
            "找个安静的地方", "思考一下", "理清头绪",
            "找个地方坐下", "四处逛逛", "看看有什么",
            # [v11] 补充规则兜底的通用选项特征词
            "休息片刻", "查看状态", "重试", "重新生成",
        ]
        text = "\n".join(str(opt.get("text", "")) for opt in options if isinstance(opt, dict))
        return any(marker in text for marker in generic_markers)

    def _refresh_options_from_narrative(self, player_input: str, narrative: str,
                                        options: list, response: dict) -> list:
        """让选项强制跟随本轮正文末尾，避免出现与上文脱节的通用选项。"""
        eng = self.engine
        if not eng.player_agent or not narrative:
            return options
        if not self._options_need_refresh(options, narrative):
            return options

        # [v11] 检测是否为规则兜底选项（"休息片刻"等），跳过中间重试直接走强约束
        text = "\n".join(str(opt.get("text", "")) for opt in options if isinstance(opt, dict))
        is_fallback = ("休息片刻" in text or "查看状态" in text)

        if not is_fallback:
            try:
                metadata = eng.player_agent.generate_metadata_from_narrative(
                    eng.player_state,
                    player_input,
                    narrative,
                    npc_names=list(eng.npc_states.keys()) if eng.npc_states else [],
                    narrative_history=eng.narrative_history,
                    world_state=eng.world_state.model_dump() if eng.world_state else None,
                    npc_states=eng.npc_states,
                )
                refreshed = metadata.get("options", [])
                # [Bug1-fix] 同步更新 dice_check：二次 metadata 调用可能产生了 dice_check，
                # 若 response 中尚无 dice_check，则回填，确保危险行动能触发骰子判定
                meta_dice = metadata.get("dice_check")
                if meta_dice and meta_dice.get("needed") and not response.get("dice_check", {}).get("needed"):
                    response["dice_check"] = meta_dice
                if refreshed and len(refreshed) >= 3:
                    if not self._options_need_refresh(refreshed, narrative):
                        response.setdefault("_options_refreshed_from_narrative", True)
                        return refreshed[:3]
            except Exception as e:
                logger.warning("Contextual option refresh failed: %s", e, exc_info=True)

        # [v11] 兜底被检测到 或 中间重试后仍通用 → 直接走强约束
        logger.info("Using force contextual options (is_fallback=%s)", is_fallback)
        stronger_options = self._force_contextual_options(
            player_input, narrative, eng
        )
        if stronger_options and len(stronger_options) >= 3:
            response.setdefault("_options_refreshed_from_narrative", True)
            return stronger_options[:3]
        return options

    def _force_contextual_options(self, player_input: str, narrative: str,
                                   eng) -> list:
        """强制生成与叙事紧密相关的选项（最强约束模式）。"""
        # 提取叙事最后一句作为锚点
        last_line = ""
        for line in reversed(narrative.strip().split("\n")):
            if line.strip():
                last_line = line.strip()
                break

        # 提取叙事中出现的NPC名字
        npc_names_in_text = []
        if eng.npc_states:
            for npc in eng.npc_states.values():
                if npc.name in narrative:
                    npc_names_in_text.append(npc.name)

        prompt = (
            f"【当前地点】{eng.player_state.location}\n"
            f"【叙事最后一句】{last_line}\n"
            f"【在场人物】{', '.join(npc_names_in_text) if npc_names_in_text else '仅主角'}\n"
            f"【主角】{eng.player_state.name}（{eng.player_state.social.position}）\n"
            f"\n【铁律】根据「叙事最后一句」的场景，生成3个选项。"
            f"每个选项必须直接回应最后一句中的具体情境。"
            f"禁止任何通用选项。选项必须具体到人、事、物。\n"
            f"只输出JSON：{{\"options\":[{{\"id\":\"A\",\"text\":\"...\",\"type\":\"action\",\"risk\":\"low\"}},"
            f"{{\"id\":\"B\",\"text\":\"...\",\"type\":\"action\",\"risk\":\"medium\"}},"
            f"{{\"id\":\"C\",\"text\":\"...\",\"type\":\"action\",\"risk\":\"high\"}}]}}\n"
        )
        try:
            resp = eng.llm.chat_json(prompt, temperature=0.3, max_tokens=0)
            opts = resp.get("options", [])
            if opts and len(opts) >= 3 and not self._options_need_refresh(opts, narrative):
                return opts[:3]
        except Exception as e:
            logger.warning("Force contextual options failed: %s", e, exc_info=True)
        return []

    # ── [v11] 行动合理性校验（cheap LLM 快速判断） ──────────────────────
    def _rule_based_validate(self, player_input: str, world_type: str):
        """[D] 规则预检：返回 (verdict, reason)。

        verdict:
        - 'reject': 明显不可能，直接拒绝（不调 LLM）
        - 'pass': 明显可能或安全，跳过 LLM
        - 'uncertain': 可疑，需要 LLM 判断

        设计原则：宁可放过，不可错杀。只拦截物理上绝对不可能的行动。
        """
        wt = (world_type or "").lower()

        # 古代/中世纪世界类型（禁止现代科技）
        ancient_keywords = ["historical", "wuxia", "xianxia", "fantasy", "历史", "武侠", "仙侠", "奇幻", "古风"]
        is_ancient = any(kw in wt for kw in ancient_keywords)

        # 现代科技词（在古代世界里绝对不可能存在）
        modern_tech = ["冲锋枪", "手枪", "步枪", "手机", "电脑", "电视", "互联网", "激光",
                       "核武", "核弹", "火箭", "电灯", "电话", "汽车", "飞机", "高铁",
                       "地铁", "wifi", "蓝牙", "无人机", "导弹", "坦克", "雷达", "gps",
                       "机器人", "人工智能", "电击器", "对讲机", "摄像机", "照相机", "广播"]

        if is_ancient:
            # 动作动词 + 现代科技词 组合才拦截（避免"我看见手机海报"这种描述被误杀）
            action_verbs = ["掏出", "拿出", "使用", "召唤", "启动", "打开", "按下", "驾驶", "发射", "装备"]
            for tech in modern_tech:
                if tech in player_input:
                    for verb in action_verbs:
                        if verb in player_input:
                            return "reject", f"当前是古代世界，不存在「{tech}」这种现代科技"

        # 凡人世界禁止超能力（仙侠/奇幻允许）
        mortal_keywords = ["historical", "modern", "apocalypse", "历史", "现代", "末日"]
        is_mortal = any(kw in wt for kw in mortal_keywords)

        if is_mortal:
            impossible_abilities = [
                ("凭空飞行", "凡人世界无法凭空飞行"),
                ("瞬间移动", "凡人世界无法瞬间移动"),
                ("分身", "凡人世界无法分身"),
                ("飞升", "凡人世界无法飞升"),
                ("长生不老", "凡人世界无法长生不老"),
                ("起死回生", "凡人世界无法起死回生"),
            ]
            for ability, reason in impossible_abilities:
                if ability in player_input:
                    return "reject", reason

        # 明显安全的行动（短输入 + 观察/休息/管理类关键词），跳过 LLM
        if len(player_input) < 30:
            safe_keywords = ["观察", "看看", "环顾", "查看", "休息", "睡觉", "坐下",
                             "整理", "打开背包", "查看状态", "看看四周", "看看周围",
                             "什么也不做", "发呆", "冥想", "打坐", "练功", "吃饭", "喝水"]
            if any(kw in player_input for kw in safe_keywords):
                return "pass", None

        return "uncertain", None

    def _validate_player_action(self, player_input: str, narrative_history: list,
                                player_state, world_state, npc_states) -> dict | None:
        """用 cheap LLM 快速判断玩家行动是否合理。返回 None 表示通过，否则返回拒绝响应 dict。
        仅拦截明显不可能的行动（如中世纪掏冲锋枪），不干涉合理范围内的自由行动。

        [D] 优化：先做规则预检（_rule_based_validate），命中 'reject' 直接拒绝，
        命中 'pass' 直接通过，只有 'uncertain' 才调 cheap LLM。
        这样能省下大量明显安全/明显不可能的回合的 LLM 调用。
        """
        eng = self.engine
        if not eng.llm:
            return None

        # 检查配置是否启用了行动校验
        config = eng._load_config() if hasattr(eng, '_load_config') else {}
        if not config.get("game", {}).get("action_validation_enabled", True):
            return None

        # 构建世界类型信息
        world_type = ""
        if world_state and hasattr(world_state, 'world_type'):
            world_type = world_state.world_type
        elif eng.world_def:
            world_type = eng.world_def.get("world_type", "")

        # [D] 规则预检：明显不可能/明显安全的行动不调 LLM
        verdict, reason = self._rule_based_validate(player_input, world_type)
        if verdict == "reject":
            logger.info("规则预检拦截（省 LLM）: %s → %s", player_input[:30], reason)
            return {
                "narrative": f"⚠️ 行动与世界观冲突：{reason}\n\n请换一个更符合当前世界设定的行动。",
                "options": [
                    {"id": "A", "text": "环顾四周，观察环境", "type": "action", "risk": "low"},
                    {"id": "B", "text": "与身边的人交谈", "type": "talk", "risk": "low"},
                    {"id": "C", "text": "坚持原行动", "type": "action", "risk": "medium"},
                ],
            }
        if verdict == "pass":
            logger.info("规则预检通过（省 LLM）: %s", player_input[:30])
            return None

        location = resolve_location_name(player_state.location, world_state) if player_state else "未知"

        # [v1.4 P2-10] Prompt injection 防护
        safe_input = sanitize_player_input(player_input)
        validation_prompt = (
            f"【世界类型】{world_type or '自定义'}\n"
            f"【当前场景】{location}\n"
            f"【玩家行动】{safe_input}\n\n"
            f"判断这个行动在当前世界中是否物理上不可能实现？\n"
            f"只回答OK或NO。\n"
            f"OK = 行动在逻辑上是可能的（即使很离谱或危险）\n"
            f"NO = 行动在物理上绝对不可能（如中世纪掏出冲锋枪、在沙漠里游泳、凡人凭空飞行）\n"
            f"注意：玩家可以尝试任何危险或愚蠢的行动，只要不是物理上不可能就不拦截。"
        )

        try:
            result = eng.llm.chat(validation_prompt, temperature=0.1, max_tokens=500)
            result = (result or "").strip()
            if result.upper().startswith("NO"):
                reason = result[3:].strip().lstrip(":：").strip() or "该行动在当前世界中不可能实现"
                logger.info("行动校验拦截: %s → %s", player_input[:30], reason)
                return {
                    "narrative": f"⚠️ 行动与世界观冲突：{reason}\n\n请换一个更符合当前世界设定的行动。",
                    "options": [
                        {"id": "A", "text": "环顾四周，观察环境", "type": "action", "risk": "low"},
                        {"id": "B", "text": "与身边的人交谈", "type": "talk", "risk": "low"},
                        {"id": "C", "text": "坚持原行动", "type": "action", "risk": "medium"},
                    ],
                }
            logger.info("行动校验通过: %s", player_input[:30])
        except Exception as e:
            logger.warning("行动校验失败（跳过校验）: %s", e, exc_info=True)

        return None

    def process(self, player_input: str) -> TurnResult:
        """处理玩家输入，返回完整响应"""
        eng = self.engine

        if not eng.player_agent or not eng.player_state:
            raise RuntimeError("游戏未初始化")

        # [v10+++] 重置蝴蝶效应预评估缓存（每回合独立）
        self._pre_impact = None

        # [v11] 检查是否有上回合遗留的蝴蝶效应结果
        pending = getattr(eng, '_pending_butterfly_result', None)
        if pending and pending.get("world_event"):
            # 将延迟的蝴蝶效应世界事件注入本回合的日志
            we = pending["world_event"]
            logger.info("Applying pending butterfly result from previous turn: %s",
                        we.get("description", "")[:60])
            if we.get("narrative"):
                eng.narrative_history.append({
                    "type": "event",
                    "day": eng.world_state.current_day if eng.world_state else 0,
                    "time": eng.world_state.current_time if eng.world_state else "",
                    "text": we.get("narrative", ""),
                    "event_type": we.get("event_type", ""),
                })
            eng._pending_butterfly_result = None  # 消费后清除

        # 初始化社会网络（仅首次）
        if not self._social_initialized and eng.npc_states:
            self.social_network.initialize(eng.npc_states, eng.world_state)
            self._social_initialized = True

        # 更新回合计数
        eng.meta.current_turn += 1
        eng.meta.save_timestamp = datetime.now().isoformat()

        is_suicide = any(kw in player_input for kw in SUICIDE_KEYWORDS)
        npc_names = [npc.name for npc in eng.npc_states.values()] if eng.npc_states else []

        # [v11] 死玩家保护：如果玩家已经死亡，直接返回死亡状态而非继续处理
        if eng.player_state and eng.player_state.stats.health <= 0:
            eng.meta.current_turn -= 1  # 死亡回合不计入有效回合
            death_data = None
            if eng.death_system:
                death_data = eng.death_system.check_death(eng.player_state, eng.world_state)
            return TurnResult(
                narrative="你的故事已经结束了……这个身躯已不再属于你。\n\n如需开始新的人生，请进行轮回转世。",
                options=[],
                death=death_data or {"cause": "已死亡", "message": "你已死亡，请重新开始"},
                intent_type="death",
                generation_strategy="death",
            )

        # ========== v9新增：Step 0: 意图分类 ==========
        context = {
            "npc_names": npc_names,
            "locations": list(eng.world_state.locations.keys()) if eng.world_state and eng.world_state.locations else [],
        }
        intent = self.intent_classifier.classify(player_input, context)
        logger.info("Intent classified: %s (strategy=%s, confidence=%.2f)",
                    intent.intent_type.value, intent.strategy.value, intent.confidence)

        # [v1.5 第一期] 闭关/隐居识别：玩家明确表达"闭关修炼 N 天"时，设置 status_effects，
        # 让 WorldTick 在跨日时大幅降低打扰概率（闭关中→0.02，隐居中→0.10）。
        # 简化实现：仅按关键词识别，不做天数推断（让叙事系统处理时间流逝）。
        # 出关由玩家后续输入"出关/离开洞府"等关键词清除。
        try:
            self._update_seclusion_status(player_input)
        except Exception as e:
            logger.warning("Seclusion status update failed: %s", e, exc_info=True)

        # [v1.5 第二期] 立场名誉系统：玩家行为 → NPC 立场反应（纯规则，不调 LLM）
        # 注意：行动校验通过后才应用反应（见下方 _validate_player_action 通过后）
        # 这里只识别但不应用，避免被拒行为产生副作用
        try:
            from .alignment_system import detect_player_action, apply_player_action_to_npcs
            self._pending_alignment_actions = detect_player_action(player_input)
        except Exception as e:
            logger.warning("Alignment detection failed: %s", e, exc_info=True)
            self._pending_alignment_actions = []

        # ========== v9新增：Step 1: 世界规则引擎计算 ==========
        rule_result = self.rules_engine.evaluate_player_action(
            player_input, eng.player_state, eng.world_state, eng.npc_states
        )
        # [Bug#3] 不在此处应用规则效果，等校验通过后再应用，避免被拒绝的行为仍有副作用

        # ========== [v11] 行动合理性校验（cheap LLM 快速判断） ==========
        validation_result = self._validate_player_action(
            player_input, eng.narrative_history,
            eng.player_state, eng.world_state, eng.npc_states
        )
        if validation_result:
            # 行动被拦截，直接返回拒绝响应（规则效果未应用，无副作用）
            return TurnResult(
                narrative=validation_result["narrative"],
                options=validation_result.get("options", []),
                intent_type=intent.intent_type.value,
                generation_strategy="validation_rejected",
            )

        # [Bug#3] 校验通过后再应用规则引擎的确定性效果
        rule_result.apply_to_player(eng.player_state)
        rule_result.apply_to_world(eng.world_state)

        # [v1.5 第二期] 校验通过后应用立场名誉反应（玩家行为 → NPC 立场反应）
        if getattr(self, "_pending_alignment_actions", None):
            try:
                from .alignment_system import apply_player_action_to_npcs
                reactions = apply_player_action_to_npcs(
                    player_input, eng.npc_states, eng.player_state,
                )
                if reactions:
                    logger.info(
                        "Alignment reactions applied: %d NPCs reacted",
                        len(reactions),
                    )
                    # 反应记录可供叙事引擎参考（暂存到 engine 上下文）
                    eng._last_alignment_reactions = reactions
            except Exception as e:
                logger.warning("Alignment reaction apply failed: %s", e, exc_info=True)
            self._pending_alignment_actions = []

        # 构建固定prompt（注入所有上下文：世界观/规则/风格/记忆/伏笔等，时间锚点单独抽出）
        fixed_prompt, scene_type, time_anchor = self._build_fixed_prompt(player_input, rule_result)

        # ========== v9新增：Step 2: 根据策略生成叙事 ==========
        narrative = ""
        options = []
        response = {}
        # [v10+++] 多智能体分工叙事标记
        multi_agent_used = False
        multi_agent_reason = ""

        # ── [v10+++] 关键剧情：尝试多智能体分工叙事（Agents' Room 式） ──
        # 仅对关键剧情启用（消耗 3-4 倍 LLM 调用），普通回合走单 LLM 路径
        if eng.multi_agent_narrative and eng.multi_agent_narrative.is_available():
            is_critical, multi_agent_reason = self._is_critical_plot(
                player_input, intent, fixed_prompt
            )
            if is_critical:
                try:
                    narrative, options, response = self._generate_multi_agent(
                        player_input, npc_names, fixed_prompt,
                        scene_type=scene_type, critical_reason=multi_agent_reason,
                        time_anchor=time_anchor
                    )
                    if narrative:
                        multi_agent_used = True
                        logger.info(
                            "Multi-agent narrative used for critical plot: %s",
                            multi_agent_reason
                        )
                    else:
                        logger.info(
                            "Multi-agent narrative returned empty, falling back to single LLM"
                        )
                except Exception as e:
                    logger.warning("Multi-agent narrative failed, falling back: %s", e, exc_info=True)
                    narrative = ""

        # [v10+++] 更新连续被动回合计数（用于转折点检测，需在检测之后更新）
        if intent.strategy == GenerationStrategy.TEMPLATE:
            self._passive_streak += 1
        else:
            self._passive_streak = 0

        # 普通回合或多智能体回退：使用原有策略生成
        if not narrative:
            if intent.strategy == GenerationStrategy.TEMPLATE:
                # 模板生成，不需要LLM
                narrative, options, response = self._generate_from_template(
                    intent, player_input, npc_names, fixed_prompt, scene_type=scene_type,
                    time_anchor=time_anchor
                )
            elif intent.strategy == GenerationStrategy.LIGHT_LLM:
                # 轻量LLM调用
                narrative, options, response = self._generate_light_llm(
                    player_input, npc_names, fixed_prompt, scene_type=scene_type,
                    time_anchor=time_anchor
                )
            else:
                # 完整LLM调用（原有逻辑）
                narrative, options, response = self._generate_full_llm(
                    player_input, npc_names, fixed_prompt, scene_type=scene_type,
                    time_anchor=time_anchor
                )

        # [Bug] 选项必须跟随本轮正文末尾。轻量模式/兜底模式容易产生“观察四周、整理思绪”
        # 这类通用选项，导致和上文情节不一致；这里统一检测并按刚生成的正文重算。
        if intent.strategy != GenerationStrategy.TEMPLATE:
            options = self._refresh_options_from_narrative(
                player_input, narrative, options, response
            )

        # Step 3: 骰子判定
        dice_result = self._handle_dice(response)

        # [v10+] 标记教训为已应用（叙事生成成功后）
        if narrative and eng.narrative_reviewer and eng.narrative_reviewer.lessons:
            eng.narrative_reviewer.mark_lessons_applied(
                current_turn=eng.meta.current_turn, max_lessons=5
            )

        # Step 4: 更新记忆
        if narrative:
            eng.action_log_today.append(narrative[:300])
            eng.player_agent.update_memory(
                eng.player_state, narrative[:400],
                eng.world_state.current_day if eng.world_state else 1
            )

        # ========== v9新增：Step 5: 社会网络更新 ==========
        if narrative and eng.npc_states:
            self._update_social_network(player_input, narrative, eng)

        # Step 6: 叙事时间感知
        time_skip_result, year_evolution_events = self._handle_time_perception(
            narrative, player_input
        )

        # Step 7: 蝴蝶效应（[v10.6] 异步执行，不阻塞玩家响应）
        # 蝴蝶效应评估使用对话 LLM，同步执行会阻塞 20-30 秒
        impact, world_event, butterfly_approval = self._handle_butterfly_v10_async(
            player_input, narrative, response
        )

        # Step 8: 死亡检测
        death, suicide_confirm = self._handle_death(is_suicide)

        # Step 9 ~ Step 14.9: 后台异步任务集合（RAG/GraphRAG/角色状态/NPC记忆/技能学习/叙事回顾/记忆整理/连续性审计/性格演化/因果链）
        bg_results = self._run_post_turn_background(player_input, narrative, rule_result, impact, world_event)

        # Step 11: 身份审计
        audit_results = self._run_identity_audit(narrative)

        # Step 12: 经验/等级处理
        exp_result = self._handle_experience(player_input, narrative)

        # Step 13: 记录叙事历史
        self._record_history(narrative, player_input, world_event)

        # Step 14: 社会网络回合处理
        if eng.npc_states:
            self.social_network.process_turn(
                eng.npc_states, eng.world_state,
                eng.world_state.current_day if eng.world_state else 1
            )

        # Step 14.2: 世界任务板推进
        task_board_result = self._advance_task_board()

        # Step 14.2.1: 任务系统检查
        quest_result = self._advance_quest_system(narrative)

        # Step 14.5: 伏笔生命周期追踪
        foreshadow_result = self._track_foreshadow(narrative, player_input)

        # Step 14.7: Agent 自主记忆管理（MemGPT/Letta 式）
        amm_result = self._run_autonomous_memory()

        # ========== v9新增：Step 15: 保存状态快照 ==========
        self._save_snapshot(eng, player_input, narrative, rule_result)

        # Step 16: 自动存档
        eng.save_game("auto")

        return TurnResult(
            narrative=narrative,
            options=options,
            dice_result=dice_result,
            status_changes=response.get("status_changes", {}),
            new_effects=response.get("new_effects", []),
            removed_effects=response.get("removed_effects", []),
            world_event=world_event,
            auto_event=None,
            impact=impact,
            death=death,
            suicide_confirm=suicide_confirm,
            identity_log=response.get("_identity_log", []),
            audit_results=audit_results,
            auto_image=eng._maybe_auto_image(narrative),
            time_skip=time_skip_result,
            year_evolution=year_evolution_events if year_evolution_events else None,
            # v9新增字段
            intent_type=intent.intent_type.value,
            rules_triggered=rule_result.triggered_rules,
            generation_strategy=intent.strategy.value,
            # [v10] 新增字段
            butterfly_approval=butterfly_approval,
            narrative_review=bg_results["review"],
            task_board=task_board_result,
            curator=bg_results["curator"],
            lessons_injected=bool(eng.narrative_reviewer and eng.narrative_reviewer.lessons),
            # [v10+] 新增字段
            foreshadow=foreshadow_result,
            continuity_audit=bg_results["audit"],
            # [v10++] Agent 自主记忆管理（MemGPT/Letta 式）
            autonomous_memory=amm_result,
            # [v10++] 角色动态状态统计（CHIRON 式）
            character_state_stats=eng.character_state_manager.get_stats() if eng.character_state_manager else {},
            # [v10+] 叙事类型感知（GraphRAG 动态启停）
            scene_type=scene_type.value if scene_type is not None else None,
            scene_stats=eng.scene_detector.get_stats() if eng.scene_detector else {},
            # [v10+++] 多智能体分工叙事（Agents' Room 式）
            multi_agent_narrative={
                "used": multi_agent_used,
                "reason": multi_agent_reason,
                "stats": eng.multi_agent_narrative.get_stats() if eng.multi_agent_narrative else {},
            },
        )

    # ── [重构] 从 process() 抽取的子方法 ──────────────────────

    def _build_plot_continuity_rules(self) -> str:
        """[v12.5] 剧情承接铁律（通用，适用于所有世界）。

        设计思路：不依赖关键词/词表判定场景（治标且误伤），而是让 LLM 严格
        以玩家输入为最高剧情指令，结合上下文（当前 location/时间/上一轮结尾）
        合理承接剧情。这是普适的写作规则，不含任何世界专属元素，对所有世界安全。

        替代 v12.4 的两界关键词判定方案（已废弃）。
        """
        return (
            "【剧情承接铁律 - 最高优先级】\n"
            "1. 玩家输入是剧情的最高指令：你必须严格围绕玩家输入描述的情节展开，"
            "不得擅自增加与输入冲突的剧情，不得忽略玩家输入中的关键情节。\n"
            "2. 场景连续性：叙事必须严格承接当前状态（玩家所在位置、当前时间、"
            "事件进展）与上一轮剧情结尾。若玩家输入明确描述了场景转换"
            "（如\"回到某地\"\"来到某地\"\"醒来\"\"穿越\"\"离开\"），叙事必须跟随转换"
            "并在后续保持该场景；若玩家未明确切换场景，必须停留在当前场景，"
            "不得无故跳转。\n"
            "3. 禁止与输入矛盾的情节：不得自行制造与玩家输入或上下文矛盾的"
            "场景跳转、地点变换、时间跳跃、人物行为。\n"
            "4. 【禁止编造前史】不得虚构玩家输入和上下文历史中**从未发生过**的"
            "过去经历来充当角色的背景或解释现状。例如：上下文从未提到主角打工赚钱，"
            "就不得编造\"昨天在码头扛麻袋挣了钱\"；主角物品的来源必须依据既有剧情"
            "（如特殊能力、NPC赠予、剧情事件），不确定时写\"来历不明\"或留白，"
            "绝不自行编造经历圆场。\n"
            "5. 创作自由边界：你的自由度仅限于细节描写、对话、心理活动、动作展开，"
            "不得改变玩家输入确定的地点、时间线、核心事件和世界归属。"
        )

    def _build_fixed_prompt(self, player_input: str, rule_result) -> tuple:
        """构建回合固定 prompt，注入所有上下文（世界观/规则/风格/记忆/伏笔等）。

        返回 (fixed_prompt, scene_type, time_anchor)。
        - fixed_prompt：不含时间锚点（时间锚点改为独立 system message 注入，避免被大量上下文淹没）
        - scene_type 为 None 表示未检测到特定场景类型。
        - time_anchor：抽离的时间锚点字符串（叙事时间追踪 + 玩家输入时间跳跃提示），无则为空串。
        """
        eng = self.engine

        # 构建基础 prompt
        fixed_prompt = eng._get_fixed_prompt()
        time_context = eng._get_time_context()

        # [Bug v9.1] 玩家输入时间跳跃预检测
        player_time_hint = self._build_player_time_skip_hint(player_input)
        if player_time_hint:
            logger.info("Player time skip hint injected: %s", player_time_hint[:120])

        # [P3-A] 时间锚点抽离：不再混入 fixed_prompt，改为独立 system message 注入。
        # [P3-B] 状态快照同样抽离，与时间锚点一起作为独立 system message 注入。
        # 原 fixed_prompt 把时间/状态与世界观/规则/记忆等大量上下文拼在一起，
        # 关键锚点信息容易被 LLM 忽略，导致叙事时间线倒退或状态脱节。
        state_snapshot = self._build_state_snapshot(eng)
        _time_anchor_parts = []
        if state_snapshot:
            _time_anchor_parts.append(state_snapshot)
        if time_context:
            _time_anchor_parts.append(time_context)
        if player_time_hint:
            _time_anchor_parts.append(player_time_hint)
        time_anchor = "\n\n".join(_time_anchor_parts) if _time_anchor_parts else ""

        # [v11.1] 注入世界观补充设定
        world_intro = (eng.world_def or {}).get("world_intro", "").strip() if eng.world_def else ""
        if world_intro:
            intro_hint = f"【世界观补充设定 - 必须遵循】\n{world_intro[:400]}"
            fixed_prompt = fixed_prompt + "\n\n" + intro_hint if fixed_prompt else intro_hint

        # v9新增：注入规则引擎上下文
        rules_context = self.rules_engine.get_world_context_summary(
            eng.player_state, eng.world_state, eng.npc_states
        )
        if rules_context:
            fixed_prompt = fixed_prompt + "\n\n" + rules_context if fixed_prompt else rules_context

        # [v10.7] 空间一致性检查：注入否定提示
        spatial_hint = self._check_spatial_consistency(player_input)
        if spatial_hint:
            fixed_prompt = fixed_prompt + "\n\n" + spatial_hint if fixed_prompt else spatial_hint
            logger.info("Spatial consistency hint injected: %s...", spatial_hint[:80])

        # [Bug] 注入叙事风格+视角指令
        try:
            world_style = ""
            if eng.world_def:
                world_style = eng.world_def.get("world_type", "")
            style_instruction = eng.narrative.style_manager.get_style_instruction(world_style)
            if style_instruction:
                fixed_prompt = fixed_prompt + "\n\n" + style_instruction if fixed_prompt else style_instruction
        except Exception:
            pass

        # v9新增：注入社会网络上下文
        social_context = self.social_network.get_network_summary(eng.npc_states)
        if social_context:
            fixed_prompt = fixed_prompt + "\n\n" + social_context if fixed_prompt else social_context

        # v9新增：叙事规则提示
        if rule_result.narrative_hints:
            hints_text = "\n".join(f"- {h}" for h in rule_result.narrative_hints)
            fixed_prompt = fixed_prompt + f"\n\n【世界规则引擎判定】\n{hints_text}"

        # [v10] 注入闭环学习教训
        if eng.narrative_reviewer and eng.narrative_reviewer.lessons:
            lessons_text = eng.narrative_reviewer.get_lessons_for_prompt(max_lessons=3)
            if lessons_text:
                fixed_prompt = fixed_prompt + "\n\n" + lessons_text

        # [v10] 注入工作记忆上下文
        if eng.memory:
            working_memory = eng.memory.get_working_memory_context(max_items=2)
            if working_memory:
                fixed_prompt = fixed_prompt + "\n\n" + working_memory

        # [v10+] 注入活跃伏笔上下文
        if eng.foreshadow_lifecycle:
            hooks_text = eng.foreshadow_lifecycle.get_hooks_for_prompt(max_hooks=3)
            if hooks_text:
                fixed_prompt = fixed_prompt + "\n\n" + hooks_text

        # [v10++] 注入角色动态状态（CHIRON 式）
        character_state_text = self._build_character_state_context(eng)
        if character_state_text:
            fixed_prompt = fixed_prompt + "\n\n" + character_state_text

        # [v1.3] 注入 MemoryBrief 常驻摘要
        try:
            if eng.memory_brief:
                brief_text = eng.memory_brief.get_briefs_for_prompt()
                if brief_text:
                    fixed_prompt = fixed_prompt + "\n\n" + brief_text if fixed_prompt else brief_text
        except Exception as e:
            logger.debug("MemoryBrief injection failed: %s", e, exc_info=True)

        # [v10+] 叙事类型感知：检测当前场景类型
        scene_result = self._detect_scene_type(eng, player_input, fixed_prompt)
        scene_type = scene_result.scene_type if scene_result else None
        if scene_type is not None:
            logger.info(
                "Scene detected: type=%s confidence=%.2f dynamic=%s",
                scene_type.value, scene_result.confidence, scene_result.is_dynamic,
            )
            scene_hint = self._build_scene_hint(scene_result)
            if scene_hint:
                fixed_prompt = fixed_prompt + "\n\n" + scene_hint

        # [v12.5] 剧情承接铁律（通用，适用于所有世界）：让 LLM 严格以玩家输入为
        # 最高剧情指令，结合上下文合理承接，取代 v12.4 的关键词场景判定方案
        continuity = self._build_plot_continuity_rules()
        if continuity:
            fixed_prompt = fixed_prompt + "\n\n" + continuity

        return fixed_prompt, scene_type, time_anchor

    def _build_state_snapshot(self, eng) -> str:
        """[P3-B] 构建当前状态快照，让 LLM 清晰感知游戏当前态。

        与散落在 world_text/npc_text/player_text 中的详细状态不同，此快照只提取
        最关键的"当前锚点"信息（天数/时段/位置/目标），作为独立 system message
        注入，避免叙事与状态脱节（如玩家已在B地，叙事却说还在A地）。
        """
        parts = ["【当前状态快照 - 叙事必须遵循】"]
        try:
            ws = eng.world_state
            if ws is not None:
                # 游戏进度
                progress_parts = [f"第{ws.current_day}天"]
                era = getattr(ws, "era_name", "")
                if era:
                    progress_parts.append(f"{era}{getattr(ws, 'era_year', 1)}年")
                season = getattr(ws, "season", "")
                if season:
                    progress_parts.append(season)
                weather = getattr(ws, "weather", "")
                if weather:
                    progress_parts.append(weather)
                parts.append("游戏进度：" + " · ".join(progress_parts))
                # 当前时间段
                cur_time = getattr(ws, "current_time", "")
                if cur_time:
                    parts.append(f"当前时段：{cur_time}")
        except Exception as e:
            logger.debug("State snapshot world_state failed: %s", e, exc_info=True)
        try:
            ps = eng.player_state
            if ps is not None:
                loc = getattr(ps, "location", "")
                if loc:
                    parts.append(f"玩家位置：{loc}")
                goal = getattr(ps, "current_goal", "")
                if goal:
                    parts.append(f"当前目标：{goal}")
        except Exception as e:
            logger.debug("State snapshot player_state failed: %s", e, exc_info=True)
        if len(parts) <= 1:
            return ""
        parts.append("叙事必须与上述当前状态保持一致，禁止倒退到之前的时间、地点或场景。")
        return "\n".join(parts)

    def _run_post_turn_background(self, player_input: str, narrative: str,
                                  rule_result, impact, world_event) -> dict:
        """执行回合后所有后台异步任务（Step 9 ~ Step 14.9）。

        返回各任务的同步结果（异步任务返回 deferred 标记）。
        """
        eng = self.engine

        # Step 9: RAG记忆存储 → 后台异步
        self._bg(self._store_to_rag_v10, narrative, player_input, impact)

        # Step 10: GraphRAG构建 → 后台异步
        self._bg(self._build_graph_rag, narrative)

        # Step 10.1: 角色动态状态分析（CHIRON 式）→ 后台异步
        self._bg(self._analyze_character_states, narrative)

        # Step 14.1: NPC 程序性记忆记录
        self._record_npc_procedural_memory(narrative, player_input)

        # Step 14.1.1: NPC 技能自学（Voyager/Hermes 式）→ 后台异步
        self._bg(self._record_npc_skill_learning, narrative, player_input)

        # Step 14.3: 叙事回顾（闭环学习）→ 异步后台执行
        self._bg(self._run_narrative_review_sync)

        # Step 14.4: 记忆 Curator 整理 → 异步后台执行
        self._bg(self._run_memory_curator_sync)

        # Step 14.4.1: MemoryBrief 增量更新 + 睡眠巩固 → 异步后台执行
        self._bg(self._run_memory_brief_update_sync)

        # Step 14.6: 多维度连续性审计 → 异步后台执行
        self._bg(self._run_continuity_audit_sync)

        # Step 14.8: NPC 性格演化检测 → 后台异步
        self._bg(self._check_personality_evolution, narrative)

        # Step 14.9: 因果链节点记录 → 后台异步
        self._bg(
            self._record_causal_node,
            player_input, narrative, rule_result, impact, world_event
        )

        # Step 14.10: [2026-08-10] 每轮生成 300 字剧情概况（分层记忆头部）→ 后台异步
        try:
            self._bg(self._generate_narrative_summary, player_input, narrative)
        except Exception as e:
            logger.debug("summary task schedule failed: %s", e)

        return {
            "review": {"skipped": True, "reason": "async_deferred"},
            "curator": {"skipped": True, "reason": "async_deferred"},
            "audit": {"skipped": True, "reason": "async_deferred"},
        }

    def _detect_new_npcs(self, narrative: str):
        """[已废弃 2026-08-10] B+C 自动检测误报率高（动词短语被当人名），
        用户决定回归手动建档（给AI下命令 / 添加角色）。
        保留方法体为空，避免历史引用报错。"""
        return

    # ── [2026-08-10] 剧情概况分层记忆（用户方案）──

    def _get_summary_store(self, eng):
        """懒加载概况存储（挂到引擎，世界切换时重建）。"""
        store = getattr(eng, "_narrative_summaries", None)
        wid = getattr(eng, "_narrative_summaries_world", None)
        if store is None or wid != getattr(eng, "current_world_id", None):
            try:
                from .narrative_summaries import NarrativeSummaryStore
                store = NarrativeSummaryStore(
                    eng.current_world_id, eng.save_manager.base_dir
                )
            except Exception as e:
                logger.warning("NarrativeSummaryStore 初始化失败: %s", e)
                store = None
            eng._narrative_summaries = store
            eng._narrative_summaries_world = getattr(eng, "current_world_id", None)
        return store

    def _build_summary_head(self, eng) -> str:
        """构建普通叙事的概况/纪要头部（早期剧情脉络）。"""
        store = self._get_summary_store(eng)
        if not store:
            return ""
        try:
            # 头部预算约 6.5 万中文字（≈10 万 token），剩余给尾部原文/设定/档案
            return store.build_head_text(max_chars=65000)
        except Exception as e:
            logger.debug("build_summary_head 失败: %s", e)
            return ""

    def _generate_narrative_summary(self, player_input: str, narrative: str):
        """每轮叙事 → 300 字剧情概况（统一走主 LLM，失败静默跳过）。"""
        eng = self.engine
        store = self._get_summary_store(eng)
        if not store or not narrative:
            return
        # [2026-08-10] 备用模型已禁用，统一走主 LLM
        llm = eng.llm
        if not llm:
            return
        try:
            prompt = (
                "请为以下游戏叙事写一段约300字的剧情概况。\n"
                "\n"
                "【要求】\n"
                "1. 包含：时间地点、关键事件、出场人物及关系变化、伏笔悬念\n"
                "2. 只陈述事实脉络，不要文学化描写、不要抒情\n"
                "3. 250-350字\n"
                "\n"
                f"【玩家行动】{player_input[:1500]}\n"  # [2026-08-10] 200→1500：概况需含完整玩家行动，截断会丢关键情节
                f"【叙事】{narrative[:4000]}\n"
                "\n"
                "直接输出概况："
            )
            summary = llm.chat(prompt, temperature=0.3, max_tokens=500)
            summary = (summary or "").strip()
            if len(summary) < 50:
                return
            turn = eng.meta.current_turn if eng.meta else 0
            day = eng.world_state.current_day if eng.world_state else 1
            store.add_summary(turn, day, summary)
            logger.info("剧情概况已生成: 轮%d 第%d天 (%d字)", turn, day, len(summary))
            self._maybe_compress_chunks(eng, store)
        except Exception as e:
            logger.debug("narrative summary 生成失败: %s", e)

    def _maybe_compress_chunks(self, eng, store):
        """概况满 100 条 → 主 LLM 压缩成 ~2 万字 chunk，成功后删除已压缩概况。"""
        try:
            rows = store.uncompressed_summaries()
            if len(rows) < 100:
                return
            text = "\n".join(
                f"[第{r['day']}天 · 轮{r['turn']}] {r['summary']}" for r in rows
            )
            prompt = (
                "请将以下100条游戏剧情概况合并压缩成一份约2万字的【剧情纪要】，供后续创作参考。\n"
                "\n"
                "【要求】\n"
                "1. 严格按时间线组织，保留关键事件、人物关系变化、重要伏笔的完整脉络\n"
                "2. 保留具体人名、地名、因果链；压缩比为原文的60%-70%（100条概况约3万字 → 约2万字）\n"
                "3. 只陈述事实脉络，不文学化\n"
                "\n"
                f"【剧情概况（100条）】\n{text}\n"
                "\n"
                "直接输出剧情纪要："
            )
            chunk_text = eng.llm.chat(prompt, temperature=0.3, max_tokens=30000)
            chunk_text = (chunk_text or "").strip()
            if len(chunk_text) < 2000:
                logger.warning("chunk 压缩结果过短(%d字)，放弃本次压缩", len(chunk_text))
                return
            start_id = rows[0]["id"]
            end_id = rows[-1]["id"]
            store.add_chunk(start_id, end_id, chunk_text)
            store.delete_summaries_upto(end_id)
            logger.info("剧情纪要已生成: 概况#%d~#%d (%d字)", start_id, end_id, len(chunk_text))
        except Exception as e:
            logger.warning("chunk 压缩失败: %s", e)

    def _generate_from_template(self, intent, player_input, npc_names, fixed_prompt, scene_type=None, time_anchor: str = "") -> tuple:
        """模板生成（不需要LLM）"""
        eng = self.engine
        narrative = ""
        options = []
        response = {}

        if intent.intent_type == IntentType.OBSERVE:
            result = TemplateGenerator.generate_observe(
                eng.player_state, eng.world_state, eng.npc_states
            )
            narrative = result["narrative"]
            options = result["options"]
        elif intent.intent_type == IntentType.REST:
            result = TemplateGenerator.generate_rest(eng.player_state)
            narrative = result["narrative"]
            options = result["options"]
        elif intent.intent_type == IntentType.MANAGE:
            result = TemplateGenerator.generate_manage(eng.player_state)
            narrative = result["narrative"]
            options = result["options"]
        else:
            # 未知意图，回退到LLM
            return self._generate_full_llm(player_input, npc_names, fixed_prompt, scene_type=scene_type, time_anchor=time_anchor)

        response = {
            "narrative": narrative, "options": options,
            "status_changes": {}, "new_effects": [], "removed_effects": [],
            "relation_changes": {}, "identity_changes": {},
        }
        # [Bug] 模板生成也要触发流式回调，否则 WebSocket 流式模式收不到内容
        if eng._stream_callback and narrative:
            try:
                eng._stream_callback(narrative)
            finally:
                eng._stream_callback(None)
        # [Bug] 模板路径不走 LLM，_last_context_debug 不会被 generate_narrative_stream 设置
        # 这里填充一个最小化的调试快照，避免 /api/context-debug 在 OBSERVE/REST/MANAGE 回合返回空
        if eng.player_agent is not None:
            try:
                from .context_budget import estimate_tokens
                history = eng.narrative_history or []
                history_text = "\n".join(str(h) for h in history[-5:]) if history else ""
                npc_states = eng.npc_states or {}
                npc_text = "\n".join(
                    f"- {n.name}({n.current_location})" for n in npc_states.values()
                ) if npc_states else ""
                fixed_prompt_text = fixed_prompt or ""
                eng.player_agent._last_context_debug = {
                    "total_estimated_tokens": estimate_tokens(fixed_prompt_text),
                    "world_context": "",
                    "world_tokens": 0,
                    "npc_context": npc_text[:300],
                    "npc_count": len(npc_states),
                    "npc_tokens": estimate_tokens(npc_text),
                    "player_context": "",
                    "player_tokens": 0,
                    "history_turns": len(history),
                    "history_tokens": estimate_tokens(history_text),
                    "lorebook_matches": 0,
                    "lorebook_entries": [],
                    "lorebook_tokens": 0,
                    "rag_results": [],
                    "rag_tokens": 0,
                    "fixed_prompt": fixed_prompt_text[:200],
                    "fixed_prompt_tokens": estimate_tokens(fixed_prompt_text),
                    "max_context": 0,
                    "context_engine_used": False,
                    "cache_stats": None,
                    "strategy": "template",
                    "intent_type": intent.intent_type.value if hasattr(intent.intent_type, "value") else str(intent.intent_type),
                }
            except Exception as _e:
                logger.debug("Failed to populate context_debug for template path: %s", _e)
        return narrative, options, response

    def _generate_light_llm(self, player_input, npc_names, fixed_prompt, scene_type=None, time_anchor: str = "") -> tuple:
        """轻量LLM调用

        [A] 修复：原先构建的 light_prompt 局部变量是死代码（构建后从未传入 LLM）。
        实际行为：使用完整 fixed_prompt 上下文，通过 max_context=2048 限制 token 上限。
        现在追加轻量叙事指令，让 LLM 知道这是探索/旅行/制造类动作，聚焦描述核心事件。
        """
        eng = self.engine
        narrative = ""
        options = []
        response = {}

        # [A] 给 fixed_prompt 追加轻量叙事指令（局部覆盖，字符串不可变不影响调用者）
        # 让 LLM 知道这是轻量模式，聚焦核心事件，避免按完整叙事的 800 字要求过度展开
        light_hint = (
            "\n\n【轻量叙事模式】当前是探索/旅行/制造类动作，"
            "请聚焦描述核心事件发展和玩家行动的直接结果，"
            "不需要过多铺垫、环境描写或心理活动。"
        )
        fixed_prompt = (fixed_prompt + light_hint) if fixed_prompt else light_hint

        # [Bug v9.1] 历史窗口与上下文预算从 config 读取
        # v9 硬编码 [-3:] + 2048 token，导致 recent 层被压缩到 500 token，
        # 上一轮 AI 输出的时间锚点丢失，引发上下文时间线倒退
        _light_history = eng.narrative_history[-5:]  # 保留最近5条（含上一轮完整 AI 输出）
        _light_max_ctx = getattr(eng, 'light_llm_max_context', 8000)  # 默认 8000，可配置

        try:
            if eng._stream_callback:
                token_gen = eng.player_agent.generate_narrative_stream(
                    eng.player_state, player_input,
                    world_state=eng.world_state.model_dump() if eng.world_state else None,
                    day=eng.world_state.current_day if eng.world_state else 1,
                    npc_states=eng.npc_states,
                    narrative_history=_light_history,
                    fixed_prompt=fixed_prompt,
                    max_context=_light_max_ctx,
                    scene_type=scene_type,
                    narrative_max_chars=getattr(eng, 'narrative_max_chars', 1000),
                    time_anchor=time_anchor,
                )
                for token in token_gen:
                    if token:
                        narrative += token
                        eng._stream_callback(token)
                # [v11-fix] 不在此处发 stream_end，等字数检测和续写完成后再发
                narrative = self._extract_narrative_from_json(narrative)
                if narrative and eng.player_agent:
                    narrative = eng.player_agent._clean_narrative(narrative)
                # [v12] 清除AI时间跳跃标记（在时间感知处理之前）
                if narrative:
                    narrative = self._strip_time_skip_tag(narrative)

                # [v11-fix] 轻量模式字数不足续写：在 stream_end 之前完成，最多3轮
                if narrative:
                    _lm_max = getattr(eng, 'narrative_max_chars', 1000)
                    _lm_min = int(_lm_max * 0.66)
                    _lm_count = len(narrative)
                    _retry = 0
                    while _lm_count > 0 and _lm_count < int(_lm_min * 0.9) and _retry < 3:
                        logger.info(
                            "轻量LLM叙事字数不足 (%d < %d 的90%%)，第%d轮续写补充",
                            _lm_count, _lm_min, _retry + 1,
                        )
                        try:
                            # [v12.3] 续写防重复：明确"已写内容"是锚点而非重写起点，并把当前叙事注入历史
                            _cont_tail = narrative[-300:]
                            _lm_continue = (
                                f"[续写] 上一段叙事太短（只有{_lm_count}字），未达要求。"
                                f"以下是【已写内容】的结尾部分：\n"
                                f"……{_cont_tail}\n"
                                f"请严格接着【已写内容】结尾之后的情节继续写，至少再写{_lm_min - _lm_count}字。"
                                f"【铁律】绝对禁止重复、复述、改写【已写内容】中出现过的任何句子、场景、动作或对话，"
                                f"直接从结尾之后的新情节开始，不要回头描写已发生的事。"
                            )
                            _cont_history = list(_light_history or [])
                            _cont_history.append({"player_input": player_input, "text": narrative[-1500:]})
                            token_gen2 = eng.player_agent.generate_narrative_stream(
                                eng.player_state, _lm_continue,
                                world_state=eng.world_state.model_dump() if eng.world_state else None,
                                day=eng.world_state.current_day if eng.world_state else 1,
                                npc_states=eng.npc_states,
                                narrative_history=_cont_history,
                                fixed_prompt=fixed_prompt,
                                max_context=_light_max_ctx,
                                scene_type=scene_type,
                                narrative_max_chars=_lm_min,
                                time_anchor=time_anchor,
                            )
                            for token in token_gen2:
                                if token:
                                    narrative += token
                                    eng._stream_callback(token)
                        except Exception as e2:
                            logger.warning("轻量LLM叙事续写失败: %s", e2, exc_info=True)
                            break
                        _lm_count = len(narrative)
                        _retry += 1
                    logger.info("轻量LLM续写完成，总字数: %d", len(narrative))

                # [v11-fix] 所续写完成后再发 stream_end
                eng._stream_callback(None)
            else:
                response = eng.player_agent.generate_full_response(
                    eng.player_state, player_input,
                    world_state=eng.world_state.model_dump() if eng.world_state else None,
                    day=eng.world_state.current_day if eng.world_state else 1,
                    npc_names=npc_names,
                    npc_states=eng.npc_states,
                    narrative_history=_light_history,
                    fixed_prompt=fixed_prompt,
                    max_context=_light_max_ctx,
                    strip_gray=eng._get_strip_gray_narrative(),
                    scene_type=scene_type,
                    narrative_max_chars=getattr(eng, 'narrative_max_chars', 1000),
                    time_anchor=time_anchor,
                )
                narrative = response.get("narrative", "")
                options = response.get("options", [])
                # [v11-fix] 非流式模式也检测字数不足：低于90%则重新生成，最多3轮，取最长结果
                if narrative:
                    _lm_max = getattr(eng, 'narrative_max_chars', 1000)
                    _lm_min = int(_lm_max * 0.66)
                    _lm_count = len(narrative)
                    _retry = 0
                    while _lm_count > 0 and _lm_count < int(_lm_min * 0.9) and _retry < 3:
                        logger.info(
                            "轻量LLM非流式叙事字数不足 (%d < %d 的90%%)，第%d轮重新生成",
                            _lm_count, _lm_min, _retry + 1,
                        )
                        try:
                            response2 = eng.player_agent.generate_full_response(
                                eng.player_state, player_input,
                                world_state=eng.world_state.model_dump() if eng.world_state else None,
                                day=eng.world_state.current_day if eng.world_state else 1,
                                npc_names=npc_names,
                                npc_states=eng.npc_states,
                                narrative_history=_light_history,
                                fixed_prompt=fixed_prompt,
                                max_context=_light_max_ctx,
                                strip_gray=eng._get_strip_gray_narrative(),
                                scene_type=scene_type,
                                narrative_max_chars=_lm_max,
                                time_anchor=time_anchor,
                            )
                            narrative2 = response2.get("narrative", "")
                            if len(narrative2) > len(narrative):
                                narrative = narrative2
                                response = response2
                        except Exception as e2:
                            logger.warning("轻量LLM叙事重新生成失败: %s", e2, exc_info=True)
                            break
                        _lm_count = len(narrative)
                        _retry += 1
        except Exception as e:
            logger.warning("Light LLM generation failed: %s", e, exc_info=True)
            narrative = ""
            # [v11-fix] 异常时也要发 stream_end，否则消费者永远等不到结束信号
            if eng._stream_callback:
                eng._stream_callback(None)

        if not narrative:
            return self._generate_full_llm(player_input, npc_names, fixed_prompt, scene_type=scene_type, time_anchor=time_anchor)

        if not options:
            options = eng.player_agent._fallback_options(eng.player_state)

        response = {
            "narrative": narrative, "options": options,
            "status_changes": response.get("status_changes", {}),
            "new_effects": response.get("new_effects", []),
            "removed_effects": response.get("removed_effects", []),
            "relation_changes": response.get("relation_changes", {}),
            "identity_changes": response.get("identity_changes", {}),
        }
        return narrative, options, response

    def _generate_full_llm(self, player_input, npc_names, fixed_prompt, scene_type=None, time_anchor: str = "") -> tuple:
        """[v10.6] 完整LLM调用 — 两阶段流式生成：
        阶段1：流式输出叙事文本（逐字打字效果）
        阶段2：生成选项/状态变化等结构化元数据"""
        eng = self.engine
        narrative = ""
        options = []
        response = {}

        if eng._stream_callback:
            # ── 阶段 1：流式生成叙事 ──
            _narrative_max = getattr(eng, 'narrative_max_chars', 1000)
            _narrative_min = int(_narrative_max * 0.66)  # [v11] 与 prompt "至少80%" 对齐
            try:
                token_gen = eng.player_agent.generate_narrative_stream(
                    eng.player_state, player_input,
                    world_state=eng.world_state.model_dump() if eng.world_state else None,
                    day=eng.world_state.current_day if eng.world_state else 1,
                    npc_states=eng.npc_states,
                    narrative_history=eng.narrative_history,
                    fixed_prompt=fixed_prompt,
                    max_context=eng._get_max_context(),
                    scene_type=scene_type,
                    narrative_max_chars=_narrative_max,
                    time_anchor=time_anchor,
                    summary_head=self._build_summary_head(eng),
                )
                for token in token_gen:
                    if token:
                        narrative += token
                        eng._stream_callback(token)

                # [v11-fix] 字数不足自动续写：叙事低于目标的90%就续写，最多3轮直到达标
                # 注意：stream_end 必须在续写完成之后发送，否则消费者退出后续写内容丢失
                _narr_char_count = len(narrative)
                _retry = 0
                while (_narr_char_count > 0
                        and _narr_char_count < int(_narrative_min * 0.9)
                        and _retry < 3):
                    logger.info(
                        "叙事字数不足 (%d < %d 的90%%)，第%d轮自动续写补充",
                        _narr_char_count, _narrative_min, _retry + 1,
                    )
                    try:
                        # [Bug#10] 续写必须保留系统prompt和身份锚定，使用 generate_narrative_stream
                        # 而非直接调用 llm.chat_stream（后者缺少世界观/NPC/身份上下文）
                        # [v12.3] 续写防重复：明确"已写内容"是锚点而非重写起点，并把当前叙事注入历史
                        _cont_tail = narrative[-300:]
                        continue_input = (
                            f"[续写] 上一段叙事太短（只有{_narr_char_count}字），未达要求。"
                            f"以下是【已写内容】的结尾部分：\n"
                            f"……{_cont_tail}\n"
                            f"请严格接着【已写内容】结尾之后的情节继续写，至少再写{_narrative_min - _narr_char_count}字。"
                            f"【铁律】绝对禁止重复、复述、改写【已写内容】中出现过的任何句子、场景、动作或对话，"
                            f"直接从结尾之后的新情节开始，不要回头描写已发生的事。"
                        )
                        _cont_history = list(eng.narrative_history or [])
                        _cont_history.append({"player_input": player_input, "text": narrative[-1500:]})
                        continue_gen = eng.player_agent.generate_narrative_stream(
                            eng.player_state, continue_input,
                            world_state=eng.world_state.model_dump() if eng.world_state else None,
                            day=eng.world_state.current_day if eng.world_state else 1,
                            npc_states=eng.npc_states,
                            narrative_history=_cont_history,
                            fixed_prompt=fixed_prompt,
                            max_context=eng._get_max_context(),
                            scene_type=scene_type,
                            narrative_max_chars=_narrative_min,
                            time_anchor=time_anchor,
                            summary_head=self._build_summary_head(eng),
                        )
                        for token in continue_gen:
                            if token:
                                narrative += token
                                eng._stream_callback(token)
                        logger.info("第%d轮续写完成，总字数: %d", _retry + 1, len(narrative))
                    except Exception as e:
                        logger.warning("叙事续写失败: %s", e, exc_info=True)
                        break
                    _narr_char_count = len(narrative)
                    _retry += 1
            except Exception as e:
                logger.warning("流式叙事生成失败: %s", e, exc_info=True)

            # [v11-fix] 无论成功/失败/续写，都必须发 stream_end，否则消费者永远等不到结束信号
            eng._stream_callback(None)

            # ── 阶段 2：用叙事文本生成选项/元数据 ──
            try:
                response = eng.player_agent.generate_metadata_from_narrative(
                    eng.player_state, player_input, narrative,
                    npc_names=npc_names,
                    narrative_history=eng.narrative_history,
                    world_state=eng.world_state.model_dump() if eng.world_state else None,
                    npc_states=eng.npc_states,
                )
            except Exception as e:
                logger.warning("Metadata generation failed, using fallback: %s", e, exc_info=True)
                response = {"narrative": narrative}
        else:
            # ── 非流式模式（HTTP 调用）：一次调用同时返回叙事和选项 ──
            response = eng.player_agent.generate_full_response(
                eng.player_state, player_input,
                world_state=eng.world_state.model_dump() if eng.world_state else None,
                day=eng.world_state.current_day if eng.world_state else 1,
                npc_names=npc_names,
                npc_states=eng.npc_states,
                narrative_history=eng.narrative_history,
                fixed_prompt=fixed_prompt,
                max_context=eng._get_max_context(),
                strip_gray=eng._get_strip_gray_narrative(),
                scene_type=scene_type,
                narrative_max_chars=getattr(eng, 'narrative_max_chars', 1000),
                time_anchor=time_anchor,
            )
            narrative = response.get("narrative", "")
            # [v11] 非流式模式也检测字数不足：低于90%则重新生成，最多3轮，取最长结果
            _narrative_max = getattr(eng, 'narrative_max_chars', 1000)
            _narrative_min = int(_narrative_max * 0.66)  # [v11] 与 prompt "至少80%" 对齐
            _narr_char_count = len(narrative)
            _retry = 0
            while (_narr_char_count > 0
                    and _narr_char_count < int(_narrative_min * 0.9)
                    and _retry < 3):
                logger.info(
                    "非流式叙事字数不足 (%d < %d 的90%%)，第%d轮重新生成",
                    _narr_char_count, _narrative_min, _retry + 1,
                )
                try:
                    response_new = eng.player_agent.generate_full_response(
                        eng.player_state, player_input,
                        world_state=eng.world_state.model_dump() if eng.world_state else None,
                        day=eng.world_state.current_day if eng.world_state else 1,
                        npc_names=npc_names,
                        npc_states=eng.npc_states,
                        narrative_history=eng.narrative_history,
                        fixed_prompt=fixed_prompt,
                        max_context=eng._get_max_context(),
                        strip_gray=eng._get_strip_gray_narrative(),
                        scene_type=scene_type,
                        narrative_max_chars=_narrative_max,
                        time_anchor=time_anchor,
                    )
                    narrative_new = response_new.get("narrative", "")
                    if len(narrative_new) > len(narrative):
                        narrative = narrative_new
                        response = response_new
                except Exception as e:
                    logger.warning("非流式叙事重新生成失败: %s", e, exc_info=True)
                    break
                _narr_char_count = len(narrative)
                _retry += 1

        narrative = response.get("narrative", narrative)
        options = response.get("options", [])

        if not options:
            options = eng.player_agent._fallback_options(eng.player_state)

        return narrative, options, response

    # ── [v10+++] 多智能体分工叙事（Agents' Room 式） ─────────

    def _is_critical_plot(self, player_input: str, intent, fixed_prompt: str) -> tuple[bool, str]:
        """[v10+++] 检测当前回合是否为关键剧情，决定是否启用多智能体叙事。

        判定标准（满足至少2个条件才触发，避免过于频繁）：
          1. 玩家输入包含重要关键词（杀/死/战/背叛等）
          2. 蝴蝶效应影响分 > 触发阈值（预评估，结果缓存供 Step 7 复用）
          3. 伏笔回收时刻（玩家输入提及已有活跃伏笔）
          4. 连续被动后的转折点（被动连击达阈值后的首次主动行动）
          5. 涉及多个NPC在场的复杂场景

        返回 (is_critical, reason)
        """
        eng = self.engine

        # 获取触发灵敏度配置
        sensitivity = getattr(eng, 'multi_agent_sensitivity', 'normal')  # high/normal/low
        if sensitivity == 'low':
            required_conditions = 3
            butterfly_threshold = 8
        elif sensitivity == 'high':
            required_conditions = 1
            butterfly_threshold = 4
        else:
            required_conditions = 2
            butterfly_threshold = 6

        conditions_met = 0
        matched_reasons = []

        # 1. 关键词检测
        if any(kw in player_input for kw in CRITICAL_PLOT_KEYWORDS):
            conditions_met += 1
            matched_reasons.append("keyword")

        # 3. 伏笔回收时刻：玩家输入提及已有活跃伏笔
        if eng.foreshadow_lifecycle:
            try:
                active_hooks = eng.foreshadow_lifecycle.get_active_hooks()
                for hook_data in active_hooks:
                    hook_content = hook_data.get("content", "")
                    if not hook_content:
                        continue
                    keywords = [w for w in hook_content.split() if len(w) >= 2]
                    if any(kw in player_input for kw in keywords[:5]):
                        conditions_met += 1
                        matched_reasons.append("foreshadow_recall")
                        break
            except Exception as e:
                logger.debug("Foreshadow recall detection failed: %s", e, exc_info=True)

        # 4. 连续被动后的转折点
        if (intent.strategy != GenerationStrategy.TEMPLATE
                and self._passive_streak >= PASSIVE_STREAK_THRESHOLD):
            conditions_met += 1
            matched_reasons.append("turning_point")

        # 5. 涉及多个NPC在场
        npc_count = len(getattr(eng, 'npc_states', {})) if hasattr(eng, 'npc_states') else 0
        if npc_count >= 3:
            conditions_met += 1
            matched_reasons.append("multiple_npcs")

        # 2. 蝴蝶效应影响分预评估（最贵，放最后；结果缓存供 Step 7 复用）
        if eng.butterfly and eng.player_state and eng.world_state:
            try:
                self._pre_impact = eng.butterfly.evaluate_impact(
                    eng.player_state, player_input, eng.world_state
                )
                if self._pre_impact.get("impact_score", 0) > butterfly_threshold:
                    conditions_met += 1
                    matched_reasons.append("butterfly_impact")
            except Exception as e:
                logger.debug("Butterfly pre-evaluation failed: %s", e, exc_info=True)
                self._pre_impact = None

        if conditions_met >= required_conditions:
            return True, "+".join(matched_reasons)

        return False, ""

    def _generate_multi_agent(self, player_input, npc_names, fixed_prompt,
                              scene_type=None, critical_reason: str = "",
                              time_anchor: str = "") -> tuple:
        """[v10+++] 多智能体分工叙事生成（Agents' Room 式）。
        流程：情节架构师 → 角色审查员 → 对话撰写师。
        失败时返回空叙事，由调用方回退到单 LLM。"""
        eng = self.engine
        narrative = ""
        options = []
        response = {}

        if not eng.multi_agent_narrative or not eng.multi_agent_narrative.is_available():
            return narrative, options, response

        # 构建上下文（截断尾部近期上下文，避免过长）
        # [P3-A] 时间锚点放在 context 最前面，确保不被尾部截断丢失
        context = fixed_prompt[-2000:] if fixed_prompt else ""
        if time_anchor:
            context = time_anchor + "\n\n" + context

        # 构建角色信息与动态状态
        character_info = self._build_character_info(eng)
        character_state = self._build_character_state_text(eng)

        # 叙事风格
        style = ""
        if eng.narrative and getattr(eng.narrative, "style_manager", None):
            world_style = ""
            if eng.world_def:
                world_style = eng.world_def.get("world_type", "")
            try:
                style = eng.narrative.style_manager.get_style_instruction(world_style)
            except Exception:
                style = ""

        try:
            draft = eng.multi_agent_narrative.generate(
                context=context,
                player_input=player_input,
                character_info=character_info,
                character_state=character_state,
                scene_type=scene_type.value if scene_type else "",
                style=style,
                narrative_max_chars=getattr(eng, 'narrative_max_chars', 2000),
            )
            narrative = draft.final_narrative or ""
        except Exception as e:
            logger.warning("Multi-agent narrative generation failed: %s", e, exc_info=True)
            narrative = ""

        if not narrative:
            return narrative, options, response

        # 清理叙事文本（去除可能的 JSON 包裹/前缀）
        if eng.player_agent:
            # [v12.6] 与 full/light 路径一致：受"允许旁白"设置控制（strip_gray=False 时跳过清理，避免误杀正文）
            if eng._get_strip_gray_narrative():
                narrative = eng.player_agent._clean_narrative(narrative)
            # [v12] 清除AI时间跳跃标记
            if narrative:
                narrative = self._strip_time_skip_tag(narrative)

        # [v12.2] 多智能体字数不足自动续写：叙事低于目标的90%就续写，最多3轮
        # 多智能体 writer 的 max_tokens 已放宽，但模型仍可能提前收尾，需兜底
        _ma_max = getattr(eng, 'narrative_max_chars', 2000)
        _ma_min = int(_ma_max * 0.66)
        _ma_count = len(narrative)
        _ma_retry = 0
        while (_ma_count > 0
                and _ma_count < int(_ma_min * 0.9)
                and _ma_retry < 3):
            logger.info(
                "多智能体叙事字数不足 (%d < %d 的90%%)，第%d轮自动续写补充",
                _ma_count, _ma_min, _ma_retry + 1,
            )
            try:
                # [v12.3] 续写防重复：明确"已写内容"是锚点而非重写起点，
                # 并把当前叙事注入历史，让 LLM 看到完整前文避免重复
                _cont_tail = narrative[-300:]
                continue_input = (
                    f"[续写] 上一段叙事太短（只有{_ma_count}字），未达要求。"
                    f"以下是【已写内容】的结尾部分：\n"
                    f"……{_cont_tail}\n"
                    f"请严格接着【已写内容】结尾之后的情节继续写，至少再写{_ma_min - _ma_count}字。"
                    f"【铁律】绝对禁止重复、复述、改写【已写内容】中出现过的任何句子、场景、动作或对话，"
                    f"直接从结尾之后的新情节开始，不要回头描写已发生的事。"
                )
                _cont_history = list(eng.narrative_history or [])
                _cont_history.append({"player_input": player_input, "text": narrative[-1500:]})
                continue_gen = eng.player_agent.generate_narrative_stream(
                    eng.player_state, continue_input,
                    world_state=eng.world_state.model_dump() if eng.world_state else None,
                    day=eng.world_state.current_day if eng.world_state else 1,
                    npc_states=eng.npc_states,
                    narrative_history=_cont_history,
                    fixed_prompt=fixed_prompt,
                    max_context=eng._get_max_context(),
                    scene_type=scene_type,
                    narrative_max_chars=_ma_min,
                    time_anchor=time_anchor,
                    summary_head=self._build_summary_head(eng),
                )
                for token in continue_gen:
                    if token:
                        narrative += token
                # 注意：不在此处逐 token 回调——多智能体路径在下方一次性
                # _stream_callback(narrative) 发送完整叙事，逐 token 发会造成重复
                logger.info("多智能体第%d轮续写完成，总字数: %d", _ma_retry + 1, len(narrative))
            except Exception as e:
                logger.warning("多智能体叙事续写失败: %s", e, exc_info=True)
                break
            _ma_count = len(narrative)
            _ma_retry += 1

        # 流式回调：一次性发送完整叙事
        if eng._stream_callback and narrative:
            try:
                eng._stream_callback(narrative)
            finally:
                # [v11-fix] 无论是否异常，都必须发 stream_end
                eng._stream_callback(None)

        # 选项：多智能体专注叙事质量，选项走轻量回退
        if eng.player_agent:
            options = eng.player_agent._fallback_options(eng.player_state)

        response = {
            "narrative": narrative, "options": options,
            "status_changes": {}, "new_effects": [], "removed_effects": [],
            "relation_changes": {}, "identity_changes": {},
            "_multi_agent": True,  # 标记多智能体生成
            "_multi_agent_reason": critical_reason,
        }
        return narrative, options, response

    def _build_character_info(self, eng) -> str:
        """[v10+++] 构建角色设定信息，供角色一致性审查员使用。"""
        parts = []
        # 玩家信息
        if eng.player_state:
            ps = eng.player_state
            player_parts = [f"玩家：{ps.name}"]
            if ps.tags:
                player_parts.append(f"标签={'、'.join(ps.tags[:5])}")
            parts.append(" | ".join(player_parts))

        # NPC 信息（最多取 8 个，避免 prompt 过长）
        if eng.npc_states:
            for npc in list(eng.npc_states.values())[:8]:
                try:
                    parts.append(npc.get_identity_summary())
                except Exception:
                    parts.append(npc.name)

        return "\n".join(parts)

    def _build_character_state_text(self, eng) -> str:
        """[v10+++] 构建角色动态状态文本（CHIRON 式），供角色审查员参考。"""
        if not eng.character_state_manager:
            return ""
        parts = []
        if eng.player_state:
            try:
                text = eng.character_state_manager.get_state_for_prompt(eng.player_state.agent_id)
                if text:
                    parts.append(f"{eng.player_state.name}：{text}")
            except Exception:
                pass
        if eng.npc_states:
            for npc_id, npc in list(eng.npc_states.items())[:8]:
                try:
                    text = eng.character_state_manager.get_state_for_prompt(npc_id)
                    if text:
                        parts.append(f"{npc.name}：{text}")
                except Exception:
                    pass
        return "\n".join(parts)

    def _update_social_network(self, player_input: str, narrative: str, eng):
        """更新社会网络"""
        # 检测叙事中提到的NPC
        for npc_id, npc in eng.npc_states.items():
            if npc.name in narrative:
                # NPC被提及，可能触发信息传播
                topic = "player_action"
                if any(kw in player_input for kw in ["告诉", "说", "透露"]):
                    topic = "information"
                elif any(kw in player_input for kw in ["杀", "打", "攻击"]):
                    topic = "conflict"

                self.social_network.add_information(
                    content=narrative[:200],
                    source_id=npc_id,
                    topic=topic,
                    day=eng.world_state.current_day if eng.world_state else 1,
                )

    def _save_snapshot(self, eng, player_input, narrative, rule_result):
        """保存状态快照"""
        try:
            from .state_history import StateHistoryManager
            from pathlib import Path

            if not eng.current_world_id:
                return
            db_path = Path("saves") / eng.current_world_id / "history.db"

            # 确保目录存在
            db_path.parent.mkdir(parents=True, exist_ok=True)

            history_mgr = StateHistoryManager(db_path)
            # [v12.7] 辅助状态快照：npc_registry（认识状态/knowledge）与
            # character_state_manager（角色状态变化记录）不在 player/world/npc 快照内，
            # 若不快照，undo/retry 回滚后这些状态会残留（如被删轮次创建的 NPC 认识记录）
            aux_state = {}
            try:
                if eng.npc_registry:
                    aux_state["npc_registry"] = eng.npc_registry.to_dict()
            except Exception as _e:
                logger.debug("npc_registry 序列化失败: %s", _e)
            try:
                if eng.character_state_manager:
                    aux_state["character_state_manager"] = eng.character_state_manager.to_dict()
            except Exception as _e:
                logger.debug("character_state_manager 序列化失败: %s", _e)
            history_mgr.save_snapshot(
                world_id=eng.current_world_id,
                turn=eng.meta.current_turn,
                day=eng.world_state.current_day if eng.world_state else 1,
                time=eng.world_state.current_time if eng.world_state else "清晨",
                player_state=eng.player_state,
                world_state=eng.world_state,
                npc_states=eng.npc_states,
                narrative=narrative or "",
                player_input=player_input,
                diff_summary="；".join(rule_result.narrative_hints) if rule_result.narrative_hints else "",
                aux_state=aux_state,
            )

            # 同时保存叙事记录
            if narrative:
                history_mgr.save_narrative_entry(
                    world_id=eng.current_world_id,
                    turn=eng.meta.current_turn,
                    day=eng.world_state.current_day if eng.world_state else 1,
                    time=eng.world_state.current_time if eng.world_state else "清晨",
                    entry_type="narrative",
                    player_input=player_input,
                    narrative=narrative,
                    options=[],
                )
        except Exception as e:
            logger.warning("Failed to save state snapshot: %s", e, exc_info=True)

    # 以下方法复用原有逻辑
    def _handle_dice(self, response):
        eng = self.engine
        if response.get("dice_check", {}).get("needed"):
            return eng.player_agent.dice_roll(
                response["dice_check"].get("stat", "intelligence"),
                response["dice_check"].get("difficulty", 10),
                eng.player_state,
            )
        return None

    # ========== [Bug v9.1] 玩家输入时间跳跃预检测 ==========
    # 时间词与场景词提取，用于在 AI 生成前注入 prompt
    _TIME_WORD_PATTERN = re.compile(
        r'(清晨|早上|早晨|上午|中午|下午|傍晚|黄昏|晚上|夜里|夜晚|黎明|拂晓|凌晨)'
    )
    _SCENE_PATTERN = re.compile(
        r'(?:到了|回到|前往|进入|抵达|到达)\s*([\u4e00-\u9fff]{0,8}(?:工作室|家里|家中|住所|诊所|医院|学校|公司|店里|府上|宅子|房间|卧室|客厅))'
    )

    def _build_player_time_skip_hint(self, player_input: str) -> str:
        """[Bug v9.1] 检测玩家输入中的明确时间跳跃，构造提示注入 prompt。

        背景：v9 timekeeper 只在 AI 生成后解析时间跳跃，导致 AI 生成时 state 停留在旧时间，
        结构化状态（current_time/location）反向注入 prompt 误导 AI 倒退时间线。
        此方法在 AI 生成前只读检测玩家输入，把"剧情已推进到第二天上午/地点X"明确告诉 AI。
        """
        if not player_input:
            return ""
        parsed = parse_time_skip_from_text(player_input, world_type="custom")
        days = parsed.get("days", 0)
        if days <= 0:
            return ""

        # 提取时间词
        time_word = ""
        m = self._TIME_WORD_PATTERN.search(player_input)
        if m:
            time_word = m.group(1)

        # 提取场景词（玩家输入里"到了工作室/回到家里"等）
        scene_word = ""
        sm = self._SCENE_PATTERN.search(player_input)
        if sm:
            scene_word = sm.group(1)

        parts = ["【剧情时间推进 - 最高优先级】"]
        parts.append(f"玩家输入明确表明剧情推进了 {days} 天。")
        if time_word:
            parts.append(f"当前叙事时间应为：第{days}天后的{time_word}。")
        else:
            parts.append(f"当前叙事时间应为：推进 {days} 天之后。")
        if scene_word:
            parts.append(f"当前场景应为：{scene_word}。")
        parts.append("请在叙事中严格体现这一时间推进，不要回退到之前的时间点或场景。")
        parts.append("绝对禁止把叙事写回推进前的时刻（如昨晚、电话挂断时等）。")
        return "\n".join(parts)

    def _handle_time_perception(self, narrative, player_input):
        eng = self.engine
        time_skip_result = None
        year_evolution_events = []
        # [Bug 修复] 时间跳跃功能已屏蔽：AI 时间标记和正则检测都容易误判，
        # 导致玩家突然老死（创世神寿终正寝 bug）。改为只走正常时段推进，
        # 闭关/数月后等叙事由 LLM 自然描写，不再自动推进游戏天数。
        # 如需恢复，删除此 return 并启用下方逻辑。
        return time_skip_result or {}, year_evolution_events
        if eng.timekeeper and eng.world_state and narrative:
            world_type = "custom"
            if eng.world_def:
                world_type = eng.world_def.get("world_type", "custom")
            elif hasattr(eng.world_state, 'world_type'):
                world_type = eng.world_state.world_type
            # [v12] 优先检查AI时间跳跃标记
            ai_skip_days = self._extract_time_skip_tag(narrative)
            if ai_skip_days and ai_skip_days > 0:
                # AI标记优先，构造兼容的时间跳跃结果
                time_skip_result = {
                    "days_advanced": ai_skip_days,
                    "matches": [{"text": "AI标记", "days": ai_skip_days}],
                    "is_vague": False,
                    "source": "ai_tag",
                }
            else:
                # 回退到正则检测
                time_skip_result = eng.timekeeper.parse_and_accumulate(
                    text=narrative, player_input=player_input,
                    current_game_day=eng.world_state.current_day, world_type=world_type,
                )
            if time_skip_result.get("days_advanced", 0) > 0:
                days_adv = time_skip_result["days_advanced"]
                # [Bug 修复] 超龄保护：非不死角色跳跃后年龄不得超过 max_age - 5。
                # 不死角色（创世神/不灭设定）跳过此检查，允许闭关几万年。
                is_immortal = (eng.death_system._is_immortal(eng.player_state)
                               if eng.death_system and eng.player_state else False)
                if not is_immortal:
                    _max_age = getattr(eng.player_state, 'max_age', 80) if eng.player_state else 80
                    _cur_age = getattr(eng.player_state, 'age', 18) if eng.player_state else 18
                    _safe_remaining_days = max(0, (_max_age - 5 - _cur_age) * 365)
                    if days_adv > _safe_remaining_days:
                        logger.warning(
                            "时间跳跃 %d 天将导致玩家超龄（当前 %d 岁/上限 %d 岁），截断到 %d 天",
                            days_adv, _cur_age, _max_age, _safe_remaining_days,
                        )
                        days_adv = _safe_remaining_days
                else:
                    logger.info("不死角色时间跳跃 %d 天（跳过超龄保护）", days_adv)
                if days_adv > 0:
                    # [Bug 修复] 大数字批量推进：闭关百年/千年逐天循环太慢，
                    # 超过 1 年（365天）改为批量更新日期字段，仅按年触发 year_evolved。
                    if days_adv <= 365:
                        for _ in range(days_adv):
                            # hours=6 推进 1 天（TIME_SLOTS 有 6 个时段），
                            # 原代码 hours=24 会推进 4 天，导致时间跳跃被放大 4 倍。
                            eng.age_system.advance_time(eng.world_state, hours=6)
                            if eng.world_state.current_day % 365 == 0:
                                eng.timekeeper.mark_year_evolved()
                                if eng.npc_life_evolution and eng.npc_states:
                                    locs = list(eng.world_state.locations.keys()) if eng.world_state.locations else []
                                    try:
                                        yr_events = eng.npc_life_evolution.evolve_year(eng.npc_states, eng.world_state, locs)
                                        if yr_events:
                                            year_evolution_events.extend(yr_events)
                                    except Exception as e:
                                        logger.warning("evolve_year (time skip) failed: %s", e, exc_info=True)
                    else:
                        # 批量推进：直接修改日期字段，按年触发演化
                        old_day = eng.world_state.current_day
                        eng.world_state.current_day += days_adv
                        # 更新月/年（30天/月，12月/年，与 age_system.advance_time 一致）
                        total_months = (eng.world_state.current_day - 1) // 30
                        eng.world_state.current_day_of_month = (eng.world_state.current_day - 1) % 30 + 1
                        eng.world_state.current_month = total_months % 12 + 1
                        eng.world_state.current_year = total_months // 12 + 1
                        if eng.world_state.era_name:
                            eng.world_state.era_year = eng.world_state.current_year
                        # 更新季节/天气
                        eng.world_state.season = eng.age_system._get_season(eng.world_state.current_day)
                        eng.world_state.weather = eng.age_system._roll_weather(eng.world_state.season)
                        # 按年触发 year_evolved
                        old_year = old_day // 365
                        new_year = eng.world_state.current_day // 365
                        if new_year > old_year:
                            eng.timekeeper.mark_year_evolved()
                            if eng.npc_life_evolution and eng.npc_states:
                                locs = list(eng.world_state.locations.keys()) if eng.world_state.locations else []
                                try:
                                    yr_events = eng.npc_life_evolution.evolve_year(
                                        eng.npc_states, eng.world_state, locs)
                                    if yr_events:
                                        year_evolution_events.extend(yr_events)
                                except Exception as e:
                                    logger.warning("evolve_year (batch time skip) failed: %s", e, exc_info=True)
                        logger.info("批量时间跳跃 %d 天完成，当前第 %d 天",
                                    days_adv, eng.world_state.current_day)
                eng._last_year_evolved = eng.world_state.current_day
                # [v10++] 时间跳跃后触发 NPC 反思（Generative Agents 式）
                # 多天跳跃后，NPC 回顾近期记忆生成洞察；内部有节流，失败不影响主流程
                # [优化] NPC 反思耗时长（最多10个NPC串行调用），改为后台异步执行
                try:
                    self._bg(eng.trigger_npc_reflection)
                except Exception as e:
                    logger.warning("NPC reflection after time skip failed: %s", e, exc_info=True)
        return time_skip_result, year_evolution_events

    def _handle_butterfly(self, player_input, narrative, response):
        eng = self.engine
        # [v10+++] 复用关键剧情检测时的预评估影响，避免重复 LLM 调用
        if self._pre_impact is not None:
            impact = self._pre_impact
            self._pre_impact = None  # 用完即清
        else:
            impact = eng.butterfly.evaluate_impact(eng.player_state, player_input, eng.world_state)
        eng.player_impacts_today.append(f"行为: {player_input[:50]} -> 影响: {impact.get('description', '')[:100]}")
        for entry in response.get("_identity_log", []):
            eng.player_impacts_today.append(entry)
        eng.butterfly.record_action(eng.player_state, player_input, narrative, eng.world_state.current_day if eng.world_state else 0)
        consequence = eng.butterfly.generate_consequence(impact, eng.world_state)
        world_event = None
        if consequence:
            eng.world_agent.update_world_state(eng.world_state, consequence)
            world_event = consequence.model_dump()
            eng.world_changes_today.append(consequence.description[:100])
        return impact, world_event

    # ── [v10] 新增方法 ─────────────────────────────────────

    def _handle_butterfly_v10(self, player_input, narrative, response):
        """[v10] 蝴蝶效应处理 — 支持审批门"""
        eng = self.engine
        if not eng.butterfly:
            return {}, None, None

        # 如果启用审批门，使用带审批的评估
        if eng.butterfly.approval_gate_enabled:
            result = eng.butterfly.evaluate_with_approval(
                eng.player_state, player_input, eng.world_state, narrative
            )
            impact = result["impact"]
            world_event = None
            approval_info = None

            if result.get("needs_approval"):
                # 需要审批，不执行后果
                approval_info = {
                    "approval_id": result["approval_id"],
                    "preview": result["preview"],
                    "message": "此行为可能引发重大世界变化，请审批。",
                }
            elif result.get("auto_executed") and result.get("consequence"):
                # 低影响，自动执行 — 直接使用 evaluate_with_approval 已生成的后果，不重复调用
                consequence_data = result["consequence"]
                from .schemas import MacroEvent
                consequence = MacroEvent(**consequence_data)
                eng.world_agent.update_world_state(eng.world_state, consequence)
                world_event = consequence_data
                eng.world_changes_today.append(consequence.description[:100])
        else:
            # 未启用审批门，使用原有逻辑
            impact, world_event = self._handle_butterfly(player_input, narrative, response)
            approval_info = None
            # [Bug] _handle_butterfly 内部已记录 player_impacts_today，此处不重复追加
            return impact, world_event, approval_info

        # 记录影响（仅审批门路径走到这里，else 分支已在上面 return）
        eng.player_impacts_today.append(
            f"行为: {player_input[:50]} -> 影响: {impact.get('description', '')[:100]}"
        )

        return impact, world_event, approval_info

    def _handle_butterfly_v10_async(self, player_input, narrative, response):
        """[v10.6] 异步蝴蝶效应 — 评估在后台执行，不阻塞玩家响应。
        蝴蝶效应评估使用对话 LLM，同步执行会阻塞 20-30 秒。
        [v11] 改为异步后，结果缓存到 engine._pending_butterfly_result，下回合生效。"""
        eng = self.engine
        if not eng.butterfly:
            return {}, None, None

        def _async_evaluate():
            try:
                impact, world_event, approval = self._handle_butterfly_v10(
                    player_input, narrative, response
                )
                # [v11] 将结果缓存到 engine 供下回合使用
                if impact or world_event:
                    eng._pending_butterfly_result = {
                        "impact": impact or {},
                        "world_event": world_event,
                        "approval": approval,
                        "input": player_input[:50],
                    }
                    if impact:
                        eng.player_impacts_today.append(
                            f"行为: {player_input[:50]} -> 影响: {impact.get('description', '')[:100]}"
                        )
            except Exception as e:
                logger.warning("Butterfly async evaluation failed: %s", e, exc_info=True)

        self._bg(_async_evaluate)
        # 返回空结果占位（蝴蝶效应在下回合生效）
        return {}, None, None

    def _handle_death(self, is_suicide):
        eng = self.engine
        death = None
        suicide_confirm = None
        if eng.death_system and eng.player_state:
            if is_suicide:
                suicide_confirm = {"type": "suicide_confirm", "message": "你确认要结束自己的生命吗？", "cause": "自尽"}
            else:
                death = eng.death_system.check_death(eng.player_state, eng.world_state)
                if death and eng.memoir:
                    eng.memoir.record_death(eng.player_state, death["cause"], eng.world_state.current_day if eng.world_state else 0, eng.world_state)
                if death:
                    # [v10.5] 使用实例级 trigger_hook
                    eng.trigger_hook("on_death",
                                 player_state=eng.player_state,
                                 death_info=death,
                                 world_state=eng.world_state)
        return death, suicide_confirm

    def _store_to_rag_v10(self, narrative, player_input, impact):
        """[v10] 带重要性评分的 RAG 存储。
        [P1-3] 即使 LLM 返回空内容（narrative 为空），也至少存储玩家输入，
        保证记忆不丢失。"""
        eng = self.engine
        if not eng.memory:
            return
        # [P1-3] narrative 为空时，至少存储玩家输入作为记忆
        if not narrative and not player_input:
            return

        day = eng.world_state.current_day if eng.world_state else 0

        # 计算重要性
        importance = 0.5  # 基础重要性
        emotional_weight = 0.0

        # 蝴蝶效应影响加分
        if impact:
            score = impact.get("impact_score", 0)
            if score >= 5:
                importance = min(1.0, 0.5 + score * 0.05)
                emotional_weight = min(1.0, score * 0.1)

        # [P1-3] narrative 为空时，用玩家输入作为记忆内容，降低重要性
        if not narrative:
            memory_text = f"玩家行动：{player_input}"
            importance = 0.3  # LLM 失败时的降级记忆，重要性降低
            has_foreshadow = False
        else:
            memory_text = narrative
            # 伏笔关键词加分
            has_foreshadow = any(kw in narrative for kw in FORESHADOW_KEYWORDS)
            if has_foreshadow:
                importance = min(1.0, importance + 0.2)

        # 使用带重要性的存储
        eng.memory.add_memory_with_importance(
            memory_text, {"day": day, "type": "narrative", "player_input": player_input[:100]},
            importance=importance,
            emotional_weight=emotional_weight,
            memory_type="narrative",
        )

        if has_foreshadow and narrative:
            eng.memory.add_foreshadow(narrative, day, importance="high")

    def _record_npc_procedural_memory(self, narrative, player_input):
        """[v10] 记录 NPC 程序性记忆"""
        eng = self.engine
        if not eng.npc_procedural_memory or not eng.npc_states or not narrative:
            return

        # 检查叙事中提到的 NPC
        for npc_id, npc in eng.npc_states.items():
            if npc.name not in narrative:
                continue

            # 推断动作类型
            action_type = self._infer_npc_action_type(narrative, npc.name)
            if not action_type:
                continue

            # 计算有效性（简化版：基于叙事中的情感倾向）
            effectiveness = self._estimate_action_effectiveness(narrative, npc.name)

            eng.npc_procedural_memory.record_action(
                npc=npc,
                action_type=action_type,
                context=player_input[:200],
                outcome=narrative[:200],
                effectiveness=effectiveness,
                energy_cost=10,
                day=eng.world_state.current_day if eng.world_state else 0,
                location=npc.current_location or "",
                turn=eng.meta.current_turn if eng.meta else -1,
            )

    def _infer_npc_action_type(self, narrative: str, npc_name: str) -> str:
        """从叙事中推断 NPC 的动作类型"""
        text = narrative.lower()
        if any(kw in text for kw in ["战斗", "攻击", "厮杀", "对战"]):
            return "combat"
        if any(kw in text for kw in ["交谈", "对话", "聊天", "商议"]):
            return "social"
        if any(kw in text for kw in ["交易", "买卖", "经商"]):
            return "trade"
        if any(kw in text for kw in ["探索", "搜索", "调查"]):
            return "explore"
        if any(kw in text for kw in ["工作", "劳作", "办公"]):
            return "work"
        return "idle"

    def _estimate_action_effectiveness(self, narrative: str, npc_name: str) -> float:
        """估算 NPC 动作的有效性"""
        text = narrative.lower()
        # 正面词汇
        positive = ["成功", "胜利", "完成", "获得", "提升", "帮助", "治愈"]
        # 负面词汇
        negative = ["失败", "受伤", "损失", "逃跑", "死亡", "被击败"]

        pos_count = sum(1 for kw in positive if kw in text)
        neg_count = sum(1 for kw in negative if kw in text)

        if pos_count + neg_count == 0:
            return 0.5
        return min(1.0, max(0.0, 0.5 + (pos_count - neg_count) * 0.15))

    def _record_npc_skill_learning(self, narrative: str, player_input: str):
        """[v10++] NPC 技能自学（Voyager/Hermes 式）— 后台异步执行，不阻塞主流程。
        扫描叙事中提及的 NPC，根据动作有效性触发技能学习或失败记录：
          - 有效性高（>=0.6）：从成功交互中提取可复用技能
          - 有效性低（<0.4）：记录相关技能使用失败，降低其成功率
        与 _record_npc_procedural_memory 互补：程序性记忆记录动作统计，技能库提取可复用策略。"""
        eng = self.engine
        if not eng.npc_skill_library or not eng.npc_states or not narrative:
            return

        current_turn = eng.meta.current_turn if eng.meta else 0
        current_day = eng.world_state.current_day if eng.world_state else 0

        for npc_id, npc in eng.npc_states.items():
            if npc.name not in narrative:
                continue

            # 推断动作类型
            action_type = self._infer_npc_action_type(narrative, npc.name)
            if not action_type or action_type == "idle":
                continue

            # 估算有效性
            effectiveness = self._estimate_action_effectiveness(narrative, npc.name)

            try:
                if effectiveness >= 0.6:
                    # 成功交互：提取可复用技能
                    eng.npc_skill_library.learn_from_success(
                        npc_id=npc.agent_id,
                        npc_name=npc.name,
                        context=player_input[:200],
                        action=f"{action_type}: {narrative[:150]}",
                        result=narrative[:200],
                        turn=current_turn,
                        day=current_day,
                    )
                elif effectiveness < 0.4:
                    # 失败交互：记录相关技能使用失败（取最匹配的技能）
                    relevant = eng.npc_skill_library.get_relevant_skills(
                        npc.agent_id, player_input[:200], top_k=1
                    )
                    if relevant:
                        eng.npc_skill_library.record_failure(
                            npc.agent_id, relevant[0].skill_id, current_turn
                        )
            except Exception as e:
                logger.warning("NPC %s 技能自学失败: %s", npc.name, e, exc_info=True)

    def _advance_task_board(self) -> dict:
        """[v10] 推进世界任务板"""
        eng = self.engine
        if not eng.world_task_board:
            return {"skipped": True}

        current_day = eng.world_state.current_day if eng.world_state else 0

        # [Bug] 从世界事件自动生成任务（之前 generate_tasks_from_event 从未被调用）
        new_tasks = []
        if eng.event_log_today:
            if not hasattr(self, '_processed_event_ids'):
                self._processed_event_ids: set = set()
            affected_locations = list(eng.world_state.locations.keys()) if eng.world_state and hasattr(eng.world_state, 'locations') else []
            for evt in eng.event_log_today:
                evt_id = evt.get("event_id", "")
                if evt_id and evt_id in self._processed_event_ids:
                    continue
                impact = evt.get("impact_level", 0)
                if impact < 3:
                    continue  # 只为中等以上影响事件生成任务
                try:
                    tasks = eng.world_task_board.generate_tasks_from_event(
                        event_description=evt.get("description", ""),
                        event_type=evt.get("event_type", "general"),
                        impact_level=impact,
                        affected_locations=affected_locations,
                        current_day=current_day,
                        world_state=eng.world_state,
                    )
                    new_tasks.extend(tasks)
                    if evt_id:
                        self._processed_event_ids.add(evt_id)
                except Exception as e:
                    logger.warning("generate_tasks_from_event failed: %s", e, exc_info=True)
            # 清理过旧的事件 ID（保留最近 100 个）
            if len(self._processed_event_ids) > 100:
                self._processed_event_ids = set(list(self._processed_event_ids)[-50:])

        # 自动分配任务
        assignments = eng.world_task_board.auto_assign_tasks(
            eng.npc_states, eng.world_state
        )

        # 推进任务进度
        progress = eng.world_task_board.advance_tasks(
            eng.npc_states, eng.world_state, current_day
        )

        return {
            "new_tasks": len(new_tasks),
            "assignments": assignments,
            "progress": progress,
            "board_summary": eng.world_task_board.get_board_summary(),
        }

    def _advance_quest_system(self, narrative: str) -> dict:
        """[Bug] 推进任务系统：检查附近可接取任务 + 截止检查。
        之前 check_nearby_quests/check_deadlines 从未被调用，整个任务系统是死代码。"""
        eng = self.engine
        if not eng.quest_system or not eng.player_state or not eng.world_state:
            return {"skipped": True}

        result = {"new_quests": [], "failed_quests": []}

        try:
            # 1. 检查附近是否有可接取的任务（每地点仅检查一次）
            nearby = eng.quest_system.check_nearby_quests(
                eng.player_state, eng.world_state,
                location_description=narrative[:200] if narrative else "",
            )
            for quest_info in nearby:
                quest = eng.quest_system.accept_quest(
                    quest_info.get("quest", {}),
                    eng.world_state.current_day,
                )
                result["new_quests"].append(quest.to_dict())
                eng.event_log_today.append({
                    "type": "quest_available",
                    "description": f"发现新任务: {quest.title}",
                    "impact_level": 2,
                })

            # 2. 检查任务截止
            failed = eng.quest_system.check_deadlines(eng.world_state)
            result["failed_quests"] = failed
            for fail_event in failed:
                eng.event_log_today.append({
                    "type": "quest_failed",
                    "description": f"任务失败: {fail_event['quest']['title']}",
                    "impact_level": 3,
                })
        except Exception as e:
            logger.warning("Quest system advance failed: %s", e, exc_info=True)

        return result

    def _run_narrative_review(self) -> dict:
        """[v10] 执行叙事回顾（闭环学习）"""
        eng = self.engine
        if not eng.narrative_reviewer:
            return {"skipped": True}

        current_turn = eng.meta.current_turn if eng.meta else 0
        current_day = eng.world_state.current_day if eng.world_state else 0

        if not eng.narrative_reviewer.should_review(current_turn):
            return {"skipped": True, "reason": "not_time"}

        # 执行回顾
        result = eng.narrative_reviewer.review(
            recent_narratives=eng.narrative_history[-10:],
            player_state=eng.player_state,
            world_state=eng.world_state,
            npc_states=eng.npc_states,
            current_turn=current_turn,
            current_day=current_day,
        )

        # [v10.5] 使用实例级 trigger_hook
        self.engine.trigger_hook("on_narrative_review", result=result)

        return result

    def _run_narrative_review_sync(self):
        """[优化] 叙事回顾的同步包装，供后台异步队列调用"""
        try:
            self._run_narrative_review()
        except Exception as e:
            logger.warning("Background narrative review failed: %s", e, exc_info=True)

    def _run_memory_curator_sync(self):
        """[优化] 记忆 Curator 的同步包装，供后台异步队列调用"""
        try:
            self._run_memory_curator()
        except Exception as e:
            logger.warning("Background memory curator failed: %s", e, exc_info=True)

    def _run_memory_brief_update_sync(self):
        """[v1.3] MemoryBrief 更新的同步包装，供后台异步队列调用。
        优先检测睡眠巩固（游戏日变更），否则检测增量更新（每8回合）。"""
        try:
            self._run_memory_brief_update()
        except Exception as e:
            logger.warning("Background memory brief update failed: %s", e, exc_info=True)

    def _run_memory_brief_update(self) -> dict:
        """[v1.3] 执行 MemoryBrief 增量更新或睡眠巩固"""
        eng = self.engine
        if not eng.memory_brief:
            return {"skipped": True, "reason": "no_brief_manager"}

        current_turn = eng.meta.current_turn if eng.meta else 0
        current_day = eng.world_state.current_day if eng.world_state else 0

        # 优先级 1：睡眠巩固（游戏日推进时全量重写）
        try:
            if eng.memory_brief.should_consolidate_on_sleep(current_day):
                result = eng.memory_brief.consolidate_on_sleep(eng, current_day)
                logger.info("MemoryBrief sleep consolidate on day %d: %s",
                            current_day, result.get("updated", []))
                return result
        except Exception as e:
            logger.warning("MemoryBrief sleep consolidate failed: %s", e, exc_info=True)

        # 优先级 2：增量更新（每8回合刷新 active_threads + player_profile）
        try:
            if eng.memory_brief.should_incremental_update(current_turn):
                result = eng.memory_brief.update_briefs_incremental(eng, current_turn)
                logger.info("MemoryBrief incremental update on turn %d: %s",
                            current_turn, result.get("updated", []))
                return result
        except Exception as e:
            logger.warning("MemoryBrief incremental update failed: %s", e, exc_info=True)

        return {"skipped": True, "reason": "not_time"}

    def _run_memory_curator(self) -> dict:
        """[v10] 执行记忆 Curator 整理"""
        eng = self.engine
        if not eng.memory_curator or not eng.memory:
            return {"skipped": True}

        current_turn = eng.meta.current_turn if eng.meta else 0
        current_day = eng.world_state.current_day if eng.world_state else 0

        if not eng.memory_curator.should_curate(current_turn):
            return {"skipped": True, "reason": "not_time"}

        result = eng.memory_curator.curate(
            memory=eng.memory,
            player_state=eng.player_state,
            world_state=eng.world_state,
            npc_states=eng.npc_states,
            lorebook=eng.lorebook,
            current_turn=current_turn,
            current_day=current_day,
        )

        # [v10.5] 使用实例级 trigger_hook
        self.engine.trigger_hook("on_memory_curated", result=result)

        return result

    @staticmethod
    def _extract_cn_keywords(text: str) -> list[str]:
        """[Bug] 从中文文本中提取关键词子串，替代 split() 分词。
        提取 2-4 字的连续中文字符子串，按出现频率排序。"""
        if not text:
            return []
        # 提取所有 2-4 字的连续中文字符子串
        import re
        cn_chunks = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
        if not cn_chunks:
            # 回退：按空白分词
            return [w for w in text.split() if len(w) >= 2]
        # 去重并保持顺序，优先较长的子串
        seen = set()
        result = []
        for chunk in cn_chunks:
            if chunk not in seen:
                seen.add(chunk)
                result.append(chunk)
        return result

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """[Bug] 将中文叙事文本按句号/问号/感叹号/分号切分为句子列表"""
        if not text:
            return []
        import re
        # 按中文和英文标点切分
        sentences = re.split(r'[。！？；!?\n]+', text)
        return [s.strip() for s in sentences if s.strip()]

    @staticmethod
    def _check_foreshadow_resolved(sentences: list[str], foreshadow_keywords: list[str]) -> bool:
        """[Bug] 检查伏笔是否在叙事中被解决：
        伏笔关键词和解决关键词必须在同一句中共现，避免全局误判。"""
        if not sentences or not foreshadow_keywords:
            return False
        for sentence in sentences:
            # 该句是否包含伏笔关键词
            has_foreshadow_kw = any(kw in sentence for kw in foreshadow_keywords)
            if not has_foreshadow_kw:
                continue
            # 该句是否包含解决关键词
            has_resolution_kw = any(kw in sentence for kw in FORESHADOW_RESOLUTION_KEYWORDS)
            if has_resolution_kw:
                return True
        return False

    def _track_foreshadow(self, narrative, player_input) -> dict:
        """[v10+] 伏笔生命周期追踪"""
        eng = self.engine
        if not eng.foreshadow_lifecycle or not narrative:
            return {"skipped": True}

        current_day = eng.world_state.current_day if eng.world_state else 0
        current_turn = eng.meta.current_turn if eng.meta else 0
        result = {"new_hooks": [], "mentions": [], "stale_check": None, "burst_check": None}

        # 检测叙事中是否包含伏笔关键词 → 插入新伏笔
        if any(kw in narrative for kw in FORESHADOW_KEYWORDS):
            hook = eng.foreshadow_lifecycle.insert(
                content=narrative[:300],
                day=current_day,
                turn=current_turn,
                importance="high",
                memory=eng.memory,
            )
            result["new_hooks"].append(hook.to_dict())

        # 检测叙事中是否提及了已有伏笔
        active_hooks = eng.foreshadow_lifecycle.get_active_hooks()
        # [Bug] 将叙事按句切分，用于判断解决关键词是否与伏笔关键词同句出现
        # 避免全局匹配导致无关伏笔被误解决
        narrative_sentences = self._split_sentences(narrative)
        for hook_data in active_hooks:
            hook_content = hook_data.get("content", "")
            if not hook_content:
                continue
            # [Bug] 中文文本 split() 无法分词，改用字符级 n-gram 匹配
            # 提取伏笔内容中的 2-4 字关键子串进行匹配
            keywords = self._extract_cn_keywords(hook_content)
            mentioned = any(kw in narrative for kw in keywords[:8])
            if mentioned:
                hook_id = hook_data["hook_id"]
                eng.foreshadow_lifecycle.mention(hook_id, current_day)
                result["mentions"].append(hook_id)

                # [Bug] 伏笔被多次提及且叙事包含解决类关键词时，标记为已解决
                # 修复：解决关键词必须与伏笔关键词在同一句中出现，避免全局误判
                hook_obj = eng.foreshadow_lifecycle.hooks.get(hook_id)
                if hook_obj and hook_obj.mention_count >= 3:
                    co_resolved = self._check_foreshadow_resolved(
                        narrative_sentences, keywords[:8]
                    )
                    if co_resolved:
                        eng.foreshadow_lifecycle.resolve(
                            hook_id, current_day,
                            resolution=f"叙事中伏笔关键词与解决关键词同句出现，伏笔经{hook_obj.mention_count}次提及后兑现",
                        )
                        result.setdefault("resolved", []).append(hook_id)

        # 每 10 回合检查一次过期和爆发
        if current_turn % 10 == 0:
            stale = eng.foreshadow_lifecycle.check_stale(current_day)
            if stale:
                result["stale_check"] = {
                    "count": len(stale),
                    "hooks": [h.to_dict() for h in stale[:3]],
                }
                # [v10.5] 使用实例级 trigger_hook
                eng.trigger_hook("on_foreshadow_stale", stale_hooks=stale)

            burst = eng.foreshadow_lifecycle.check_burst()
            result["burst_check"] = burst

        # [NovelRoleplay] 检查并失效既定未来伏笔（偏离度追踪）
        if (hasattr(eng, 'butterfly') and eng.butterfly
            and getattr(eng.butterfly, '_novel_mode', False)):
            try:
                invalidated = eng.butterfly.check_and_invalidate_future_foreshadows(
                    eng.foreshadow_lifecycle, current_day
                )
                if invalidated:
                    result["novel_divergence"] = {
                        "invalidated_hooks": invalidated,
                        "divergence_summary": eng.butterfly.get_divergence_summary(),
                    }
            except Exception as e:
                logger.warning("[NovelRoleplay] 偏离度检查失败: %s", e, exc_info=True)

        return result

    def _run_continuity_audit(self) -> dict:
        """[v10+] 多维度连续性审计（异步，每 N 回合一次）"""
        eng = self.engine
        if not eng.continuity_auditor:
            return {"skipped": True}

        current_turn = eng.meta.current_turn if eng.meta else 0
        if not eng.continuity_auditor.should_audit(current_turn):
            return {"skipped": True, "reason": "not_time"}

        current_day = eng.world_state.current_day if eng.world_state else 0

        report = eng.continuity_auditor.audit(
            recent_narratives=eng.narrative_history[-5:],
            player_state=eng.player_state,
            world_state=eng.world_state,
            npc_states=eng.npc_states,
            foreshadow=eng.foreshadow_lifecycle,
            current_turn=current_turn,
            current_day=current_day,
        )

        # [v10.5] 使用实例级 trigger_hook
        self.engine.trigger_hook("on_continuity_audit", report=report)

        return report.to_dict()

    def _run_continuity_audit_sync(self):
        """[优化] 连续性审计的同步包装，供后台异步队列调用"""
        try:
            self._run_continuity_audit()
        except Exception as e:
            logger.warning("Background continuity audit failed: %s", e, exc_info=True)

    def _run_autonomous_memory(self) -> dict:
        """[v10++] 触发 Agent 自主记忆管理（MemGPT/Letta 式）。
        后台异步执行，避免 LLM 摘要等耗时操作阻塞回合处理。
        失败时不影响主流程。"""
        eng = self.engine
        if not eng.autonomous_memory or not eng.memory:
            return {"skipped": True}
        if not eng.meta or not eng.world_state or not eng.player_state:
            return {"skipped": True}
        # 后台异步执行记忆管理（含 LLM 摘要、ChromaDB 读写），不阻塞主流程
        self._bg(eng.trigger_autonomous_memory)
        return {"scheduled": True}

    def _check_personality_evolution(self, narrative: str):
        """[v1.3] NPC 性格演化检测（后台异步执行）
        按 check_interval_turns 周期执行，避免每回合都跑。
        检测到创伤事件后由 LLM 生成新的性格描述。"""
        try:
            eng = self.engine
            if not eng.npc_states or not narrative:
                return
            if not eng.meta or not eng.world_state:
                return

            # 从 engine 的 config 缓存读取周期配置
            _cfg = eng._load_config() if hasattr(eng, '_load_config') else {}
            _feat_cfg = _cfg.get("features", {}).get("npc_personality_evolution", {})
            if not _feat_cfg.get("enabled", True):
                return
            try:
                interval = int(_feat_cfg.get("check_interval_turns", 5))
            except Exception:
                interval = 5
            if interval <= 0:
                interval = 5

            current_turn = eng.meta.current_turn
            # 周期检查：每 N 回合执行一次
            if current_turn % interval != 0:
                return

            current_day = eng.world_state.current_day

            # 世界背景（用于让 LLM 生成更贴合的叙事）
            world_context = ""
            if eng.world_state:
                world_context = f"世界：{eng.world_state.world_name or ''}\n"
                if eng.world_state.description:
                    world_context += f"背景：{eng.world_state.description[:200]}"

            # 批量检测
            from modules.personality_evolution import batch_check_npcs
            shifts = batch_check_npcs(
                eng.npc_states,
                narrative,
                current_turn,
                current_day,
                llm=eng.llm,
                world_context=world_context,
            )

            if shifts:
                logger.info(
                    "[PersonalityEvolution] 本回合 %d 个 NPC 发生性格转折",
                    len(shifts)
                )
                # 把转折叙事写入 narrative_history（让玩家可见）
                for shift in shifts:
                    narrative_text = shift.get("narrative", "")
                    if narrative_text and hasattr(eng, 'narrative_history'):
                        try:
                            eng.narrative_history.append({
                                "type": "personality_shift",
                                "day": current_day,
                                "time": getattr(eng.world_state, 'current_time', ''),
                                "text": f"【{shift.get('trauma', '性格转折')}】{narrative_text}",
                                "event_type": "personality_shift",
                                "npc_name": shift.get("npc_name", ""),
                            })
                        except Exception:
                            pass
        except Exception as e:
            logger.warning("[PersonalityEvolution] 检测失败: %s", e, exc_info=True)

    def _record_causal_node(
        self,
        player_input: str,
        narrative: str,
        rule_result,
        impact: dict | None,
        world_event: dict | None,
    ):
        """[v1.3] 因果链节点记录（后台异步执行）

        综合本回合的多维度信号计算重要性，超阈值则写入 eng.causal_graph。
        - 重要性维度：蝴蝶效应分、规则效果数、NPC 参与数、性格转折、伏笔、文本长度
        - parent_turn_ids：暂不主动建立，依赖前端从 triggered_events 反推
        """
        try:
            eng = self.engine
            if not eng.causal_graph:
                return
            if not eng.meta or not eng.world_state:
                return

            # 从 engine 的 config 缓存读取开关
            _cfg = eng._load_config() if hasattr(eng, '_load_config') else {}
            from modules.causal_graph import (
                compute_importance, build_effects_summary,
                is_feature_enabled, get_min_importance, CausalNode,
            )
            if not is_feature_enabled(_cfg):
                return

            # 动态同步阈值（用户可能在设置中调整过）
            new_threshold = get_min_importance(_cfg)
            if new_threshold != eng.causal_graph.min_importance:
                eng.causal_graph.min_importance = new_threshold

            # 收集信号
            rule_effects_count = 0
            try:
                rule_effects_count = len(rule_result.effects) if rule_result and rule_result.effects else 0
            except Exception:
                rule_effects_count = 0

            # 涉及 NPC 数：从叙事中匹配 NPC 名字
            involved_npc_names = []
            if eng.npc_states:
                for npc in eng.npc_states.values():
                    if npc.name and npc.name in (narrative or ""):
                        involved_npc_names.append(npc.name)
            involved_npc_count = len(involved_npc_names)

            # 蝴蝶效应分
            butterfly_score = 0.0
            if impact and isinstance(impact, dict):
                try:
                    butterfly_score = float(impact.get("impact_score", 0) or 0)
                except Exception:
                    butterfly_score = 0.0

            # 小说模式偏离度
            novel_divergence = 0.0
            if getattr(eng, 'is_novel_roleplay', False) and eng.butterfly:
                try:
                    novel_divergence = float(getattr(eng.butterfly, 'novel_divergence_score', 0.0) or 0.0)
                except Exception:
                    novel_divergence = 0.0

            # [Bug fix] 检测本回合叙事中的性格转折事件（不依赖 narrative_history，避免误报上回合事件）
            has_personality_shift = False
            try:
                from modules.personality_evolution import detect_trauma_events
                if eng.npc_states and narrative:
                    for npc in eng.npc_states.values():
                        if not npc.name or len(npc.name) < 2:
                            continue
                        trauma_events = detect_trauma_events(narrative, npc.name)
                        if trauma_events:
                            has_personality_shift = True
                            break
            except Exception:
                pass

            # 伏笔检测
            has_foreshadow = False
            try:
                if world_event and isinstance(world_event, dict):
                    et = world_event.get("event_type", "")
                    if "foreshadow" in et or "伏笔" in (world_event.get("narrative", "") or ""):
                        has_foreshadow = True
            except Exception:
                pass

            # 计算重要性（蝴蝶效应分和小说偏离度分开传入，量纲不同）
            importance, triggered_events = compute_importance(
                turn_id=eng.meta.current_turn,
                player_input=player_input,
                narrative=narrative,
                rule_effects_count=rule_effects_count,
                involved_npc_count=involved_npc_count,
                butterfly_score=butterfly_score,
                novel_divergence=novel_divergence,
                triggered_events=[],
                has_personality_shift=has_personality_shift,
                has_foreshadow=has_foreshadow,
            )

            # 后果摘要
            butterfly_narrative = ""
            if world_event and isinstance(world_event, dict):
                butterfly_narrative = world_event.get("narrative", "") or world_event.get("description", "")
            effects_summary = build_effects_summary(
                player_input=player_input,
                narrative=narrative,
                rule_effects_count=rule_effects_count,
                npc_names=involved_npc_names,
                butterfly_narrative=butterfly_narrative,
            )

            # 构造节点
            node = CausalNode(
                turn_id=eng.meta.current_turn,
                day=eng.world_state.current_day,
                player_input=player_input,
                narrative=narrative,
                importance=importance,
                triggered_events=triggered_events,
                effects_summary=effects_summary,
                parent_turn_ids=[],  # 暂不主动建立依赖；可后续扩展
                novel_divergence=novel_divergence,
            )

            added = eng.causal_graph.add_node(node)
            if added:
                logger.debug(
                    "[CausalGraph] 记录回合 T%d 重要性=%.2f 事件=%s",
                    node.turn_id, node.importance, node.triggered_events
                )
        except Exception as e:
            logger.warning("[CausalGraph] 记录失败: %s", e, exc_info=True)

    def _build_graph_rag(self, narrative):
        eng = self.engine
        if eng.graph_rag and narrative:
            try:
                day = eng.world_state.current_day if eng.world_state else 0
                turn = eng.meta.current_turn if eng.meta else 0
                eng.graph_rag.build_from_narrative(narrative, day=day, turn=turn)
            except Exception as e:
                logger.warning("GraphRAG build failed: %s", e, exc_info=True)

    # ── [v10++] 角色动态状态（CHIRON 式） ─────────────────

    def _build_character_state_context(self, eng) -> str:
        """构建角色动态状态上下文，注入到 prompt。
        包含玩家和叙事中相关 NPC 的动态状态。"""
        if not eng.character_state_manager:
            return ""

        parts = []
        # 玩家动态状态
        if eng.player_state:
            player_state_text = eng.character_state_manager.get_state_for_prompt(eng.player_state.agent_id)
            if player_state_text:
                parts.append(f"【{eng.player_state.name}的动态状态】\n{player_state_text}")

        # NPC 动态状态（只注入有记录的）
        if eng.npc_states:
            for npc_id, npc in eng.npc_states.items():
                npc_state_text = eng.character_state_manager.get_state_for_prompt(npc_id)
                if npc_state_text:
                    parts.append(f"【{npc.name}的动态状态】\n{npc_state_text}")

        if not parts:
            return ""
        return "【角色动态状态（CHIRON 式追踪）- 反映角色当前真实状态】\n" + "\n\n".join(parts)

    # ── [v10+] 叙事类型感知（GraphRAG 动态启停） ───────────

    def _detect_scene_type(self, eng, player_input: str, fixed_prompt: str = ""):
        """[v10+] 检测当前场景类型。
        综合玩家输入和最近一段剧情进行关键词匹配。
        SceneDetector 不可用时返回 None，调用方回退到默认检索权重。

        [Bug 修复] 原实现拼接 fixed_prompt[-500:] 作为上下文摘要，但 fixed_prompt
        含世界观/规则/记忆/伏笔/角色状态等大量与场景无关的文本，SceneDetector 用
        子串匹配（如 "想"/"心"/"情"/"爱" 等单字）会被误判为 INTROSPECTIVE 场景，
        导致 _build_scene_hint 注入"节奏舒缓，注重内心活动"的错误提示，使 LLM
        完全不响应玩家动作（如"召唤四大宫主"被识别成内省，AI 改写为"整理物品"）。
        现改为只用玩家输入 + 上一段叙事，精准反映"当前正在发生什么场景"。
        """
        if not eng.scene_detector:
            return None
        try:
            recent_narrative = ""
            if eng.narrative_history:
                recent_narrative = (eng.narrative_history[-1].get("text", "") or "")[-300:]
            detect_text = f"{player_input}\n{recent_narrative}"
            return eng.scene_detector.detect(detect_text)
        except Exception as e:
            logger.warning("Scene detection failed: %s", e, exc_info=True)
            return None

    def _build_scene_hint(self, scene_result) -> str:
        """[v10+] 根据场景检测结果构建叙事氛围提示，注入到 prompt。
        帮助 LLM 理解当前场景氛围，引导叙事节奏。"""
        if scene_result is None:
            return ""
        scene_type = scene_result.scene_type
        # 场景类型 -> 氛围描述
        atmosphere_map = {
            "action": "动作/战斗场景：节奏紧凑，注重动作描写、攻防细节与紧张感。",
            "exploration": "探索/冒险场景：注重环境描写、发现感与未知悬念。",
            "introspective": "内省/心理场景：节奏舒缓，注重内心活动、情感与氛围刻画。",
            "social": "社交/对话场景：注重人物互动、对话与关系展现。",
            "commerce": "交易场景：注重物品、价格与讨价还价细节。",
            "study": "学习/修炼场景：注重领悟过程与成长感。",
            "daily": "日常场景：节奏平缓，注重生活细节与氛围。",
        }
        atmosphere = atmosphere_map.get(scene_type.value, "")
        if not atmosphere:
            return ""
        lines = [f"【当前场景类型：{scene_type.value}】", atmosphere]
        if scene_result.is_dynamic:
            lines.append("（动感叙事：可适当加快节奏，强化动作与环境变化）")
        return "\n".join(lines)

    def _analyze_character_states(self, narrative: str):
        """[v10++] 分析叙事中角色状态变化（后台异步执行，不阻塞主流程）。
        对玩家和叙事中提及的 NPC 调用 LLM 提取结构化状态变更。"""
        eng = self.engine
        if not eng.character_state_manager or not narrative:
            return

        current_turn = eng.meta.current_turn if eng.meta else 0
        current_day = eng.world_state.current_day if eng.world_state else 0

        # 分析玩家状态变化
        if eng.player_state:
            try:
                eng.character_state_manager.analyze_changes_from_narrative(
                    eng.player_state.agent_id, narrative, current_turn, current_day
                )
            except Exception as e:
                logger.warning("Player dynamic state analysis failed: %s", e, exc_info=True)

        # 分析叙事中提及的 NPC 状态变化
        if eng.npc_states:
            for npc_id, npc in eng.npc_states.items():
                # 只分析叙事中提到的 NPC
                if npc.name not in narrative:
                    continue
                # [v12.7] 更新出场时间戳：首次出场记 first_seen，每次出场更新 last_seen。
                # 世界初始 NPC（first_seen_turn=-1）不记 first_seen（保持 -1 = 世界固有，
                # 回滚永不删除）；仅记录 last_seen 供回滚时恢复出场记录。
                try:
                    if npc.first_seen_turn == -1 and npc.original_chapter == -1:
                        # 世界固有 NPC：不设置 first_seen（保持 -1），只更新 last_seen
                        npc.last_seen_turn = current_turn
                    else:
                        if npc.first_seen_turn == -1:
                            npc.first_seen_turn = current_turn
                        npc.last_seen_turn = current_turn
                except Exception as _e:
                    logger.debug("NPC %s 时间戳更新失败: %s", npc.name, _e)
                try:
                    eng.character_state_manager.analyze_changes_from_narrative(
                        npc_id, narrative, current_turn, current_day
                    )
                except Exception as e:
                    logger.warning("NPC %s dynamic state analysis failed: %s", npc.name, e, exc_info=True)

    def _run_identity_audit(self, narrative):
        eng = self.engine
        audit_results = []
        eng.turns_since_audit += 1
        if (eng.turns_since_audit >= eng.audit_interval and narrative and eng.npc_states and eng.player_agent):
            eng.turns_since_audit = 0
            try:
                discrepancies = eng.player_agent.audit_identity_consistency(narrative, eng.npc_states, day=eng.world_state.current_day if eng.world_state else 0)
                for d in discrepancies:
                    matched_npc = None
                    for nid, npc in eng.npc_states.items():
                        if (d.get("npc_id") == nid or d.get("npc_id") == npc.name or d.get("npc_id") in nid or nid in d.get("npc_id", "")):
                            matched_npc = npc
                            break
                    if matched_npc and d.get("is_legitimate_change") and d.get("suggested_fix"):
                        old_role = matched_npc.role
                        new_role = d["suggested_fix"]
                        if new_role != old_role:
                            matched_npc.record_role_change(new_role, d.get("reason", "剧情演变"), eng.world_state.current_day if eng.world_state else 0)
                            audit_results.append(f"审计补标记: {matched_npc.name} {old_role}→{new_role}")
                            if eng.lorebook:
                                eng.lorebook.update_npc_entry(matched_npc.name, matched_npc.get_identity_summary())
            except Exception as e:
                logger.warning("Identity audit failed: %s", e, exc_info=True)
        return audit_results

    def _handle_experience(self, player_input, narrative):
        eng = self.engine
        if not (eng.level_system and eng.level_system.system_type != "none"):
            return None
        action_type = eng._classify_action_type(player_input, narrative or "")
        exp_amount = eng.level_system.calc_exp_for_action(action_type)
        exp_result = eng.level_system.add_experience(exp_amount)
        if exp_result.get("leveled_up") and eng.player_state:
            eng.level_system.apply_level_bonuses(eng.player_state)
        near_hint = eng.level_system.get_near_level_up_hint()
        if near_hint and not exp_result.get("leveled_up"):
            exp_result["near_level_hint"] = near_hint
        if eng.player_state:
            level_names = eng.level_system.get_all_level_names()
            level_keywords = {"martial": ["凡人", "炼体", "内息", "先天", "宗师", "大宗师", "武圣", "武道"], "cultivation": ["凡人", "炼气", "筑基", "金丹", "元婴", "化神", "渡劫", "大乘"], "magic": ["学徒", "法师", "元素师", "魔导", "法圣", "魔法", "魔力"], "none": []}
            keywords = level_keywords.get(eng.level_system.system_type, [])
            eng.player_state.tags = [t for t in eng.player_state.tags if t not in level_names and not any(kw in t for kw in keywords)]
        return exp_result

    def _extract_narrative_from_json(self, text):
        if not text:
            return text
        stripped = text.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                import json
                data = json.loads(stripped)
                if isinstance(data, dict) and "narrative" in data:
                    return data["narrative"]
                for key in ["text", "content", "story", "description"]:
                    if key in data and isinstance(data[key], str):
                        return data[key]
            except (json.JSONDecodeError, KeyError):
                pass
        json_start = stripped.find('{"narrative"')
        if json_start >= 0:
            try:
                depth = 0
                for i in range(json_start, len(stripped)):
                    if stripped[i] == '{': depth += 1
                    elif stripped[i] == '}':
                        depth -= 1
                        if depth == 0:
                            import json
                            data = json.loads(stripped[json_start:i+1])
                            if "narrative" in data:
                                return data["narrative"]
                            break
            except (json.JSONDecodeError, KeyError):
                pass
        return text

    # [v12] AI时间跳跃标记提取与清除
    _TIME_SKIP_PATTERN = re.compile(r'<!--\s*TIME_SKIP\s*:\s*(\d+)\s*-->')
    # [Bug 修复] 单次时间跳跃上限：防 LLM 极端幻觉（如 99999999）。
    # 设为 1 万年（3650000 天），兼容修仙闭关几万年的叙事需求，
    # 同时拦住明显的荒谬数字。超龄保护和不死标签另行处理真实寿命约束。
    _MAX_TIME_SKIP_DAYS = 3650000

    def _extract_time_skip_tag(self, text):
        """[v12] 从叙事文本中提取AI时间跳跃标记，返回天数（int或None）

        [Bug 修复] 加上限校验：LLM 幻觉可能输出 TIME_SKIP: 99999999 之类荒谬数字。
        上限设为 1 万年（兼容修仙闭关），超过视为幻觉截断并记 warning。
        真正的寿命约束由超龄保护 + death_system 不死标签处理。
        """
        if not text:
            return None
        m = self._TIME_SKIP_PATTERN.search(text)
        if m:
            try:
                days = int(m.group(1))
                if days > 0:
                    if days > self._MAX_TIME_SKIP_DAYS:
                        logger.warning(
                            "[v12] AI时间跳跃标记 %d 天超过上限 %d，截断（疑似LLM幻觉）",
                            days, self._MAX_TIME_SKIP_DAYS,
                        )
                        days = self._MAX_TIME_SKIP_DAYS
                    logger.info("[v12] 检测到AI时间跳跃标记: +%d天", days)
                    return days
            except ValueError:
                pass
        return None

    def _strip_time_skip_tag(self, text):
        """[v12] 从叙事文本中移除AI时间跳跃标记"""
        if not text:
            return text
        return self._TIME_SKIP_PATTERN.sub('', text).strip()

    def _record_history(self, narrative, player_input, world_event):
        eng = self.engine
        if narrative:
            eng.narrative_history.append({"type": "narrative", "day": eng.world_state.current_day if eng.world_state else 0, "time": eng.world_state.current_time if eng.world_state else "", "text": narrative, "player_input": player_input})
        if world_event:
            eng.narrative_history.append({"type": "event", "day": eng.world_state.current_day if eng.world_state else 0, "time": eng.world_state.current_time if eng.world_state else "", "text": world_event.get("narrative", ""), "event_type": world_event.get("event_type", "")})
        # [v10.1] 实时检查叙事历史长度，超过阈值生成摘要（不替换原始记录）
        if len(eng.narrative_history) > eng.MAX_NARRATIVE_HISTORY and eng.memory_curator:
            try:
                eng.memory_curator.generate_summary_only(
                    eng.narrative_history,
                    current_turn=eng.meta.current_turn if eng.meta else 0,
                    current_day=eng.world_state.current_day if eng.world_state else 1,
                )
                logger.info("Summary generated (history preserved): %d entries",
                            len(eng.narrative_history))
            except Exception as e:
                logger.warning("Summary generation failed: %s", e, exc_info=True)
