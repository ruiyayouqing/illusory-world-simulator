"""
[v9] GraphRAG 知识图谱

参考 MiroFish 的 GraphRAG 设计，实现：
- 从叙事文本中自动提取实体和关系
- 构建 NetworkX 知识图谱
- 图遍历检索 + 向量检索混合
- 实体消歧和关系推理
- [v9] 两层实体验证（规则引擎 + LLM二次确认）
"""
from __future__ import annotations
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM

logger = logging.getLogger("chronoverse.graph_rag")

# 延迟导入 NetworkX（可选依赖）
_nx = None

def _get_nx():
    global _nx
    if _nx is None:
        try:
            import networkx as nx
            _nx = nx
        except ImportError:
            logger.warning("NetworkX 未安装，GraphRAG 功能不可用。pip install networkx")
            return None
    return _nx


class GraphEntity:
    """图谱实体"""
    def __init__(self, name: str, entity_type: str = "unknown",
                 description: str = "", attributes: dict = None):
        self.name = name
        self.entity_type = entity_type
        self.description = description
        self.attributes = attributes or {}
        self.mention_count = 1
        # [v11] 时间索引：实体首次出现、最后出现的回合和天数
        self.first_seen_turn = 0
        self.last_seen_turn = 0
        self.mention_days: set[int] = set()
        # [v12] 来源标记：novel=原著既定 / game=玩家游玩产生 / derived=推演推导
        self.source_type: str = "game"
        # [NovelRoleplay] 未来标记：玩家进入时间点之后才出现的实体/关系
        # is_future=True 的实体在检索时降权，避免剧透；但存在于图谱中供潜在 NPC 使用
        self.is_future: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.entity_type,
                "description": self.description, "mentions": self.mention_count,
                "source_type": self.source_type, "is_future": self.is_future}

    @property
    def is_active(self) -> bool:
        """实体是否仍有效（未被取代）"""
        return self.attributes.get("temporal_validity", "active") == "active"


# [v12] 关系类型常量
RELATION_TYPES = {
    "social": ["friends_with", "enemy_of", "family_of", "mentor_of", "allied_with"],
    "spatial": ["located_in", "near", "travels_to"],
    "temporal": ["happened_before", "happened_after", "concurrent_with"],
    "causal": ["caused_by", "leads_to", "prevents", "enables"],
    "possession": ["owns", "holds", "lost"],
    "membership": ["belongs_to", "member_of", "leads"],
    "influence": ["influences", "changed_by"],
}


class GraphRelation:
    """图谱关系（[v12] 增强时序+因果+有效性）"""
    def __init__(self, source: str, target: str, relation_type: str,
                 description: str = "", weight: float = 1.0):
        self.source = source
        self.target = target
        self.relation_type = relation_type
        self.description = description
        self.weight = weight
        # [v11] 关系产生的回合
        self.turn = 0
        self.day = 0  # [v12] 关系产生的天数
        # [v12] 时序有效性
        self.temporal_validity: str = "active"
        # active=当前有效 / superseded=已被后续事件取代 / expired=已失效
        self.effective_turn = 0    # 关系生效回合
        self.expired_turn: int | None = None  # 关系失效回合（None=仍有效）
        self.expired_day: int | None = None
        # [v12] 因果链
        self.causal_chain: list[str] = []  # ["事件A导致事件B", ...]
        self.caused_by_event: str = ""  # 触发此关系的事件描述
        # [v12] 来源标记
        self.source_type: str = "game"
        # novel=原著既定事实 / game=玩家游玩产生 / derived=推演推导
        self.superseded_by: str | None = None  # 被哪条新关系取代（关系ID）
        # [NovelRoleplay] 未来标记：玩家进入时间点之后才产生的关系
        # is_future=True 的关系在检索时降权；但保留在图谱中，当未来角色登场后可激活
        self.is_future: bool = False

    @property
    def is_active(self) -> bool:
        """关系是否仍有效"""
        return self.temporal_validity == "active"

    @property
    def relation_id(self) -> str:
        """关系唯一标识"""
        return f"{self.source}->{self.target}:{self.relation_type}:{self.effective_turn}"

    def to_dict(self) -> dict:
        return {
            "source": self.source, "target": self.target,
            "type": self.relation_type, "description": self.description,
            "weight": self.weight, "turn": self.turn, "day": self.day,
            "temporal_validity": self.temporal_validity,
            "effective_turn": self.effective_turn,
            "expired_turn": self.expired_turn,
            "expired_day": self.expired_day,
            "causal_chain": self.causal_chain,
            "caused_by_event": self.caused_by_event,
            "source_type": self.source_type,
            "superseded_by": self.superseded_by,
            "is_future": self.is_future,
        }


EXTRACT_ENTITIES_PROMPT = """从以下文本中提取重要实体（人物、地点、物品、组织、事件）。

【文本】
{text}

【输出JSON格式】
{{
    "entities": [
        {{"name": "实体名", "type": "person/place/item/org/event", "description": "一句话描述"}}
    ]
}}
只输出JSON。最多提取15个实体。"""

