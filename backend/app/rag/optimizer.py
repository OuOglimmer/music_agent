"""RAG 自动调参器：网格搜索检索参数，目标 recall@k ≥ 90%。

流程（``python -m app.rag.optimizer``）：
1. 载入 knowledge_base/ 文档，按当前 chunk 配置切块并构建 golden 集；
2. 建临时混合检索索引（向量用注入的嵌入，词法用 BM25）；
3. 网格遍历 top_k / fetch_k / 融合权重组合，逐组合评估 recall 与 MRR；
4. 选优：达到目标 recall 的组合里 MRR 最高者（并列取更小 top_k）；
   全部未达标时取 recall 最高者并在报告中标 ``met_target=false``；
5. 最优参数写入 ``rag_tuning.json``，retriever 启动时热加载。

测试/离线场景可注入 ``HashEmbeddings``，全程不触网。
"""

import itertools
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.rag.config import config as rag_config
from app.rag.evaluation import (
    HashEmbeddings,
    build_golden_set,
    evaluate,
    split_corpus,
)
from app.rag.hybrid import BM25Index, TuningParams, rrf_fuse, tokenize

logger = logging.getLogger(__name__)

TUNING_FILE = Path(__file__).resolve().parent / "rag_tuning.json"

# 默认搜索空间：覆盖"纯向量 → 纯词法"的权重谱系
DEFAULT_GRID: dict[str, list[Any]] = {
    "top_k": [4, 6, 8],
    "fetch_k": [12, 18, 24],
    "vector_weight": [1.0, 0.7, 0.5, 0.0],
    "bm25_weight": [0.0, 0.5, 0.7, 1.0],
}


@dataclass
class OptimizeReport:
    """一次调参的完整报告（可 JSON 化落盘）。"""

    met_target: bool
    target_recall: float
    best: dict[str, Any]
    results: list[dict[str, Any]] = field(default_factory=list)
    corpus_chunks: int = 0
    golden_queries: int = 0
    elapsed_ms: float = 0.0
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "met_target": self.met_target,
            "target_recall": self.target_recall,
            "best": self.best,
            "top_results": sorted(
                self.results, key=lambda r: (-r["recall_at_k"], -r["mrr"])
            )[:10],
            "corpus_chunks": self.corpus_chunks,
            "golden_queries": self.golden_queries,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "generated_at": self.generated_at,
        }


class HybridSearchHarness:
    """独立于生产 Chroma 的临时混合检索器（评估/调参专用）。

    生产 retriever 与本类共享 BM25Index / rrf_fuse 原子组件，
    保证"调参时测的"与"线上跑的"是同一套融合逻辑。
    """

    def __init__(
        self,
        chunks: list[Document],
        embeddings: Embeddings | None = None,
    ) -> None:
        self.chunks = chunks
        self.chunk_ids = [c.metadata["chunk_id"] for c in chunks]
        self.bm25 = BM25Index([tokenize(c.page_content) for c in chunks])
        self._embeddings = embeddings
        self._store: Chroma | None = None
        if embeddings is not None:
            self._store = Chroma(
                collection_name="rag_optimize_tmp",
                embedding_function=embeddings,
                persist_directory=None,  # 内存态，进程结束即回收
            )
            self._store.add_documents(
                [
                    Document(
                        page_content=c.page_content,
                        metadata={"chunk_id": c.metadata["chunk_id"]},
                    )
                    for c in chunks
                ]
            )

    def _bm25_ranking(self, query: str, fetch_k: int) -> list[str]:
        hits = self.bm25.search(query, fetch_k)
        return [self.chunk_ids[i] for i, _score in hits]

    def _vector_ranking(self, query: str, fetch_k: int) -> list[str]:
        if self._store is None:
            return []
        docs = self._store.similarity_search(query, k=fetch_k)
        return [str(d.metadata.get("chunk_id")) for d in docs]

    def search(self, query: str, k: int, params: TuningParams) -> list[str]:
        fetch_k = max(params.fetch_k, k)
        rankings = [
            self._vector_ranking(query, fetch_k),
            self._bm25_ranking(query, fetch_k),
        ]
        fused = rrf_fuse(rankings, weights=[params.vector_weight, params.bm25_weight])
        return [chunk_id for chunk_id, _score in fused[:k]]

    def as_search_fn(self, params: TuningParams) -> Callable[[str, int], list[str]]:
        return lambda query, k: self.search(query, k, params)


