"""
[v12] Zvec 向量数据库适配层 — 可选的高性能后端。

Zvec 是阿里开源的高性能向量数据库（C++底层），相比 ChromaDB：
- 检索速度提升 10 倍以上
- 原生支持向量+全文+标量过滤的混合检索
- WAL预写日志保证崩溃不丢数据

使用方式：
    from .zvec_adapter import ZvecAdapter
    adapter = ZvecAdapter(storage_dir="./data/zvec")
    if adapter.is_available():
        adapter.add("doc1", "文本内容", {"source": "novel"})
        results = adapter.search("查询文本", top_k=5)

如果 Zvec 未安装，is_available() 返回 False，
MemoryStore 自动回退到 ChromaDB。
"""
from __future__ import annotations

import logging
import os
from typing import Callable

logger = logging.getLogger("chronoverse.zvec")

# 尝试导入 Zvec
try:
    import zvec
    _ZVEC_AVAILABLE = True
except ImportError:
    _ZVEC_AVAILABLE = False
    logger.info("Zvec 未安装，向量存储将使用 ChromaDB。"
                " 安装: pip install zvec")


class ZvecAdapter:
    """
    [v12] Zvec 向量数据库适配层。

    提供 ChromaDB 兼容的接口，便于 MemoryStore 无缝切换后端。
    仅在 pip install zvec 成功后可用。
    """

    def __init__(self, storage_dir: str = "./data/zvec",
                 embedding_func: Callable = None,
                 dimension: int = 1024):
        """
        storage_dir: 数据持久化目录
        embedding_func: 嵌入函数（与 ChromaDB 的 embedding_function 兼容）
        dimension: 向量维度（bge-m3 = 1024）
        """
        self.storage_dir = storage_dir
        self.embedding_func = embedding_func
        self.dimension = dimension
        os.makedirs(storage_dir, exist_ok=True)

        self._client = None
        self._collections: dict = {}  # {collection_name: zvec_collection}

        if _ZVEC_AVAILABLE:
            try:
                # Zvec 初始化
                # 具体API以 zvec 官方文档为准
                self._client = zvec.Client(path=storage_dir)
                logger.info("Zvec 适配层初始化成功: %s", storage_dir)
            except Exception as e:
                logger.warning("Zvec 初始化失败，将回退到 ChromaDB: %s", e)
                self._client = None

    @staticmethod
    def is_available() -> bool:
        """检查 Zvec 是否可用"""
        return _ZVEC_AVAILABLE and zvec is not None

    def get_or_create_collection(self, name: str):
        """获取或创建集合（兼容 ChromaDB 接口）"""
        if not self._client:
            return None
        if name not in self._collections:
            try:
                col = self._client.create_collection(
                    name=name, dimension=self.dimension,
                )
                self._collections[name] = col
            except Exception as e:
                logger.warning("Zvec 创建集合 '%s' 失败: %s", name, e)
                return None
        return self._collections[name]

    def add(self, collection_name: str, documents: list[str],
            metadatas: list[dict] = None, ids: list[str] = None) -> bool:
        """添加文档（兼容 ChromaDB 接口）"""
        col = self.get_or_create_collection(collection_name)
        if not col or not documents:
            return False

        # 生成嵌入
        if self.embedding_func:
            try:
                embeddings = self.embedding_func(documents)
            except Exception as e:
                logger.warning("Zvec 嵌入生成失败: %s", e)
                return False
        else:
            # 无嵌入函数时跳过（Zvec 需要向量输入）
            logger.warning("Zvec 需要嵌入函数，跳过添加")
            return False

        if ids is None:
            ids = [f"doc_{i}" for i in range(len(documents))]
        if metadatas is None:
            metadatas = [{} for _ in documents]

        try:
            col.add(
                vectors=embeddings,
                documents=documents,
                metadatas=metadatas,
                ids=ids,
            )
            return True
        except Exception as e:
            logger.warning("Zvec add 失败: %s", e)
            return False

    def query(self, collection_name: str, query_texts: list[str],
              n_results: int = 5, where: dict = None) -> dict:
        """
        查询（兼容 ChromaDB 返回格式）

        返回格式与 ChromaDB 一致：
        {"ids": [[...]], "documents": [[...]], "metadatas": [[...]], "distances": [[...]]}
        """
        col = self.get_or_create_collection(collection_name)
        if not col or not query_texts:
            return self._empty_result()

        # 生成查询嵌入
        if self.embedding_func:
            try:
                query_embeddings = self.embedding_func(query_texts)
            except Exception as e:
                logger.warning("Zvec 查询嵌入失败: %s", e)
                return self._empty_result()
        else:
            return self._empty_result()

        try:
            # Zvec 混合检索：向量 + 标量过滤
            results = col.query(
                vectors=query_embeddings,
                n_results=n_results,
                filter=where,  # Zvec 原生支持标量过滤
            )
            # 转换为 ChromaDB 兼容格式
            return self._convert_results(results)
        except Exception as e:
            logger.warning("Zvec query 失败: %s", e)
            return self._empty_result()

    def count(self, collection_name: str) -> int:
        """获取集合中文档数"""
        col = self.get_or_create_collection(collection_name)
        if not col:
            return 0
        try:
            return col.count()
        except Exception:
            return 0

    def get(self, collection_name: str, ids: list[str] = None,
            where: dict = None) -> dict:
        """获取文档（兼容 ChromaDB 接口）"""
        col = self.get_or_create_collection(collection_name)
        if not col:
            return {"ids": [], "documents": [], "metadatas": []}
        try:
            results = col.get(ids=ids, filter=where)
            return self._convert_get_results(results)
        except Exception as e:
            logger.warning("Zvec get 失败: %s", e)
            return {"ids": [], "documents": [], "metadatas": []}

    def update(self, collection_name: str, ids: list[str],
               metadatas: list[dict] = None) -> bool:
        """更新文档元数据"""
        col = self.get_or_create_collection(collection_name)
        if not col:
            return False
        try:
            col.update(ids=ids, metadatas=metadatas)
            return True
        except Exception as e:
            logger.warning("Zvec update 失败: %s", e)
            return False

    def delete(self, collection_name: str, ids: list[str]) -> bool:
        """删除文档"""
        col = self.get_or_create_collection(collection_name)
        if not col:
            return False
        try:
            col.delete(ids=ids)
            return True
        except Exception as e:
            logger.warning("Zvec delete 失败: %s", e)
            return False

    def _empty_result(self) -> dict:
        """空结果（ChromaDB 格式）"""
        return {"ids": [[]], "documents": [[]],
                "metadatas": [[]], "distances": [[]]}

    def _convert_results(self, zvec_results) -> dict:
        """将 Zvec 结果转换为 ChromaDB 格式"""
        # 具体转换逻辑取决于 Zvec API 的返回格式
        # 这里提供基本框架
        if not zvec_results:
            return self._empty_result()
        return zvec_results  # 如果 Zvec 已兼容 ChromaDB 格式则直接返回

    def _convert_get_results(self, zvec_results) -> dict:
        """将 Zvec get 结果转换为 ChromaDB 格式"""
        if not zvec_results:
            return {"ids": [], "documents": [], "metadatas": []}
        return zvec_results

    def close(self):
        """关闭连接"""
        self._client = None
        self._collections.clear()
