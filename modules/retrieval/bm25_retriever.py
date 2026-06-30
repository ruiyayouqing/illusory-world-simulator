"""BM25 关键词检索器，用于混合检索。"""
import re
from collections import Counter, defaultdict
from math import log

# [v11] 尝试导入 jieba 分词，失败则回退到 bigram 分词
try:
    import jieba
    _HAS_JIEBA = True
except ImportError:
    _HAS_JIEBA = False


class BM25Retriever:
    """简单的 BM25 检索器，支持中文（词级/字级分词）。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs: list[dict] = []  # [{"id": str, "text": str, "tokens": list}]
        self.doc_freq: dict[str, int] = defaultdict(int)  # 词 -> 包含该词的文档数
        self.avg_doc_len: float = 0.0

    def _tokenize(self, text: str) -> list[str]:
        """[v11] 中文分词：优先 jieba，回退到 bigram (2-gram)。"""
        # 提取英文单词
        tokens = re.findall(r'[a-zA-Z]{2,}', text.lower())
        # 提取中文
        cn_text = ''.join(re.findall(r'[\u4e00-\u9fff]+', text))
        if not cn_text:
            return tokens

        if _HAS_JIEBA:
            # jieba 精确模式分词
            cn_words = list(jieba.cut(cn_text, cut_all=False))
            # 过滤单字（单字在检索中噪声大）
            cn_tokens = [w for w in cn_words if len(w) >= 2]
            if cn_tokens:
                return tokens + cn_tokens
            # 如果没有双字词，回退到单字
            return tokens + list(cn_text)

        # 回退：bigram（重叠 2-gram）
        # "我是中国人" → ["我是", "是中", "中国", "国人"]
        cn_tokens = [cn_text[i:i+2] for i in range(len(cn_text) - 1)]
        # 补充单字（保证短文本也能匹配）
        cn_tokens += re.findall(r'[\u4e00-\u9fff]', cn_text)
        return tokens + cn_tokens

    def add_docs(self, docs: list[dict]):
        """添加文档。docs: [{"id": str, "text": str}]"""
        for doc in docs:
            tokens = self._tokenize(doc["text"])
            self.docs.append({**doc, "tokens": tokens, "len": len(tokens)})
            for token in set(tokens):
                self.doc_freq[token] += 1
        self.avg_doc_len = sum(d["len"] for d in self.docs) / max(1, len(self.docs))

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """检索，返回 [{"id": str, "score": float, "text": str}]"""
        if not self.docs:
            return []
        query_tokens = self._tokenize(query)
        scores = []
        N = len(self.docs)
        for doc in self.docs:
            score = 0.0
            tf = Counter(doc["tokens"])
            for qt in query_tokens:
                if qt not in tf:
                    continue
                df = self.doc_freq.get(qt, 0)
                idf = log((N - df + 0.5) / (df + 0.5) + 1)
                tf_val = tf[qt]
                norm = 1 - self.b + self.b * (doc["len"] / max(1, self.avg_doc_len))
                score += idf * (tf_val * (self.k1 + 1)) / (tf_val + self.k1 * norm)
            if score > 0:
                scores.append({"id": doc["id"], "score": score, "text": doc["text"]})
        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_k]

    def rebuild(self, docs: list[dict]):
        """重建索引。"""
        self.docs = []
        self.doc_freq = defaultdict(int)
        self.avg_doc_len = 0.0
        self.add_docs(docs)

    def add_doc(self, doc_id: str, text: str):
        """增量添加单个文档。"""
        self.add_docs([{"id": doc_id, "text": text}])