EXTRACT_RELATIONS_PROMPT = """根据以下实体列表和文本，提取实体之间的关系。

【实体列表】
{entities_text}

【文本】
{text}

【输出JSON格式】
{{
    "relations": [
        {{"source": "实体A", "target": "实体B", "type": "关系类型", "description": "关系描述", "caused_by": "触发此关系的事件（如有）"}}
    ]
}}
只输出JSON。关系类型如：
- 社会关系：friends_with、enemy_of、family_of、mentor_of、allied_with
- 因果关系：caused_by、leads_to、prevents、enables
- 拥有关系：owns、holds、lost
- 空间关系：located_in、near、travels_to
- 隶属关系：belongs_to、member_of、leads
- 影响关系：influences、changed_by"""


# [v12] schema_hint 常量，传给 chat_json 避免硬编码游戏叙事格式
EXTRACT_ENTITIES_SCHEMA_HINT = """{
    "entities": [
        {"name": "实体名", "type": "person/place/item/org/event", "description": "一句话描述"}
    ]
}
（最多提取15个实体）"""

EXTRACT_RELATIONS_SCHEMA_HINT = """{
    "relations": [
        {"source": "实体A", "target": "实体B", "type": "关系类型", "description": "关系描述", "caused_by": "触发此关系的事件（如有）"}
    ]
}"""


# [v12加速] 批量提取 prompt：实体+关系一次输出
EXTRACT_BATCH_PROMPT = """从以下文本中提取重要实体（人物、地点、物品、组织、事件）和实体之间的关系。

【文本】
{text}

【输出JSON格式】
{{
    "entities": [
        {{"name": "实体名", "type": "person/place/item/org/event", "description": "一句话描述"}}
    ],
    "relations": [
        {{"source": "实体A", "target": "实体B", "type": "关系类型", "description": "关系描述", "caused_by": "触发此关系的事件（如有）"}}
    ]
}}
只输出JSON。最多提取15个实体和10条关系。
关系类型如：friends_with、enemy_of、family_of、mentor_of、allied_with、caused_by、leads_to、owns、located_in、belongs_to、member_of、leads、influences。"""

EXTRACT_BATCH_SCHEMA_HINT = """{
    "entities": [
        {"name": "实体名", "type": "person/place/item/org/event", "description": "一句话描述"}
    ],
    "relations": [
        {"source": "实体A", "target": "实体B", "type": "关系类型", "description": "关系描述", "caused_by": "触发此关系的事件（如有）"}
    ]
}
（最多提取15个实体和10条关系）"""


# [v12] 候选人物确认 prompt：让 LLM 从规则扫描结果中判断真人物
CONFIRM_CHARACTERS_PROMPT = """你是一个中文小说分析专家。下面是从小说中通过规则扫描得到的"疑似人物名"候选名单。
但是其中混杂了很多误识别词（如"有人""不知""女子"等描述性词语）。
请你判断哪些是真的人物名，哪些是误识别。

【候选名单】
{candidates}

【小说样本（用于理解上下文）】
{novel_sample}

【任务】
1. 只返回你确信是"真实人物名"的候选
2. 过滤掉描述性词语（如"有人""女子""老者"等通用称呼）
3. 过滤掉动词/副词（如"不知""于是"等）
4. 为每个真人物提供一句话身份描述

【输出JSON格式】
{{
    "characters": [
        {{"name": "人物名", "description": "身份描述（如：主角/反派/配角等）"}}
    ]
}}"""

CONFIRM_CHARACTERS_SCHEMA_HINT = """{
    "characters": [
        {"name": "人物名", "description": "身份描述（如：主角/反派/配角等）"}
    ]
}
（只返回确信是真人物名的候选，过滤掉描述性词语和误识别词）"""


