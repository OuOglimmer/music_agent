"""基于 MCP 协议的工具集成框架 —— 服务端。

用 FastMCP 把本项目的核心能力（乐理查询 / 创作流水线 / RAG 检索）
发布为标准 MCP Server（Streamable HTTP），任何 MCP 客户端
（Claude Desktop、其他 Agent、测试工具）都能发现并调用。

设计原则：
- 高内聚：MCP 门面只做"协议适配"，每个工具函数都转发到与
  LangChain 工具相同的底层实现（单一事实来源，两套暴露零漂移）；
- 低耦合：本模块不 import agent 编排层，只依赖 tools/ 与 rag/ 的实现。
"""

from typing import Any

from fastmcp import FastMCP

from app.agent.tools import music_tools
from app.agent.tools.composition import (
    generate_chord_progression_data,
    generate_drum_pattern_data,
    render_midi_data,
)

mcp = FastMCP(
    "music-agent",
    instructions=(
        "音乐创作 Agent 的工具集：乐理分析（和弦/音阶/时长/歌词结构）、"
        "编曲生成（和弦进行/贝斯/鼓组）、总谱渲染（MIDI）与知识库检索。"
    ),
)


# ---------------------------------------------------------------------------
# 乐理查询工具（转发到 music_tools 的同一实现）
# ---------------------------------------------------------------------------


@mcp.tool
def analyze_chord_progression(chords: str) -> str:
    """分析和弦进行的调性与级数（罗马数字）。输入如 "C G Am F"。"""
    return str(music_tools.analyze_chord_progression.invoke({"chords": chords}))


@mcp.tool
def get_scale_notes(key_name: str, scale_type: str = "major") -> str:
    """查询某个调的音阶组成音与顺阶三和弦（scale_type: major/minor）。"""
    return str(
        music_tools.get_scale_notes.invoke(
            {"key_name": key_name, "scale_type": scale_type}
        )
    )


@mcp.tool
def calculate_song_timing(bpm: int, bars: int, beats_per_bar: int = 4) -> str:
    """按 BPM 计算歌曲/段落时长，用于规划曲式结构。"""
    return str(
        music_tools.calculate_song_timing.invoke(
            {"bpm": bpm, "bars": bars, "beats_per_bar": beats_per_bar}
        )
    )


@mcp.tool
def check_lyrics_structure(lyrics: str) -> str:
    """检查歌词的结构完整度：段落标记、行数与每行字数。"""
    return str(music_tools.check_lyrics_structure.invoke({"lyrics": lyrics}))


# ---------------------------------------------------------------------------
# 创作流水线工具（转发到 composition 的确定性实现）
# ---------------------------------------------------------------------------


@mcp.tool
def generate_chord_progression(key_name: str, mode: str, mood: str, bars: int) -> str:
    """按调式/情绪/小节数生成和弦进行（返回 JSON：chords + degrees + rationale）。"""
    data = generate_chord_progression_data(key_name, mode, mood, bars)
    import json

    return json.dumps(data, ensure_ascii=False)


@mcp.tool
def generate_drum_pattern(style: str, bars: int, beats_per_bar: int = 4) -> str:
    """按风格生成鼓组节奏型（style: ballad/pop/rock/edm）。"""
    import json

    return json.dumps(
        generate_drum_pattern_data(style, bars, beats_per_bar), ensure_ascii=False
    )


@mcp.tool
def render_midi(
    key_name: str,
    mode: str,
    bpm: int,
    chords_json: str,
    melody_json: str = "",
    bass_json: str = "",
    drums_json: str = "",
    beats_per_bar: int = 4,
    title: str = "mcp-score",
) -> str:
    """把和弦/旋律/贝斯/鼓组合成为总谱 MIDI 文件，返回结果 JSON（含产物路径）。"""
    import json

    result = render_midi_data(
        key_name,
        mode,
        bpm,
        json.loads(chords_json),
        melody=json.loads(melody_json) if melody_json else None,
        bass=json.loads(bass_json) if bass_json else None,
        drums=json.loads(drums_json) if drums_json else None,
        beats_per_bar=beats_per_bar,
        title=title,
    )
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# RAG 检索工具（知识库是 Agent 的长期记忆，也开放给外部 MCP 客户端）
# ---------------------------------------------------------------------------


@mcp.tool
def search_knowledge_base(query: str, session_id: str = "global") -> str:
    """在向量知识库中检索与 query 相关的资料片段（混合检索：向量 + BM25）。"""
    from app.rag.retriever import retrieve_context

    return retrieve_context(session_id, query)


# ---------------------------------------------------------------------------
# 听觉解析工具（转发本地 audio-analyzer MCP 服务，仅在其已注册时可用）
# ---------------------------------------------------------------------------


@mcp.tool
async def analyze_audio(path: str) -> str:
    """对服务器本地的音频文件做听觉解析，返回特征 JSON。

    节奏骨架（BPM/置信度/稳定性/拍数/拍位）+ 和声骨架（调性/置信度/突出音级）。
    path 是服务器本地文件路径；audio-analyzer 未注册或解析失败时返回 error JSON。
    """
    import json

    from app.agent.mcp_audio import analyze_audio_file

    features = await analyze_audio_file(path)
    if features is None:
        return json.dumps(
            {"error": "audio analyzer unavailable or parse failed"},
            ensure_ascii=False,
        )
    return json.dumps(features, ensure_ascii=False)


def build_server_info() -> dict[str, Any]:
    """返回 MCP Server 元信息（给 /agent/mcp 接口与运维排障用）。"""
    return {
        "name": "music-agent",
        "protocol": "mcp/streamable-http",
        "path": "/mcp",
        "tools": [
            "analyze_chord_progression",
            "get_scale_notes",
            "calculate_song_timing",
            "check_lyrics_structure",
            "generate_chord_progression",
            "generate_drum_pattern",
            "render_midi",
            "search_knowledge_base",
            "analyze_audio",
        ],
    }


def create_http_app() -> Any:
    """返回可挂载到 FastAPI 的 Streamable HTTP ASGI 子应用（/mcp 端点）。"""
    return mcp.http_app(path="/mcp", stateless_http=True)
