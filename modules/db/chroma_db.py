from __future__ import annotations
import hashlib
import logging
import time
import uuid
import chromadb
from pathlib import Path

logger = logging.getLogger("chronoverse.memory")


class MemoryStore:
    def __init__(self, persist_dir: str, collection_name: str = "player_memory",
                 embedding_function=None):
        """[v10.5] 新增 embedding_function 参数，支持 SiliconFlow bge-m3 等外部嵌入模型。
        若不传，ChromaDB 使用默认的 all-MiniLM-L6-v2（英文小模型）。"""
        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        self.persist_dir = persist_dir
        self._embedding_function = embedding_function
        try:
            self.client = chromadb.PersistentClient(
                path=persist_dir,
                settings=chromadb.Settings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                )
            )
        except Exception as e:
            logger.warning("ChromaDB init failed, backing up and retrying: %s", e)
            # 损坏时先备份再重置，避免静默删除数据
            import shutil
            db_dir = Path(persist_dir)
            backup_dir = db_dir / f"backup_{int(time.time())}"
            try:
                shutil.copytree(db_dir, backup_dir)
                logger.warning("Backed up corrupt DB to %s", backup_dir)
            except Exception:
                pass
            for f in db_dir.glob("chroma.sqlite3*"):
                try: f.unlink()
                except Exception: pass
            self.client = chromadb.PersistentClient(path=persist_dir)

        # [v10.5] 若提供了 embedding_function，所有 collection 都使用它
        ef = embedding_function
        self.collection = self._get_or_create_with_migration(
            collection_name, ef)
        self.npc_collection = self._get_or_create_with_migration(
            f"{collection_name}_npc", ef)
        self.foreshadow_collection = self._get_or_create_with_migration(
            f"{collection_name}_foreshadow", ef)
        # 双过程记忆：身份语义核心（长期整合层）
        self.identity_collection = self._get_or_create_with_migration(
            f"{collection_name}_identity", ef)
        # [v12] 小说人物扮演：分离静态/动态/因果记忆
        self.novel_facts_collection = self._get_or_create_with_migration(
            f"{collection_name}_novel_facts", ef)
        self.player_events_collection = self._get_or_create_with_migration(
            f"{collection_name}_player_events", ef)
        self.causal_events_collection = self._get_or_create_with_migration(
            f"{collection_name}_causal", ef)
        logger.info("MemoryStore ready at %s (collections: %d)",
                     persist_dir, self.collection.count())
        # [Bug H3] 创建实例级副本，避免实例方法原地修改类级 _ranked_weights
        self._ranked_weights = dict(type(self)._ranked_weights)
        self._last_access_update_ids = set()
        # [v10+] BM25 检索器（可选注入，用于混合检索）
        self.bm25_retriever = None

    def health_check(self) -> dict:
        """健康检查：验证 ChromaDB 是否正常运行"""
        try:
            count = self.collection.count()
            return {"status": "ok", "count": count, "path": self.persist_dir}
        except Exception as e:
            return {"status": "error", "error": str(e), "path": self.persist_dir}

    # [v12.1] 记忆层快照辅助：撤销/重试时按 ID 集合整体回滚 chroma
    _SNAPSHOT_COLLECTIONS = (
        "collection", "npc_collection", "foreshadow_collection", "identity_collection",
        "novel_facts_collection", "player_events_collection", "causal_events_collection",
    )

    def get_collection_ids_snapshot(self) -> dict:
        """快照所有 collection 的文档 ID（{属性名: [ids]}），供记忆层回滚"""
        result = {}
        for attr in self._SNAPSHOT_COLLECTIONS:
            col = getattr(self, attr, None)
            if col is None:
                continue
            try:
                res = col.get(include=[])
                result[attr] = list(res.get("ids", [])) if res else []
            except Exception as e:
                logger.warning("MemoryStore snapshot %s failed: %s", attr, e)
                result[attr] = []
        return result

    def delete_collection_ids(self, ids_map: dict) -> int:
        """按 collection 删除指定 ID 的文档（{属性名: [ids]}），返回删除总数"""
        total = 0
        for attr, ids in (ids_map or {}).items():
            if not ids:
                continue
            col = getattr(self, attr, None)
            if col is None:
                continue
            try:
                col.delete(ids=ids)
                total += len(ids)
            except Exception as e:
                logger.warning("MemoryStore delete %s (%d ids) failed: %s", attr, len(ids), e)
        return total

    def _get_or_create_with_migration(self, name: str, embedding_function):
        """[v10.5] 获取或创建 collection，处理 embedding function 冲突。
        若现有 collection 用旧嵌入模型（如 default）创建，而新 ef 不同，
        则迁移数据：读取旧数据 → 删除旧 collection → 用新 ef 重建 → 重新插入。"""
        metadata = {"hnsw:space": "cosine"}
        # 无新 ef 时直接获取/创建
        if embedding_function is None:
            return self.client.get_or_create_collection(
                name=name, metadata=metadata)
        try:
            return self.client.get_or_create_collection(
                name=name, metadata=metadata,
                embedding_function=embedding_function)
        except Exception as e:
            err_msg = str(e)
            if "embedding function conflict" not in err_msg.lower() and "already exists" not in err_msg.lower():
                raise
            logger.warning(
                "Embedding function conflict for '%s', migrating data: %s",
                name, e)
            # 读取旧数据（不传 ef 以避免冲突）
            try:
                old_col = self.client.get_or_create_collection(
                    name=name, metadata=metadata)
                old_data = old_col.get()
                docs = old_data.get("documents", []) or []
                metas = old_data.get("metadatas", []) or []
                ids = old_data.get("ids", []) or []
                logger.info("Migrating %d documents from '%s'", len(ids), name)
            except Exception as read_err:
                logger.warning("Failed to read old collection '%s': %s", name, read_err)
                docs, metas, ids = [], [], []
            # 删除旧 collection
            try:
                self.client.delete_collection(name=name)
            except Exception as del_err:
                logger.warning("Failed to delete old collection '%s': %s", name, del_err)
            # 用新 ef 重建
            new_col = self.client.get_or_create_collection(
                name=name, metadata=metadata,
                embedding_function=embedding_function)
            # 重新插入数据（会自动用新 ef 重新嵌入）
            if docs:
                new_col.add(documents=docs, metadatas=metas, ids=ids)
                logger.info("Migrated %d documents to '%s' with new embedding",
                            len(docs), name)
            return new_col

    def _content_hash(self, text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]

    def _find_similar_existing(self, collection, text: str,
                                threshold: float = 0.92) -> tuple[str | None, float]:
        """[B] 在指定 collection 中查找与 text 高度相似的现有条目。
        返回 (existing_id, similarity)。无相似条目或集合为空时返回 (None, 0.0)。
        用于 6 个无 MD5 去重的 collection（npc/foreshadow/identity/novel_facts/
        player_events/causal）写入前的向量相似度去重，
        避免同一事实被反复存储导致检索结果被重复条目占据。
        ChromaDB 使用 cosine 距离，similarity = 1 - distance。"""
        try:
            if collection.count() == 0:
                return None, 0.0
            results = collection.query(query_texts=[text], n_results=1)
            if not results or not results.get("ids") or not results["ids"][0]:
                return None, 0.0
            existing_id = results["ids"][0][0]
            distance = (results["distances"][0][0]
                        if results.get("distances") else 1.0)
            similarity = max(0.0, 1.0 - distance)
            if similarity >= threshold:
                return existing_id, similarity
            return None, similarity
        except Exception as e:
            logger.debug("Similarity dedup query failed: %s", e)
            return None, 0.0

    def add_memory(self, text: str, metadata: dict | None = None) -> str:
        if metadata is None:
            metadata = {}
        content_hash = self._content_hash(text)
        existing = self.collection.get(where={"content_hash": content_hash})
        if existing and existing["ids"]:
            return existing["ids"][0]
        doc_id = f"mem_{self.collection.count() + 1}"
        meta = {**metadata, "content_hash": content_hash}
        self.collection.add(
            documents=[text],
            metadatas=[meta],
            ids=[doc_id],
        )
        # [v10+] 同步更新 BM25 索引（如果已注入）
        if self.bm25_retriever is not None:
            try:
                self.bm25_retriever.add_doc(doc_id, text)
            except Exception as e:
                logger.debug("BM25 index sync failed: %s", e)
        return doc_id

    def search_memory(self, query: str, n_results: int = 5) -> list[dict]:
        if self.collection.count() == 0:
            return []
        n = min(n_results, self.collection.count())
        results = self.collection.query(
            query_texts=[query],
            n_results=n,
        )
        memories = []
        for i in range(len(results["ids"][0])):
            memories.append({
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i] if results.get("distances") else None,
            })
        return memories

    def add_npc_memory(self, npc_name: str, content: str, npc_type: str = "character"):
        """存储NPC设定（角色卡）"""
        # [B] 向量相似度去重：同名 NPC 高度相似设定直接返回已有 id
        existing_id, sim = self._find_similar_existing(
            self.npc_collection, content, threshold=0.92)
        if existing_id:
            logger.debug("NPC memory dedup hit (sim=%.3f): %s", sim, npc_name)
            return existing_id
        doc_id = f"npc_{npc_name}_{uuid.uuid4().hex[:12]}"
        self.npc_collection.add(
            documents=[content],
            metadatas=[{"npc_name": npc_name, "type": npc_type}],
            ids=[doc_id],
        )
        # [v10+] 同步更新 BM25 索引（如果已注入）
        if self.bm25_retriever is not None:
            try:
                self.bm25_retriever.add_doc(doc_id, content)
            except Exception as e:
                logger.debug("BM25 index sync failed (npc): %s", e)
        return doc_id

    def search_npc(self, query: str, n_results: int = 3) -> list[dict]:
        """检索相关NPC信息"""
        if self.npc_collection.count() == 0:
            return []
        n = min(n_results, self.npc_collection.count())
        results = self.npc_collection.query(
            query_texts=[query],
            n_results=n,
        )
        memories = []
        for i in range(len(results["ids"][0])):
            memories.append({
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
            })
        return memories

    def add_foreshadow(self, content: str, day: int, importance: str = "normal"):
        """存储伏笔/重要剧情线索"""
        # [B] 向量相似度去重：高度相似的伏笔直接返回已有 id
        existing_id, sim = self._find_similar_existing(
            self.foreshadow_collection, content, threshold=0.92)
        if existing_id:
            logger.debug("Foreshadow dedup hit (sim=%.3f)", sim)
            return existing_id
        doc_id = f"foreshadow_{self.foreshadow_collection.count() + 1}"
        self.foreshadow_collection.add(
            documents=[content],
            metadatas=[{"day": day, "type": "foreshadow", "importance": importance}],
            ids=[doc_id],
        )
        return doc_id

    def search_foreshadow(self, query: str, n_results: int = 3) -> list[dict]:
        """检索相关伏笔"""
        if self.foreshadow_collection.count() == 0:
            return []
        n = min(n_results, self.foreshadow_collection.count())
        results = self.foreshadow_collection.query(
            query_texts=[query],
            n_results=n,
        )
        memories = []
        for i in range(len(results["ids"][0])):
            memories.append({
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
            })
        return memories

    def add_narrative(self, content: str, day: int, player_input: str = ""):
        """存储叙事章节（自动去重）"""
        content_hash = self._content_hash(content)
        existing = self.collection.get(where={"content_hash": content_hash})
        if existing and existing["ids"]:
            return existing["ids"][0]
        doc_id = f"nar_{self.collection.count() + 1}"
        self.collection.add(
            documents=[content],
            metadatas=[{"day": day, "type": "narrative", "player_input": player_input[:100],
                        "content_hash": content_hash}],
            ids=[doc_id],
        )
        return doc_id

    def rebuild_from_history(self, history: list[dict]):
        self.collection.delete(ids=self.collection.get()["ids"])
        for i, action in enumerate(history):
            text = (
                f"第{action.get('day', '?')}天，"
                f"{action.get('agent_name', '某人')}"
                f"{action.get('action_type', '')}"
                f"{action.get('detail', '')}"
            )
            metadata = {
                "day": action.get("day", 0),
                "type": action.get("action_type", "unknown"),
                "agent_id": action.get("agent_id", ""),
            }
            self.add_memory(text, metadata)

    def add_event_memory(self, day: int, event_type: str,
                         description: str, importance: str = "normal"):
        text = f"第{day}天发生{event_type}事件：{description}"
        # [Bug H1] 将 event_type 写入 metadata.type，使历史知识检索能正确过滤
        metadata = {"day": day, "type": event_type, "importance": importance}
        self.add_memory(text, metadata)

    def add_dialogue_memory(self, day: int, speaker: str,
                            listener: str, content: str):
        text = f"第{day}天，{speaker}对{listener}说：{content}"
        metadata = {"day": day, "type": "dialogue", "speaker": speaker}
        self.add_memory(text, metadata)

    def get_memory_count(self) -> int:
        return self.collection.count()

    def clear_all(self):
        ids = self.collection.get()["ids"]
        if ids:
            self.collection.delete(ids=ids)

    # ── [v10+] BM25 索引维护 ──────────────────────────────

    def set_bm25_retriever(self, bm25_retriever):
        """注入 BM25 检索器，并从现有记忆重建索引。"""
        self.bm25_retriever = bm25_retriever
        self.rebuild_bm25_index()

    def rebuild_bm25_index(self):
        """从 ChromaDB 现有记忆重建 BM25 索引。"""
        if self.bm25_retriever is None:
            return
        try:
            docs = []
            # 主集合（玩家记忆 + 叙事）
            if self.collection.count() > 0:
                all_data = self.collection.get()
                for i, doc_id in enumerate(all_data["ids"]):
                    text = all_data["documents"][i] if all_data["documents"] else ""
                    if text:
                        docs.append({"id": doc_id, "text": text})
            # NPC 集合
            if self.npc_collection.count() > 0:
                npc_data = self.npc_collection.get()
                for i, doc_id in enumerate(npc_data["ids"]):
                    text = npc_data["documents"][i] if npc_data["documents"] else ""
                    if text:
                        docs.append({"id": doc_id, "text": text})
            self.bm25_retriever.rebuild(docs)
            logger.info("BM25 index rebuilt with %d docs", len(docs))
        except Exception as e:
            logger.warning("BM25 index rebuild failed: %s", e)

    # ── [v10] 三层记忆系统：工作记忆 + 情景记忆 + 语义记忆 ──

    def add_memory_with_importance(self, text: str, metadata: dict | None = None,
                                    importance: float = 0.5,
                                    emotional_weight: float = 0.0,
                                    memory_type: str = "narrative") -> str:
        """
        [v10] 带重要性评分的记忆存储。
        importance 影响检索排序：高重要性记忆优先返回。
        """
        if metadata is None:
            metadata = {}
        metadata["importance"] = min(1.0, max(0.0, importance))
        metadata["emotional_weight"] = min(1.0, max(0.0, emotional_weight))
        metadata["access_count"] = 0
        metadata["memory_type"] = memory_type
        return self.add_memory(text, metadata)

    # ── [v1.6 P1-8] 情感记忆系统：Plutchik 8 类情感标记 ──
    # joy(喜悦) sadness(悲伤) anger(愤怒) fear(恐惧)
    # surprise(惊讶) disgust(厌恶) trust(信任) anticipation(期待)
    _EMOTION_TYPES = {
        "joy", "sadness", "anger", "fear",
        "surprise", "disgust", "trust", "anticipation",
    }

    def add_emotional_memory(self, text: str, emotion_type: str,
                              emotional_weight: float = 0.5,
                              valence: float = 0.0,
                              arousal: float = 0.5,
                              metadata: dict | None = None,
                              importance: float = 0.5,
                              related_entities: list[str] = None) -> str:
        """
        [v1.6 P1-8] 存储带情感标记的记忆。
        - emotion_type: Plutchik 8 类之一（joy/sadness/anger/fear/surprise/disgust/trust/anticipation）
        - emotional_weight: 情感强度 0-1（影响检索权重）
        - valence: 效价 -1(消极) ~ +1(积极)，0=中性
        - arousal: 唤醒度 0(平静) ~ 1(激动)
        - related_entities: 关联实体名（NPC/物品/地点），用于按实体检索情感记忆
        """
        emotion_type = emotion_type.lower() if emotion_type else "neutral"
        if emotion_type not in self._EMOTION_TYPES:
            logger.warning("Unknown emotion_type '%s', stored as 'neutral'", emotion_type)
            emotion_type = "neutral"
        if metadata is None:
            metadata = {}
        metadata.update({
            "emotion_type": emotion_type,
            "emotional_weight": min(1.0, max(0.0, emotional_weight)),
            "valence": min(1.0, max(-1.0, valence)),
            "arousal": min(1.0, max(0.0, arousal)),
            "importance": min(1.0, max(0.0, importance)),
            "access_count": 0,
            "memory_type": "emotional",
            "related_entities": ",".join(related_entities or []),
        })
        return self.add_memory(text, metadata)

    def search_by_emotion(self, emotion_type: str,
                           n_results: int = 5,
                           min_weight: float = 0.0,
                           related_entity: str = None) -> list[dict]:
        """
        [v1.6 P1-8] 按情感类型检索记忆。
        - emotion_type: 8 类情感之一
        - min_weight: 仅返回情感强度 ≥ 此值的记忆
        - related_entity: 过滤含此实体的记忆
        """
        emotion_type = (emotion_type or "").lower()
        if emotion_type not in self._EMOTION_TYPES:
            return []
        if self.collection.count() == 0:
            return []
        # ChromaDB where 子句仅支持等值匹配，min_weight 与 entity 过滤在内存中做
        try:
            where = {"emotion_type": emotion_type}
            results = self.collection.get(where=where, limit=min(n_results * 4, 200))
        except Exception as e:
            logger.debug("search_by_emotion query failed: %s", e)
            return []
        if not results or not results.get("ids"):
            return []
        items = []
        for i, mid in enumerate(results["ids"]):
            meta = results["metadatas"][i] if results.get("metadatas") else {}
            weight = float(meta.get("emotional_weight", 0.0) or 0.0)
            if weight < min_weight:
                continue
            if related_entity:
                ents_str = meta.get("related_entities", "") or ""
                if related_entity not in ents_str.split(","):
                    continue
            items.append({
                "id": mid,
                "text": results["documents"][i] if results.get("documents") else "",
                "metadata": meta,
                "emotion_type": emotion_type,
                "emotional_weight": weight,
                "valence": float(meta.get("valence", 0.0) or 0.0),
                "arousal": float(meta.get("arousal", 0.0) or 0.0),
            })
        # 按情感强度降序
        items.sort(key=lambda x: x["emotional_weight"], reverse=True)
        return items[:n_results]

    def get_emotional_summary(self, related_entity: str = None) -> dict:
        """
        [v1.6 P1-8] 获取情感记忆统计：8 类情感的强度分布、效价均值。
        - related_entity: 限定某实体（NPC/物品），None=全局
        """
        if self.collection.count() == 0:
            return {"emotions": {}, "total": 0, "avg_valence": 0.0}
        try:
            results = self.collection.get(
                where={"memory_type": "emotional"},
                limit=500,
            )
        except Exception as e:
            logger.debug("get_emotional_summary failed: %s", e)
            return {"emotions": {}, "total": 0, "avg_valence": 0.0}
        if not results or not results.get("ids"):
            return {"emotions": {}, "total": 0, "avg_valence": 0.0}
        emotion_map: dict[str, dict] = {}
        total = 0
        valence_sum = 0.0
        for i, mid in enumerate(results["ids"]):
            meta = results["metadatas"][i] if results.get("metadatas") else {}
            if meta.get("memory_type") != "emotional":
                continue
            if related_entity:
                ents_str = meta.get("related_entities", "") or ""
                if related_entity not in ents_str.split(","):
                    continue
            etype = meta.get("emotion_type", "neutral")
            weight = float(meta.get("emotional_weight", 0.0) or 0.0)
            valence = float(meta.get("valence", 0.0) or 0.0)
            entry = emotion_map.setdefault(etype, {"count": 0, "total_weight": 0.0, "valence_sum": 0.0})
            entry["count"] += 1
            entry["total_weight"] += weight
            entry["valence_sum"] += valence
            total += 1
            valence_sum += valence
        emotions = {
            et: {
                "count": v["count"],
                "avg_weight": round(v["total_weight"] / v["count"], 3) if v["count"] else 0.0,
                "avg_valence": round(v["valence_sum"] / v["count"], 3) if v["count"] else 0.0,
            }
            for et, v in emotion_map.items()
        }
        return {
            "emotions": emotions,
            "total": total,
            "avg_valence": round(valence_sum / total, 3) if total else 0.0,
        }

    # [v10] 可配置的检索权重（可通过 configure_ranked_weights 修改）
    _ranked_weights = {
        "similarity": 0.45,
        "importance": 0.25,
        "time_decay": 0.15,
        "emotional": 0.10,
        "access": 0.05,
    }
    _time_decay_half_life = 30
    _last_access_update_ids: set = set()  # 防止同一回合重复更新访问计数

    def configure_ranked_weights(self, weights: dict = None, half_life: int = None):
        """[v10] 运行时配置检索权重（从 config.json 读取）"""
        if weights:
            self._ranked_weights.update(weights)
        if half_life:
            self._time_decay_half_life = half_life

    def search_memory_ranked(self, query: str, n_results: int = 5,
                              current_turn: int = 0) -> list[dict]:
        """
        [v10] 带重要性+时间衰减的检索。
        综合评分 = 向量相似度 * W1 + 重要性 * W2 + 时间衰减 * W3 + 情感权重 * W4 + 访问加成 * W5
        """
        if self.collection.count() == 0:
            return []
        n = min(n_results * 3, self.collection.count())
        results = self.collection.query(
            query_texts=[query], n_results=n,
        )
        memories = []
        w = self._ranked_weights
        half_life = self._time_decay_half_life
        for i in range(len(results["ids"][0])):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            distance = results["distances"][0][i] if results.get("distances") else 1.0
            similarity = max(0.0, 1.0 - distance)

            importance = meta.get("importance", 0.5)
            emotional_weight = meta.get("emotional_weight", 0.0)
            access_count = meta.get("access_count", 0)
            created_day = meta.get("created_day", meta.get("day", 0))

            # 时间衰减（可配置半衰期）
            if current_turn > 0 and created_day > 0:
                age = current_turn - created_day
                time_decay = max(0.3, 2 ** (-age / half_life))
            else:
                time_decay = 1.0

            # 访问频率加成
            access_bonus = min(0.2, access_count * 0.02)

            # 综合评分（使用可配置权重）
            score = (
                similarity * w["similarity"] +
                importance * w["importance"] +
                emotional_weight * w["emotional"] +
                time_decay * w["time_decay"] +
                access_bonus * w["access"]
            )

            memories.append({
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "metadata": meta,
                "distance": distance,
                "score": round(score, 4),
            })

        # 按综合评分排序
        memories.sort(key=lambda m: m["score"], reverse=True)

        # 更新访问计数（防膨胀：同一回合内同一 ID 只更新一次）
        ids_to_update = []
        for mem in memories[:n_results]:
            mem_id = mem["id"]
            if mem_id not in self._last_access_update_ids:
                ids_to_update.append(mem)
                self._last_access_update_ids.add(mem_id)

        for mem in ids_to_update:
            try:
                self.collection.update(
                    ids=[mem["id"]],
                    metadatas=[{**mem["metadata"],
                                "access_count": mem["metadata"].get("access_count", 0) + 1}]
                )
            except Exception:
                pass

        # 每 50 回合清空去重集合，防止内存泄漏
        if current_turn > 0 and current_turn % 50 == 0:
            self._last_access_update_ids.clear()

        return memories[:n_results]

    def get_working_memory_context(self, max_items: int = 5) -> str:
        """
        [v10] 获取工作记忆上下文 — 最近的、高重要性的记忆。
        用于注入到 LLM prompt 的最优先位置。
        """
        if self.collection.count() == 0:
            return ""
        try:
            # [Bug M1] 使用 where 过滤器配合 limit，避免加载全部记忆到内存
            all_mem = self.collection.get(
                where={"importance": {"$gte": 0.6}},
                limit=50
            )
            if not all_mem or not all_mem["ids"]:
                return ""
        except Exception:
            return ""

        entries = []
        for i, meta in enumerate(all_mem["metadatas"]):
            if not meta:
                continue
            importance = meta.get("importance", 0.5)
            day = meta.get("day", meta.get("created_day", 0))
            entries.append({
                "text": all_mem["documents"][i],
                "importance": importance,
                "day": day,
            })

        # 按重要性排序
        entries.sort(key=lambda e: e["importance"], reverse=True)
        if not entries:
            return ""

        parts = []
        for entry in entries[:max_items]:
            parts.append(f"[重要性{entry['importance']:.0%}] {entry['text'][:200]}")

        return "【核心记忆】\n" + "\n".join(parts)

    # ── 双过程记忆：身份整合层 ──────────────────────────────

    def add_identity_trait(self, trait_type: str, content: str,
                           source: str = "consolidation"):
        """存储长期身份特征（价值观/性格/习惯/社交记录）"""
        # [B] 向量相似度去重：同一类身份特征高度相似时返回已有 id
        existing_id, sim = self._find_similar_existing(
            self.identity_collection, content, threshold=0.92)
        if existing_id:
            logger.debug("Identity trait dedup hit (sim=%.3f, type=%s)",
                         sim, trait_type)
            return existing_id
        doc_id = f"identity_{trait_type}_{uuid.uuid4().hex[:12]}"
        self.identity_collection.add(
            documents=[content],
            metadatas=[{"trait_type": trait_type, "source": source}],
            ids=[doc_id],
        )
        return doc_id

    def search_identity(self, query: str, n_results: int = 5,
                        trait_type: str = None) -> list[dict]:
        """检索相关身份特征"""
        if self.identity_collection.count() == 0:
            return []
        n = min(n_results, self.identity_collection.count())
        where = {"trait_type": trait_type} if trait_type else None
        try:
            results = self.identity_collection.query(
                query_texts=[query], n_results=n, where=where,
            )
        except Exception:
            results = self.identity_collection.query(
                query_texts=[query], n_results=n,
            )
        return [
            {"id": results["ids"][0][i], "text": results["documents"][0][i],
             "metadata": results["metadatas"][0][i]}
            for i in range(len(results["ids"][0]))
        ]

    def get_identity_context(self) -> str:
        """生成身份上下文字符串，注入到 LLM prompt"""
        if self.identity_collection.count() == 0:
            return ""
        parts = []
        for trait_type in ["values", "personality", "habits", "social", "knowledge"]:
            traits = self.search_identity("", n_results=3, trait_type=trait_type)
            if traits:
                type_label = {
                    "values": "价值观", "personality": "性格特征",
                    "habits": "习惯", "social": "社交记录", "knowledge": "知识"
                }.get(trait_type, trait_type)
                texts = [t["text"][:100] for t in traits]
                parts.append(f"【{type_label}】{'; '.join(texts)}")
        if not parts:
            return ""
        return "【长期身份记忆】\n" + "\n".join(parts)

    def get_identity_count(self) -> int:
        return self.identity_collection.count()

    def clear_identity(self):
        ids = self.identity_collection.get()["ids"]
        if ids:
            self.identity_collection.delete(ids=ids)

    # ── [v12] 小说人物扮演：分离式记忆管理 ────────────────

    def add_novel_fact(self, text: str, chapter: int = 0,
                       entities: list[str] = None,
                       fact_type: str = "event",
                       importance: float = 0.8) -> str:
        """
        [v12] 存储原著既定事实（导入小说时调用）。
        fact_type: character_relation / world_event / foreshadow / character_state
        这些是静态只读记忆，进入游戏时注入，游戏过程中不修改。
        """
        # [B] 向量相似度去重：原著事实跨章节重复时返回已有 id
        existing_id, sim = self._find_similar_existing(
            self.novel_facts_collection, text, threshold=0.92)
        if existing_id:
            logger.debug("Novel fact dedup hit (sim=%.3f, chapter=%s)",
                         sim, chapter)
            return existing_id
        doc_id = f"novel_{self.novel_facts_collection.count() + 1}"
        metadata = {
            "chapter": chapter,
            "entities": ",".join(entities or []),
            "fact_type": fact_type,
            "source": "novel",
            "is_active": True,
            "superseded_by": "",
            "importance": importance,
        }
        self.novel_facts_collection.add(
            documents=[text], metadatas=[metadata], ids=[doc_id],
        )
        return doc_id

    def search_novel_facts(self, query: str, n_results: int = 5,
                           fact_type: str = None,
                           active_only: bool = True) -> list[dict]:
        """[v12] 检索原著事实，默认只返回仍有效的事实"""
        if self.novel_facts_collection.count() == 0:
            return []
        n = min(n_results, self.novel_facts_collection.count())
        where_filter = {}
        if active_only:
            where_filter["is_active"] = True
        if fact_type:
            where_filter["fact_type"] = fact_type
        try:
            results = self.novel_facts_collection.query(
                query_texts=[query], n_results=n,
                where=where_filter if where_filter else None,
            )
        except Exception:
            results = self.novel_facts_collection.query(
                query_texts=[query], n_results=n,
            )
        return [
            {"id": results["ids"][0][i],
             "text": results["documents"][0][i],
             "metadata": results["metadatas"][0][i],
             "source": "novel_facts",
             "score": 1.0 - (results["distances"][0][i]
                             if results.get("distances") else 0.0)}
            for i in range(len(results["ids"][0]))
        ]

    def supersede_novel_fact(self, fact_id: str, superseded_by: str = "player_action"):
        """
        [v12] 标记原著事实已被玩家行为取代。
        不删除数据，只标记 is_active=False。
        """
        try:
            existing = self.novel_facts_collection.get(ids=[fact_id])
            if existing and existing["metadatas"]:
                meta = existing["metadatas"][0]
                meta["is_active"] = False
                meta["superseded_by"] = superseded_by
                self.novel_facts_collection.update(
                    ids=[fact_id], metadatas=[meta])
        except Exception as e:
            logger.warning("supersede_novel_fact failed: %s", e)

    def add_player_event(self, text: str, turn: int = 0, day: int = 0,
                         caused_by: list[str] = None,
                         affects_entities: list[str] = None,
                         event_type: str = "action",
                         importance: float = 0.5) -> str:
        """
        [v12] 存储玩家游玩产生的新事件。
        caused_by: 这个事件由哪些先前事件导致（因果链回溯用）
        affects_entities: 受影响的实体名
        """
        # [B] 向量相似度去重：玩家事件高度相似时返回已有 id
        existing_id, sim = self._find_similar_existing(
            self.player_events_collection, text, threshold=0.92)
        if existing_id:
            logger.debug("Player event dedup hit (sim=%.3f, turn=%s)",
                         sim, turn)
            return existing_id
        doc_id = f"player_{self.player_events_collection.count() + 1}"
        metadata = {
            "turn": turn, "day": day,
            "caused_by": ",".join(caused_by or []),
            "affects": ",".join(affects_entities or []),
            "event_type": event_type,
            "source": "game",
            "is_active": True,
            "importance": importance,
        }
        self.player_events_collection.add(
            documents=[text], metadatas=[metadata], ids=[doc_id],
        )
        return doc_id

    def search_player_events(self, query: str, n_results: int = 5,
                             active_only: bool = True) -> list[dict]:
        """[v12] 检索玩家新剧情"""
        if self.player_events_collection.count() == 0:
            return []
        n = min(n_results, self.player_events_collection.count())
        where_filter = {"is_active": True} if active_only else None
        try:
            results = self.player_events_collection.query(
                query_texts=[query], n_results=n, where=where_filter,
            )
        except Exception:
            results = self.player_events_collection.query(
                query_texts=[query], n_results=n,
            )
        return [
            {"id": results["ids"][0][i],
             "text": results["documents"][0][i],
             "metadata": results["metadatas"][0][i],
             "source": "player_events",
             "score": 1.0 - (results["distances"][0][i]
                             if results.get("distances") else 0.0)}
            for i in range(len(results["ids"][0]))
        ]

    def add_causal_event(self, text: str, cause: str, effect: str,
                         turn: int = 0, day: int = 0,
                         entities: list[str] = None) -> str:
        """
        [v12] 存储因果事件链节点。
        用于追踪"A导致B，B导致C"的因果链。
        """
        # [B] 向量相似度去重：相似因果节点返回已有 id
        existing_id, sim = self._find_similar_existing(
            self.causal_events_collection, text, threshold=0.92)
        if existing_id:
            logger.debug("Causal event dedup hit (sim=%.3f, turn=%s)",
                         sim, turn)
            return existing_id
        doc_id = f"causal_{self.causal_events_collection.count() + 1}"
        metadata = {
            "turn": turn, "day": day,
            "cause": cause, "effect": effect,
            "entities": ",".join(entities or []),
            "source": "causal",
        }
        self.causal_events_collection.add(
            documents=[text], metadatas=[metadata], ids=[doc_id],
        )
        return doc_id

    def search_causal_chain(self, query: str, n_results: int = 5) -> list[dict]:
        """[v12] 检索因果事件链"""
        if self.causal_events_collection.count() == 0:
            return []
        n = min(n_results, self.causal_events_collection.count())
        results = self.causal_events_collection.query(
            query_texts=[query], n_results=n,
        )
        return [
            {"id": results["ids"][0][i],
             "text": results["documents"][0][i],
             "metadata": results["metadatas"][0][i],
             "source": "causal",
             "score": 1.0 - (results["distances"][0][i]
                             if results.get("distances") else 0.0)}
            for i in range(len(results["ids"][0]))
        ]

    def search_unified(self, query: str, n_results: int = 5,
                       current_turn: int = 0) -> list[dict]:
        """
        [v12] 统一检索：原著事实 + 玩家新剧情 + 因果链。
        玩家新剧情优先级高于原著（因为玩家在改写历史），
        但原著事实如果未被取代，仍然生效。
        """
        novel = self.search_novel_facts(query, n_results=n_results, active_only=True)
        player = self.search_player_events(query, n_results=n_results, active_only=True)
        causal = self.search_causal_chain(query, n_results=n_results)

        # 合并：玩家事件优先（权重1.2x），原著事实次之（1.0x），因果链补充（0.8x）
        for r in player:
            r["score"] = r.get("score", 0.5) * 1.2
            r["priority"] = "high"
        for r in novel:
            r["score"] = r.get("score", 0.5) * 1.0
            r["priority"] = "normal"
        for r in causal:
            r["score"] = r.get("score", 0.5) * 0.8
            r["priority"] = "supplement"

        merged = player + novel + causal
        merged.sort(key=lambda x: x.get("score", 0), reverse=True)
        return merged[:n_results]

    def get_novel_facts_count(self) -> int:
        return self.novel_facts_collection.count()

    def get_player_events_count(self) -> int:
        return self.player_events_collection.count()

    def clear_novel_facts(self):
        """清除原著事实（新小说导入时调用）"""
        ids = self.novel_facts_collection.get()["ids"]
        if ids:
            self.novel_facts_collection.delete(ids=ids)

    def clear_player_events(self):
        """清除玩家事件（新游戏开始时调用）"""
        for col in [self.player_events_collection, self.causal_events_collection]:
            ids = col.get()["ids"]
            if ids:
                col.delete(ids=ids)

    def close(self):
        try:
            self.client = None
            self.collection = None
        except Exception:
            pass
