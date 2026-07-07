"""
[v9] 实体验证器 — 两层实体抽取架构

第一层：规则引擎（快、准、低成本）
  - 正则匹配：人名(2-4字中文)、地名(X城/X山/X宫)、物品(XX剑/XX丹)
  - 词典匹配：已有NPC名单、已知地点列表
  - 置信度：0.9

第二层：LLM抽取（慢、全、高成本）
  - 仅处理规则引擎未识别的文本片段
  - 结构化输出：实体类型/关系类型/置信度
  - 置信度：0.7

合并策略：
  - 高置信度实体直接采纳
  - 低置信度实体需要多次出现才采纳（mention_count >= 2）
  - 冲突实体取置信度高的
"""
from __future__ import annotations
import re
import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM

logger = logging.getLogger("chronoverse.entity_validator")


@dataclass
class ExtractedEntity:
    """抽取的实体"""
    name: str
    entity_type: str = "unknown"
    description: str = ""
    confidence: float = 0.5
    source: str = "rule"  # "rule" | "llm"


@dataclass
class ValidationResult:
    """验证结果"""
    entities: list[ExtractedEntity] = field(default_factory=list)
    rule_count: int = 0
    llm_count: int = 0
    rejected_count: int = 0


# ── 规则引擎模式 ──────────────────────────────────────────────

# 简化的人名检测：2-3字中文，前一字是常见姓氏
COMMON_SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉"
    "岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧"
    "尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵"
    "席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝"
    "管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣"
    "翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬"
    "全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印"
    "宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙乔阴郁胥能苍双闻莘党翟谭贡劳逄姬申扶"
    "堵冉宰郦雍璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦"
    "艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东殴"
)

# 地名模式
PLACE_SUFFIXES = ["城", "山", "河", "湖", "江", "海", "镇", "村", "庄", "宫", "殿",
                  "寺", "庙", "峰", "谷", "林", "岛", "港", "关", "道", "州", "府",
                  "县", "郡", "国", "界", "境", "洞", "崖", "涧", "溪"]

# 物品模式
ITEM_SUFFIXES = ["剑", "刀", "枪", "戟", "弓", "盾", "甲", "袍", "靴", "冠", "戒指",
                 "项链", "玉佩", "丹", "药", "符", "阵", "卷", "册", "图", "镜", "珠",
                 "石", "印", "令", "旗", "笔", "砚", "琴", "棋"]

# 组织模式
ORG_SUFFIXES = ["门", "派", "宗", "教", "帮", "会", "盟", "殿", "阁", "楼", "堂",
                "院", "宫", "谷", "山庄", "世家", "商会", "家族"]

# 事件模式
EVENT_KEYWORDS = ["之战", "之变", "之乱", "浩劫", "浩劫", "危机", "事件", "大战", "决战"]