def _hybrid_bonus(row: dict[str, Any]) -> int:
    """选优打破平局时优先保留混合冗余：两路召回都启用 > 单路。

    离线调参用的嵌入未必与线上一致（哈希词袋 vs 真实嵌入模型），
    recall/MRR 打平时保留 bm25_weight>0 的组合，可对嵌入模型漂移
    提供词法召回兜底。
    """
    return int(row["vector_weight"] > 0 and row["bm25_weight"] > 0)


def optimize(
    corpus: list[tuple[str, str]],
    target_recall: float = 0.90,
    embeddings: Embeddings | None = None,
    grid: dict[str, list[Any]] | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> tuple[OptimizeReport, TuningParams]:
    """跑一轮自动调参，返回 (报告, 最优参数)。不产生副作用。"""
    started = time.perf_counter()
    grid = grid or DEFAULT_GRID
    chunk_size = chunk_size or rag_config.chunk_size
    chunk_overlap = chunk_overlap or rag_config.chunk_overlap

    chunks = split_corpus(corpus, chunk_size, chunk_overlap)
    golden = build_golden_set(chunks)
    if not golden:
        raise ValueError("语料过少，无法构建 golden 集（每个块至少要有 2 个术语）")
    harness = HybridSearchHarness(chunks, embeddings=embeddings or HashEmbeddings())

    results: list[dict[str, Any]] = []
    best_item: dict[str, Any] | None = None
    for top_k, fetch_k, vec_w, bm25_w in itertools.product(
        grid["top_k"], grid["fetch_k"], grid["vector_weight"], grid["bm25_weight"]
    ):
        if vec_w == 0.0 and bm25_w == 0.0:
            continue  # 两路全关没有意义
        params = TuningParams(
            top_k=top_k, fetch_k=fetch_k, vector_weight=vec_w, bm25_weight=bm25_w
        )
        metrics = evaluate(golden, harness.as_search_fn(params), k=top_k)
        row = {
            **params.as_dict(),
            "recall_at_k": metrics["recall_at_k"],
            "mrr": metrics["mrr"],
        }
        results.append(row)
        better = best_item is None or (
            row["recall_at_k"],
            row["mrr"],
            _hybrid_bonus(row),
            -row["top_k"],
        ) > (
            best_item["recall_at_k"],
            best_item["mrr"],
            _hybrid_bonus(best_item),
            -best_item["top_k"],
        )
        if better:
            best_item = row

    assert best_item is not None
    best_params = TuningParams(
        top_k=int(best_item["top_k"]),
        fetch_k=int(best_item["fetch_k"]),
        vector_weight=float(best_item["vector_weight"]),
        bm25_weight=float(best_item["bm25_weight"]),
        target_recall=target_recall,
    )
    report = OptimizeReport(
        met_target=best_item["recall_at_k"] >= target_recall,
        target_recall=target_recall,
        best={
            **best_params.as_dict(),
            "recall_at_k": best_item["recall_at_k"],
            "mrr": best_item["mrr"],
        },
        results=results,
        corpus_chunks=len(chunks),
        golden_queries=len(golden),
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )
    return report, best_params


def persist(
    report: OptimizeReport, params: TuningParams, path: Path = TUNING_FILE
) -> None:
    """把最优参数与报告写入 rag_tuning.json（retriever 启动时热加载）。"""
    payload = {
        **params.as_dict(),
        "last_report": report.as_dict(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("RAG tuning persisted to %s: %s", path, params.as_dict())


def load_knowledge_base_corpus() -> list[tuple[str, str]]:
    """读取 knowledge_base/ 全部 txt/md 作为调参语料。"""
    kb_dir = Path(__file__).resolve().parent / "knowledge_base"
    files = (
        sorted(p for p in kb_dir.iterdir() if p.suffix.lower() in {".txt", ".md"})
        if kb_dir.exists()
        else []
    )
    return [(f.name, f.read_text(encoding="utf-8", errors="replace")) for f in files]


def main() -> None:
    corpus = load_knowledge_base_corpus()
    if not corpus:
        print(f"knowledge_base/ 目录为空，无语料可调参：{rag_config.chroma_dir}")  # noqa: T201
        return
    report, params = optimize(corpus, target_recall=0.90)
    persist(report, params)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))  # noqa: T201
    print(f"\n最优参数已写入 {TUNING_FILE}")  # noqa: T201


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
