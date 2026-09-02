"""混合检索原子组件测试：分词 / BM25 / RRF / 调参参数。"""

from app.rag.hybrid import BM25Index, TuningParams, rrf_fuse, tokenize


def test_tokenize_mixed_language() -> None:
    tokens = tokenize("BPM 120 的副歌 Chorus")
    assert "bpm" in tokens
    assert "120" in tokens
    assert "chorus" in tokens
    assert "副歌" in tokens  # CJK 二元组
    assert "副" in tokens and "歌" in tokens  # CJK 单字


def test_tokenize_pure_chinese_pairs() -> None:
    tokens = tokenize("贝斯手")
    assert "贝斯" in tokens
    assert "斯手" in tokens


def test_bm25_ranks_relevant_doc_first() -> None:
    corpus = [
        tokenize("副歌是全曲记忆点 音区最高"),
        tokenize("贝斯负责低音律动 与鼓组配合"),
        tokenize("桥段提供转折 与主副歌形成对比"),
    ]
    index = BM25Index(corpus)
    hits = index.search("副歌 记忆点", top_k=2)
    assert hits[0][0] == 0
    assert len(hits) == 2  # 只有两个文档含查询词
    # 无关查询全部 0 分时不返回
    assert index.search("钢琴", top_k=3) == []


def test_rrf_fuse_prefers_consensus() -> None:
    ranking_a = ["doc1", "doc2", "doc3"]
    ranking_b = ["doc1", "doc4", "doc2"]
    fused = rrf_fuse([ranking_a, ranking_b])
    assert fused[0][0] == "doc1"  # 两路都排第一

    scores = dict(fused)
    assert scores["doc2"] > scores["doc3"]  # doc2 两路在榜 vs doc3 单路


def test_rrf_fuse_weights_and_zero_weight() -> None:
    fused = rrf_fuse(
        [["a", "b"], ["c", "d"]],
        weights=[1.0, 0.0],
    )
    assert [doc for doc, _ in fused] == ["a", "b"]  # 权重 0 的那路不贡献
    # 权重放大影响排序
    boosted = rrf_fuse([["a"], ["b"]], weights=[1.0, 10.0])
    assert boosted[0][0] == "b"


def test_tuning_params_roundtrip_ignores_unknown() -> None:
    params = TuningParams.from_dict(
        {
            "top_k": 8,
            "fetch_k": 24,
            "vector_weight": 0.5,
            "bm25_weight": 1.0,
            "bogus": 1,
        }
    )
    assert params.top_k == 8
    assert params.fetch_k == 24
    assert params.vector_weight == 0.5
    assert params.bm25_weight == 1.0
    assert "bogus" not in params.as_dict()
