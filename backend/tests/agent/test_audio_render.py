"""音频渲染（MIDI→MP3）测试：降级路径离线验证，真实渲染按环境跳过。"""

import shutil
from pathlib import Path

import pytest

from app.agent import audio_render
from app.agent.audio_render import find_soundfont, render_mp3
from app.agent.tools.composition import render_midi_data


def test_find_soundfont_returns_first_existing(monkeypatch, tmp_path: Path) -> None:
    fake = tmp_path / "fake.sf2"
    fake.write_bytes(b"fake")
    monkeypatch.setattr(audio_render, "_SOUNDFONT_CANDIDATES", ())
    assert find_soundfont((str(fake),)) == str(fake)


def test_find_soundfont_none_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(audio_render, "_SOUNDFONT_CANDIDATES", ())
    assert find_soundfont(()) is None


@pytest.mark.anyio
async def test_render_mp3_skips_without_tools(monkeypatch, tmp_path: Path) -> None:
    midi = tmp_path / "x.mid"
    midi.write_bytes(b"MThd")
    monkeypatch.setattr(audio_render.shutil, "which", lambda name: None)
    assert await render_mp3(str(midi)) is None


@pytest.mark.anyio
async def test_render_mp3_missing_midi(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(audio_render.shutil, "which", lambda name: "x")
    assert await render_mp3(str(tmp_path / "ghost.mid")) is None


@pytest.mark.skipif(
    shutil.which("fluidsynth") is None or find_soundfont() is None,
    reason="本机未安装 fluidsynth 或 GM 音色库",
)
@pytest.mark.anyio
async def test_render_mp3_real_fluidsynth(tmp_path: Path) -> None:
    """真实渲染冒烟：用系统音色库把一小节总谱转成 MP3。"""
    result = render_midi_data("C", "major", 96, ["C", "F"], title="mp3test")
    out_dir = tmp_path / "audio"
    out_dir.mkdir()
    mp3 = await render_mp3(result["path"], str(out_dir / "out.mp3"))
    assert mp3 is not None
    produced = Path(mp3)
    assert produced.suffix in (".mp3", ".wav")
    assert produced.stat().st_size > 1000
    # 中间 WAV 已清理
    assert not (out_dir / "out.wav.tmp").exists()
