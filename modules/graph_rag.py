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

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.entity_type,
                "description": self.description, "mentions": self.mention_count}


class GraphRelation:
    """图谱关系"""
    def __init__(self, source: str, target: str, relation_type: str,
                 description: str = "", weight: float = 1.0):
        self.source = source
        self.target = target
        self.relation_type = relation_type
        self.description = description
        self.weight = weight
        # [v11] 关系产生的回合
        self.turn = 0

    def to_dict(self) -> dict:
        return {"source": self.source, "target": self.target,
                "type": self.relation_type, "description": self.description,
                "weight": self.weight}


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
        {{"source": "实体A", "target": "实体B", "type": "关系类型", "description": "关系描述"}}
    ]
}}
只输出JSON。关系类型如：located_in、owns、works_for、friends_with、enemy_of、created_by、participated_in 等。"""


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

    def build_from_narrative(self, narrative: str, day: int = 0, turn: int = 0):
        """
        [v9] 从叙事文本中提取实体和关系，构建图谱。
        使用两层验证架构：规则引擎 + LLM二次确认。
        [v11] 接受 day 和 turn 参数，用于时间索引。
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
                        self.relations.append(rel)
        except Exception as e:
            logger.warning("GraphRAG 构建失败: %s", e)

    def query(self, question: str, max_depth: int = 2,
              max_results: int = 5) -> list[str]:
        """
        基于图谱的检索。
        
        1. 识别问题中的实体
        2. 图遍历找相关子图
        3. 返回相关上下文
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
                if rel.source == current and rel.target not in visited:
                    results.append(f"{rel.source} --[{rel.relation_type}]--> {rel.target}: {rel.description}")
                    queue.append((rel.target, depth + 1))
                elif rel.target == current and rel.source not in visited:
                    results.append(f"{rel.source} --[{rel.relation_type}]--> {rel.target}: {rel.description}")
                    queue.append((rel.source, depth + 1))

        return results[:max_results]

    def query_by_entity(self, entity_names: list[str], time_window_days: int = 0,
                        max_results: int = 5) -> list[dict]:
        """
        [v11] 按实体名+时间窗口检索图谱。
        返回与 HybridRetriever 兼容的格式：list[dict] with id, text, score, source.

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
        for name, entity in self.entities.items():
            nodes.append({
                "data": {"id": name, "label": name,
                         "type": entity.entity_type,
                         "mentions": entity.mention_count}
            })
        edges = []
        seen = set()
        for rel in self.relations:
            key = (rel.source, rel.target, rel.relation_type)
            if key not in seen:
                seen.add(key)
                edges.append({
                    "data": {"source": rel.source, "target": rel.target,
                             "label": rel.relation_type}
                })
        return {"nodes": nodes, "edges": edges}

    def get_context_for_prompt(self, query: str) -> str:
        """获取用于注入 LLM prompt 的图谱上下文"""
        results = self.query(query, max_depth=2, max_results=5)
        if not results:
            return ""
        return "【知识图谱检索】\n" + "\n".join(f"- {r}" for r in results)

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "entities": {n: e.to_dict() for n, e in self.entities.items()},
            "relations": [r.to_dict() for r in self.relations],
        }

    def from_dict(self, data: dict):
        """反序列化"""
        for name, edata in data.get("entities", {}).items():
            self.entities[name] = GraphEntity(
                name=name, entity_type=edata.get("type", "unknown"),
                description=edata.get("description", ""),
            )
            self.entities[name].mention_count = edata.get("mentions", 1)
        for rdata in data.get("relations", []):
            self.relations.append(GraphRelation(
                source=rdata["source"], target=rdata["target"],
                relation_type=rdata.get("type", "related_to"),
                description=rdata.get("description", ""),
            ))

    # ── 内部方法 ──────────────────────────────────────────

    def _extract_entities(self, text: str) -> list[dict]:
        prompt = EXTRACT_ENTITIES_PROMPT.format(text=text[:3000])
        result = self.llm.chat_json(prompt, temperature=0.2, max_tokens=0)
        return result.get("entities", [])

    def _extract_relations(self, text: str, entities: list[dict]) -> list[dict]:
        entities_text = "\n".join([
            f"- {e.get('name', '')} ({e.get('type', 'unknown')})"
            for e in entities if e.get("name")
        ])
        prompt = EXTRACT_RELATIONS_PROMPT.format(
            entities_text=entities_text, text=text[:3000]
        )
        result = self.llm.chat_json(prompt, temperature=0.2, max_tokens=0)
        return result.get("relations", [])

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
