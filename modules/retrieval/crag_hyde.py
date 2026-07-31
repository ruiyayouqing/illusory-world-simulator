"""[v1.6 P1-5] CRAG 检索自评估 + HyDE 查询重写 + 检索审计日志。

参考：
- Corrective Retrieval Augmented Learning (CRAG, Yan et al. 2024)：
  对检索结果做相关性评估，过滤低质量文档，必要时触发网页检索兜底。
  本实现去掉网页兜底（无网络），改为：
    (a) 轻量打分评估每条检索结果与查询的相关性
    (b) 阈值过滤：低分文档标记为 "low_rel" 并降权（不完全丢弃，保留作上下文）
    (c) 触发查询重写（HyDE）当整体检索置信度过低时

- HyDE (Hypothetical Document Embeddings, Gao et al. 2022)：
  让 LLM 先生成一个"假设性答案"文档，再用该文档做向量检索。
  动机：query 与 answer 的语义分布更接近，能召回更相关的记忆。
  本实现：
    (a) 检索前可选触发：当原始 query 较短/含代词时，让 LLM 生成假设性段落
    (b) 用假设段落 + 原 query 共同检索（双路召回，再融合）
    (c) 失败回退到原 query

- 检索审计日志：
  记录每次检索的 query、重写后 query、各路召回数、CRAG 评分、最终采纳数。
  支持调试模式（debug=True）打印详细过程。
  保留最近 N 次检索记录，供 /api/retrieval/debug 查看。
"""
from __future__ import annotations
import logging
import time
import threading
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm.base_llm import BaseLLM

logger = logging.getLogger("chronoverse.retrieval.crag")


# ──────────────────────────────────────────────────────────────
# 检索审计日志（进程内单例）
# ──────────────────────────────────────────────────────────────

class RetrievalAuditLog:
    """[v1.6 P1-5] 检索审计日志：线程安全的环形缓冲。

    记录最近 N 次检索的完整信息，供调试 API 查询。
    """

    _instance: "RetrievalAuditLog | None" = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._records = deque(maxlen=50)  # 保留最近 50 次检索
                    inst._enabled = True
                    inst._debug = False  # 调试模式：打印详细过程
                    cls._instance = inst
        return cls._instance

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, v: bool):
        self._enabled = bool(v)

    @property
    def debug(self) -> bool:
        return self._debug

    @debug.setter
    def debug(self, v: bool):
        self._debug = bool(v)

    def record(self, entry: dict) -> None:
        """记录一次检索审计条目。"""
        if not self._enabled:
            return
        entry.setdefault("ts", time.time())
        with self._lock:
            self._records.append(entry)
        if self._debug:
            logger.debug(
                "[RetrievalAudit] query=%r rewritten=%r recalls=%s kept=%d "
                "avg_score=%.3f trigger_hyde=%s",
                entry.get("query", "")[:40],
                entry.get("rewritten_query", "")[:40],
                entry.get("recalls", {}),
                entry.get("kept", 0),
                entry.get("avg_score", 0.0),
                entry.get("trigger_hyde", False),
            )

    def recent(self, limit: int = 20) -> list[dict]:
        """获取最近 N 次检索记录（最新在前）。"""
        with self._lock:
            items = list(self._records)
        items.reverse()
        return items[:limit]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def stats(self) -> dict:
        with self._lock:
            n = len(self._records)
        return {
            "total_records": n,
            "max_capacity": self._records.maxlen,
            "enabled": self._enabled,
            "debug": self._debug,
        }


# 模块级单例
audit_log = RetrievalAuditLog()


# ──────────────────────────────────────────────────────────────
# HyDE 查询重写
# ──────────────────────────────────────────────────────────────

HYDE_PROMPT = """请根据下面的查询，写一段"假设性答案"段落（80-150字）。
假设这段答案已经存在于故事记录中，描述了与查询相关的事实、人物、地点、事件。
不要回答"我不知道"，必须基于世界观做出合理假设。

【查询】
{query}

【世界背景】
{world_context}

【输出格式】
直接输出假设性段落，不要加引号或前缀。"""

# 触发 HyDE 的条件：query 过短或包含代词
_PRONOUNS = ("他", "她", "它", "这", "那", "此", "其", "某", "谁", "什么", "怎样", "如何")


