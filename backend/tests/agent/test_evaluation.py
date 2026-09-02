"""评估闭环测试：评审输出解析与聚合计算。"""

from app.agent.evaluation import _aggregate, parse_judge_output

_VALID = (
    '{"relevance": 8, "professionalism": 7, "actionability": 9, '
    '"clarity": 8, "overall": 8, "comment": "副歌钩子不错"}'
)


def test_parse_judge_output_valid() -> None:
    parsed = parse_judge_output(_VALID)
    assert parsed is not None
    assert parsed["overall"] == 8
    assert parsed["comment"] == "副歌钩子不错"


def test_parse_judge_output_tolerates_wrapped_text() -> None:
    parsed = parse_judge_output(f"评审结果如下：\n{_VALID}\n以上。")
    assert parsed is not None
    assert parsed["relevance"] == 8


def test_parse_judge_output_rejects_missing_dimension() -> None:
    bad = '{"relevance": 8, "overall": 8, "comment": "缺维度"}'
    assert parse_judge_output(bad) is None
    assert parse_judge_output("完全不是 JSON") is None


def test_parse_judge_output_clamps_range() -> None:
    parsed = parse_judge_output(
        '{"relevance": 99, "professionalism": -3, "actionability": 5, '
        '"clarity": 5, "overall": 5, "comment": ""}'
    )
    assert parsed is not None
    assert parsed["relevance"] == 10
    assert parsed["professionalism"] == 0


def test_aggregate_overall_and_groups() -> None:
    results = [
        {
            "intent": "lyric_writing",
            "prompt_version": "2.1.0",
            "scores": {
                "relevance": 8,
                "professionalism": 8,
                "actionability": 8,
                "clarity": 8,
                "overall": 8,
                "comment": "",
            },
        },
        {
            "intent": "lyric_writing",
            "prompt_version": "2.1.0",
            "scores": {
                "relevance": 6,
                "professionalism": 6,
                "actionability": 6,
                "clarity": 6,
                "overall": 6,
                "comment": "",
            },
        },
        {
            "intent": "chat",
            "prompt_version": "2.0.0",
            "scores": {
                "relevance": 4,
                "professionalism": 4,
                "actionability": 4,
                "clarity": 4,
                "overall": 4,
                "comment": "",
            },
        },
        {"intent": "chat", "prompt_version": None, "scores": None},
    ]
    report = _aggregate(results)
    assert report["sample_count"] == 4
    assert report["judged_count"] == 3
    assert report["failed_count"] == 1
    assert report["overall_avg"] == 6.0  # (8+6+4)/3
    assert report["dimensions_avg"]["relevance"] == 6.0
    assert report["by_intent"] == {"chat": 4.0, "lyric_writing": 7.0}
    assert report["by_prompt_version"] == {"2.0.0": 4.0, "2.1.0": 7.0}
