"""
[v9] 意图分类器 — 玩家输入分析与生成策略选择

设计原则：
  - 不同意图走不同的LLM生成路径
  - 简单意图用模板生成，不需要LLM
  - 复杂意图才调用LLM，节省token和时间
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("chronoverse.intent_classifier")


class IntentType(Enum):
    """玩家意图类型"""
    OBSERVE = "observe"           # 观察环境
    EXPLORE = "explore"           # 探索搜索
    DIALOGUE = "dialogue"         # 与NPC对话
    TRADE = "trade"               # 交易买卖
    COMBAT = "combat"             # 战斗冲突
    REST = "rest"                 # 休息恢复
    TRAVEL = "travel"             # 移动旅行
    MANAGE = "manage"             # 管理/修整
    CRAFT = "craft"               # 制作/修炼
    QUEST = "quest"               # 任务相关
    CUSTOM = "custom"             # 自定义/复杂


class GenerationStrategy(Enum):
    """生成策略"""
    TEMPLATE = "template"         # 模板生成（不需要LLM）
    LIGHT_LLM = "light_llm"      # 轻量LLM调用（短叙事）
    FULL_LLM = "full_llm"        # 完整LLM调用
    COMPLEX_LLM = "complex_llm"  # 复杂LLM调用（多轮推理）


@dataclass
class IntentAnalysis:
    """意图分析结果"""
    intent_type: IntentType
    strategy: GenerationStrategy
    target: str = ""              # 目标NPC/地点
    detail: str = ""              # 具体动作
    confidence: float = 0.8       # 分类置信度
    is_time_sensitive: bool = False  # 是否需要时间推进
    needs_dice: bool = False      # 是否需要骰子判定


class IntentClassifier:
    """基于规则的意图分类器"""

    # 关键词映射表
    _OBSERVE_KEYWORDS = [
        "观察", "环顾", "打量", "查看", "看看", "扫视", "环视",
        "注意", "审视", "端详", "眺望", "张望", "巡视",
    ]
    _EXPLORE_KEYWORDS = [
        "搜索", "搜查", "翻找", "探索", "寻找", "搜寻", "查找",
        "挖掘", "翻开", "检查", "搜遍", "探查",
    ]
    _DIALOGUE_KEYWORDS = [
        "问", "说", "聊", "谈", "告诉", "回答", "回应",
        "对话", "交谈", "商议", "讨论", "诉说", "询问",
        "请教", "禀报", "回禀", "禀告",
    ]
    _TRADE_KEYWORDS = [
        "买", "卖", "购买", "出售", "交易", "买卖", "交换",
        "典当", "赊账", "讨价", "还价", "采购", "购置",
    ]
    _COMBAT_KEYWORDS = [
        "打", "杀", "斩", "刺", "砍", "攻", "战", "斗",
        "攻击", "战斗", "搏斗", "对决", "比武", "较量",
        "还击", "反击", "抵抗", "防守", "格挡", "闪避",
        "处决", "诛杀", "讨伐", "剿灭",
    ]
    _REST_KEYWORDS = [
        "休息", "歇息", "睡觉", "入睡", "就寝", "打盹",
        "小憩", "养伤", "疗伤", "恢复", "静养",
    ]
    _TRAVEL_KEYWORDS = [
        "走", "去", "前往", "离开", "出发", "赶路", "启程",
        "进入", "走出", "穿过", "来到", "抵达", "奔赴",
        "快马", "乘船", "骑马",
    ]
    _MANAGE_KEYWORDS = [
        "整理", "收拾", "清点", "检查", "维护", "修理",
        "管理", "安排", "部署", "规划", "准备",
    ]
    _CRAFT_KEYWORDS = [
        "修炼", "练功", "打坐", "运功", "吐纳", "闭关",
        "练剑", "练刀", "操练", "对练", "制作", "锻造",
        "炼丹", "炼药", "研习",
    ]
    _QUEST_KEYWORDS = [
        "任务", "委托", "使命", "目标", "完成", "报告",
        "交差", "复命", "线索", "调查",
    ]

    def classify(self, player_input: str, context: dict = None) -> IntentAnalysis:
        """分类玩家意图"""
        text = player_input.strip()

        # 先检查是否包含NPC名字（对话意图）
        target_npc = ""
        if context and context.get("npc_names"):
            for name in context["npc_names"]:
                if name in text:
                    target_npc = name
                    break

        # 检查是否包含地点名（移动意图）
        target_location = ""
        if context and context.get("locations"):
            for loc in context["locations"]:
                if loc in text:
                    target_location = loc
                    break

        # 关键词匹配
        scores = {
            IntentType.OBSERVE: self._count_keywords(text, self._OBSERVE_KEYWORDS),
            IntentType.EXPLORE: self._count_keywords(text, self._EXPLORE_KEYWORDS),
            IntentType.DIALOGUE: self._count_keywords(text, self._DIALOGUE_KEYWORDS),
            IntentType.TRADE: self._count_keywords(text, self._TRADE_KEYWORDS),
            IntentType.COMBAT: self._count_keywords(text, self._COMBAT_KEYWORDS),
            IntentType.REST: self._count_keywords(text, self._REST_KEYWORDS),
            IntentType.TRAVEL: self._count_keywords(text, self._TRAVEL_KEYWORDS),
            IntentType.MANAGE: self._count_keywords(text, self._MANAGE_KEYWORDS),
            IntentType.CRAFT: self._count_keywords(text, self._CRAFT_KEYWORDS),
            IntentType.QUEST: self._count_keywords(text, self._QUEST_KEYWORDS),
        }

        # 有NPC名字 → 倾向对话
        if target_npc:
            scores[IntentType.DIALOGUE] += 3

        # 有地名 → 倾向移动
        if target_location:
            scores[IntentType.TRAVEL] += 3

        # 问号 → 对话
        if "？" in text or "?" in text:
            scores[IntentType.DIALOGUE] += 2

        # [Bug] 输入长度超过20字或包含引号对话 → 强制走完整LLM，避免模板忽略玩家输入
        has_dialogue = ("'" in text or '"' in text or '"' in text or "'" in text or "说" in text or "道" in text or "：" in text)
        if len(text) > 20 or has_dialogue:
            # 复杂输入必须用 LLM 理解，不能用模板
            scores[IntentType.CUSTOM] = max(scores.values()) + 1 if max(scores.values()) > 0 else 5

        # 选择最高分
        max_score = max(scores.values())
        if max_score == 0:
            best_intent = IntentType.CUSTOM
        else:
            best_intent = max(scores, key=scores.get)

        # 确定生成策略
        strategy = self._select_strategy(best_intent)

        # 判断是否需要骰子
        needs_dice = best_intent == IntentType.COMBAT

        # 判断是否时间敏感
        is_time_sensitive = best_intent in (
            IntentType.TRAVEL, IntentType.REST, IntentType.CRAFT
        )

        return IntentAnalysis(
            intent_type=best_intent,
            strategy=strategy,
            target=target_npc or target_location,
            detail=text,
            confidence=min(0.95, 0.5 + max_score * 0.1),
            is_time_sensitive=is_time_sensitive,
            needs_dice=needs_dice,
        )

    def _count_keywords(self, text: str, keywords: list[str]) -> int:
        """统计文本中匹配的关键词数量"""
        count = 0
        for kw in keywords:
            if kw in text:
                count += 1
        return count

    def _select_strategy(self, intent: IntentType) -> GenerationStrategy:
        """根据意图选择生成策略"""
        strategy_map = {
            IntentType.OBSERVE: GenerationStrategy.TEMPLATE,      # 模板生成
            IntentType.EXPLORE: GenerationStrategy.LIGHT_LLM,     # 轻量LLM
            IntentType.DIALOGUE: GenerationStrategy.FULL_LLM,     # 完整LLM
            IntentType.TRADE: GenerationStrategy.LIGHT_LLM,       # 轻量LLM
            IntentType.COMBAT: GenerationStrategy.FULL_LLM,       # 完整LLM
            IntentType.REST: GenerationStrategy.TEMPLATE,         # 模板生成
            IntentType.TRAVEL: GenerationStrategy.LIGHT_LLM,      # 轻量LLM
            IntentType.MANAGE: GenerationStrategy.TEMPLATE,       # 模板生成
            IntentType.CRAFT: GenerationStrategy.LIGHT_LLM,       # 轻量LLM
            IntentType.QUEST: GenerationStrategy.FULL_LLM,        # 完整LLM
            IntentType.CUSTOM: GenerationStrategy.FULL_LLM,       # 完整LLM
        }
        return strategy_map.get(intent, GenerationStrategy.FULL_LLM)


class TemplateGenerator:
    """模板生成器 — 不需要LLM的简单叙事"""

    @staticmethod
    def generate_observe(player_state, world_state, npc_states: dict = None) -> dict:
        """生成观察环境的叙事"""
        # [Bug] 使用 location_name（如"汴京城"）而非 location code（如"bianjing"）
        loc_code = player_state.location if player_state else "此处"
        location = loc_code
        if world_state and hasattr(world_state, 'locations') and loc_code in world_state.locations:
            loc_obj = world_state.locations[loc_code]
            # 兼容 dict 和对象两种形式
            if isinstance(loc_obj, dict):
                location = loc_obj.get('location_name') or loc_obj.get('name') or loc_code
            elif hasattr(loc_obj, 'location_name'):
                location = loc_obj.location_name or loc_code
            elif hasattr(loc_obj, 'name'):
                location = loc_obj.name or loc_code
        time = world_state.current_time if world_state else "此时"
        season = world_state.season if world_state else "春季"
        weather = world_state.weather if world_state else "晴朗"

        # 环境描写模板（加长版，提升沉浸感）
        templates = {
            "清晨": "晨光熹微，{weather}的天空渐渐泛白。{location}在晨雾中缓缓苏醒，远处传来几声鸡鸣犬吠，炊烟袅袅升起。{season}的空气中带着一丝凉意，街道上已有早起的行人匆匆走过。",
            "上午": "阳光洒落，{location}人来人往，一派繁忙景象。商贩的吆喝声此起彼伏，马车碾过石板路发出辚辚声响。{season}的阳光温暖而明媚，街边的店铺陆续开张，伙计们忙着搬运货物、招揽客人。",
            "中午": "日头正烈，{location}的喧嚣声此起彼伏。正午的阳光直射而下，街上的行人纷纷寻找阴凉处歇脚。酒楼茶馆里坐满了用膳的客人，谈笑声、杯盏声交织在一起，热闹非凡。",
            "下午": "午后时分，阳光渐斜，{location}的节奏稍稍放缓。{season}的微风拂过街巷，带起些许尘土。街边的老人摇着蒲扇纳凉，孩童在巷口嬉戏追逐，一派悠闲景象。",
            "傍晚": "夕阳西下，暮色渐浓，{location}笼罩在一片金红之中。归巢的飞鸟掠过天际，街上的行人脚步匆匆，赶着在天黑前回到家中。店铺开始打烊，夜市的摊贩却在悄悄支起摊位。",
            "深夜": "夜深人静，{location}只有零星灯火，万籁俱寂。月光洒在空旷的街道上，投下斑驳的影子。偶尔传来几声犬吠，更显得夜色深沉。巡夜的更夫敲着梆子走过，一声声回荡在寂静的街巷中。",
        }

        template = templates.get(time, "此时{location}一片{weather}，{season}的气息弥漫在空气中。")
        narrative = template.format(
            weather=weather,
            location=location,
            season=season,
        )

        # 描述附近的NPC
        if npc_states:
            nearby = [npc for npc in npc_states.values()
                     if npc.current_location == player_state.location]
            if nearby:
                names = "、".join([npc.name for npc in nearby[:3]])
                # 根据NPC数量调整描述
                if len(nearby) == 1:
                    narrative += f"\n\n你注意到{nearby[0].name}就在不远处。"
                    if hasattr(nearby[0], 'title') and nearby[0].title:
                        narrative += f"这位{nearby[0].title}似乎正在做什么，神情专注。"
                else:
                    narrative += f"\n\n附近可以看到{names}等人，各自忙碌着不同的事情。"

        return {
            "narrative": narrative,
            "options": [
                {"id": "A", "text": "仔细观察周围环境", "type": "search", "risk": "low"},
                {"id": "B", "text": "找个人问问情况", "type": "dialogue", "risk": "low"},
                {"id": "C", "text": "继续前进", "type": "move", "risk": "low"},
            ],
        }

    @staticmethod
    def generate_rest(player_state) -> dict:
        """生成休息的叙事"""
        health = player_state.stats.health
        max_health = player_state.stats.max_health
        energy = player_state.stats.energy

        if health <= max_health * 0.3:
            narrative = "你找到一处安全的角落，盘膝坐下，闭目养伤。伤势虽重，但总算暂时安全了。"
        elif energy <= 20:
            narrative = "你疲惫不堪，找了个地方歇息。片刻之后，体力渐渐恢复。"
        else:
            narrative = "你稍作休息，调整状态。虽然没有大碍，但养精蓄锐总是好的。"

        return {
            "narrative": narrative,
            "options": [
                {"id": "A", "text": "继续休息", "type": "rest", "risk": "low"},
                {"id": "B", "text": "起身活动", "type": "action", "risk": "low"},
                {"id": "C", "text": "观察四周", "type": "search", "risk": "low"},
            ],
        }

    @staticmethod
    def generate_manage(player_state) -> dict:
        """生成管理/修整的叙事"""
        inventory = player_state.inventory
        items_text = ""
        if inventory.items:
            items = [f"{item.name}×{item.quantity}" for item in inventory.items[:5]]
            items_text = f"你清点了随身物品：{', '.join(items)}。"

        narrative = f"你利用闲暇时间整理了一下随身物品和装备。{items_text}" if items_text else "你整理了一下随身物品，一切井然有序。"

        return {
            "narrative": narrative,
            "options": [
                {"id": "A", "text": "继续整理", "type": "manage", "risk": "low"},
                {"id": "B", "text": "出发行动", "type": "action", "risk": "low"},
                {"id": "C", "text": "休息片刻", "type": "rest", "risk": "low"},
            ],
        }
