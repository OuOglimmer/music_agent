"""听觉解析模块测试：文本解析器与降级行为（不触网、不依赖二进制）。"""

from app.agent import mcp_audio
from app.agent.mcp_audio import analysis_summary, parse_analysis_text

RHYTHM_TEXT = """Rhythm Analysis: /tmp/test.wav

Estimated Tempo: 120.2 BPM (confidence: 0.822)
Detected Beats: 15

Beat Statistics:
  Mean tempo: 120.0 BPM | Median: 120.2 BPM
  Stability: 0.994 (0=erratic, 1=metronomic)"""

HARMONIC_TEXT = """Harmonic Analysis: /tmp/test.wav

Estimated Key: Bb major (confidence: 0.836)"""

INFO_TEXT = (
    "File: /tmp/test.wav\nSample rate: 44100 Hz\n"
    "Samples: 352800\nDuration: 8.00 seconds"
)


def test_parse_analysis_text_extracts_features() -> None:
    features = parse_analysis_text(RHYTHM_TEXT, HARMONIC_TEXT, INFO_TEXT)
    assert features is not None
    assert features["bpm"] == 120
    assert features["bpm_confidence"] == 0.822
    assert features["key"] == "Bb"
    assert features["mode"] == "major"
    assert features["key_confidence"] == 0.836
    assert features["tempo_stability"] == 0.994
    assert features["duration_seconds"] == 8.0


def test_parse_analysis_text_bpm_clamped() -> None:
    text = "Estimated Tempo: 300.0 BPM (confidence: 0.5)"
    features = parse_analysis_text(text, "", "")
    assert features is not None
    assert features["bpm"] == 220  # 夹到合法上限


def test_parse_analysis_text_returns_none_without_signal() -> None:
    assert parse_analysis_text("nothing", "useful", "here") is None
    assert parse_analysis_text("", "", "") is None


def test_analysis_summary() -> None:
    assert "不可用" in analysis_summary(None)
    features = parse_analysis_text(RHYTHM_TEXT, HARMONIC_TEXT, INFO_TEXT)
    summary = analysis_summary(features)
    assert "120 BPM" in summary
    assert "大调" in summary
    assert "8.0 秒" in summary


def test_analyzer_available(monkeypatch) -> None:
    import tempfile
    from pathlib import Path

    from app.agent.config import config as agent_config

    # 二进制不存在 → 不可用
    monkeypatch.setattr(agent_config, "audio_analyzer_command", "/no/such/binary")
    assert mcp_audio.analyzer_available() is False
    # 留空 → 禁用
    monkeypatch.setattr(agent_config, "audio_analyzer_command", "")
    assert mcp_audio.analyzer_available() is False
    # 存在 → 可用
    with tempfile.NamedTemporaryFile() as tmp:
        monkeypatch.setattr(agent_config, "audio_analyzer_command", tmp.name)
        assert mcp_audio.analyzer_available() is True
        assert Path(tmp.name).is_file()
