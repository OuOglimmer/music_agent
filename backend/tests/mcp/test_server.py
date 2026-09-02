"""MCP Server 测试：用 fastmcp 内存客户端验证工具发布与真实调用往返。"""

import pytest
from fastmcp import Client

from app.mcp.server import build_server_info, mcp

EXPECTED_TOOLS = {
    "analyze_chord_progression",
    "get_scale_notes",
    "calculate_song_timing",
    "check_lyrics_structure",
    "generate_chord_progression",
    "generate_drum_pattern",
    "render_midi",
    "search_knowledge_base",
}


@pytest.mark.anyio
async def test_server_lists_all_tools() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    descriptions = {t.name: t.description for t in tools}
    assert all(descriptions.values()), "每个工具都要有描述"


@pytest.mark.anyio
async def test_calculate_song_timing_round_trip() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "calculate_song_timing", {"bpm": 120, "bars": 8, "beats_per_bar": 4}
        )
    text = result.content[0].text
    assert "16 秒" in text


@pytest.mark.anyio
async def test_generate_chord_progression_round_trip() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "generate_chord_progression",
            {"key_name": "C", "mode": "major", "mood": "温暖", "bars": 4},
        )
    assert '"chords"' in result.content[0].text


@pytest.mark.anyio
async def test_get_scale_notes_round_trip() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_scale_notes", {"key_name": "C", "scale_type": "major"}
        )
    text = result.content[0].text
    assert "顺阶三和弦" in text


def test_build_server_info_lists_tools() -> None:
    info = build_server_info()
    assert info["path"] == "/mcp"
    assert set(info["tools"]) == EXPECTED_TOOLS