class GraphRAG:
    """[v9] GraphRAG 知识图谱 — 集成两层实体验证"""

    def __init__(self, llm: "BaseLLM" = None, entity_validator=None):
        self.llm = llm
        self.entities: dict[str, GraphEntity] = {}
        self.relations: list[GraphRelation] = []
        self._nx_graph = None
        # [v9] 实体验证器（可选注入）
        self._validator = entity_validator

    def set_validator(self, validator):
        """设置实体验证器"""
        self._validator = validator

    def build_from_narrative(self, narrative: str, day: int = 0, turn: int = 0,
                             source_type: str = "game"):
        """
        [v9] 从叙事文本中提取实体和关系，构建图谱。
        使用两层验证架构：规则引擎 + LLM二次确认。
        [v11] 接受 day 和 turn 参数，用于时间索引。
        [v12] 接受 source_type 标记来源（novel/game/derived）。
        """
        if not self.llm:
            return
        try:
            # [v9] 使用实体验证器（如果可用）
            if self._validator:
                validation = self._validator.validate(narrative, use_llm=True)
                for e in validation.entities:
                    if e.name in self.entities:
                        self.entities[e.name].mention_count += 1
                        # [v11] 更新时间索引
                        if turn > self.entities[e.name].last_seen_turn:
                            self.entities[e.name].last_seen_turn = turn
                        if day > 0:
                            self.entities[e.name].mention_days.add(day)
                    else:
                        self.entities[e.name] = GraphEntity(
                            name=e.name,
                            entity_type=e.entity_type,
                            description=e.description,
                        )
                        # [v11] 设置首次出现时间
                        self.entities[e.name].first_seen_turn = turn
                        self.entities[e.name].last_seen_turn = turn
                        if day > 0:
                            self.entities[e.name].mention_days.add(day)
                    # [v12] 标记来源
                    self.entities[e.name].source_type = source_type
                logger.debug("实体验证: 规则=%d, LLM=%d, 总计=%d",
                             validation.rule_count, validation.llm_count,
                             len(validation.entities))
            else:
                # 回退：原有LLM抽取逻辑
                entities = self._extract_entities(narrative)
                for e in entities:
                    name = e.get("name", "")
                    if not name:
                        continue
                    if name in self.entities:
                        self.entities[name].mention_count += 1
                        # [v11] 更新时间索引
                        if turn > self.entities[name].last_seen_turn:
                            self.entities[name].last_seen_turn = turn
                        if day > 0:
                            self.entities[name].mention_days.add(day)
                    else:
                        self.entities[name] = GraphEntity(
                            name=name,
                            entity_type=e.get("type", "unknown"),
                            description=e.get("description", ""),
                        )
                        # [v11] 设置首次出现时间
                        self.entities[name].first_seen_turn = turn
                        self.entities[name].last_seen_turn = turn
                        if day > 0:
                            self.entities[name].mention_days.add(day)
                    # [v12] 标记来源
                    self.entities[name].source_type = source_type

            # 提取关系
            entity_list = [{"name": n, "type": e.entity_type} for n, e in self.entities.items()]
            if len(entity_list) >= 2:
                relations = self._extract_relations(narrative, entity_list)
                for r in relations:
                    if r.get("source") and r.get("target"):
                        rel = GraphRelation(
                            source=r["source"], target=r["target"],
                            relation_type=r.get("type", "related_to"),
                            description=r.get("description", ""),
                        )
                        rel.turn = turn  # [v11] 记录关系产生的回合
                        rel.day = day    # [v12] 记录天数
                        rel.effective_turn = turn  # [v12] 生效回合
                        rel.source_type = source_type  # [v12] 来源标记
                        self.relations.append(rel)
        except Exception as e:
            logger.warning("GraphRAG 构建失败: %s", e)

    # [v12] 时序关系管理 ─────────────────────────────────

    def build_from_narrative_batch(self, narrative: str, day: int = 0, turn: int = 0,
                                    source_type: str = "novel") -> dict:
        """
        [v12加速] 实体+关系合并为单次 LLM 调用。

        与 build_from_narrative 的区别：
        - 原方法：2 次 LLM 调用（先实体后关系）
        - 此方法：1 次 LLM 调用，一次输出 entities + relations

        返回：
            {"entities": N, "relations": N}
        """
        if not self.llm:
            return {"entities": 0, "relations": 0}
        try:
            # 单次 prompt 同时提取实体和关系
            prompt = EXTRACT_BATCH_PROMPT.format(text=narrative[:6000])
            result = self.llm.chat_json(
                prompt, temperature=0.2, max_tokens=0,
                schema_hint=EXTRACT_BATCH_SCHEMA_HINT,
            )

            # 处理实体
            entities_data = result.get("entities", [])
            for e in entities_data:
                name = e.get("name", "")
                if not name:
                    continue
                if name in self.entities:
                    self.entities[name].mention_count += 1
                    if turn > self.entities[name].last_seen_turn:
                        self.entities[name].last_seen_turn = turn
                    if day > 0:
                        self.entities[name].mention_days.add(day)
                else:
                    self.entities[name] = GraphEntity(
                        name=name,
                        entity_type=e.get("type", "unknown"),
                        description=e.get("description", ""),
                    )
                    self.entities[name].first_seen_turn = turn
                    self.entities[name].last_seen_turn = turn
                    if day > 0:
                        self.entities[name].mention_days.add(day)
                self.entities[name].source_type = source_type

            # 处理关系
            relations_data = result.get("relations", [])
            for r in relations_data:
                if r.get("source") and r.get("target"):
                    rel = GraphRelation(
                        source=r["source"], target=r["target"],
                        relation_type=r.get("type", "related_to"),
                        description=r.get("description", ""),
                    )
                    rel.turn = turn
                    rel.day = day
                    rel.effective_turn = turn
                    rel.source_type = source_type
                    rel.caused_by_event = r.get("caused_by", "")
                    self.relations.append(rel)

            return {"entities": len(entities_data), "relations": len(relations_data)}
        except Exception as e:
            logger.warning("GraphRAG 批量构建失败: %s", e)
            return {"entities": 0, "relations": 0}

    def confirm_characters_from_candidates(self, candidates: list,
                                            novel_text_sample: str = "") -> list:
        """
        [v12] 让 LLM 从规则扫描的候选名单中判断哪些是真人物。

        替代之前的"规则补全"逻辑：不再把所有候选都加入图谱，
        而是把候选名单 + 代表性上下文交给 LLM，让 LLM 判断：
        - 哪些是真人物名
        - 人物的身份/描述

        参数：
            candidates: CharacterCandidate 对象列表
            novel_text_sample: 小说样本（用于让 LLM 理解上下文）

        返回：
            确认的人物名列表
        """
        if not self.llm or not candidates:
            return []

        # 构建候选名单文本
        candidate_lines = []
        for cand in candidates:
            ctx = cand.sample_contexts[0] if cand.sample_contexts else ""
            candidate_lines.append(
                f"- {cand.name}（出现{cand.mention_count}次，"
                f"首次章节{cand.first_chapter + 1}）: {ctx[:60]}"
            )
        candidates_text = "\n".join(candidate_lines[:60])  # 限制最多60个候选

        prompt = CONFIRM_CHARACTERS_PROMPT.format(
            candidates=candidates_text,
            novel_sample=novel_text_sample[:2000],
        )

        try:
            result = self.llm.chat_json(
                prompt, temperature=0.1, max_tokens=0,
                schema_hint=CONFIRM_CHARACTERS_SCHEMA_HINT,
            )
            confirmed = result.get("characters", [])

            # 把确认的人物加入图谱
            new_count = 0
            existing_names = set(self.entities.keys())
            for ch in confirmed:
                name = ch.get("name", "")
                if not name or name in existing_names:
                    continue
                # 找对应的候选对象，取 mention_count 和 first_chapter
                matching_cand = next(
                    (c for c in candidates if c.name == name), None
                )
                self.entities[name] = GraphEntity(
                    name=name,
                    entity_type="person",
                    description=ch.get("description", ""),
                )
                if matching_cand:
                    self.entities[name].mention_count = matching_cand.mention_count
                    self.entities[name].first_seen_turn = matching_cand.first_chapter
                    self.entities[name].last_seen_turn = matching_cand.first_chapter
                self.entities[name].source_type = "novel_llm_confirmed"
                existing_names.add(name)
                new_count += 1

            logger.info("LLM 确认 %d/%d 个候选人物为真人物",
                        new_count, len(candidates))
            return [ch.get("name", "") for ch in confirmed if ch.get("name")]
        except Exception as e:
            logger.warning("LLM 确认候选人物失败: %s", e)
            return []

    def update_relation(self, source: str, target: str, new_relation_type: str,
                        description: str = "", turn: int = 0, day: int = 0,
                        caused_by_event: str = "", source_type: str = "game"):
        """
        [v12] 更新关系：标记旧同类关系为 superseded，新增当前有效关系。
        这是实现"事实失效"的核心方法。

        例如：玩家和李四从仇人变盟友
        → 旧关系 enemy_of 标记为 superseded（expired_turn=当前）
        → 新关系 allied_with 生效（effective_turn=当前）
        """
        new_rel = GraphRelation(
            source=source, target=target,
            relation_type=new_relation_type,
            description=description,
        )
        new_rel.turn = turn
        new_rel.day = day
        new_rel.effective_turn = turn
        new_rel.source_type = source_type
        new_rel.caused_by_event = caused_by_event

        # 标记同 source→target 的旧关系为 superseded
        for rel in self.relations:
            if (rel.source == source and rel.target == target
                    and rel.is_active):
                rel.temporal_validity = "superseded"
                rel.expired_turn = turn
                rel.expired_day = day
                rel.superseded_by = new_rel.relation_id
                # 因果链延续
                new_rel.causal_chain = list(rel.causal_chain)
                if rel.caused_by_event:
                    new_rel.causal_chain.append(
                        f"[turn={rel.effective_turn}] {rel.caused_by_event}"
                    )
                if caused_by_event:
                    new_rel.causal_chain.append(
                        f"[turn={turn}] {caused_by_event}"
                    )
                logger.debug("关系取代: %s(%s) → %s",
                             rel.relation_type, rel.temporal_validity,
                             new_relation_type)

        if not new_rel.causal_chain and caused_by_event:
            new_rel.causal_chain.append(f"[turn={turn}] {caused_by_event}")

        self.relations.append(new_rel)
        return new_rel

    def expire_relation(self, source: str, target: str,
                        relation_type: str = None, turn: int = 0, day: int = 0,
                        reason: str = ""):
        """
        [v12] 使关系失效（如角色死亡导致盟约失效）。
        不新增关系，只标记现有关系为 expired。
        """
        for rel in self.relations:
            if (rel.source == source and rel.target == target
                    and rel.is_active
                    and (relation_type is None
                         or rel.relation_type == relation_type)):
                rel.temporal_validity = "expired"
                rel.expired_turn = turn
                rel.expired_day = day
                if reason:
                    rel.causal_chain.append(
                        f"[turn={turn}] 失效原因: {reason}"
                    )
                logger.debug("关系失效: %s->%s (%s)",
                             source, target, rel.relation_type)

    def get_active_relations(self, entity_name: str = None,
                             source_type: str = None) -> list[GraphRelation]:
        """
        [v12] 获取当前有效的关系。
        可按实体名和来源类型过滤。
        """
        result = []
        for rel in self.relations:
            if not rel.is_active:
                continue
            if entity_name and rel.source != entity_name and rel.target != entity_name:
                continue
            if source_type and rel.source_type != source_type:
                continue
            result.append(rel)
        return result

    def get_relation_history(self, source: str, target: str) -> list[GraphRelation]:
        """
        [v12] 获取两个实体间的关系演变历史（按时间排序）。
        用于"你们之间发生过什么"这类查询。
        """
        history = [
            rel for rel in self.relations
            if (rel.source == source and rel.target == target)
            or (rel.source == target and rel.target == source)
        ]
        return sorted(history, key=lambda r: r.effective_turn)

    def get_causal_chain(self, event_description: str = None,
                         entity_name: str = None) -> list[str]:
        """
        [v12] 获取因果链。
        可按事件描述或实体名查询因果链。
        """
        chains = []
        for rel in self.relations:
            if event_description and event_description in rel.caused_by_event:
                chains.extend(rel.causal_chain)
            elif entity_name and (rel.source == entity_name
                                  or rel.target == entity_name):
                chains.extend(rel.causal_chain)
        return chains

    def query(self, question: str, max_depth: int = 2,
              max_results: int = 5, active_only: bool = True) -> list[str]:
        """
        基于图谱的检索。

        1. 识别问题中的实体
        2. 图遍历找相关子图
        3. 返回相关上下文
        [v12] active_only=True 时只检索有效关系（默认）
        """
        # 从问题中提取可能的实体名
        mentioned = []
        for name in self.entities:
            if name in question:
                mentioned.append(name)

        if not mentioned:
            # 模糊匹配：取问题中的关键词
            keywords = re.findall(r'[\u4e00-\u9fff]{2,}', question)
            for name in self.entities:
                for kw in keywords:
                    if kw in name or name in kw:
                        mentioned.append(name)
                        break

        if not mentioned:
            return []

        # BFS 遍历
        results = []
        visited = set()
        queue = [(name, 0) for name in mentioned]
        while queue and len(results) < max_results:
            current, depth = queue.pop(0)
            if current in visited or depth > max_depth:
                continue
            visited.add(current)

            entity = self.entities.get(current)
            if entity:
                results.append(f"[{entity.entity_type}] {entity.name}: {entity.description}")

            # 找相关关系
            for rel in self.relations:
                # [v12] 有效性过滤
                if active_only and not rel.is_active:
                    continue
                if rel.source == current and rel.target not in visited:
                    results.append(f"{rel.source} --[{rel.relation_type}]--> {rel.target}: {rel.description}")
                    queue.append((rel.target, depth + 1))
                elif rel.target == current and rel.source not in visited:
                    results.append(f"{rel.source} --[{rel.relation_type}]--> {rel.target}: {rel.description}")
                    queue.append((rel.source, depth + 1))

        return results[:max_results]

    def query_by_entity(self, entity_names: list[str], time_window_days: int = 0,
                        max_results: int = 5, active_only: bool = True) -> list[dict]:
        """
        [v11] 按实体名+时间窗口检索图谱。
        返回与 HybridRetriever 兼容的格式：list[dict] with id, text, score, source.
        [v12] active_only=True 时只检索有效关系（默认）

        entity_names: 要检索的实体名列表
        time_window_days: 时间窗口（天）。0 表示不限时间。
        max_results: 最大返回数
        """
        if not entity_names:
            return []

        results = []
        visited = set()
        # 以指定实体为起点 BFS
        queue = [(name, 0) for name in entity_names if name in self.entities]
        while queue and len(results) < max_results:
            current, depth = queue.pop(0)
            if current in visited or depth > 2:
                continue
            visited.add(current)

            entity = self.entities.get(current)
            if entity and entity.description:
                # 检查时间窗口
                if time_window_days > 0 and entity.mention_days:
                    max_day = max(entity.mention_days)
                    if max_day > time_window_days:
                        min_allowed = max_day - time_window_days
                        if not any(d >= min_allowed for d in entity.mention_days):
                            continue  # 不在时间窗口内
                score = min(1.0, entity.mention_count / 10.0)
                results.append({
                    "id": f"graph_entity_{current}",
                    "text": f"[{entity.entity_type}] {entity.name}: {entity.description}",
                    "score": score,
                    "source": "graph",
                })

            # 找相关关系
            for rel in self.relations:
                # [v12] 有效性过滤
                if active_only and not rel.is_active:
                    continue
                if rel.source == current and rel.target not in visited:
                    # 检查关系的时间窗口
                    if time_window_days > 0 and rel.turn > 0:
                        # 关系 turn 粗略估计（没有精确天数的就用 turn 近似）
                        pass
                    text = f"{rel.source} --[{rel.relation_type}]--> {rel.target}: {rel.description}"
                    if text not in visited:
                        results.append({
                            "id": f"graph_rel_{rel.source}_{rel.target}",
                            "text": text,
                            "score": max(0.5, rel.weight),
                            "source": "graph",
                        })
                        visited.add(text)
                    if rel.target not in visited:
                        queue.append((rel.target, depth + 1))
                elif rel.target == current and rel.source not in visited:
                    text = f"{rel.source} --[{rel.relation_type}]--> {rel.target}: {rel.description}"
                    if text not in visited:
                        results.append({
                            "id": f"graph_rel_{rel.source}_{rel.target}",
                            "text": text,
                            "score": max(0.5, rel.weight),
                            "source": "graph",
                        })
                        visited.add(text)
                    if rel.source not in visited:
                        queue.append((rel.source, depth + 1))

        return results[:max_results]

    def get_subgraph(self, entity_name: str, depth: int = 2) -> dict:
        """获取以某实体为中心的子图"""
        nx = _get_nx()
        if not nx:
            return {"nodes": [], "edges": []}
        graph = self._ensure_nx_graph()
        if entity_name not in graph:
            return {"nodes": [], "edges": []}
        # BFS 获取子图
        nodes = set()
        edges = []
        queue = [(entity_name, 0)]
        visited = set()
        while queue:
            current, d = queue.pop(0)
            if current in visited or d > depth:
                continue
            visited.add(current)
            nodes.add(current)
            for neighbor in graph.neighbors(current):
                edge_data = graph[current][neighbor]
                edges.append({"from": current, "to": neighbor,
                              "type": edge_data.get("type", "related")})
                queue.append((neighbor, d + 1))
        return {"nodes": list(nodes), "edges": edges}

    def to_visualization_data(self) -> dict:
        """导出为前端可视化数据（Cytoscape.js 格式）"""
        nodes = []
        _entity_names = set(self.entities.keys())
        for name, entity in self.entities.items():
            nodes.append({
                "data": {"id": name, "label": name,
                         "type": entity.entity_type,
                         "mentions": entity.mention_count}
            })
        edges = []
        seen = set()
        for rel in self.relations:
            # [Bug fix] 跳过引用未知实体的悬空边，避免 cytoscape 报错
            if rel.source not in _entity_names or rel.target not in _entity_names:
                continue
            key = (rel.source, rel.target, rel.relation_type)
            if key not in seen:
                seen.add(key)
                edges.append({
                    "data": {"source": rel.source, "target": rel.target,
                             "label": rel.relation_type}
                })
        return {"nodes": nodes, "edges": edges}

    # ── [v1.6 P1-4] 社区检测（势力划分） ──────────────────────

    # 社区配色（最多 12 个势力，超出循环复用）
    COMMUNITY_COLORS = [
        "#d4af37", "#4a8bc9", "#5a9a5a", "#c94545",
        "#8b5ac9", "#c98a3a", "#3ac9b5", "#c93a8a",
        "#7a9a3a", "#3a7ac9", "#c95a3a", "#5ac9a9",
    ]

    def detect_communities(self, method: str = "louvain",
                           active_only: bool = True,
                           min_community_size: int = 2) -> dict:
        """
        [v1.6 P1-4] 社区检测：识别图谱中的"势力"。

        使用 NetworkX 的 Louvain 算法对图谱做社区划分。
        社区内的实体连接紧密，可视为同一势力/集团/门派。

        参数：
            method: 检测算法，目前支持 "louvain"。后续可扩展 "label_propagation" 等。
            active_only: 是否仅基于有效关系（is_active=True）构建图。默认 True。
            min_community_size: 最小社区规模，小于此值的社区归入"散人"。

        返回：
            {
                "communities": [
                    {
                        "id": "community_0",
                        "color": "#d4af37",
                        "members": ["张三", "李四", ...],
                        "size": 5,
                        "leader": "张三",  # 入度/出度最高的成员
                        "cohesion": 0.78,  # 社区凝聚度（内部边数/理论最大值）
                    },
                    ...
                ],
                "lone_entities": ["路人甲", ...],  # 不属于任何社区的散人
                "stats": {
                    "total_entities": 30,
                    "total_relations": 50,
                    "community_count": 4,
                    "largest_community_size": 8,
                    "modularity": 0.42,  # 模块度，越高表示社区划分越明显
                }
            }
        """
        nx = _get_nx()
        if not nx or not self.entities:
            return {"communities": [], "lone_entities": [], "stats": {
                "total_entities": len(self.entities),
                "total_relations": len(self.relations),
                "community_count": 0, "largest_community_size": 0,
                "modularity": 0.0,
            }}

        # 构建无向图（社区检测需要）
        ug = nx.Graph()
        for name, entity in self.entities.items():
            ug.add_node(name, entity_type=entity.entity_type,
                        mention_count=entity.mention_count)
        edge_count = 0
        _entity_names = set(self.entities.keys())
        for rel in self.relations:
            if active_only and not rel.is_active:
                continue
            # [Bug fix] 跳过引用未知实体的悬空关系，避免引入幽灵节点
            if rel.source not in _entity_names or rel.target not in _entity_names:
                continue
            # 累加权重：多次同类关系增强连接强度
            if ug.has_edge(rel.source, rel.target):
                ug[rel.source][rel.target]["weight"] += rel.weight
            else:
                ug.add_edge(rel.source, rel.target, weight=rel.weight)
                edge_count += 1

        # 孤立节点（无任何连接）直接归入散人
        isolated = list(nx.isolates(ug))
        connected_nodes = [n for n in ug.nodes() if n not in isolated]

        if len(connected_nodes) < min_community_size:
            # 连接节点不足，全部归入散人
            return {
                "communities": [],
                "lone_entities": list(self.entities.keys()),
                "stats": {
                    "total_entities": len(self.entities),
                    "total_relations": edge_count,
                    "community_count": 0,
                    "largest_community_size": 0,
                    "modularity": 0.0,
                },
            }

        # 执行社区检测
        communities_list = []
        try:
            if method == "louvain":
                from networkx.algorithms.community import louvain_communities
                communities_list = list(louvain_communities(
                    ug, weight="weight", seed=42,
                ))
            elif method == "label_propagation":
                from networkx.algorithms.community import label_propagation_communities
                communities_list = list(label_propagation_communities(ug))
            elif method == "greedy":
                from networkx.algorithms.community import greedy_modularity_communities
                communities_list = list(greedy_modularity_communities(ug, weight="weight"))
            else:
                from networkx.algorithms.community import louvain_communities
                communities_list = list(louvain_communities(
                    ug, weight="weight", seed=42,
                ))
        except Exception as e:
            logger.warning("社区检测失败 (method=%s): %s", method, e)
            return {
                "communities": [], "lone_entities": list(self.entities.keys()),
                "stats": {
                    "total_entities": len(self.entities),
                    "total_relations": edge_count,
                    "community_count": 0, "largest_community_size": 0,
                    "modularity": 0.0, "error": str(e),
                },
            }

        # 计算模块度
        modularity = 0.0
        try:
            from networkx.algorithms.community import modularity
            modularity = float(modularity(
                ug, communities_list, weight="weight", resolution=1.0,
            ))
        except Exception:
            pass

        # 转换为势力结构
        communities = []
        lone_entities_set = set(isolated)  # 用集合去重
        for i, comm in enumerate(communities_list):
            members = list(comm)
            if len(members) < min_community_size:
                lone_entities_set.update(members)
                continue
            # 颜色循环复用
            color = self.COMMUNITY_COLORS[i % len(self.COMMUNITY_COLORS)]
            # 识别"首领"：在原图中度数最高的成员
            leader = self._detect_community_leader(ug, members)
            # 凝聚度：内部边数 / 理论最大边数
            cohesion = self._compute_cohesion(ug, members)
            communities.append({
                "id": f"community_{i}",
                "color": color,
                "members": members,
                "size": len(members),
                "leader": leader,
                "cohesion": round(cohesion, 3),
            })

        # 按规模降序
        communities.sort(key=lambda c: c["size"], reverse=True)
        # 重新编号
        for i, c in enumerate(communities):
            c["id"] = f"faction_{i + 1}"
            c["color"] = self.COMMUNITY_COLORS[i % len(self.COMMUNITY_COLORS)]

        # 转为列表（保留原始实体顺序，便于前端稳定展示）
        lone_entities = [n for n in self.entities.keys() if n in lone_entities_set]
        largest = communities[0]["size"] if communities else 0
        return {
            "communities": communities,
            "lone_entities": lone_entities,
            "stats": {
                "total_entities": len(self.entities),
                "total_relations": edge_count,
                "community_count": len(communities),
                "largest_community_size": largest,
                "modularity": round(modularity, 3),
            },
        }

    @staticmethod
    def _detect_community_leader(graph, members: list) -> str:
        """识别社区首领：度数（含权重）最高的成员"""
        if not members:
            return ""
        best = members[0]
        best_score = -1.0
        for n in members:
            score = 0.0
            for _, _, data in graph.edges(n, data=True):
                score += data.get("weight", 1.0)
            if score > best_score:
                best_score = score
                best = n
        return best

    @staticmethod
    def _compute_cohesion(graph, members: list) -> float:
        """社区凝聚度：实际内部边数 / 理论最大边数"""
        n = len(members)
        if n < 2:
            return 0.0
        member_set = set(members)
        internal_edges = 0
        for u, v in graph.edges():
            if u in member_set and v in member_set:
                internal_edges += 1
        max_possible = n * (n - 1) / 2  # 无向图最大边数
        return internal_edges / max_possible if max_possible > 0 else 0.0

    def get_faction_visualization_data(self, method: str = "louvain",
                                        active_only: bool = True) -> dict:
        """
        [v1.6 P1-4] 获取势力图可视化数据（Cytoscape.js 格式）。

        在 to_visualization_data 基础上，附加社区信息：
        - 节点带 community_id 和 color
        - 边按 inter/intra 社区着色
        - 顶层含 communities 数组和 stats

        返回：
            {
                "elements": {"nodes": [...], "edges": [...]},
                "communities": [...],
                "stats": {...},
            }
        """
        community_data = self.detect_communities(
            method=method, active_only=active_only,
        )
        communities = community_data.get("communities", [])
        lone_entities = community_data.get("lone_entities", [])

        # 实体 -> 所属势力
        entity_to_community = {}
        entity_to_color = {}
        leader_set = set()
        for c in communities:
            for m in c["members"]:
                entity_to_community[m] = c["id"]
                entity_to_color[m] = c["color"]
            if c.get("leader"):
                leader_set.add(c["leader"])
        for name in lone_entities:
            entity_to_community[name] = "lone"
            entity_to_color[name] = "#8a7d6b"  # dim 灰色

        # 节点
        nodes = []
        # [Bug fix] 记录所有出现过的实体名（含边中引用但未在 entities 中的悬空节点）
        # 否则 cytoscape 会因找不到节点报 "Can not create edge" 错误，导致势力图空白
        _known_entity_names = set(self.entities.keys())
        for name, entity in self.entities.items():
            nodes.append({
                "data": {
                    "id": name, "label": name,
                    "type": entity.entity_type,
                    "mentions": entity.mention_count,
                    "community": entity_to_community.get(name, "lone"),
                    "color": entity_to_color.get(name, "#8a7d6b"),
                    "is_leader": name in leader_set,
                }
            })

        # 边
        edges = []
        seen = set()
        for rel in self.relations:
            if active_only and not rel.is_active:
                continue
            # [Bug fix] 跳过引用未知实体的悬空边，避免 cytoscape 报错
            if rel.source not in _known_entity_names or rel.target not in _known_entity_names:
                continue
            key = (rel.source, rel.target, rel.relation_type)
            if key in seen:
                continue
            seen.add(key)
            src_comm = entity_to_community.get(rel.source, "lone")
            tgt_comm = entity_to_community.get(rel.target, "lone")
            # 同社区边金色，跨社区边紫色虚线
            if src_comm == tgt_comm and src_comm != "lone":
                color = "rgba(212,175,55,0.5)"
                is_internal = True
            else:
                color = "rgba(139,90,201,0.4)"
                is_internal = False
            edges.append({
                "data": {
                    "source": rel.source, "target": rel.target,
                    "label": rel.relation_type,
                    "color": color,
                    "internal": is_internal,
                }
            })

        return {
            "elements": {"nodes": nodes, "edges": edges},
            "communities": communities,
            "lone_entities": lone_entities,
            "stats": community_data.get("stats", {}),
        }

    def get_context_for_prompt(self, query: str) -> str:
        """获取用于注入 LLM prompt 的图谱上下文"""
        results = self.query(query, max_depth=2, max_results=5)
        if not results:
            return ""
        return "【知识图谱检索】\n" + "\n".join(f"- {r}" for r in results)

    def to_dict(self) -> dict:
        """序列化（[v12] 包含时序/因果/来源字段）"""
        return {
            "entities": {n: e.to_dict() for n, e in self.entities.items()},
            "relations": [r.to_dict() for r in self.relations],
        }

    def from_dict(self, data: dict):
        """反序列化（[v12] 恢复时序/因果/来源字段，向后兼容旧数据）"""
        for name, edata in data.get("entities", {}).items():
            self.entities[name] = GraphEntity(
                name=name, entity_type=edata.get("type", "unknown"),
                description=edata.get("description", ""),
            )
            self.entities[name].mention_count = edata.get("mentions", 1)
            # [v12] 恢复来源标记
            self.entities[name].source_type = edata.get("source_type", "game")
            # [NovelRoleplay] 恢复未来标记
            self.entities[name].is_future = edata.get("is_future", False)
        for rdata in data.get("relations", []):
            rel = GraphRelation(
                source=rdata["source"], target=rdata["target"],
                relation_type=rdata.get("type", "related_to"),
                description=rdata.get("description", ""),
            )
            rel.turn = rdata.get("turn", 0)
            # [v12] 恢复时序字段
            rel.day = rdata.get("day", 0)
            rel.temporal_validity = rdata.get("temporal_validity", "active")
            rel.effective_turn = rdata.get("effective_turn", rel.turn)
            rel.expired_turn = rdata.get("expired_turn")
            rel.expired_day = rdata.get("expired_day")
            rel.caused_by_event = rdata.get("caused_by_event", "")
            rel.source_type = rdata.get("source_type", "game")
            rel.superseded_by = rdata.get("superseded_by")
            # [v12] 恢复因果链
            chain = rdata.get("causal_chain", [])
            if isinstance(chain, list):
                rel.causal_chain = chain
            # [NovelRoleplay] 恢复未来标记
            rel.is_future = rdata.get("is_future", False)
            self.relations.append(rel)

    # ── 内部方法 ──────────────────────────────────────────

    def _extract_entities(self, text: str) -> list[dict]:
        prompt = EXTRACT_ENTITIES_PROMPT.format(text=text[:3000])
        # [v12修复] 传入 schema_hint 避免被 chat_json 强制改为游戏叙事格式
        result = self.llm.chat_json(
            prompt, temperature=0.2, max_tokens=0,
            schema_hint=EXTRACT_ENTITIES_SCHEMA_HINT,
        )
        return result.get("entities", [])

    def _extract_relations(self, text: str, entities: list[dict]) -> list[dict]:
        entities_text = "\n".join([
            f"- {e.get('name', '')} ({e.get('type', 'unknown')})"
            for e in entities if e.get("name")
        ])
        prompt = EXTRACT_RELATIONS_PROMPT.format(
            entities_text=entities_text, text=text[:3000]
        )
        result = self.llm.chat_json(
            prompt, temperature=0.2, max_tokens=0,
            schema_hint=EXTRACT_RELATIONS_SCHEMA_HINT,
        )
        return result.get("relations", [])

    def _extract_relations_enhanced(self, text: str, entities: list[dict],
                                    day: int = 0, turn: int = 0,
                                    source_type: str = "game") -> list[GraphRelation]:
        """
        [v12] 增强关系提取：自动应用时序和因果字段。
        被 build_from_narrative 调用。
        """
        raw_relations = self._extract_relations(text, entities)
        result = []
        for r in raw_relations:
            if not (r.get("source") and r.get("target")):
                continue
            rel = GraphRelation(
                source=r["source"], target=r["target"],
                relation_type=r.get("type", "related_to"),
                description=r.get("description", ""),
            )
            rel.turn = turn
            rel.day = day
            rel.effective_turn = turn
            rel.source_type = source_type
            caused_by = r.get("caused_by", "")
            if caused_by:
                rel.caused_by_event = caused_by
                rel.causal_chain.append(f"[turn={turn}] {caused_by}")
            result.append(rel)
        return result

    def _ensure_nx_graph(self):
        """构建或更新 NetworkX 图"""
        nx = _get_nx()
        if not nx:
            return None
        if self._nx_graph is None:
            self._nx_graph = nx.DiGraph()
        for name, entity in self.entities.items():
            self._nx_graph.add_node(name, **entity.attributes,
                                     entity_type=entity.entity_type)
        for rel in self.relations:
            self._nx_graph.add_edge(rel.source, rel.target,
                                     type=rel.relation_type,
                                     weight=rel.weight)
        return self._nx_graph
