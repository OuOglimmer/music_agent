"""听觉解析模块测试：文本解析器（对齐 analyzer 真实输出格式）与注册中心链路。

不触网、不依赖二进制：registry 用独立实例注入，call_many 打假会话。
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from app.agent import mcp_audio
from app.agent.mcp_registry import McpRegistry, McpServerConfig

# 节奏/和声样例取自真实 analyzer 输出（/tmp/test_melody.wav，120BPM C 大调）
RHYTHM_TEXT = """Rhythm Analysis: /tmp/test.wav

Estimated Tempo: 120.2 BPM (confidence: 0.775)
Detected Beats: 16

Beat Statistics:
  Mean tempo: 120.4 BPM | Median: 120.2 BPM
  Stability: 0.987 (0=erratic, 1=metronomic)
  IBI std dev: 0.007 sec

First 16 beats: 0.01s, 0.49s, 0.99s, 1.49s, 1.99s, 2.48s, 2.98s, 3.48s, 3.98s, 4.48s, 4.99s, 5.49s, 5.99s, 6.49s, 6.99s, 7.49s"""

HARMONIC_TEXT = """Harmonic Analysis: /tmp/test.wav

Estimated Key: Bb major (confidence: 0.836)

Pitch Class Distribution:
   1. Bb 0.502 ███████████████
   2. F  0.418 ████████████
   3. D  0.355 ██████████
   4. G# 0.166 ████
   5. C# 0.136 ████

