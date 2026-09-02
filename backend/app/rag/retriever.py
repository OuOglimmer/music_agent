"""向量知识库检索：向量召回 + BM25 词法召回 + RRF 融合（混合检索）。

为什么混合：纯向量检索对"精确术语/专有名词"召回弱（嵌入空间里
"4536 进行"和"卡农逆行"可能很远），BM25 恰好互补；RRF 按排名融合，
对两路分数刻度不敏感，稳健且免归一化。

检索参数（top_k/fetch_k/权重）支持自动调优：
- 存在 ``rag_tuning.json`` 时热加载（由 ``python -m app.rag.optimize`` 生成）；
- 否则用经过验证的默认值。
注意：chunk_size/chunk_overlap 属于入库参数（改了需要重建索引），
保持在 RagConfig 里由环境变量控制，不进自动调参。
"""

import json
import logging
import time
from pathlib import Path
from threading import Lock

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.observability import metrics, traced_span
from app.rag.config import config
from app.rag.embedding import get_embeddings
from app.rag.hybrid import BM25Index, TuningParams, rrf_fuse, tokenize

logger = logging.getLogger(__name__)

# 全局共享知识库的作用域，通过 build_knowledge_base.py 导入的文档对所有会话可见。
GLOBAL_SCOPE = "global"

_store: Chroma | None = None
_lock = Lock()

_TUNING_FILE = Path(__file__).resolve().parent / "rag_tuning.json"
_tuning: TuningParams | None = None
_tuning_lock = Lock()


def get_tuning() -> TuningParams:
    """返回当前检索参数；rag_tuning.json 存在时以它为准（进程内缓存）。"""
    global _tuning
    if _tuning is not None:
        return _tuning
    with _tuning_lock:
        if _tuning is None:
            tuning = TuningParams()
            if _TUNING_FILE.is_file():
                try:
                    tuning = TuningParams.from_dict(
                        json.loads(_TUNING_FILE.read_text(encoding="utf-8"))
                    )
                    logger.info("Loaded RAG tuning from %s", _TUNING_FILE)
                except Exception:
                    logger.warning(
                        "Invalid rag_tuning.json, using defaults", exc_info=True
                    )
            _tuning = tuning
    return _tuning


def set_tuning(tuning: TuningParams) -> None:
    """更新进程内检索参数（自动调参器调用；持久化由调用方负责）。"""
    global _tuning
    with _tuning_lock:
        _tuning = tuning


def get_vectorstore() -> Chroma:
    """返回持久化的 Chroma 向量库单例。"""
    global _store
    if _store is not None:
        return _store

    with _lock:
        if _store is None:
            _store = Chroma(
                collection_name=config.collection_name,
                embedding_function=get_embeddings(),
                persist_directory=str(config.chroma_dir),
            )
    return _store


def ingest_documents(session_id: str, documents: list[tuple[str, str]]) -> int:
    """把 (文件名, 文本) 切块后写入向量库，返回写入的文本块数量。"""
    if not documents:
        return 0
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
    )
    docs: list[Document] = []
    for filename, text in documents:
        source_doc = Document(
            page_content=text,
            metadata={"source": filename, "session_id": session_id},
        )
        docs.extend(splitter.split_documents([source_doc]))
    if docs:
        get_vectorstore().add_documents(docs)
        _invalidate_bm25(session_id)
    logger.info(
        "Ingested %s chunks from %s document(s) into session %s",
        len(docs),
        len(documents),
        session_id,
    )
    return len(docs)


# ---------------------------------------------------------------------------
# BM25 作用域索引（带语料计数失效）
# ---------------------------------------------------------------------------

_bm25_cache: dict[str, tuple[int, BM25Index, list[str], list[str]]] = {}
_bm25_lock = Lock()


def _invalidate_bm25(scope: str) -> None:
    with _bm25_lock:
        _bm25_cache.pop(scope, None)


