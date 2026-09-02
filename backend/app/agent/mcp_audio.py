"""听觉解析（四阶段方法论第 1 阶段）：调用本地 audio-analyzer MCP 服务。

传输统一走 ``mcp_registry``（audio-analyzer 以内置 stdio 条目注册），
本模块只保留领域语义：调用节奏/和声分析工具、把人类可读的分析文本
解析成结构化特征清单。服务不可用时返回 None，不阻塞创作。

解析覆盖（对照 analyzer 实际输出格式）：
- 节奏骨架：BPM/置信度、稳定性、**拍数与逐拍位置**；
- 和声骨架：调性/置信度、**音级分布（pitch class distribution）**；
- 基本信息：时长。
"""

import asyncio
import logging
import re
from typing import Any

from app.agent.config import config as agent_config
from app.agent.mcp_registry import mcp_registry

logger = logging.getLogger(__name__)

_BPM_RE = re.compile(r"Estimated Tempo:\s*([\d.]+)\s*BPM\s*\(confidence:\s*([\d.]+)\)")
_KEY_RE = re.compile(
    r"Estimated Key:\s*([A-G][#b]?)\s*(major|minor)\s*\(confidence:\s*([\d.]+)\)"
)
_STABILITY_RE = re.compile(r"Stability:\s*([\d.]+)")
_DURATION_RE = re.compile(r"Duration:\s*([\d.]+)\s*seconds")
_BEAT_COUNT_RE = re.compile(r"Detected Beats:\s*(\d+)")
# 整行捕获拍位列表（"First 16 beats: 0.01s, 0.49s, ..."），再按逗号清洗
_BEATS_LIST_RE = re.compile(r"First\s+\d+\s+beats?:\s*([^\n]+)")
# 音级行：形如 "   1. C  0.502 ███"（排名. 音名 数值 色块）
_PITCH_ROW_RE = re.compile(r"^\s*\d+\.\s*([A-G][#b]?)\s+([\d.]+)", re.MULTILINE)

AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".aac", ".m4a"}

_BEATS_CAP = 64  # 拍位列表上限，避免长曲目撑爆特征清单
_PITCH_CLASSES_TOP = 7  # 突出音级最多保留数
_PITCH_THRESHOLD = 0.25  # 音级分布归一化值低于此阈值视为背景音

ANALYZER_SERVER = "audio-analyzer"


def analyzer_available() -> bool:
    """audio-analyzer 是否已在注册中心启用（由 AGENT_AUDIO_ANALYZER_COMMAND 决定）。"""
    server = mcp_registry.get_server(ANALYZER_SERVER)
    return bool(server and server.enabled)


def parse_analysis_text(
    rhythm_text: str, harmonic_text: str, info_text: str = ""
) -> dict[str, Any] | None:
    """把 audio-analyzer 的文本输出解析成特征清单；关键信息缺失返回 None。"""
    features: dict[str, Any] = {}
    if bpm := _BPM_RE.search(rhythm_text):
        features["bpm"] = max(40, min(220, round(float(bpm.group(1)))))
        features["bpm_confidence"] = float(bpm.group(2))
    if key := _KEY_RE.search(harmonic_text):
        features["key"] = key.group(1)
        features["mode"] = key.group(2)
        features["key_confidence"] = float(key.group(3))
    if stability := _STABILITY_RE.search(rhythm_text):
        features["tempo_stability"] = float(stability.group(1))
    if duration := _DURATION_RE.search(info_text):
        features["duration_seconds"] = float(duration.group(1))
    # 节奏骨架：拍数 + 逐拍位置（供旋律/鼓节点做拍位对齐）
    if beat_count := _BEAT_COUNT_RE.search(rhythm_text):
        features["beat_count"] = int(beat_count.group(1))
    if beats_list := _BEATS_LIST_RE.search(rhythm_text):
        try:
            beats = [
                round(float(t), 2)
                for t in beats_list.group(1).replace("s", "").split(",")
                if t.strip()
            ]
            features["beats"] = beats[:_BEATS_CAP]
        except ValueError:
            pass
    # 和声骨架：音级分布中突出的音（供和声节点校验和弦假设）
    pitch_rows = _PITCH_ROW_RE.findall(harmonic_text)
    if pitch_rows:
        features["top_pitch_classes"] = [
            name for name, value in pitch_rows if float(value) >= _PITCH_THRESHOLD
        ][:_PITCH_CLASSES_TOP]
    if "bpm" not in features and "key" not in features:
        return None
    return features


def analysis_summary(features: dict[str, Any] | None) -> str:
    """特征清单 → 一句给用户看的解析结论。"""
    if not features:
        return "（听觉解析不可用，按请求参数创作）"
    parts: list[str] = []
    if "bpm" in features:
        parts.append(
            f"检测到 {features['bpm']} BPM（置信 {features.get('bpm_confidence', '?')}）"
        )
    if "beat_count" in features:
        parts.append(f"{features['beat_count']} 拍")
    if "key" in features:
        mode_cn = "大调" if features.get("mode") == "major" else "小调"
        parts.append(
            f"{features['key']} {mode_cn}（置信 {features.get('key_confidence', '?')}）"
        )
    if top := features.get("top_pitch_classes"):
        parts.append(f"突出音级 {'-'.join(top)}")
    if "duration_seconds" in features:
        parts.append(f"时长 {features['duration_seconds']:.1f} 秒")
    return "；".join(parts) or "解析完成"


async def analyze_audio_file(path: str) -> dict[str, Any] | None:
    """对音频文件跑节奏 + 和声分析，返回特征清单；不可用/超时/解析失败返回 None。

    传输经 mcp_registry（audio-analyzer 内置 stdio 条目），
    与 mcp42 等外部服务共享同一套超时/埋点/状态管理。
    """
    if not analyzer_available():
        logger.info("Audio analyzer not registered, skip analysis")
        return None

    async def _run() -> dict[str, Any] | None:
        # 同一会话内连续调用三个工具（stdio 型 Server 一次进程拉起完成全部解析）
        info, rhythm, harmonic = await mcp_registry.call_many(
            ANALYZER_SERVER,
            [
                ("audio_info", {"path": path}),
                ("rhythm_analysis", {"path": path}),
                ("harmonic_analysis", {"path": path}),
            ],
        )
        return parse_analysis_text(
            rhythm if isinstance(rhythm, str) else "",
            harmonic if isinstance(harmonic, str) else "",
            info if isinstance(info, str) else "",
        )

    try:
        return await asyncio.wait_for(
            _run(), timeout=agent_config.audio_analyzer_timeout
        )
    except Exception:
        logger.warning(
            "Audio analysis failed, fallback to request params", exc_info=True
        )
        return None