def _should_trigger_hyde(query: str) -> bool:
    """判断是否需要触发 HyDE 查询重写。

    触发条件：
    - query 长度过短（< 8 字符）
    - query 包含代词（指代不明）
    - query 是疑问句（含 "？" 或 "?"）
    """
    if not query or len(query.strip()) < 8:
        return True
    if any(p in query for p in _PRONOUNS):
        return True
    if "？" in query or "?" in query:
        return True
    return False


class HyDERewriter:
    """[v1.6 P1-5] HyDE 查询重写器。

    让 LLM 生成假设性文档，用于增强向量检索。
    """

    def __init__(self, llm: "BaseLLM | None" = None,
                 max_attempts: int = 1,
                 cache_ttl: float = 60.0):
        self.llm = llm
        self.max_attempts = max_attempts
        # 简单缓存：避免同一 query 短时间重复调用 LLM
        # [v1.7 P2-3] 加锁防止并发 race
        self._cache: dict[str, tuple[float, str]] = {}
        self._cache_lock = threading.Lock()
        self.cache_ttl = cache_ttl

    def set_llm(self, llm: "BaseLLM"):
        self.llm = llm

    def rewrite(self, query: str, world_context: str = "") -> str:
        """生成 HyDE 假设性文档。

        返回：假设性段落文本。失败时返回空字符串（调用方回退到原 query）。
        """
        if not self.llm:
            return ""

        # 检查缓存
        now = time.time()
        with self._cache_lock:
            cached = self._cache.get(query)
            if cached and now - cached[0] < self.cache_ttl:
                return cached[1]

        # 不需要触发 HyDE 时直接返回空
        if not _should_trigger_hyde(query):
            return ""

        prompt = HYDE_PROMPT.format(
            query=query[:500],
            world_context=(world_context or "（无）")[:500],
        )

        for attempt in range(self.max_attempts):
            try:
                text = self.llm.chat(
                    prompt, temperature=0.5, max_tokens=200,
                )
                text = (text or "").strip()
                if text and len(text) > 10:
                    # 缓存并返回
                    with self._cache_lock:
                        self._cache[query] = (now, text)
                    return text
            except Exception as e:
                logger.warning("HyDE rewrite attempt %d failed: %s", attempt + 1, e)

        return ""

    def should_trigger(self, query: str) -> bool:
        """外部查询是否需要触发（供审计日志使用）。"""
        return _should_trigger_hyde(query)


# ──────────────────────────────────────────────────────────────
# CRAG 检索自评估
# ──────────────────────────────────────────────────────────────


