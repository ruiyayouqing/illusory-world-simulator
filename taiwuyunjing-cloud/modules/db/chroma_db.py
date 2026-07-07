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

    def close(self):
        try:
            self.client = None
            self.collection = None
        except Exception:
            pass
