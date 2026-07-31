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

    # [L] 按世界类型扩展的关键词表
    # 基础 SCENE_KEYWORDS 是通用的，但不同世界类型有专属词汇（如仙侠的"筑基"、科幻的"星舰"）
    # 这些额外关键词会合并到基础关键词里，提升场景检测精度
    WORLD_TYPE_EXTRA_KEYWORDS: dict[str, dict[SceneType, list[str]]] = {
        "historical": {  # 历史/穿越
            SceneType.SOCIAL: ["朝堂", "科举", "皇帝", "藩镇", "使节", "贡品", "奏折", "御史"],
            SceneType.ACTION: ["兵变", "谋反", "锦衣卫", "东厂", "禁军", "边关"],
        },
        "wuxia": {  # 武侠
            SceneType.ACTION: ["江湖", "内功", "外功", "轻功", "暗器", "毒药", "解药", "武林", "盟主", "仇家", "比武"],
            SceneType.SOCIAL: ["门派", "掌门", "师兄", "师妹", "师姐", "师弟"],
            SceneType.STUDY: ["秘籍", "心法", "招式"],
        },
        "xianxia": {  # 仙侠/修真
            SceneType.STUDY: ["修真", "筑基", "金丹", "元婴", "渡劫", "飞升", "灵气", "灵石",
                              "法器", "丹药", "宗门", "长老", "功法", "参悟", "顿悟"],
            SceneType.ACTION: ["妖兽", "魔修", "斗法", "雷劫"],
            SceneType.SOCIAL: ["弟子", "师尊", "同门"],
        },
        "fantasy": {  # 奇幻
            SceneType.ACTION: ["魔法", "咒语", "法师", "骑士", "巨龙", "精灵", "矮人", "巫师",
                              "圣剑", "咒文", "魔物"],
            SceneType.COMMERCE: ["魔药", "冒险者公会", "委托"],
            SceneType.SOCIAL: ["国王", "公主", "贵族"],
        },
        "scifi": {  # 科幻
            SceneType.ACTION: ["飞船", "星舰", "机器人", "殖民", "外星", "激光", "机甲"],
            SceneType.STUDY: ["量子", "纳米", "基因", "脑机"],
            SceneType.EXPLORATION: ["赛博", "虚拟现实", "殖民星", "虫洞", "星系"],
            SceneType.SOCIAL: ["联邦", "帝国", "议会"],
        },
        "postapocalyptic": {  # 末日/废土
            SceneType.ACTION: ["丧尸", "变异体", "辐射", "病毒", "感染者"],
            SceneType.EXPLORATION: ["避难所", "搜刮", "废墟", "遗迹", "补给点"],
            SceneType.SOCIAL: ["幸存者", "营地", "据点", "商队"],
        },
        "modern": {  # 现代/都市
            SceneType.SOCIAL: ["公司", "老板", "同事", "上班", "加班", "会议", "客户"],
            SceneType.DAILY: ["地铁", "咖啡", "手机", "网络", "外卖"],
            SceneType.COMMERCE: ["股市", "房地产", "投资", "基金"],
        },
    }

    def __init__(self, world_type: str = ""):
        self._history: list[SceneDetectionResult] = []
        self._max_history: int = 20
        # [L] 当前世界类型（影响场景检测关键词）
        self._world_type: str = (world_type or "").lower()
        # [L] 合并后的关键词表（基础 + 世界类型扩展）
        self._merged_keywords: dict[SceneType, list[str]] = self._build_merged_keywords()

    def _build_merged_keywords(self) -> dict[SceneType, list[str]]:
        """[L] 构建合并后的关键词表：基础关键词 + 当前世界类型的扩展关键词"""
        merged = {st: list(kws) for st, kws in self.SCENE_KEYWORDS.items()}
        extra = self.WORLD_TYPE_EXTRA_KEYWORDS.get(self._world_type, {})
        for scene_type, kws in extra.items():
            if scene_type in merged:
                merged[scene_type].extend(kws)
            else:
                merged[scene_type] = list(kws)
        return merged

    def set_world_type(self, world_type: str):
        """[L] 设置世界类型并重建关键词表。

        应在 GameEngine 加载世界后调用，让场景检测器适配当前世界类型。
        """
        new_wt = (world_type or "").lower()
        if new_wt != self._world_type:
            self._world_type = new_wt
            self._merged_keywords = self._build_merged_keywords()
            logger.info("SceneDetector world_type updated: %s", new_wt or "(default)")

    def detect(self, text: str) -> SceneDetectionResult:
        """
        检测文本的场景类型。
        基于关键词匹配 + 频率统计。

        [L] 使用合并后的关键词表（基础 + 当前世界类型扩展），
        提升特定世界类型（如仙侠/科幻/末日）的场景检测精度。
        """
        if not text:
            return SceneDetectionResult(SceneType.UNKNOWN, 0.0, [], False)

        scores: dict[SceneType, int] = {}
        matched: dict[SceneType, list[str]] = {}

        for scene_type, keywords in self._merged_keywords.items():
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