class CRAGEvaluator:
    """[v1.6 P1-5] Corrective Retrieval 检索自评估。

    对检索结果做相关性评估，过滤/降权低质量文档。
    采用"轻量打分 + 阈值过滤"策略，不依赖额外 LLM 调用（保持低延迟）。

    评分维度：
    1. 关键词覆盖：query 中的关键词在文档中出现的比例
    2. 实体命中：query 中的实体名（如果有）出现在文档中
    3. 长度合理性：过短（<20字）或过长（>2000字）的文档降权
    4. 来源可信度：向量库 > BM25 > 图谱 > 小说（动态事件优先于静态）
    """

    # 来源可信度基础分
    SOURCE_TRUST: dict[str, float] = {
        "vector": 0.85,
        "bm25": 0.70,
        "graph": 0.75,
        "novel": 0.55,       # 原著静态知识，可信度较低
        "novel_facts": 0.55,
        "player_events": 0.90,  # 玩家亲历事件最可信
        "causal": 0.80,
    }

    def __init__(self,
                 low_relevance_threshold: float = 0.25,
                 high_relevance_threshold: float = 0.55,
                 min_doc_len: int = 20,
                 max_doc_len: int = 2000):
        self.low_threshold = low_relevance_threshold
        self.high_threshold = high_relevance_threshold
        self.min_doc_len = min_doc_len
        self.max_doc_len = max_doc_len

    def evaluate(self, query: str, results: list[dict],
                 entity_hints: list[str] | None = None) -> tuple[list[dict], dict]:
        """对检索结果做 CRAG 自评估。

        参数：
            query: 原始查询
            results: 待评估的检索结果列表
            entity_hints: 实体名列表（可选）

        返回：
            (评估后的结果列表, 评估统计信息)
            评估后的结果列表中每个条目新增字段：
              - crag_score: 相关性评分 [0, 1]
              - crag_label: "high" / "medium" / "low"
              - crag_kept: 是否保留（low 也保留但降权）
            统计信息：
              {
                "total": N,
                "high": N, "medium": N, "low": N,
                "avg_score": float,
                "trigger_hyde": bool,  # 整体置信度过低时建议触发 HyDE
              }
        """
        if not results:
            return [], {
                "total": 0, "high": 0, "medium": 0, "low": 0,
                "avg_score": 0.0, "trigger_hyde": False,
            }

        query_terms = self._extract_terms(query)
        entity_set = set(entity_hints or [])

        scored: list[dict] = []
        for r in results:
            text = r.get("text", "") or ""
            source = r.get("source", "") or r.get("sources", [""])[0] if isinstance(r.get("sources"), list) else r.get("source", "")

            # 1. 关键词覆盖
            kw_score = self._keyword_coverage(query_terms, text)

            # 2. 实体命中
            ent_score = 0.0
            if entity_set:
                hits = sum(1 for e in entity_set if e in text)
                ent_score = min(1.0, hits / max(1, len(entity_set)))

            # 3. 长度合理性
            len_score = self._length_score(len(text))

            # 4. 来源可信度
            trust = self.SOURCE_TRUST.get(source, 0.65)

            # 5. 原始 RRF 分数（归一化）
            rrf_score = r.get("score", 0.0)
            rrf_norm = min(1.0, rrf_score * 10.0)  # RRF 分数通常很小，放大归一化

            # 加权融合
            crag_score = (
                kw_score * 0.35 +
                ent_score * 0.20 +
                len_score * 0.10 +
                trust * 0.15 +
                rrf_norm * 0.20
            )

            # [v1.6 P1-7] 长期记忆摘要 bonus：高层语义浓缩，应给予 CRAG 相关性 bonus
            # 避免 L1/L2/L3 摘要因关键词覆盖较低被误判为 low relevance
            metadata = r.get("metadata") or r.get("metadatas") or {}
            if isinstance(metadata, dict) and metadata.get("type") == "long_term_summary":
                summary_level = metadata.get("summary_level", "")
                if summary_level == "L3":
                    crag_score += 0.20  # 里程碑：强 bonus，确保进入 high
                elif summary_level == "L2":
                    crag_score += 0.12  # 周期：中等 bonus
                elif summary_level == "L1":
                    crag_score += 0.04  # 日常：轻微 bonus
                # 标注供前端展示
                r["is_long_term_summary"] = True
                r["summary_level"] = summary_level
                if metadata.get("milestone_type"):
                    r["milestone_type"] = metadata["milestone_type"]

            # 标记
            if crag_score >= self.high_threshold:
                label = "high"
                kept = True
            elif crag_score >= self.low_threshold:
                label = "medium"
                kept = True
            else:
                label = "low"
                kept = True  # 保留但降权（不完全丢弃）

            r["crag_score"] = round(crag_score, 3)
            r["crag_label"] = label
            r["crag_kept"] = kept
            # low 相关性结果降权（乘以 0.3）
            if label == "low":
                r["score"] = r.get("score", 0.0) * 0.3

            scored.append(r)

        # 按评分降序
        scored.sort(key=lambda x: x.get("crag_score", 0.0), reverse=True)

        # 统计
        n_high = sum(1 for r in scored if r.get("crag_label") == "high")
        n_med = sum(1 for r in scored if r.get("crag_label") == "medium")
        n_low = sum(1 for r in scored if r.get("crag_label") == "low")
        avg_score = sum(r.get("crag_score", 0.0) for r in scored) / len(scored)

        # 整体置信度过低时建议触发 HyDE
        trigger_hyde = (n_high == 0 and avg_score < 0.35)

        stats = {
            "total": len(scored),
            "high": n_high,
            "medium": n_med,
            "low": n_low,
            "avg_score": round(avg_score, 3),
            "trigger_hyde": trigger_hyde,
        }

        return scored, stats

    @staticmethod
    def _extract_terms(query: str) -> set[str]:
        """提取查询中的关键词（粗粒度，用于覆盖度计算）。

        策略：中文按 2-gram，英文按单词。
        """
        import re
        terms: set[str] = set()
        # 英文单词
        for w in re.findall(r"[a-zA-Z]{2,}", query.lower()):
            terms.add(w)
        # 中文 2-gram
        cn = "".join(re.findall(r"[\u4e00-\u9fff]+", query))
        for i in range(len(cn) - 1):
            terms.add(cn[i:i + 2])
        # 单字也加入（用于短查询）
        for ch in cn:
            terms.add(ch)
        return terms

    @staticmethod
    def _keyword_coverage(query_terms: set[str], text: str) -> float:
        """query 关键词在文档中的覆盖率。"""
        if not query_terms:
            return 0.0
        hits = sum(1 for t in query_terms if t in text)
        return hits / len(query_terms)

    def _length_score(self, length: int) -> float:
        """文档长度合理性评分。"""
        if length < self.min_doc_len:
            # 过短，信息量不足
            return max(0.1, length / self.min_doc_len)
        if length > self.max_doc_len:
            # 过长，可能包含无关内容
            return max(0.5, 1.0 - (length - self.max_doc_len) / 5000.0)
        return 1.0


