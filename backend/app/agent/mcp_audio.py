"""听觉解析（四阶段方法论第 1 阶段）：调用本地 audio-analyzer MCP 服务。

通过 stdio 连接 Rust 版 audio-analyzer（mcp-server 二进制），对上传音频跑
rhythm_analysis（节奏骨架：BPM/稳定性）与 harmonic_analysis（和声骨架：调性/音级），
把人类可读的分析文本解析成结构化特征清单。服务不可用时返回 None，不阻塞创作。
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.agent.config import config as agent_config

logger = logging.getLogger(__name__)

_BPM_RE = re.compile(r"Estimated Tempo:\s*([\d.]+)\s*BPM\s*\(confidence:\s*([\d.]+)\)")
_KEY_RE = re.compile(
    r"Estimated Key:\s*([A-G][#b]?)\s*(major|minor)\s*\(confidence:\s*([\d.]+)\)"
)
_STABILITY_RE = re.compile(r"Stability:\s*([\d.]+)")
_DURATION_RE = re.compile(r"Duration:\s*([\d.]+)\s*seconds")

AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".aac", ".m4a"}


def analyzer_available() -> bool:
    """audio-analyzer MCP 二进制是否已配置且存在。"""
    return (
        bool(agent_config.audio_analyzer_command)
        and Path(agent_config.audio_analyzer_command).is_file()
    )


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
    if "key" in features:
        mode_cn = "大调" if features.get("mode") == "major" else "小调"
        parts.append(
            f"{features['key']} {mode_cn}（置信 {features.get('key_confidence', '?')}）"
        )
    if "duration_seconds" in features:
        parts.append(f"时长 {features['duration_seconds']:.1f} 秒")
    return "；".join(parts) or "解析完成"


def _tool_text(result: Any) -> str:
    """从 MCP ToolResult 提取首个文本块（兼容图片/资源等其他内容类型）。"""
    first = result.content[0] if result.content else None
    return getattr(first, "text", "") or ""


async def analyze_audio_file(path: str) -> dict[str, Any] | None:
    """对音频文件跑节奏 + 和声分析，返回特征清单；不可用/超时/解析失败返回 None。"""
    if not analyzer_available():
        logger.info("Audio analyzer not configured, skip analysis")
        return None

    async def _run() -> dict[str, Any] | None:
        params = StdioServerParameters(
            command=agent_config.audio_analyzer_command, args=[]
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                info = await session.call_tool("audio_info", {"path": path})
                rhythm = await session.call_tool("rhythm_analysis", {"path": path})
                harmonic = await session.call_tool("harmonic_analysis", {"path": path})
        return parse_analysis_text(
            _tool_text(rhythm), _tool_text(harmonic), _tool_text(info)
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
