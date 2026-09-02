"""mcp42 乐理校正门面测试（mock registry，不触网）。"""

import pytest

from app.agent import mcp_clients
from app.agent.mcp_clients import (
    analyze_progression,
    find_off_scale_chords,
    theory_summary,
)

ANALYSIS: dict = {
    "key": "C major",
    "romanNumerals": ["I", "V", "vi", "IV"],
    "cadences": [{"type": "deceptive", "index": 2, "chords": ["G", "Am"]}],
    "modulations": [],
}


@pytest.mark.anyio
async def test_analyze_progression_calls_registry(monkeypatch) -> None:
    calls: list[tuple[str, str, dict]] = []

    async def fake_call_tool(_server: str, _tool: str, _args: dict) -> dict:
        calls.append((_server, _tool, _args))
        return dict(ANALYSIS)

    monkeypatch.setattr(mcp_clients.mcp_registry, "call_tool", fake_call_tool)
    data = await analyze_progression(["C", "G", "Am", "F"], key_hint="C major")
    assert data["key"] == "C major"
    assert calls == [
        (
            "mcp42",
            "analyze-progression",
            {"chords": ["C", "G", "Am", "F"], "keyHint": "C major"},
        )
    ]


@pytest.mark.anyio
async def test_analyze_progression_parses_json_text(monkeypatch) -> None:
    async def fake_call_tool(_server: str, _tool: str, _args: dict) -> str:
        import json

        return json.dumps({"key": "A minor"})

    monkeypatch.setattr(mcp_clients.mcp_registry, "call_tool", fake_call_tool)
    assert await analyze_progression(["Am"]) == {"key": "A minor"}


@pytest.mark.anyio
async def test_analyze_progression_degrades_on_error(monkeypatch) -> None:
    async def broken(_server: str, _tool: str, _args: dict) -> dict:
        raise ConnectionError("remote down")

    monkeypatch.setattr(mcp_clients.mcp_registry, "call_tool", broken)
    assert await analyze_progression(["C"]) is None


def test_analyze_progression_empty_chords(monkeypatch) -> None:
    import asyncio

    async def should_not_be_called(_server: str, _tool: str, _args: dict) -> dict:
        raise AssertionError("不应发起调用")

    monkeypatch.setattr(mcp_clients.mcp_registry, "call_tool", should_not_be_called)
    assert asyncio.run(analyze_progression([])) is None


def test_theory_summary() -> None:
    assert "跳过" in theory_summary(None)
    summary = theory_summary(ANALYSIS)
    assert "C major" in summary
    assert "I-V-vi-IV" in summary
    assert "deceptive" in summary


def test_find_off_scale_chords() -> None:
    analysis = {"romanNumerals": ["I", "V", "bVII", "#iv"]}
    chords = ["C", "G", "Bb", "F#"]
    assert find_off_scale_chords(analysis, chords) == ["Bb", "F#"]
    assert find_off_scale_chords(None, chords) == []