# ──────────────────────────────────────────────────────────────
# CRAG + HyDE 集成包装器
# ──────────────────────────────────────────────────────────────


class CRAGHyDEPipeline:
    """[v1.6 P1-5] CRAG + HyDE 检索管道。

    包装 HybridRetriever，在检索前后增加：
    1. 检索前：可选触发 HyDE 生成假设性文档，用于增强向量检索
    2. 检索后：CRAG 自评估，过滤/降权低质量结果
    3. 全程：审计日志记录

    使用方式：
        pipeline = CRAGHyDEPipeline(
            retriever=hybrid_retriever,
            hyde=HyDERewriter(llm=cheap_llm),
            evaluator=CRAGEvaluator(),
            audit=audit_log,
        )
        results = pipeline.retrieve(query, top_k=5, ...)
    """

    def __init__(self,
                 retriever,
                 hyde: "HyDERewriter | None" = None,
                 evaluator: "CRAGEvaluator | None" = None,
                 audit: "RetrievalAuditLog | None" = None):
        self.retriever = retriever
        self.hyde = hyde
        self.evaluator = evaluator or CRAGEvaluator()
        self.audit = audit or audit_log
        # [v1.6 P1-7] 长期记忆摘要器（延迟注入，用于里程碑强制召回）
        self._long_term_summarizer = None

    def set_long_term_summarizer(self, summarizer) -> None:
        """[v1.6 P1-7] 注入 LongTermMemorySummarizer，启用里程碑强制召回。"""
        self._long_term_summarizer = summarizer
        logger.info("CRAGHyDEPipeline wired to LongTermMemorySummarizer (milestone recall)")

    def retrieve(self, query: str, top_k: int = 5,
                world_context: str = "",
                entity_hints: list[str] | None = None,
                **kwargs) -> list[dict]:
        """执行 CRAG + HyDE 检索管道。

        参数：
            query: 原始查询
            top_k: 返回数量
            world_context: 世界背景（供 HyDE 使用）
            entity_hints: 实体名列表
            **kwargs: 传给底层 retriever 的额外参数
                      （current_turn, current_day, scene_type, filters）

        返回：评估后的检索结果列表
        """
        start_ts = time.time()
        trigger_hyde = False
        rewritten_query = ""
        hyde_doc = ""

        # 1. 判断是否触发 HyDE
        if self.hyde and self.hyde.should_trigger(query):
            trigger_hyde = True
            try:
                hyde_doc = self.hyde.rewrite(query, world_context=world_context)
                if hyde_doc:
                    rewritten_query = hyde_doc
            except Exception as e:
                logger.warning("HyDE rewrite failed: %s", e)

        # 2. 执行检索
        # 如果有 HyDE 文档，做双路召回：原 query + HyDE 文档
        actual_query = rewritten_query or query
        try:
            results = self.retriever.retrieve(
                actual_query, top_k=top_k,
                entity_hints=entity_hints,
                **kwargs,
            )
        except Exception as e:
            logger.warning("Retriever failed in CRAG pipeline: %s", e)
            results = []

        # 如果有 HyDE 文档且第一次检索结果不足，用原 query 再检索一次并合并
        if hyde_doc and len(results) < top_k:
            try:
                extra = self.retriever.retrieve(
                    query, top_k=top_k,
                    entity_hints=entity_hints,
                    **kwargs,
                )
                # 合并去重
                existing_ids = {r.get("id") for r in results}
                for r in extra:
                    if r.get("id") not in existing_ids:
                        results.append(r)
                        existing_ids.add(r.get("id"))
            except Exception as e:
                logger.warning("Secondary retrieval failed: %s", e)

        # 3. CRAG 自评估
        evaluated, eval_stats = self.evaluator.evaluate(
            query, results, entity_hints=entity_hints,
        )

        # [v1.6 P1-7] 3.1 里程碑强制召回：当查询包含"突破/死亡/结婚"等关键词时，
        # 从 L3 摘要中强制召回相关里程碑，注入到候选结果中
        milestone_recalled: list[dict] = []
        if self._long_term_summarizer:
            try:
                milestone_recalled = self._long_term_summarizer.fetch_milestones_for_retrieval(
                    query, max_results=3,
                )
                if milestone_recalled:
                    # 合并到 evaluated，去重；对已存在的条目更新长期摘要标记
                    existing_map = {r.get("id"): r for r in evaluated}
                    for mr in milestone_recalled:
                        mr_id = mr.get("id")
                        if mr_id in existing_map:
                            # 已存在：更新长期摘要标记字段（避免漏标）
                            existing_map[mr_id]["is_long_term_summary"] = True
                            existing_map[mr_id]["summary_level"] = mr.get("summary_level", "L3")
                            existing_map[mr_id]["milestone_type"] = mr.get("milestone_type", "")
                            existing_map[mr_id]["forced_recall"] = True
                            # 同步 metadata 供 CRAG bonus 识别
                            meta = existing_map[mr_id].get("metadata") or {}
                            if isinstance(meta, dict):
                                meta["type"] = "long_term_summary"
                                meta["summary_level"] = mr.get("summary_level", "L3")
                                existing_map[mr_id]["metadata"] = meta
                        else:
                            evaluated.append(mr)
                            existing_map[mr_id] = mr
                    # 重新评估（里程碑会被 CRAG bonus 提权到 high）
                    evaluated, eval_stats = self.evaluator.evaluate(
                        query, evaluated, entity_hints=entity_hints,
                    )
                    logger.debug("Milestone recall: %d L3 summaries (injected/updated)",
                                 len(milestone_recalled))
            except Exception as e:
                logger.warning("Milestone recall failed: %s", e)

        # 4. 如果 CRAG 评估触发 HyDE 但首次未执行，再触发一次
        if eval_stats["trigger_hyde"] and not trigger_hyde and self.hyde:
            try:
                hyde_doc2 = self.hyde.rewrite(query, world_context=world_context)
                if hyde_doc2:
                    extra2 = self.retriever.retrieve(
                        hyde_doc2, top_k=top_k,
                        entity_hints=entity_hints,
                        **kwargs,
                    )
                    # 合并去重并重新评估
                    existing_ids = {r.get("id") for r in evaluated}
                    for r in extra2:
                        if r.get("id") not in existing_ids:
                            evaluated.append(r)
                            existing_ids.add(r.get("id"))
                    evaluated, eval_stats = self.evaluator.evaluate(
                        query, evaluated, entity_hints=entity_hints,
                    )
                    trigger_hyde = True
                    rewritten_query = hyde_doc2
            except Exception as e:
                logger.warning("Secondary HyDE retrieval failed: %s", e)

        # 5. 截断到 top_k
        final = evaluated[:top_k]

        # 6. 记录审计日志
        elapsed_ms = int((time.time() - start_ts) * 1000)
        recalls = {}
        for r in evaluated:
            src = r.get("source") or (r.get("sources", [""])[0] if isinstance(r.get("sources"), list) else "")
            if src:
                recalls[src] = recalls.get(src, 0) + 1

        self.audit.record({
            "query": query[:200],
            "rewritten_query": rewritten_query[:200],
            "trigger_hyde": trigger_hyde,
            "hyde_doc": hyde_doc[:200] if hyde_doc else "",
            "recalls": recalls,
            "total_candidates": len(evaluated),
            "kept": len(final),
            "avg_score": eval_stats.get("avg_score", 0.0),
            "high": eval_stats.get("high", 0),
            "medium": eval_stats.get("medium", 0),
            "low": eval_stats.get("low", 0),
            "elapsed_ms": elapsed_ms,
        })

        return final
