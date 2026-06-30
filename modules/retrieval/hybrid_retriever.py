"""混合检索器：BM25 + 向量 + GraphRAG，使用 RRF 融合 + 轻量重排。

[v10+] 叙事类型感知：根据当前场景类型动态调整三路检索权重。
  - 动感叙事（战斗/探险）：提升 GraphRAG 权重
  - 内省叙事（心理/浪漫）：跳过 GraphRAG，避免有害干扰
  - 其他场景：均衡或按关系网络价值调整
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bm25_retriever import BM25Retriever
    from ..narrative_scene_detector import SceneDetector, SceneType

logger = logging.getLogger("chronoverse.retrieval")

# 默认检索权重（无场景检测器或场景类型未知时使用）
DEFAULT_WEIGHTS: dict[str, float] = {
    "bm25": 0.33,
    "vector": 0.40,
    "graph": 0.27,
}


class HybridRetriever:
    """混合检索器，融合 BM25、向量检索和图谱检索结果。"""

    def __init__(self, bm25: "BM25Retriever | None" = None, vector_store=None,
                 graph_rag=None, scene_detector: "SceneDetector | None" = None):
        self.bm25 = bm25
        self.vector_store = vector_store  # MemoryStore 实例
        self.graph_rag = graph_rag
        # [v10+] 叙事场景检测器（可选注入，用于动态调整检索权重）
        self.scene_detector = scene_detector
        self._rrf_k: int = 60  # RRF 常数

    def set_vector_store(self, vector_store):
        """延迟注入向量存储（MemoryStore 在世界加载后才创建）。"""
        self.vector_store = vector_store

    def set_scene_detector(self, scene_detector: "SceneDetector | None"):
        """[v10+] 注入叙事场景检测器。"""
        self.scene_detector = scene_detector

    def retrieve(self, query: str, top_k: int = 10, filters: dict | None = None,
                 current_turn: int = 0, current_day: int = 0,
                 scene_type: "SceneType | None" = None,
                 entity_hints: list[str] | None = None) -> list[dict]:
        """
        混合检索。
        返回: [{"id": str, "text": str, "score": float, "source": str}]

        [v10+] scene_type: 当前叙事场景类型。若提供且已注入 scene_detector，
        将按场景类型动态调整三路检索权重；内省场景跳过 GraphRAG。
        [v11] entity_hints: 查询扩展提取的实体名列表，传递给图谱查询。
        [v11] current_day: 当前游戏天数，用于时间衰减排序。
        """
        # [v10+] 根据场景类型解析检索权重
        weights = self._resolve_weights(scene_type)
        use_graph = self._should_use_graph(scene_type)

        if scene_type is not None:
            logger.debug(
                "Hybrid retrieve scene=%s weights=%s use_graph=%s",
                getattr(scene_type, "value", scene_type), weights, use_graph,
            )

        bm25_results = []
        vector_results = []
        graph_results = []

        # 1. BM25 检索
        if self.bm25:
            try:
                bm25_results = self.bm25.search(query, top_k=top_k * 2)
                for r in bm25_results:
                    r["source"] = "bm25"
            except Exception as e:
                logger.warning("BM25 search failed: %s", e)

        # 2. 向量检索（优先使用带三维度评分的 ranked 检索）
        if self.vector_store:
            try:
                if hasattr(self.vector_store, "search_memory_ranked"):
                    vector_results = self.vector_store.search_memory_ranked(
                        query, n_results=top_k * 2, current_turn=current_turn
                    )
                else:
                    vector_results = self.vector_store.search_memory(
                        query, n_results=top_k * 2
                    )
                for r in vector_results:
                    r["source"] = "vector"
            except Exception as e:
                logger.warning("Vector search failed: %s", e)

        # 3. GraphRAG 检索（query 返回 list[str]，需转换为统一 dict 结构）
        # [v10+] 内省场景跳过 GraphRAG（IJHCI 2025：对内省叙事有害）
        if self.graph_rag and use_graph:
            try:
                # [v11] 优先使用 entity_hints 进行精准图谱查询
                if entity_hints:
                    raw_graph = self.graph_rag.query_by_entity(
                        entity_hints, time_window_days=30, max_results=top_k
                    )
                else:
                    # 回退到基于文本的模糊查询
                    raw_list = self.graph_rag.query(query, max_results=top_k)
                    raw_graph = []
                    for i, text in enumerate(raw_list):
                        raw_graph.append({
                            "id": f"graph_{i}",
                            "text": text,
                            "score": 0.0,
                            "source": "graph",
                        })
                graph_results = raw_graph if isinstance(raw_graph, list) else []
                for r in graph_results:
                    r.setdefault("source", "graph")
            except Exception as e:
                logger.warning("GraphRAG search failed: %s", e)
        elif self.graph_rag and not use_graph:
            logger.debug("GraphRAG skipped for introspective scene")

        # 4. RRF 融合（带场景感知权重）
        fused = self._rrf_fuse(
            bm25_results, vector_results, graph_results,
            weights=weights,
        )

        # 5. 轻量重排
        reranked = self._rerank(fused, query, current_day=current_day,
                                 entity_hints=entity_hints)

        return reranked[:top_k]

    def _resolve_weights(self, scene_type: "SceneType | None") -> dict[str, float]:
        """[v10+] 解析当前检索权重。失败时回退到默认权重。"""
        if scene_type is None or self.scene_detector is None:
            return dict(DEFAULT_WEIGHTS)
        try:
            return self.scene_detector.get_retrieval_weights(scene_type)
        except Exception as e:
            logger.warning("SceneDetector weight resolution failed, using defaults: %s", e)
            return dict(DEFAULT_WEIGHTS)

    def _should_use_graph(self, scene_type: "SceneType | None") -> bool:
        """[v10+] 判断是否启用 GraphRAG。内省场景关闭。"""
        if scene_type is None or self.scene_detector is None:
            return True
        try:
            return self.scene_detector.should_use_graph_rag(scene_type)
        except Exception as e:
            logger.warning("SceneDetector should_use_graph_rag failed: %s", e)
            return True

    def _rrf_fuse(self, *result_lists, weights: dict[str, float] | None = None) -> list[dict]:
        """Reciprocal Rank Fusion 融合多路检索结果。

        [v10+] 支持按来源权重加权：每路检索的 RRF 分数乘以对应权重。
        weights: {"bm25": float, "vector": float, "graph": float}
        """
        if weights is None:
            weights = DEFAULT_WEIGHTS

        # 按来源映射权重，缺省为 1.0
        source_weights = {
            "bm25": weights.get("bm25", 1.0),
            "vector": weights.get("vector", 1.0),
            "graph": weights.get("graph", 1.0),
        }

        scores: dict[str, float] = {}
        meta: dict[str, dict] = {}

        for results in result_lists:
            for rank, r in enumerate(results):
                doc_id = r.get("id", "")
                if not doc_id:
                    continue
                source = r.get("source", "")
                w = source_weights.get(source, 1.0)
                rrf_score = 1.0 / (self._rrf_k + rank + 1)
                # [v10+] 按场景感知权重加权
                scores[doc_id] = scores.get(doc_id, 0) + rrf_score * w
                if doc_id not in meta:
                    meta[doc_id] = {"id": doc_id, "text": r.get("text", ""), "sources": []}
                meta[doc_id]["sources"].append(source)

        fused = []
        for doc_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
            entry = meta[doc_id]
            entry["score"] = score
            fused.append(entry)
        return fused

    def _rerank(self, results: list[dict], query: str, current_day: int = 0,
                entity_hints: list[str] | None = None) -> list[dict]:
        """[v11] 增强重排：实体匹配 + 来源多样性 + 时间衰减 + 重要性加分。"""
        query_chars = set(query)
        entity_set = set(entity_hints or [])

        for r in results:
            # 1. 实体匹配加分：查询中的字符在文档中出现的比例（原有）
            doc_chars = set(r.get("text", ""))
            overlap = len(query_chars & doc_chars) / max(1, len(query_chars))
            r["score"] += overlap * 0.10

            # 2. 来源多样性加分：多源命中的文档更可靠（原有）
            source_count = len(set(r.get("sources", [])))
            r["score"] += (source_count - 1) * 0.05

            # [v11] 3. 时间衰减：更近期的记忆权重更高（半衰期 ~30天）
            # 从 metadata 或结果字段中提取 day
            metadata = r.get("metadata") or r.get("metadatas") or {}
            doc_day = None
            if isinstance(metadata, dict):
                doc_day = metadata.get("day")
            if doc_day is None and r.get("day"):
                doc_day = r["day"]
            if current_day > 0 and doc_day is not None:
                days_diff = max(0, current_day - doc_day)
                time_boost = 0.20 * (2.0 ** (-days_diff / 30.0))
                r["score"] += time_boost

            # [v11] 4. 重要性加分：高重要性记忆优先
            importance = None
            if isinstance(metadata, dict):
                importance = metadata.get("importance")
            if importance is not None:
                try:
                    imp = float(importance)
                    r["score"] += imp * 0.15
                except (ValueError, TypeError):
                    pass

            # [v11] 5. 实体命中加分：查询扩展出的实体名出现在结果中
            if entity_set:
                doc_text = r.get("text", "")
                entity_hits = sum(1 for e in entity_set if e in doc_text)
                if entity_hits > 0:
                    r["score"] += entity_hits * 0.10

        results.sort(key=lambda x: x["score"], reverse=True)
        return results