Tonnetz (avg across 915 frames):
  Fifths: (0.217, 0.246) | Minor 3rds: (0.019, 0.146) | Major 3rds: (0.049, 0.030)"""

INFO_TEXT = (
    "File: /tmp/test.wav\nSample rate: 44100 Hz\n"
    "Samples: 352800\nDuration: 8.00 seconds"
)


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------


def test_parse_analysis_text_extracts_features() -> None:
    features = mcp_audio.parse_analysis_text(RHYTHM_TEXT, HARMONIC_TEXT, INFO_TEXT)
    assert features is not None
    assert features["bpm"] == 120
    assert features["bpm_confidence"] == 0.775
    assert features["key"] == "Bb"
    assert features["mode"] == "major"
    assert features["key_confidence"] == 0.836
    assert features["tempo_stability"] == 0.987
    assert features["duration_seconds"] == 8.0


def test_parse_analysis_text_extracts_beats() -> None:
    features = mcp_audio.parse_analysis_text(RHYTHM_TEXT, HARMONIC_TEXT, INFO_TEXT)
    assert features is not None
    assert features["beat_count"] == 16
    assert features["beats"][:3] == [0.01, 0.49, 0.99]
    assert features["beats"][-1] == 7.49
    assert len(features["beats"]) == 16


def test_parse_analysis_text_extracts_top_pitch_classes() -> None:
    features = mcp_audio.parse_analysis_text(RHYTHM_TEXT, HARMONIC_TEXT, INFO_TEXT)
    assert features is not None
    # 阈值 0.25：Bb/F/D 入选，G#/C#（0.166/0.136）被过滤
    assert features["top_pitch_classes"] == ["Bb", "F", "D"]


def test_parse_analysis_text_bpm_clamped() -> None:
    text = "Estimated Tempo: 300.0 BPM (confidence: 0.5)"
    features = mcp_audio.parse_analysis_text(text, "", "")
    assert features is not None
    assert features["bpm"] == 220  # 夹到合法上限


def test_parse_analysis_text_beats_cap() -> None:
    many = ", ".join(f"{i * 0.5:.2f}s" for i in range(200))
    rhythm = f"Estimated Tempo: 100 BPM (confidence: 0.9)\nDetected Beats: 200\nFirst 200 beats: {many}"
    features = mcp_audio.parse_analysis_text(rhythm, "", "")
    assert features is not None
    assert len(features["beats"]) == mcp_audio._BEATS_CAP  # 长曲目截断


def test_parse_analysis_text_returns_none_without_signal() -> None:
    assert mcp_audio.parse_analysis_text("nothing", "useful", "here") is None
    assert mcp_audio.parse_analysis_text("", "", "") is None


def test_analysis_summary() -> None:
    assert "不可用" in mcp_audio.analysis_summary(None)
    features = mcp_audio.parse_analysis_text(RHYTHM_TEXT, HARMONIC_TEXT, INFO_TEXT)
    summary = mcp_audio.analysis_summary(features)
    assert "120 BPM" in summary
    assert "16 拍" in summary
    assert "大调" in summary
    assert "Bb-F-D" in summary  # 突出音级
    assert "8.0 秒" in summary


# ---------------------------------------------------------------------------
# 可用性（registry 注入）
# ---------------------------------------------------------------------------


def _registry_with(command: str) -> McpRegistry:
    registry = McpRegistry()
    if command:
        registry.register(
            McpServerConfig(
                name="audio-analyzer", transport="stdio", command=command
            )
        )
    return registry


def test_analyzer_available_checks_registry(monkeypatch, tmp_path: Path) -> None:
    # 未注册 → 不可用
    monkeypatch.setattr(mcp_audio, "mcp_registry", _registry_with(""))
    assert mcp_audio.analyzer_available() is False
    # 注册但命令文件不存在 → 不可用（提前失败优于拉起时失败）
    monkeypatch.setattr(
        mcp_audio, "mcp_registry", _registry_with("/no/such/binary")
    )
    assert mcp_audio.analyzer_available() is False
    # 注册且存在 → 可用
    binary = tmp_path / "analyzer"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mcp_audio, "mcp_registry", _registry_with(str(binary)))
    assert mcp_audio.analyzer_available() is True


# ---------------------------------------------------------------------------
# analyze_audio_file：经注册中心 call_many 的同会话批量调用
# ---------------------------------------------------------------------------


class FakeSession:
    def __init__(self, outputs: dict[str, str]):
        self._outputs = outputs
        self.calls: list[tuple[str, dict]] = []

    async def initialize(self) -> None:
        pass

    async def call_tool(self, name: str, args: dict) -> Any:
        self.calls.append((name, args))
        return self._outputs[name]


@pytest.mark.anyio
async def test_analyze_audio_file_uses_registry_single_session(
    monkeypatch, tmp_path: Path
) -> None:
    registry = _registry_with(str(tmp_path / "analyzer"))
    session = FakeSession(
        {
            "audio_info": INFO_TEXT,
            "rhythm_analysis": RHYTHM_TEXT,
            "harmonic_analysis": HARMONIC_TEXT,
        }
    )

    @asynccontextmanager
    async def _cm(_server: McpServerConfig):
        yield session

    monkeypatch.setattr(mcp_audio, "mcp_registry", registry)
    monkeypatch.setattr(registry, "_open_session", _cm)

    features = await mcp_audio.analyze_audio_file("/tmp/x.wav")

    # 三个工具在同一次会话（一个 stdio 进程）内完成
    assert [name for name, _ in session.calls] == [
        "audio_info",
        "rhythm_analysis",
        "harmonic_analysis",
    ]
    assert all(args == {"path": "/tmp/x.wav"} for _, args in session.calls)
    assert features is not None
    assert features["bpm"] == 120
    assert features["beat_count"] == 16
    assert features["top_pitch_classes"] == ["Bb", "F", "D"]


@pytest.mark.anyio
async def test_analyze_audio_file_degrades_on_failure(monkeypatch, tmp_path: Path) -> None:
    registry = _registry_with(str(tmp_path / "analyzer"))

    async def broken(*args: object, **kwargs: object) -> list:
        raise ConnectionError("analyzer crashed")

    monkeypatch.setattr(mcp_audio, "mcp_registry", registry)
    monkeypatch.setattr(registry, "call_many", broken)
    assert await mcp_audio.analyze_audio_file("/tmp/x.wav") is None


@pytest.mark.anyio
async def test_analyze_audio_file_skips_when_unregistered(monkeypatch) -> None:
    monkeypatch.setattr(mcp_audio, "mcp_registry", _registry_with(""))
    assert await mcp_audio.analyze_audio_file("/tmp/x.wav") is None
