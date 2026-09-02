"""RAG 召回率评估器：golden 集构建 + recall@k / MRR 计算。

评估思路（可复现、可离线）：
- **golden 集**：对每个语料块自动生成 1 条查询——取该块最有区分度的
  术语（分词后按 TF 挑选、剔除通用停用词），标注期望命中的块 ID；
- **指标**：recall@k（前 k 结果覆盖期望块的比例）为主，
  MRR（期望块平均倒数排名）为辅，用于同等 recall 下比排序质量；
- **嵌入可注入**：生产用 DashScope 嵌入；评估/调参可注入
  ``HashEmbeddings``（确定性哈希词袋，离线跑、结果稳定），
  验证检索管线本身（BM25/RRF/参数）的逻辑正确性。

注意：golden 集由语料自动生成，衡量的是"给定问法下管线能否找回
出处块"，是回归基线而非真实用户分布的替身；上线后应逐步补充
真实问答对（`extend_golden_set`）。
"""

import logging
import zlib
from collections import Counter
from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.hybrid import tokenize

logger = logging.getLogger(__name__)

# 通用停用词：生成查询时剔除，避免 golden 查询被虚词稀释
_STOPWORDS = {
    "的",
    "了",
    "是",
    "在",
    "和",
    "与",
    "或",
    "等",
    "一",
    "二",
    "三",
    "四",
    "五",
    "六",
    "可以",
    "使用",
    "以及",
    "通过",
    "对于",
    "进行",
    "要有",
    "用",
    "把",
    "要",
    "会",
    "不",
    "也",
    "这",
    "那",
    "就",
    "都",
    "而",
    "the",
    "a",
    "an",
    "of",
    "to",
    "and",
    "or",
    "is",
    "are",
    "in",
    "on",
}


class HashEmbeddings(Embeddings):
    """确定性哈希词袋嵌入：离线测试/调参用，结果跨进程稳定。

    每个 token 经 CRC32 映射到固定维度的一位（带符号），累加 TF 后
    L2 归一化。词汇重叠越多相似度越高——足以驱动检索管线验证。
    """

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in tokenize(text):
            crc = zlib.crc32(token.encode("utf-8"))
            index = crc % self.dim
            sign = 1.0 if (crc >> 16) & 1 else -1.0
            vec[index] += sign
        norm = sum(v * v for v in vec) ** 0.5
        return [v / norm for v in vec] if norm else vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


@dataclass
class GoldenItem:
    """一条评估样本：查询 + 期望命中的块 ID。"""

    query: str
    expected_chunk_id: str


def split_corpus(
    texts: list[tuple[str, str]], chunk_size: int, chunk_overlap: int
) -> list[Document]:
    """把 (来源名, 正文) 切成带稳定 chunk_id 的评估语料。"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    chunks: list[Document] = []
    for source, text in texts:
        for doc in splitter.split_documents([Document(page_content=text)]):
            chunk_id = f"{source}#{len(chunks)}"
            chunks.append(
                Document(
                    page_content=doc.page_content,
                    metadata={"source": source, "chunk_id": chunk_id},
                )
            )
    return chunks


def build_golden_set(
    chunks: list[Document], terms_per_query: int = 4
) -> list[GoldenItem]:
    """每个块生成一条术语查询；区分度不足（<2 个术语）的块跳过。"""
    golden: list[GoldenItem] = []
    for chunk in chunks:
        tokens = [t for t in tokenize(chunk.page_content) if t not in _STOPWORDS]
        freqs = Counter(tokens)
        # 过滤单字 CJK（区分度低），保留 ASCII 词与 CJK 二元组/多字词
        candidates = [
            (token, count)
            for token, count in freqs.most_common()
            if token.isascii() or len(token) >= 2
        ]
        if len(candidates) < 2:
            continue
        top_terms = [token for token, _ in candidates[:terms_per_query]]
        golden.append(
            GoldenItem(
                query=" ".join(top_terms), expected_chunk_id=chunk.metadata["chunk_id"]
            )
        )
    return golden


def evaluate(
    golden: list[GoldenItem],
    search_fn,
    k: int,
) -> dict[str, float]:
    """执行评估。

    search_fn(query, k) -> list[chunk_id]（按相关性降序）。
    返回 {"recall_at_k", "mrr", "queries", "hits"}。
    """
    if not golden:
        return {"recall_at_k": 0.0, "mrr": 0.0, "queries": 0, "hits": 0}
    hits = 0
    reciprocal_total = 0.0
    for item in golden:
        ranked = search_fn(item.query, k)
        if item.expected_chunk_id in ranked:
            hits += 1
            reciprocal_total += 1.0 / (ranked.index(item.expected_chunk_id) + 1)
    return {
        "recall_at_k": hits / len(golden),
        "mrr": reciprocal_total / len(golden),
        "queries": len(golden),
        "hits": hits,
    }


def extend_golden_set(
    extra: list[tuple[str, str]], golden: list[GoldenItem]
) -> list[GoldenItem]:
    """补充真实问答对：(query, expected_chunk_id)，覆盖自动生成的样本。"""
    return golden + [GoldenItem(query=q, expected_chunk_id=c) for q, c in extra]
