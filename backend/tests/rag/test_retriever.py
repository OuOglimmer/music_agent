"""RAG 混合检索集成测试（内存 Chroma + Hash 嵌入，离线跑）与调参器测试。"""

import json
from pathlib import Path

import pytest
from langchain_chroma import Chroma

from app.rag import retriever
from app.rag.evaluation import HashEmbeddings
from app.rag.hybrid import TuningParams
from app.rag.optimizer import optimize, persist

CORPUS: list[tuple[str, str]] = [
    ("chorus.md", "副歌是全曲的记忆点，音区最高，情绪最满，核心句要重复出现。"),
    ("bass.md", "贝斯负责低音律动，和鼓组一起构成节奏骨架，八度与根五音最常见。"),
    ("bridge.md", "桥段提供转折和新视角，与主副歌形成对比，常放在第二遍副歌之后。"),
    ("bpm.md", "慢速抒情是60到80拍，中速民谣80到110拍，流行摇滚110到160拍。"),
    ("progression.md", "卡农进行1-5-6-4温暖正向，4536进行在华语流行副歌高频出现。"),
    ("lyrics.md", "歌词要用具体意象代替抽象口号，教室后排的风扇好于青春的记忆。"),
    ("suno.md", "Suno提示词用英文逗号分隔风格标签，描述主奏乐器情绪速度人声质感。"),
    ("arrangement.md", "前奏只用一到两件乐器留白，副歌全奏推高音区，桥段做减法。"),
]


@pytest.fixture
def _fake_store(monkeypatch):
    """把生产 retriever 指向内存 Chroma（Hash 嵌入，离线）。"""
    store = Chroma(
        collection_name="test_rag",
        embedding_function=HashEmbeddings(dim=256),
        persist_directory=None,
    )
    monkeypatch.setattr(retriever, "_store", store)
    # 检索参数用确定值（不受本地 rag_tuning.json 影响）
    retriever.set_tuning(TuningParams())
    yield store
    retriever._tuning = None


def test_hybrid_search_finds_session_and_global_docs(_fake_store) -> None:
    retriever.ingest_documents(
        "sess-1", [("my-notes.md", " user_notes 提到：副歌要有钩子，用具体意象。")]
    )
    retriever.ingest_documents(
        retriever.GLOBAL_SCOPE, [("kb.md", "贝斯负责低音律动，与鼓组配合。")]
    )

    results = retriever.hybrid_search("sess-1", "副歌钩子 具体意象")

    assert results, "应至少命中一条"
    assert all(isinstance(doc.metadata.get("session_id"), str) for doc, _ in results)
    scores = [score for _, score in results]
    assert scores == sorted(scores, reverse=True)  # 分数降序


def test_hybrid_search_scope_isolation(_fake_store) -> None:
    retriever.ingest_documents("sess-a", [("a.md", "副歌要有钩子，口语化可重复。")])
    retriever.ingest_documents("sess-b", [("b.md", "贝斯八度跳跃，律动感强。")])

    hits_a = retriever.hybrid_search("sess-a", "副歌 钩子")
    assert hits_a
    assert all(doc.metadata["session_id"] in ("sess-a", "global") for doc, _ in hits_a)


def test_hybrid_search_empty_query(_fake_store) -> None:
    assert retriever.hybrid_search("s", "   ") == []


def test_retrieve_context_format(_fake_store) -> None:
    retriever.ingest_documents(
        retriever.GLOBAL_SCOPE, [("kb.md", "贝斯低音律动 鼓组节奏骨架。")]
    )
    context = retriever.retrieve_context("sess-x", "贝斯 律动")
    assert "[kb.md]" in context


def test_honors_tuning_top_k(_fake_store) -> None:
    retriever.ingest_documents(
        "s", [("m.md", "副歌记忆点。" * 3), ("n.md", "贝斯律动。" * 3)]
    )
    retriever.set_tuning(TuningParams(top_k=1))
    results = retriever.hybrid_search("s", "副歌 记忆点")
    assert len(results) <= 1


def test_tuning_file_hot_load(monkeypatch, tmp_path: Path) -> None:
    tuning_file = tmp_path / "rag_tuning.json"
    tuning_file.write_text(
        json.dumps(
            {"top_k": 7, "fetch_k": 21, "vector_weight": 0.8, "bm25_weight": 0.6}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(retriever, "_TUNING_FILE", tuning_file)
    monkeypatch.setattr(retriever, "_tuning", None)  # 清缓存触发重载

    tuning = retriever.get_tuning()

    assert tuning.top_k == 7
    assert tuning.vector_weight == 0.8


# ---------------------------------------------------------------------------
# 自动调参器
# ---------------------------------------------------------------------------


def test_optimize_reaches_90_percent_recall_on_corpus() -> None:
    """核心验收：混合检索 + 自动调参在评估语料上 recall ≥ 90%。"""
    report, params = optimize(
        CORPUS,
        target_recall=0.90,
        embeddings=HashEmbeddings(dim=256),
        grid={
            "top_k": [4, 6],
            "fetch_k": [12],
            "vector_weight": [1.0, 0.5, 0.0],
            "bm25_weight": [0.0, 0.5, 1.0],
        },
        chunk_size=120,
        chunk_overlap=0,
    )

    assert report.met_target, f"调参未达标：{report.best}"
    assert report.best["recall_at_k"] >= 0.90
    assert report.corpus_chunks >= len(CORPUS)
    assert report.golden_queries >= len(CORPUS) - 2  # 个别块可能因术语不足跳过
    assert params.top_k in (4, 6)
    assert params.vector_weight + params.bm25_weight > 0


def test_optimize_report_is_serializable() -> None:
    report, params = optimize(
        CORPUS,
        target_recall=0.9,
        embeddings=HashEmbeddings(dim=256),
        grid={
            "top_k": [4],
            "fetch_k": [12],
            "vector_weight": [1.0],
            "bm25_weight": [0.7],
        },
        chunk_size=120,
        chunk_overlap=0,
    )
    payload = report.as_dict()
    assert (
        json.loads(json.dumps(payload, ensure_ascii=False))["met_target"]
        == report.met_target
    )
    assert payload["best"]["top_k"] == params.top_k


def test_persist_writes_tuning_file(tmp_path: Path) -> None:
    report, params = optimize(
        CORPUS,
        target_recall=0.9,
        embeddings=HashEmbeddings(dim=256),
        grid={
            "top_k": [4],
            "fetch_k": [12],
            "vector_weight": [1.0],
            "bm25_weight": [0.7],
        },
        chunk_size=120,
        chunk_overlap=0,
    )
    target = tmp_path / "rag_tuning.json"
    persist(report, params, path=target)

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["top_k"] == params.top_k
    assert data["last_report"]["best"]["recall_at_k"] >= 0.9


def test_optimizer_corpus_requirement() -> None:
    with pytest.raises(ValueError, match="语料过少"):
        optimize(
            [("tiny.md", "太短")],
            grid={
                "top_k": [2],
                "fetch_k": [4],
                "vector_weight": [1.0],
                "bm25_weight": [0.5],
            },
        )