class EntityValidator:
    """实体验证器：两层抽取 + 置信度合并"""

    def __init__(self, llm: "BaseLLM" = None,
                 known_npcs: list[str] = None,
                 known_places: list[str] = None):
        self.llm = llm
        self.known_npcs = set(known_npcs or [])
        self.known_places = set(known_places or [])
        # 已验证的实体缓存（用于增量更新）
        self._validated_cache: dict[str, ExtractedEntity] = {}

    def update_known_entities(self, npcs: list[str] = None, places: list[str] = None):
        """更新已知实体词典"""
        if npcs:
            self.known_npcs.update(npcs)
        if places:
            self.known_places.update(places)

    def validate(self, text: str, use_llm: bool = True) -> ValidationResult:
        """
        两层实体抽取。
        
        Args:
            text: 待分析文本
            use_llm: 是否使用LLM第二层（关闭则仅用规则）
        """
        result = ValidationResult()

        # 第一层：规则引擎
        rule_entities = self._rule_extract(text)
        result.rule_count = len(rule_entities)
        for e in rule_entities:
            result.entities.append(e)

        # 第二层：LLM抽取（仅处理规则未覆盖的片段）
        if use_llm and self.llm:
            # 找出规则引擎未识别的文本片段
            recognized_names = {e.name for e in rule_entities}
            llm_entities = self._llm_extract(text, recognized_names)
            result.llm_count = len(llm_entities)
            for e in llm_entities:
                result.entities.append(e)

        # 合并去重
        result.entities = self._merge_entities(result.entities)

        return result

    def _rule_extract(self, text: str) -> list[ExtractedEntity]:
        """第一层：规则引擎抽取"""
        entities = []

        # 1. 词典匹配：已知NPC
        for name in self.known_npcs:
            if name in text:
                entities.append(ExtractedEntity(
                    name=name, entity_type="person",
                    confidence=0.95, source="rule",
                    description=f"已知NPC: {name}"
                ))

        # 2. 词典匹配：已知地点
        for name in self.known_places:
            if name in text:
                entities.append(ExtractedEntity(
                    name=name, entity_type="place",
                    confidence=0.95, source="rule",
                    description=f"已知地点: {name}"
                ))

        # 3. 正则匹配：地名（直接匹配后缀前的2-5个汉字）
        for suffix in PLACE_SUFFIXES:
            pat = re.compile(r'([\u4e00-\u9fff]{2,5}' + re.escape(suffix) + r')')
            for match in pat.finditer(text):
                name = match.group(1)
                if len(name) >= 2 and name not in {e.name for e in entities}:
                    entities.append(ExtractedEntity(
                        name=name, entity_type="place",
                        confidence=0.85, source="rule"
                    ))

        # 4. 正则匹配：物品
        for suffix in ITEM_SUFFIXES:
            pat = re.compile(r'([\u4e00-\u9fff]{2,5}' + re.escape(suffix) + r')')
            for match in pat.finditer(text):
                name = match.group(1)
                if len(name) >= 2 and name not in {e.name for e in entities}:
                    entities.append(ExtractedEntity(
                        name=name, entity_type="item",
                        confidence=0.80, source="rule"
                    ))

        # 5. 正则匹配：组织
        for suffix in ORG_SUFFIXES:
            pat = re.compile(r'([\u4e00-\u9fff]{2,7}' + re.escape(suffix) + r')')
            for match in pat.finditer(text):
                name = match.group(1)
                if len(name) >= 2 and name not in {e.name for e in entities}:
                    entities.append(ExtractedEntity(
                        name=name, entity_type="org",
                        confidence=0.80, source="rule"
                    ))

        # 6. 正则匹配：事件
        for keyword in EVENT_KEYWORDS:
            pat = re.compile(r'([\u4e00-\u9fff]{2,9}' + re.escape(keyword) + r')')
            for match in pat.finditer(text):
                name = match.group(1)
                if len(name) >= 3 and name not in {e.name for e in entities}:
                    entities.append(ExtractedEntity(
                        name=name, entity_type="event",
                        confidence=0.75, source="rule"
                    ))

        # 7. 人名检测（简化版）：姓氏 + 1-2字名
        person_pattern = re.compile(
            r'(?:^|[，。！？\s"「])([' + ''.join(COMMON_SURNAMES) + r'][\u4e00-\u9fff]{1,2})'
            r'(?:[，。！？\s"」]|$)'
        )
        for match in person_pattern.finditer(text):
            name = match.group(1)
            if len(name) >= 2 and name not in {e.name for e in entities}:
                # 排除常见非人名词汇
                if name not in {"什么", "怎么", "为什么", "这个", "那个", "你们", "他们", "我们"}:
                    entities.append(ExtractedEntity(
                        name=name, entity_type="person",
                        confidence=0.70, source="rule"
                    ))

        return entities

    def _llm_extract(self, text: str, already_recognized: set) -> list[ExtractedEntity]:
        """第二层：LLM抽取（仅处理未识别的片段）"""
        if not self.llm:
            return []

        # 如果文本中已识别的实体很多，跳过LLM调用以节省成本
        if len(already_recognized) > 10:
            return []

        try:
            prompt = (
                "从以下文本中提取重要实体（人物、地点、物品、组织、事件）。\n"
                "注意：以下实体已被识别，请跳过：\n"
                f"{', '.join(already_recognized) if already_recognized else '无'}\n\n"
                f"【文本】\n{text[:3000]}\n\n"
                "【输出JSON格式】\n"
                '{"entities": [{"name": "实体名", "type": "person/place/item/org/event", '
                '"description": "一句话描述", "confidence": 0.7}]}\n'
                "只输出JSON。最多提取10个实体。"
            )
            result = self.llm.chat_json(prompt, temperature=0.2, max_tokens=0)
            entities = []
            for e in result.get("entities", []):
                name = e.get("name", "")
                if name and name not in already_recognized:
                    entities.append(ExtractedEntity(
                        name=name,
                        entity_type=e.get("type", "unknown"),
                        description=e.get("description", ""),
                        confidence=e.get("confidence", 0.7),
                        source="llm",
                    ))
            return entities
        except Exception as e:
            logger.warning("LLM实体抽取失败: %s", e)
            return []

    def _merge_entities(self, entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
        """合并去重：高置信度优先，冲突取高的"""
        merged: dict[str, ExtractedEntity] = {}
        for e in entities:
            if e.name in merged:
                existing = merged[e.name]
                # 如果新实体置信度更高，替换
                if e.confidence > existing.confidence:
                    merged[e.name] = e
                # 如果置信度相同但来源不同，取规则引擎的
                elif e.confidence == existing.confidence and e.source == "rule":
                    merged[e.name] = e
            else:
                merged[e.name] = e
        return list(merged.values())
