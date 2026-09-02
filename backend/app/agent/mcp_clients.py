"""mcp42 乐理校正门面（第 2 阶段"知识匹配"的外脑增强）。

传输层已统一走 ``mcp_registry``（MCP 注册中心），本模块只保留
领域语义：入参组装、结果解析、摘要与调外和弦判定。
服务不可达时静默降级（返回 None），不阻塞创作流水线。
"""

import json
import logging
from typing import Any

from app.agent.mcp_registry import mcp_registry
from app.core.observability import traced_span

logger = logging.getLogger(__name__)


async def analyze_progression(
    chords: list[str], key_hint: str | None = None
) -> dict[str, Any] | None:
    """调用 mcp42 analyze-progression，返回结构化分析；失败返回 None。

    返回示例：{"key": "C major", "romanNumerals": ["I","V","vi","IV"],
              "cadences": [{"type": "deceptive", "index": 2, ...}], "modulations": []}
    """
    if not chords:
        return None
    params: dict[str, Any] = {"chords": chords}
    if key_hint:
        params["keyHint"] = key_hint
    try:
        with traced_span("mcp42.analyze_progression", chords=len(chords)):
            raw = await mcp_registry.call_tool("mcp42", "analyze-progression", params)
        if isinstance(raw, str):
            raw = json.loads(raw)
        return raw if isinstance(raw, dict) else None
    except Exception as exc:
        logger.warning("mcp42 unreachable, theory check skipped: %s", exc)
        return None


def theory_summary(analysis: dict[str, Any] | None) -> str:
    """把 mcp42 的分析浓缩成一句给用户/评审看的说明。"""
    if not analysis:
        return "（乐理校验服务不可用，跳过）"
    key = analysis.get("key", "?")
    romans = "-".join(str(r) for r in (analysis.get("romanNumerals") or []))
    cadences = analysis.get("cadences") or []
    parts = [f"{key}｜{romans}"]
    for c in cadences[:2]:
        ctype = c.get("type", "")
        chords = "/".join(str(x) for x in (c.get("chords") or []))
        if ctype and chords:
            parts.append(f"{chords} 构成{ctype}终止")
    return "；".join(parts)


def find_off_scale_chords(
    analysis: dict[str, Any] | None, chords: list[str]
) -> list[str]:
    """找出分析中被标记为调外（罗马数字含 b/# 前缀）的和弦，供评审参考。"""
    if not analysis:
        return []
    romans = [str(r) for r in (analysis.get("romanNumerals") or [])]
    return [
        chord
        for chord, roman in zip(chords, romans, strict=False)
        if roman.startswith(("b", "#"))
    ]
