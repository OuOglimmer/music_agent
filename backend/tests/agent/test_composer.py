"""多 Agent 编排 graph 测试：通过注入假 Specialist 验证数据流、并行与返工循环。"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.agent import composer
from app.agent.config import config as agent_config
from app.agent.tools.composition import generate_chord_progression_data

FEATURES: dict[str, Any] = {
    "key": "C",
    "mode": "major",
    "bpm": 96,
    "mood": "温暖",
    "style": "pop",
    "bars": 4,
    "beats_per_bar": 4,
}


def fake_specialist(responses: dict[str, str], calls: list[str] | None = None):
    async def _fake(role: str, _system_prompt: str, _task: str) -> str:
        if calls is not None:
            calls.append(role)
        if role not in responses:
            raise RuntimeError(f"no canned response for {role}")
        return responses[role]

    return _fake


async def run_graph(
    monkeypatch, responses: dict[str, str], calls: list[str] | None = None
) -> dict[str, Any]:
    monkeypatch.setattr(composer, "_call_specialist", fake_specialist(responses, calls))

    # mcp42 是外部服务：graph 测试一律 mock
    async def fake_analysis(chords, key_hint=None):
        return {"key": key_hint, "romanNumerals": ["I"] * len(chords), "cadences": []}

    monkeypatch.setattr(composer, "analyze_progression", fake_analysis)
    monkeypatch.setattr(agent_config, "enable_mp3", False)
    graph = composer.build_compose_graph()
    final: dict[str, Any] = {}
    async for mode, payload in graph.astream(
        {"features": dict(FEATURES), "references": ""},
        stream_mode=["updates", "values"],
    ):
        if mode == "values":
            final = payload
    return final


@pytest.mark.anyio
async def test_graph_falls_back_when_llm_invalid(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(agent_config, "scores_dir", str(tmp_path))
    calls: list[str] = []
    final = await run_graph(
        monkeypatch,
        {"harmony": "这不是JSON", "melody": "也不是", "review": "随便"},
        calls,
    )

    # LLM 输出无效 → 全部走确定性兜底，流水线不中断
    assert (
        final["chords"]
        == generate_chord_progression_data("C", "major", "温暖", 4)["chords"]
    )
    assert len(final["melody"]) == 16  # 4 小节 × 每小节 4 音琶音
    assert "兜底" in final["melody_summary"]
    assert final["review"]["passed"] is True
    assert Path(final["midi_path"]).exists()
    assert set(calls) == {"harmony", "melody", "review"}


@pytest.mark.anyio
async def test_graph_llm_outputs_flow_between_nodes(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(agent_config, "scores_dir", str(tmp_path))
    final = await run_graph(
        monkeypatch,
        {
            "harmony": json.dumps(
                {"chords": ["C", "Am", "F", "G"], "rationale": "温暖正向"},
                ensure_ascii=False,
            ),
            "melody": json.dumps(
                {
                    "notes": [
                        {"pitch": "C5", "start": 0, "duration": 2, "velocity": 90},
                        {"pitch": "E5", "start": 2, "duration": 2, "velocity": 85},
                        {"pitch": "A4", "start": 4, "duration": 2, "velocity": 85},
                        {"pitch": "G4", "start": 6, "duration": 2, "velocity": 85},
                    ],
                    "summary": "平静开场",
                },
                ensure_ascii=False,
            ),
            "review": json.dumps(
                {"passed": True, "notes": ["副歌有记忆点"]}, ensure_ascii=False
            ),
        },
    )

    # 上游产出被下游消费：melody 与和弦对齐（4 小节），渲染拿到全部四轨
    assert final["chords"] == ["C", "Am", "F", "G"]
    assert final["harmony_rationale"] == "温暖正向"
    assert len(final["melody"]) == 4
    assert final["melody_summary"] == "平静开场"
    assert len(final["bass"]) == 8  # octave 风格：每小节 2 音
    assert len(final["drums"]) > 0
    assert final["review"]["passed"] is True
    assert final["revision_round"] == 1
    assert Path(final["midi_path"]).exists()
    assert "总谱已生成" in final["render_summary"]


@pytest.mark.anyio
async def test_graph_review_failure_triggers_rework_loop(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(agent_config, "scores_dir", str(tmp_path))
    calls: list[str] = []
    final = await run_graph(
        monkeypatch,
        {
            "harmony": json.dumps({"chords": ["C", "Am", "F", "G"]}),
            "melody": json.dumps(
                {"notes": [{"pitch": "C5", "start": 0, "duration": 4, "velocity": 90}]}
            ),
            "review": json.dumps(
                {"passed": False, "notes": ["副歌缺记忆点", "旋律太短"]},
                ensure_ascii=False,
            ),
        },
        calls,
    )

    # 评审不通过 → 回到旋律节点返工，直到达到最大返工轮数后仍渲染交付
    max_rounds = agent_config.max_revisions
    assert calls.count("review") == max_rounds + 1
    assert calls.count("melody") == max_rounds + 1
    assert final["revision_round"] == max_rounds + 1
    assert final["review"]["passed"] is False
    assert final["midi_path"]


@pytest.mark.anyio
async def test_graph_review_pass_renders_immediately(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(agent_config, "scores_dir", str(tmp_path))
    calls: list[str] = []
    await run_graph(
        monkeypatch,
        {
            "harmony": "坏输出",
            "melody": "坏输出",
            "review": json.dumps({"passed": True}),
        },
        calls,
    )
    assert calls.count("review") == 1
    assert calls.count("melody") == 1


def test_stage_detail_extracts_progress_text() -> None:
    assert (
        composer.stage_detail("harmony", {"harmony_rationale": "用卡农进行"})
        == "用卡农进行"
    )
    assert (
        composer.stage_detail("theory", {"theory_summary": "C major｜I-V-vi-IV"})
        == "C major｜I-V-vi-IV"
    )
    assert (
        composer.stage_detail("bass", {"bass_summary": "octave 风格"}) == "octave 风格"
    )
    assert (
        composer.stage_detail(
            "review", {"review": {"passed": False, "notes": ["太短"]}}
        )
        == "评审结论：需修改｜太短"
    )
    assert composer.stage_detail("render", {"midi_path": "/tmp/x.mid"}) == ""


@pytest.mark.parametrize(
    ("review", "round_num", "expected"),
    [
        ({"passed": True}, 1, "render"),
        ({"passed": False, "notes": []}, 1, "melody"),
        ({"passed": False, "notes": []}, agent_config.max_revisions, "melody"),
        ({"passed": False, "notes": []}, agent_config.max_revisions + 1, "render"),
        (None, 0, "render"),
    ],
)
def test_route_after_review(review: dict | None, round_num: int, expected: str) -> None:
    state: dict[str, Any] = {"review": review, "revision_round": round_num}
    assert composer.route_after_review(state) == expected  # type: ignore[arg-type]
