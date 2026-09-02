"""创作工具层测试：和弦生成、贝斯、鼓组、旋律校验、MIDI 渲染。"""

import json
from pathlib import Path

import music21

from app.agent.config import config as agent_config
from app.agent.tools.composition import (
    fallback_melody_data,
    generate_bass_line_data,
    generate_chord_progression_data,
    generate_drum_pattern_data,
    render_midi_data,
    validate_melody_data,
)


def test_chord_progression_major_warm() -> None:
    data = generate_chord_progression_data("C", "major", "温暖", 8)
    assert data["chords"] == ["C", "G", "Am", "F"] * 2
    assert data["degrees"] == ["I", "V", "vi", "IV"]


def test_chord_progression_minor_sad() -> None:
    data = generate_chord_progression_data("a", "minor", "伤感", 8)
    assert data["chords"] == ["Am", "F", "C", "G"] * 2
    assert data["degrees"] == ["i", "VI", "III", "VII"]


def test_chord_progression_mood_alias() -> None:
    # "遗憾" 命中 emotional 模板（6-4-1-5）
    data = generate_chord_progression_data("C", "major", "遗憾的情歌", 4)
    assert data["chords"] == ["Am", "F", "C", "G"]


def test_chord_progression_bars_beyond_template() -> None:
    data = generate_chord_progression_data("C", "major", "温暖", 5)
    assert len(data["chords"]) == 5
    assert data["chords"][:4] == ["C", "G", "Am", "F"]


def test_bass_line_octave_style() -> None:
    notes = generate_bass_line_data(["C", "G"], "octave")
    first_bar = [n for n in notes if n["start"] < 4]
    assert {n["pitch"] for n in first_bar} == {"C2", "C3"}
    second_bar = [n for n in notes if n["start"] >= 4]
    assert {n["pitch"] for n in second_bar} == {"G2", "G3"}


def test_bass_line_root_fifth_style() -> None:
    notes = generate_bass_line_data(["C"], "root_fifth")
    pitches = [n["pitch"] for n in notes]
    assert pitches == ["C2", "G2"]


def test_bass_line_unknown_style_falls_back() -> None:
    notes = generate_bass_line_data(["C"], "不存在的风格")
    # 回退到 octave：一小节两个音符
    assert len(notes) == 2


def test_drum_pattern_pop() -> None:
    events = generate_drum_pattern_data("pop", 2)
    bar0 = [e for e in events if e["start"] < 4]
    kick_starts = [e["start"] for e in bar0 if e["drum"] == "kick"]
    snare_starts = [e["start"] for e in bar0 if e["drum"] == "snare"]
    assert kick_starts == [0, 2]
    assert snare_starts == [1, 3]
    # 开场有一次 crash
    assert any(e["drum"] == "crash" and e["start"] == 0 for e in events)


def test_drum_pattern_edm_four_on_floor() -> None:
    events = generate_drum_pattern_data("edm", 1)
    kick_starts = [e["start"] for e in events if e["drum"] == "kick"]
    assert kick_starts == [0, 1, 2, 3]


def test_validate_melody_flags_off_scale() -> None:
    in_scale = [
        {"pitch": "C5", "start": 0, "duration": 1, "velocity": 90},
        {"pitch": "E5", "start": 1, "duration": 1, "velocity": 85},
        {"pitch": "G5", "start": 2, "duration": 1, "velocity": 85},
        {"pitch": "F5", "start": 3, "duration": 1, "velocity": 85},
    ]
    _, problems = validate_melody_data("C", "major", in_scale)
    assert problems == []

    off_scale = in_scale[:3] + [
        {"pitch": "F#5", "start": 3, "duration": 1, "velocity": 85},
    ]
    _, problems = validate_melody_data("C", "major", off_scale)
    assert any("不在调内" in p for p in problems)


def test_validate_melody_flags_overlap() -> None:
    overlapping = [
        {"pitch": "C5", "start": 0, "duration": 2, "velocity": 90},
        {"pitch": "E5", "start": 1, "duration": 1, "velocity": 85},
    ]
    _, problems = validate_melody_data("C", "major", overlapping)
    assert any("重叠" in p for p in problems)


def test_fallback_melody_follows_chord_tones() -> None:
    notes = fallback_melody_data(["C", "Am"], beats_per_bar=4)
    assert len(notes) == 8
    first_bar = [n["pitch"] for n in notes if n["start"] < 4]
    # C 大三和弦的根三五琶音
    assert first_bar == ["C5", "E5", "G5", "E5"]
    second_bar = [n["pitch"] for n in notes if n["start"] >= 4]
    assert second_bar[0] == "A5"


def test_render_midi_writes_four_track_score(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(agent_config, "scores_dir", str(tmp_path))
    chords = ["C", "G", "Am", "F"]
    result = render_midi_data(
        "C",
        "major",
        96,
        chords,
        melody=fallback_melody_data(chords),
        bass=generate_bass_line_data(chords, "octave"),
        drums=generate_drum_pattern_data("pop", 4),
        title="测试进行",
    )
    assert result["parts"] == 4
    assert result["melody_notes"] == 16
    assert result["chord_bars"] == 4
    path = Path(result["path"])
    assert path.exists() and path.suffix == ".mid"

    parsed = music21.converter.parse(str(path))
    assert len(parsed.parts) == 4


def test_render_midi_drums_on_gm_channel(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(agent_config, "scores_dir", str(tmp_path))
    result = render_midi_data(
        "C",
        "major",
        96,
        ["C"],
        drums=generate_drum_pattern_data("pop", 1),
        title="drums",
    )
    raw = Path(result["path"]).read_bytes()
    # channel 10（0 起算 9）的 note-on 事件：kick=36, snare=38, hihat=42
    events: list[int] = []
    for i, byte in enumerate(raw):
        if byte == 0x99:
            events.append(raw[i + 1])
    assert 36 in events and 38 in events and 42 in events


def test_render_midi_fills_missing_tracks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(agent_config, "scores_dir", str(tmp_path))
    result = render_midi_data("C", "major", 96, ["C", "F"], title="sparse")
    # 未提供旋律/贝斯/鼓时自动兜底补齐
    assert result["melody_notes"] == 8
    assert result["bass_notes"] == 4
    assert result["drum_hits"] > 0
    assert json.dumps(result["path"])  # 路径可序列化