def _get_scope_bm25(scope: str) -> tuple[BM25Index, list[str], list[str]]:
    """返回 (索引, doc_ids, doc_texts)。语料条数变化时自动重建。"""
    store = get_vectorstore()
    count = store._collection.count()  # noqa: SLF001 — langchain-chroma 未暴露 count
    cached = _bm25_cache.get(scope)
    if cached is not None and cached[0] == count:
        return cached[1], cached[2], cached[3]

    data = store._collection.get(  # noqa: SLF001
        where={"session_id": scope}, include=["documents"]
    )
    ids = list(data.get("ids") or [])
    texts = [str(t or "") for t in (data.get("documents") or [])]
    index = BM25Index([tokenize(t) for t in texts])
    with _bm25_lock:
        _bm25_cache[scope] = (count, index, ids, texts)
    return index, ids, texts


# ---------------------------------------------------------------------------
# 混合检索
# ---------------------------------------------------------------------------


def hybrid_search(
    session_id: str,
    query: str,
    top_k: int | None = None,
    fetch_k: int | None = None,
) -> list[tuple[Document, float]]:
    """混合检索：向量召回 + BM25 召回 → RRF 融合 → 取前 top_k。

    返回 [(Document, rrf_score)]，分数仅用于排序（无量纲）。
    任一路召回失败都降级为另一路（保证可用性）。
    """
    if not query.strip():
        return []
    tuning = get_tuning()
    k = top_k or tuning.top_k
    fk = max(fetch_k or tuning.fetch_k, k)
    scopes = [session_id, GLOBAL_SCOPE]
    started = time.perf_counter()

    with traced_span("rag.hybrid_search", query_chars=len(query), top_k=k) as span:
        # 1) 向量召回（作用域过滤）
        vec_docs: list[Document] = []
        try:
            vec_docs = get_vectorstore().similarity_search(
                query,
                k=fk,
                filter={"$or": [{"session_id": s} for s in scopes]},
            )
        except Exception:
            logger.warning("Vector retrieval failed, BM25 only", exc_info=True)

        # 2) BM25 召回（各作用域，重建 Document 附元数据）
        bm25_docs: list[Document] = []
        try:
            scored: list[tuple[float, str, str]] = []  # (score, scope, text)
            for scope in scopes:
                index, _ids, texts = _get_scope_bm25(scope)
                for doc_index, score in index.search(query, fk):
                    scored.append((score, scope, texts[doc_index]))
            scored.sort(key=lambda item: item[0], reverse=True)
            seen: set[str] = set()
            for _score, scope, text in scored:
                if text in seen:
                    continue
                seen.add(text)
                bm25_docs.append(
                    Document(page_content=text, metadata={"session_id": scope})
                )
        except Exception:
            logger.warning("BM25 retrieval failed, vector only", exc_info=True)

        # 3) RRF 融合（以文本为键对齐两路）
        fused = rrf_fuse(
            [
                [doc.page_content for doc in vec_docs],
                [doc.page_content for doc in bm25_docs],
            ],
            weights=[tuning.vector_weight, tuning.bm25_weight],
        )
        content_map = {doc.page_content: doc for doc in vec_docs}
        for doc in bm25_docs:
            content_map.setdefault(doc.page_content, doc)

        results = [
            (content_map[key], score) for key, score in fused[:k] if key in content_map
        ]
        span.attributes["vector_hits"] = len(vec_docs)
        span.attributes["bm25_hits"] = len(bm25_docs)
        span.attributes["returned"] = len(results)

    duration_ms = (time.perf_counter() - started) * 1000
    metrics.incr("rag.requests")
    metrics.observe("rag.duration", duration_ms)
    return results


def retrieve_context(session_id: str, query: str, k: int | None = None) -> str:
    """检索与问题相关的片段（当前会话 + 全局知识库），拼接为参考资料。"""
    found = hybrid_search(session_id, query, top_k=k)
    if not found:
        return ""
    return "\n\n".join(
        f"[{doc.metadata.get('source', '文档')}] {doc.page_content}"
        for doc, _score in found
    )
