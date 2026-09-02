"""混合检索的原子组件：中文友好分词、轻量 BM25、RRF 融合。

纯标准库实现（不新增依赖），针对音乐领域中文语料调优：
- 分词：ASCII 词（小写）+ CJK 单字 + CJK 二元组 —— 中文没有空格，
  二元组让 BM25 能命中"副歌""贝斯"这类术语，ASCII 词保留 BPM/Suno 等；
- BM25（Okapi）：k1=1.5, b=0.75，语料一次性建索引；
- RRF（Reciprocal Rank Fusion）：按排名融合多路召回，对分数刻度不敏感。
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

_ASCII_TOKEN = re.compile(r"[a-zA-Z0-9]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """中文友好分词：ASCII 词 + CJK 单字 + CJK 二元组（全小写）。"""
    tokens: list[str] = []
    lowered = text.lower()
    for match in _ASCII_TOKEN.finditer(lowered):
        tokens.append(match.group())
    # 提取全部 CJK 字符（保序）
    cjk_chars = [ch for ch in lowered if _CJK.match(ch)]
    tokens.extend(cjk_chars)
    tokens.extend(f"{a}{b}" for a, b in zip(cjk_chars, cjk_chars[1:], strict=False))
    return tokens


class BM25Index:
    """Okapi BM25 索引：构建一次，可多次查询。"""

    def __init__(
        self,
        corpus: list[list[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.k1 = k1
        self.b = b
        self.doc_count = len(corpus)
        self.doc_lens = [len(doc) for doc in corpus]
        self.avg_len = sum(self.doc_lens) / self.doc_count if corpus else 0.0
        self.term_freqs: list[Counter[str]] = [Counter(doc) for doc in corpus]
        df: Counter[str] = Counter()
        for freqs in self.term_freqs:
            df.update(freqs.keys())
        self.doc_freq = df

    def idf(self, term: str) -> float:
        df = self.doc_freq.get(term, 0)
        if df == 0:
            return 0.0
        return math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1.0)

    def score(self, query_tokens: list[str], doc_index: int) -> float:
        freqs = self.term_freqs[doc_index]
        if not freqs:
            return 0.0
        doc_len = self.doc_lens[doc_index]
        norm = self.k1 * (1 - self.b + self.b * doc_len / (self.avg_len or 1.0))
        total = 0.0
        for term in query_tokens:
            tf = freqs.get(term, 0)
            if not tf:
                continue
            total += self.idf(term) * tf * (self.k1 + 1) / (tf + norm)
        return total

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """返回 [(doc_index, score)]，按分数降序，最多 top_k 条（分数 0 剔除）。"""
        query_tokens = tokenize(query)
        scored = [(i, self.score(query_tokens, i)) for i in range(self.doc_count)]
        hits = [(i, s) for i, s in scored if s > 0]
        hits.sort(key=lambda pair: pair[1], reverse=True)
        return hits[:top_k]


def rrf_fuse(
    rankings: list[list[str]],
    weights: list[float] | None = None,
    k: int = 60,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：输入多路召回的 doc_id 排名，输出融合排名。

    score(doc) = Σ weight_i / (k + rank_i(doc))，rank 从 1 开始；
    返回 [(doc_id, score)] 按分数降序。
    """
    weights = weights or [1.0] * len(rankings)
    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        if weight <= 0:
            continue
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


@dataclass
class TuningParams:
    """可自动调优的检索参数（持久化于 rag_tuning.json）。"""

    top_k: int = 6
    fetch_k: int = 18
    vector_weight: float = 1.0
    bm25_weight: float = 0.7
    target_recall: float = 0.90

    def as_dict(self) -> dict[str, float]:
        return {
            "top_k": self.top_k,
            "fetch_k": self.fetch_k,
            "vector_weight": self.vector_weight,
            "bm25_weight": self.bm25_weight,
            "target_recall": self.target_recall,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TuningParams:
        known = {f for f in cls.__dataclass_fields__}  # noqa: C416
        return cls(**{k: v for k, v in data.items() if k in known})
