"""RAG 评估器测试：Hash 嵌入确定性 / golden 集构建 / recall 与 MRR。"""

from app.rag.evaluation import (
    GoldenItem,
    HashEmbeddings,
    build_golden_set,
    evaluate,
    split_corpus,
)


def test_hash_embeddings_deterministic_and_normalized() -> None:
    emb = HashEmbeddings(dim=256)
    a1 = emb.embed_query("副歌是全曲记忆点")
    a2 = emb.embed_documents(["副歌是全曲记忆点"])[0]
    assert a1 == a2  # 跨方法、跨调用一致
    norm = sum(v * v for v in a1) ** 0.5
    assert abs(norm - 1.0) < 1e-6
    # 相似文本相似度高于无关文本

    def cosine(u: list[float], v: list[float]) -> float:
        return sum(x * y for x, y in zip(u, v, strict=True)) / (
            (sum(x * x for x in u) ** 0.5) * (sum(y * y for y in v) ** 0.5) + 1e-9
        )

    b = emb.embed_query("副歌记忆点 音区最高")
    c = emb.embed_query("贝斯低音律动 鼓组节奏")
    assert cosine(a1, b) > cosine(a1, c)


def test_split_corpus_assigns_stable_chunk_ids() -> None:
    corpus = [
        ("a.md", "第一段内容，讲副歌的写法。" * 10),
        ("b.md", "第二段内容，讲贝斯的律动。" * 10),
    ]
    chunks = split_corpus(corpus, chunk_size=120, chunk_overlap=0)
    assert len(chunks) >= 2
    ids = [c.metadata["chunk_id"] for c in chunks]
    assert len(set(ids)) == len(ids)
    assert ids[0].startswith("a.md#")


def test_build_golden_set_picks_distinctive_terms() -> None:
    corpus = [("a.md", "副歌是全曲记忆点，音区最高，核心句重复。" * 3)]
    chunks = split_corpus(corpus, chunk_size=200, chunk_overlap=0)
    golden = build_golden_set(chunks)
    assert len(golden) >= 1
    assert golden[0].query
    assert golden[0].expected_chunk_id


def test_evaluate_recall_and_mrr() -> None:
    golden = [
        GoldenItem(query="q1", expected_chunk_id="c1"),
        GoldenItem(query="q2", expected_chunk_id="c2"),
    ]

    def perfect(query: str, _k: int) -> list[str]:
        return ["c1", "c2"] if query == "q1" else ["c2", "c1"]  # 期望块恒排第 1

    result = evaluate(golden, perfect, k=2)
    assert result["recall_at_k"] == 1.0
    assert abs(result["mrr"] - 1.0) < 1e-9

    def rank2(query: str, _k: int) -> list[str]:
        expected = "c1" if query == "q1" else "c2"
        return ["x", expected]  # 期望块恒排第 2

    result = evaluate(golden, rank2, k=2)
    assert result["recall_at_k"] == 1.0
    assert abs(result["mrr"] - 0.5) < 1e-9

    def miss(_query: str, _k: int) -> list[str]:
        return ["x", "y"]

    result = evaluate(golden, miss, k=2)
    assert result["recall_at_k"] == 0.0
    assert evaluate([], miss, k=2)["queries"] == 0
