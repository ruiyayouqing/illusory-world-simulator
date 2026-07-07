"""叙事场景类型检测器：检测当前场景类型，动态调整检索策略。

参考 IJHCI 2025 研究发现：
  - GraphRAG 对动感叙事（战斗/探险/科幻）显著正面
  - GraphRAG 对内省叙事（心理/浪漫）反而有害

因此按当前场景类型动态调整 BM25 / 向量 / GraphRAG 三路检索权重，
在内省场景下降权甚至关闭 GraphRAG，在动作/探索场景提升 GraphRAG 权重。

检测基于关键词匹配 + 频率统计，不调用 LLM，保证快速。
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("chronoverse.scene_detector")


class SceneType(Enum):
    """场景类型。"""
    ACTION = "action"          # 动作/战斗/探险
    SOCIAL = "social"          # 社交/对话
    INTROSPECTIVE = "introspective"  # 内省/心理/浪漫
    EXPLORATION = "exploration"  # 探索/旅行
    COMMERCE = "commerce"      # 交易/经济
    STUDY = "study"            # 学习/修炼
    DAILY = "daily"            # 日常生活
    UNKNOWN = "unknown"


@dataclass
class SceneDetectionResult:
    """场景检测结果。"""
    scene_type: SceneType
    confidence: float  # 0-1
    keywords_matched: list[str] = field(default_factory=list)
    is_dynamic: bool = False  # 是否为动感叙事（action/exploration）


class SceneDetector:
    """场景类型检测器。

    通过关键词匹配 + 频率统计判断当前叙事场景类型，
    并据此返回 HybridRetriever 各路检索（bm25/vector/graph）的权重。
    """

    # 场景关键词映射
    SCENE_KEYWORDS: dict[SceneType, list[str]] = {
        SceneType.ACTION: [
            "战斗", "攻击", "杀", "打", "剑", "刀", "拳", "掌", "踢", "挡", "闪",
            "魔法", "法术", "咒语", "射击", "弓箭", "爆炸", "冲锋", "防御", "格挡",
            "受伤", "流血", "死亡", "敌人", "怪物", "妖兽", "魔兽", "对决", "决斗",
            "战场", "厮杀", "猛攻", "突袭", "伏击", "撤退", "逃跑",
        ],
        SceneType.SOCIAL: [
            "对话", "聊天", "说", "问", "答", "笑", "哭", "怒", "骂", "劝",
            "朋友", "同伴", "聚会", "宴会", "酒馆", "交谈", "讨论", "争论",
            "道歉", "感谢", "赞美", "嘲讽", "威胁", "承诺", "誓言",
        ],
        SceneType.INTROSPECTIVE: [
            "想", "思考", "回忆", "记忆", "梦", "感觉", "感受", "内心",
            "孤独", "悲伤", "忧郁", "思念", "怀念", "遗憾", "悔恨",
            "爱", "恋", "情", "心", "灵魂", "意识", "觉醒", "感悟",
            "犹豫", "纠结", "矛盾", "挣扎", "迷茫", "彷徨",
        ],
        SceneType.EXPLORATION: [
            "探索", "发现", "寻找", "搜索", "调查", "检查", "观察",
            "旅行", "出发", "到达", "路径", "地图", "方向", "北方", "南方",
            "森林", "山脉", "河流", "洞穴", "遗迹", "古城", "密室", "宝藏",
        ],
        SceneType.COMMERCE: [
            "买", "卖", "交易", "价格", "金币", "银两", "钱", "商店",
            "市场", "商人", "讨价还价", "拍卖", "典当", "雇佣",
        ],
        SceneType.STUDY: [
            "学习", "修炼", "练习", "研读", "参悟", "领悟", "突破",
            "功法", "秘籍", "书籍", "卷轴", "师傅", "教导", "指点",
        ],
        SceneType.DAILY: [
            "吃饭", "喝水", "睡觉", "休息", "起床", "洗漱", "穿衣",
            "散步", "闲逛", "发呆", "打盹", "日常", "清晨", "傍晚",
        ],
    }

    # 动感叙事类型（GraphRAG 有正面效果）
    DYNAMIC_TYPES = {SceneType.ACTION, SceneType.EXPLORATION}
    # 内省叙事类型（GraphRAG 有负面效果）
    INTROSPECTIVE_TYPES = {SceneType.INTROSPECTIVE}

    def __init__(self):
        self._history: list[SceneDetectionResult] = []
        self._max_history: int = 20

    def detect(self, text: str) -> SceneDetectionResult:
        """
        检测文本的场景类型。
        基于关键词匹配 + 频率统计。
        """
        if not text:
            return SceneDetectionResult(SceneType.UNKNOWN, 0.0, [], False)

        scores: dict[SceneType, int] = {}
        matched: dict[SceneType, list[str]] = {}

        for scene_type, keywords in self.SCENE_KEYWORDS.items():
            count = 0
            matched_kws = []
            for kw in keywords:
                if kw in text:
                    count += text.count(kw)
                    matched_kws.append(kw)
            if count > 0:
                scores[scene_type] = count
                matched[scene_type] = matched_kws

        if not scores:
            result = SceneDetectionResult(SceneType.UNKNOWN, 0.0, [], False)
        else:
            # 选择得分最高的类型
            best_type = max(scores, key=scores.get)
            total_score = sum(scores.values())
            confidence = scores[best_type] / total_score if total_score > 0 else 0
            is_dynamic = best_type in self.DYNAMIC_TYPES
            result = SceneDetectionResult(
                scene_type=best_type,
                confidence=min(1.0, confidence),
                keywords_matched=matched[best_type],
                is_dynamic=is_dynamic,
            )

        self._history.append(result)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        return result

    def get_retrieval_weights(self, scene_type: SceneType) -> dict[str, float]:
        """
        根据场景类型返回检索策略权重。
        权重用于 HybridRetriever 的各路检索。
        """
        if scene_type in self.DYNAMIC_TYPES:
            # 动感叙事：GraphRAG 权重高
            return {
                "bm25": 0.30,
                "vector": 0.35,
                "graph": 0.35,
            }
        elif scene_type in self.INTROSPECTIVE_TYPES:
            # 内省叙事：GraphRAG 权重低甚至关闭
            return {
                "bm25": 0.35,
                "vector": 0.60,
                "graph": 0.05,  # 极低权重
            }
        elif scene_type == SceneType.SOCIAL:
            # 社交：中等图谱权重（关系网络有用）
            return {
                "bm25": 0.30,
                "vector": 0.45,
                "graph": 0.25,
            }
        elif scene_type == SceneType.COMMERCE:
            # 交易：图谱权重中等（物品关系有用）
            return {
                "bm25": 0.35,
                "vector": 0.40,
                "graph": 0.25,
            }
        else:
            # 默认：均衡
            return {
                "bm25": 0.33,
                "vector": 0.40,
                "graph": 0.27,
            }

    def should_use_graph_rag(self, scene_type: SceneType) -> bool:
        """是否应该使用 GraphRAG。"""
        if scene_type in self.INTROSPECTIVE_TYPES:
            return False
        return True

    def get_current_trend(self) -> SceneType:
        """获取最近的场景趋势（最近5次的众数）。"""
        if not self._history:
            return SceneType.UNKNOWN
        recent = self._history[-5:]
        type_counts: dict[SceneType, int] = {}
        for r in recent:
            type_counts[r.scene_type] = type_counts.get(r.scene_type, 0) + 1
        return max(type_counts, key=type_counts.get) if type_counts else SceneType.UNKNOWN

    def get_stats(self) -> dict:
        return {
            "history_size": len(self._history),
            "current_trend": self.get_current_trend().value,
            "last_scene": self._history[-1].scene_type.value if self._history else None,
        }
