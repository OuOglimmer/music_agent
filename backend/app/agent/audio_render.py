"""音频渲染：MIDI → MP3（fluidsynth + lame，阶段 4 的"可听化"出口）。

外部命令均可用 ffmpeg 兜底；音色库优先环境变量指定的 sf2，
其次 FluidR3_GM（apt 包 fluid-soundfont-gm 的默认安装位置）。
渲染失败不影响 MIDI 产物，只是没有 mp3_url。
"""

import asyncio
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# 常见 GM 音色库候选路径（按优先级）
_SOUNDFONT_CANDIDATES = (
    "/usr/share/sounds/sf2/FluidR3_GM.sf2",
    "/usr/share/sounds/sf2/default-GM.sf2",
    "/usr/share/soundfonts/FluidR3_GM.sf2",
    "/usr/share/soundfonts/GeneralUser.sf2",
)


def find_soundfont(extra_paths: tuple[str, ...] = ()) -> str | None:
    """返回第一个存在的 GM 音色库路径；都没装则 None。"""
    for path in (*extra_paths, *_SOUNDFONT_CANDIDATES):
        if path and Path(path).is_file():
            return path
    return None


async def render_mp3(midi_path: str, output_path: str | None = None) -> str | None:
    """把 MIDI 渲染为 MP3，成功返回 mp3 路径，失败（缺 fluidsynth/音色库等）返回 None。

    用 fluidsynth 先渲染 WAV（临时文件），再 lame 压缩 MP3；
    fluidsynth 不可用时退回 ffmpeg（自带 fluidsynth 合成器）。
    """
    if not Path(midi_path).is_file():
        logger.warning("MIDI not found for mp3 render: %s", midi_path)
        return None

    out = Path(output_path) if output_path else Path(midi_path).with_suffix(".mp3")
    wav_tmp = out.with_suffix(".wav.tmp")

    soundfont = find_soundfont()
    try:
        if shutil.which("fluidsynth") and soundfont:
            cmd = [
                "fluidsynth",
                "-ni",
                "-g",
                "0.5",
                "-r",
                "44100",
                "-F",
                str(wav_tmp),
                soundfont,
                midi_path,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0 or not wav_tmp.is_file():
                raise RuntimeError(f"fluidsynth failed: {stderr.decode()[:200]}")
            if shutil.which("lame"):
                proc = await asyncio.create_subprocess_exec(
                    "lame",
                    "--quiet",
                    str(wav_tmp),
                    str(out),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(f"lame failed: {stderr.decode()[:200]}")
            else:
                # lame 缺失时直接用 WAV 结果（浏览器也能播）
                shutil.move(str(wav_tmp), str(out.with_suffix(".wav")))
                return str(out.with_suffix(".wav"))
        elif shutil.which("ffmpeg"):
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                midi_path,
                "-b:a",
                "128k",
                str(out),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {stderr.decode()[:200]}")
        else:
            logger.warning("No fluidsynth/ffmpeg available, skip mp3 render")
            return None
        return str(out) if out.is_file() else None
    except Exception:
        logger.warning("MP3 render failed for %s", midi_path, exc_info=True)
        return None
    finally:
        # 清理中间 WAV（渲染成功与失败都只留最终产物）
        wav_tmp.unlink(missing_ok=True)
