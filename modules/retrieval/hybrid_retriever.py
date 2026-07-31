"""混合检索器：BM25 + 向量 + GraphRAG + NovelKnowledgeBase，使用 RRF 融合 + 轻量重排。

[v10+] 叙事类型感知：根据当前场景类型动态调整三路检索权重。
  - 动感叙事（战斗/探险）：提升 GraphRAG 权重
  - 内省叙事（心理/浪漫）：跳过 GraphRAG，避免有害干扰
  - 其他场景：均衡或按关系网络价值调整
[v12] 新增第四路：NovelKnowledgeBase 静态知识库检索。
  - 仅在小说人物扮演模式启用
  - 检索原著背景知识，权重较低（可能已被玩家改写）
  - 有效性过滤：已被取代的原著事实降权
"""
from __future__ import annotations
import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bm25_retriever import BM25Retriever
    from ..narrative_scene_detector import SceneDetector, SceneType

logger = logging.getLogger("chronoverse.retrieval")

# 默认检索权重（无场景检测器或场景类型未知时使用）
DEFAULT_WEIGHTS: dict[str, float] = {
    "bm25": 0.25,
    "vector": 0.30,
    "graph": 0.25,
    "novel": 0.20,  # [v12] 第四路：小说知识库
}


class HybridRetriever:
    """混合检索器，融合 BM25、向量、图谱和小说知识库结果。"""

    # [C] 关键剧情场景：启用完整三路检索（BM25 + 向量 + GraphRAG）
    # 日常场景（DAILY/COMMERCE/STUDY）只用 BM25，避免每回合的向量检索 + GraphRAG 开销
    # 参考 IJHCI 2025：日常动作的检索增益有限，关键剧情（战斗/探索/内省/社交）才有显著价值
    KEY_PLOT_SCENE_TYPES: set[str] = {
        "action", "exploration", "introspective", "social", "unknown",
    }

    def __init__(self, bm25: "BM25Retriever | None" = None, vector_store=None,
                 graph_rag=None, scene_detector: "SceneDetector | None" = None,
                 novel_kb=None):
        self.bm25 = bm25
        self.vector_store = vector_store  # MemoryStore 实例
        self.graph_rag = graph_rag
        # [v10+] 叙事场景检测器（可选注入，用于动态调整检索权重）
        self.scene_detector = scene_detector
        # [v12] 小说知识库（可选注入）
        self.novel_kb = novel_kb
        self._rrf_k: int = 60  # RRF 常数

    def set_vector_store(self, vector_store):
        """延迟注入向量存储（MemoryStore 在世界加载后才创建）。"""
        self.vector_store = vector_store

    def set_scene_detector(self, scene_detector: "SceneDetector | None"):
        """[v10+] 注入叙事场景检测器。"""
        self.scene_detector = scene_detector

    def set_novel_kb(self, novel_kb):
        """[v12] 注入小说知识库（启用第四路检索）。"""
        self.novel_kb = novel_kb

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
        [v12] 新增第四路：novel_kb 检索原著背景知识（异步查询）。
        """
        # [v10+] 根据场景类型解析检索权重
        weights = self._resolve_weights(scene_type)
        use_graph = self._should_use_graph(scene_type)
        # [C] 关键剧情判断：日常场景只用 BM25，跳过向量 + GraphRAG
        is_key_plot = self._is_key_plot(scene_type)

        if scene_type is not None:
            logger.debug(
                "Hybrid retrieve scene=%s weights=%s use_graph=%s key_plot=%s",
                getattr(scene_type, "value", scene_type), weights, use_graph,
                is_key_plot,
            )

        bm25_results = []
        vector_results = []
        graph_results = []
        novel_results = []

        # 1. BM25 检索（始终执行，是日常场景的唯一检索路径）
        if self.bm25:
            try:
                bm25_results = self.bm25.search(query, top_k=top_k * 2)
                for r in bm25_results:
                    r["source"] = "bm25"
            except Exception as e:
                logger.warning("BM25 search failed: %s", e)

        # 2. 向量检索（优先使用带三维度评分的 ranked 检索）
        # [C] 仅关键剧情场景启用向量检索；日常场景跳过
        if self.vector_store and is_key_plot:
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
        elif self.vector_store and not is_key_plot:
            logger.debug("Vector search skipped for daily scene")

        # 3. GraphRAG 检索（query 返回 list[str]，需转换为统一 dict 结构）
        # [v10+] 内省场景跳过 GraphRAG（IJHCI 2025：对内省叙事有害）
        # [C] 日常场景也跳过 GraphRAG（同 #2 向量检索条件）
        if self.graph_rag and use_graph and is_key_plot:
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

        # [v12] 4. NovelKnowledgeBase 检索（原著背景知识）
        if self.novel_kb:
            try:
                # novel_kb.search_unified 返回统一格式结果
                if hasattr(self.novel_kb, "search_unified"):
                    novel_results = self.novel_kb.search_unified(
                        query, n_results=top_k
                    )
                elif hasattr(self.vector_store, "search_novel_facts"):
                    # 从 MemoryStore 检索原著事实
                    novel_results = self.vector_store.search_novel_facts(
                        query, n_results=top_k, active_only=True
                    )
                for r in novel_results:
                    r.setdefault("source", "novel")
            except Exception as e:
                logger.warning("NovelKB search failed: %s", e)

        # 5. RRF 融合（带场景感知权重）
        fused = self._rrf_fuse(
            bm25_results, vector_results, graph_results, novel_results,
            weights=weights,
        )

        # 6. 轻量重排（含有效性过滤）
        reranked = self._rerank(fused, query, current_day=current_day,
                                 entity_hints=entity_hints)

        # [v1.3] 7. 更新 top-k 记忆的 last_accessed_day / access_count（异步、容错）
        # 仅对最终被采用的结果更新，避免每回合写入过多
        if current_day > 0:
            try:
                self._update_access_tracking(reranked[:top_k], current_day)
            except Exception as e:
                logger.debug("Access tracking update failed: %s", e)

        return reranked[:top_k]

    def _update_access_tracking(self, top_results: list[dict], current_day: int):
        """[v1.3] 更新 top-k 记忆的访问跟踪字段。
        防膨胀：每回合每个 ID 只更新一次。"""
        if not self.vector_store or not top_results:
            return
        try:
            collection = getattr(self.vector_store, "collection", None)
            if collection is None:
                return
            # 复用 MemoryStore 的去重集合（如果存在）
            dedup_set = getattr(self.vector_store, "_last_access_update_ids", None)
            if dedup_set is None:
                dedup_set = set()

            ids_to_update = []
            new_metadatas = []
            for r in top_results:
                rid = r.get("id")
                if not rid or rid in dedup_set:
                    continue
                metadata = r.get("metadata") or r.get("metadatas") or {}
                if not isinstance(metadata, dict):
                    continue
                # 跳过非向量库结果（如 BM25/graph 纯文本结果无 id 在 collection 中）
                if r.get("source") not in ("vector", None):
                    # 仅更新向量库中的记忆
                    if r.get("source") in ("bm25", "graph", "novel"):
                        continue
                new_meta = dict(metadata)
                new_meta["last_accessed_day"] = current_day
                new_meta["access_count"] = int(metadata.get("access_count", 0)) + 1
                ids_to_update.append(rid)
                new_metadatas.append(new_meta)
                dedup_set.add(rid)

            if ids_to_update:
                try:
                    collection.update(ids=ids_to_update, metadatas=new_metadatas)
                except Exception as e:
                    logger.debug("Collection update for access tracking failed: %s", e)
        except Exception as e:
            logger.debug("Access tracking update error: %s", e)

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

    def _is_key_plot(self, scene_type: "SceneType | None") -> bool:
        """[C] 判断当前场景是否为关键剧情（需启用完整三路检索）。
        日常场景（DAILY/COMMERCE/STUDY）只用 BM25 即可，避免向量 + GraphRAG 开销。
        - None 或 UNKNOWN：保守走完整检索（避免漏召回）
        - ACTION/EXPLORATION/INTROSPECTIVE/SOCIAL：关键剧情，完整三路
        - DAILY/COMMERCE/STUDY：日常场景，只用 BM25
        """
        if scene_type is None:
            return True  # 保守：无场景信息时走完整检索
        scene_value = (scene_type.value
                       if hasattr(scene_type, "value")
                       else str(scene_type))
        return scene_value in self.KEY_PLOT_SCENE_TYPES

    def _rrf_fuse(self, *result_lists, weights: dict[str, float] | None = None) -> list[dict]:
        """Reciprocal Rank Fusion 融合多路检索结果。

        [v10+] 支持按来源权重加权：每路检索的 RRF 分数乘以对应权重。
        [v12] 新增 novel 来源权重。
        weights: {"bm25": float, "vector": float, "graph": float, "novel": float}
        """
        if weights is None:
            weights = DEFAULT_WEIGHTS

        # 按来源映射权重，缺省为 1.0
        source_weights = {
            "bm25": weights.get("bm25", 1.0),
            "vector": weights.get("vector", 1.0),
            "graph": weights.get("graph", 1.0),
            "novel": weights.get("novel", 0.8),  # [v12] 原著知识权重稍低
            "novel_facts": weights.get("novel", 0.8),
            "player_events": weights.get("vector", 1.0),  # 玩家事件走向量权重
            "causal": weights.get("graph", 1.0),         # 因果链走图谱权重
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
        """[v11] 增强重排：实体匹配 + 来源多样性 + 时间衰减 + 重要性加分。
        [v12] 新增有效性过滤：已被取代的原著事实降权。
        [v1.3] 艾宾浩斯遗忘曲线调优：半衰期 20 天 + 重要性影响衰减 + last_accessed 复习效应。"""
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

            # 提取 metadata 与 day/importance/last_accessed/access_count
            metadata = r.get("metadata") or r.get("metadatas") or {}
            doc_day = None
            importance = 0.0
            last_accessed_day = None
            access_count = 0
            if isinstance(metadata, dict):
                doc_day = metadata.get("day")
                imp_raw = metadata.get("importance")
                if imp_raw is not None:
                    try:
                        importance = float(imp_raw)
                    except (ValueError, TypeError):
                        pass
                la_raw = metadata.get("last_accessed_day")
                if la_raw is not None:
                    try:
                        last_accessed_day = int(la_raw)
                    except (ValueError, TypeError):
                        pass
                ac_raw = metadata.get("access_count", 0)
                try:
                    access_count = int(ac_raw)
                except (ValueError, TypeError):
                    access_count = 0
            if doc_day is None and r.get("day"):
                doc_day = r["day"]

            # [v1.3] 3. 时间尺度自适应的艾宾浩斯遗忘曲线
            # 修仙小说场景下 current_day 可能达几十万天，固定半衰期会失效
            # 公式：base_half_life = 20 × sqrt(current_day / 10 + 1)
            #   day=10:     base ≈ 28    (凡人期)
            #   day=100:    base ≈ 66    (筑基期)
            #   day=1000:   base ≈ 200   (金丹期，百年闭关)
            #   day=10000:  base ≈ 632   (元婴期，千年闭关)
            #   day=100000: base ≈ 2000  (仙界，万年+)
            #   day=1000000: base ≈ 6324 (天界，几十万年)
            # 重要性影响衰减速度：half_life = base × (1 + imp/10)
            # 最低保底 0.01，避免长时段下重要记忆完全归零
            if current_day > 0 and doc_day is not None:
                days_diff = max(0, current_day - doc_day)
                time_scale = math.sqrt(max(1.0, current_day / 10.0 + 1.0))
                base_half_life = 20.0 * time_scale
                half_life = max(7.0, base_half_life * (1.0 + importance / 10.0))
                time_boost = max(0.01, 0.22 * (2.0 ** (-days_diff / half_life)))
                r["score"] += time_boost

            # [v1.3] 4. 重要性加分：高重要性记忆优先（提高权重，0.15 → 0.18）
            if importance > 0:
                r["score"] += importance * 0.18

            # [v1.6 P1-7] 4.1 长期记忆摘要专用加权
            # L2 周期摘要 (importance≈0.8) 与 L3 里程碑摘要 (importance≈0.95)
            # 作为高层语义浓缩，应优先于日常叙事返回给 LLM
            if isinstance(metadata, dict):
                mem_type = metadata.get("type", "")
                if mem_type == "long_term_summary":
                    summary_level = metadata.get("summary_level", "")
                    if summary_level == "L3":
                        r["score"] += 0.25  # 里程碑：强加权
                    elif summary_level == "L2":
                        r["score"] += 0.15  # 周期：中等加权
                    elif summary_level == "L1":
                        r["score"] += 0.05  # 日常：轻微加权
                    # 标注供前端展示
                    r["is_long_term_summary"] = True
                    r["summary_level"] = summary_level
                    if metadata.get("milestone_type"):
                        r["milestone_type"] = metadata["milestone_type"]

            # [v1.3] 5. 复习效应（last_accessed 跟踪）：
            # 最近被检索过的记忆获得加分（最高 +0.08，3天内衰减到 0）
            if current_day > 0 and last_accessed_day is not None:
                days_since_access = max(0, current_day - last_accessed_day)
                if days_since_access <= 3:
                    review_boost = 0.08 * (1.0 - days_since_access / 4.0)
                    r["score"] += review_boost

            # [v1.3] 6. 访问频次加分：高频访问的记忆代表反复相关（上限 +0.05）
            if access_count > 0:
                freq_boost = min(0.05, access_count * 0.01)
                r["score"] += freq_boost

            # [v11] 7. 实体命中加分：查询扩展出的实体名出现在结果中
            if entity_set:
                doc_text = r.get("text", "")
                entity_hits = sum(1 for e in entity_set if e in doc_text)
                if entity_hits > 0:
                    r["score"] += entity_hits * 0.10

            # [v12] 8. 有效性过滤：已被取代的原著事实降权
            if isinstance(metadata, dict):
                is_active = metadata.get("is_active", True)
                if not is_active:
                    r["score"] *= 0.3  # 降权但不完全排除（保留历史参考）
                # 玩家新事件优先于原著事实
                source = metadata.get("source", "")
                if source == "game":
                    r["score"] += 0.05  # 玩家新剧情略加优先

            # [v1.3] 标记建议更新 last_accessed_day/access_count（调用方决定是否写入）
            if current_day > 0:
                r["suggested_last_accessed_day"] = current_day
                r["suggested_access_count"] = access_count + 1

        results.sort(key=lambda x: x["score"], reverse=True)
        return results
