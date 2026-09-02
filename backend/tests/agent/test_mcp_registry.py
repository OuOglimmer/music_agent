"""MCP 注册中心测试：解析、发现、桥接、调用、降级（全部 mock，不触网）。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.config import config as agent_config
from app.agent.mcp_registry import (
    McpRegistry,
    McpServerConfig,
    McpServerNotFoundError,
    McpTool,
    parse_servers,
)


class FakeSession:
    """假 MCP 会话：按 server 名返回预置的 list_tools / call_tool 结果。"""

    def __init__(self, registry: dict[str, Any]):
        self._registry = registry

    async def initialize(self) -> None:
        pass

    async def list_tools(self) -> Any:
        return self._registry["tools"]

    async def call_tool(self, name: str, args: dict) -> Any:
        return self._registry["calls"][name]


def patch_registry_sessions(
    monkeypatch: pytest.MonkeyPatch, registry: McpRegistry, sessions: dict[str, Any]
) -> None:
    """把实例的 _open_session 替换为按 URL 前缀选假会话（打到实例避免方法绑定）。

    新签名收 McpServerConfig 对象，这里按 server.url 前缀匹配；
    键写 "$cmd:<command>" 则匹配 stdio 传输。
    """

    @asynccontextmanager
    async def _cm(server: McpServerConfig):
        key = f"$cmd:{server.command}" if server.transport == "stdio" else server.url
        for prefix, session in sessions.items():
            if key.startswith(prefix):
                yield session
                return
        raise ConnectionError(f"no fake session for {key}")

    monkeypatch.setattr(registry, "_open_session", _cm)


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------


def test_parse_servers_valid_and_invalid() -> None:
    raw = (
        '[{"name": "wiki", "url": "http://x/mcp"},'
        '{"name": "bad name!", "url": "http://y/mcp"},'
        '{"name": "off", "url": "http://z/mcp", "enabled": false}]'
    )
    servers = parse_servers(raw)
    assert [s.name for s in servers] == ["wiki", "off"]
    assert servers[0].timeout == 20  # 默认超时
    assert servers[1].enabled is False


def test_parse_servers_garbage_json() -> None:
    assert parse_servers("") == []
    assert parse_servers("not json") == []
    assert parse_servers('{"name": "wiki"}') == []  # 不是数组


def test_load_from_config_includes_builtin_servers(monkeypatch) -> None:
    monkeypatch.setattr(agent_config, "mcp42_url", "https://mcp42.example/mcp")
    monkeypatch.setattr(
        agent_config, "audio_analyzer_command", "/opt/audio-analyzer"
    )
    monkeypatch.setattr(
        agent_config, "mcp_servers_json", '[{"name": "wiki", "url": "http://w/mcp"}]'
    )
    registry = McpRegistry()
    registry.load_from_config()
    entries = {s["name"]: s for s in registry.status()}
    # 内置 mcp42（HTTP）+ audio-analyzer（stdio）+ 环境变量条目
    assert set(entries) == {"mcp42", "audio-analyzer", "wiki"}
    assert entries["audio-analyzer"]["transport"] == "stdio"
    assert entries["audio-analyzer"]["command"] == "/opt/audio-analyzer"
    assert entries["mcp42"]["transport"] == "http"


def test_load_from_config_skips_analyzer_without_command(monkeypatch) -> None:
    monkeypatch.setattr(agent_config, "mcp42_url", "https://mcp42.example/mcp")
    monkeypatch.setattr(agent_config, "audio_analyzer_command", "")
    monkeypatch.setattr(agent_config, "mcp_servers_json", "")
    registry = McpRegistry()
    registry.load_from_config()
    assert [s["name"] for s in registry.status()] == ["mcp42"]


def test_get_server_lookup() -> None:
    registry = McpRegistry()
    registry.register(McpServerConfig(name="mcp42", url="http://x/mcp"))
    assert registry.get_server("mcp42") is not None
    assert registry.get_server("ghost") is None


# ---------------------------------------------------------------------------
# 工具发现与桥接
# ---------------------------------------------------------------------------


def _fake_tools() -> Any:
    return SimpleNamespace(
        tools=[
            SimpleNamespace(
                name="analyze-progression",
                description="乐理分析",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "chords": {"type": "array", "description": "和弦列表"},
                        "keyHint": {"type": "string"},
                    },
                    "required": ["chords"],
                },
            ),
            SimpleNamespace(name="weird name!", description="", inputSchema={}),
        ]
    )


@pytest.mark.anyio
async def test_discover_bridges_tools_to_langchain(monkeypatch) -> None:
    registry = McpRegistry()
    registry.register(McpServerConfig(name="mcp42", url="http://mcp42/mcp"))
    patch_registry_sessions(
        monkeypatch, registry, {"http://mcp42": FakeSession({"tools": _fake_tools()})}
    )

    count = await registry.discover("mcp42")

    assert count == 2
    tools = registry.get_langchain_tools()
    assert [t.remote_name for t in tools] == ["analyze-progression", "weird name!"]
    # LangChain 工具名必须合法（MCP 工具名里的空格/! 会被清洗）
    assert all(
        t.name and t.name.replace("_", "").replace("-", "").isalnum() for t in tools
    )
    bridge = tools[0]
    assert isinstance(bridge, McpTool)
    assert bridge.server_name == "mcp42"
    schema = bridge.args_schema.model_json_schema()
    assert "chords" in schema["properties"]
    assert set(schema.get("required") or []) == {"chords"}
    assert schema["properties"]["chords"]["description"] == "和弦列表"


@pytest.mark.anyio
async def test_discover_all_isolates_failures(monkeypatch) -> None:
    registry = McpRegistry()
    registry.register(McpServerConfig(name="good", url="http://good/mcp"))
    registry.register(McpServerConfig(name="bad", url="http://bad/mcp"))
    patch_registry_sessions(
        monkeypatch,
        registry,
        {
            "http://good": FakeSession({"tools": _fake_tools()}),
            "http://bad": FakeSession({}),
        },
    )

    # bad 的 list_tools 返回缺 tools 字段的对象 → 异常被隔离
    results = await registry.discover_all()

    assert results["good"] == 2
    assert results["bad"] == 0
    bridged = registry.get_langchain_tools()
    assert len(bridged) == 2
    assert {t.server_name for t in bridged} == {"good"}


@pytest.mark.anyio
async def test_discover_unknown_server_raises() -> None:
    registry = McpRegistry()
    with pytest.raises(McpServerNotFoundError):
        await registry.discover("ghost")


# ---------------------------------------------------------------------------
# 工具调用
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_call_tool_returns_text(monkeypatch) -> None:
    registry = McpRegistry()
    registry.register(McpServerConfig(name="mcp42", url="http://mcp42/mcp"))
    patch_registry_sessions(
        monkeypatch,
        registry,
        {
            "http://mcp42": FakeSession(
                {
                    "tools": SimpleNamespace(tools=[]),
                    "calls": {
                        "analyze-progression": SimpleNamespace(
                            is_error=False,
                            structured_content=None,
                            content=[SimpleNamespace(text="I-V-vi-IV")],
                        )
                    },
                }
            )
        },
    )
    assert (
        await registry.call_tool("mcp42", "analyze-progression", {"chords": ["C"]})
        == "I-V-vi-IV"
    )


@pytest.mark.anyio
async def test_call_tool_prefers_structured_content(monkeypatch) -> None:
    registry = McpRegistry()
    registry.register(McpServerConfig(name="mcp42", url="http://mcp42/mcp"))
    patch_registry_sessions(
        monkeypatch,
        registry,
        {
            "http://mcp42": FakeSession(
                {
                    "tools": SimpleNamespace(tools=[]),
                    "calls": {
                        "analyze-progression": SimpleNamespace(
                            is_error=False,
                            structured_content={"key": "C major"},
                            content=[SimpleNamespace(text="raw")],
                        )
                    },
                }
            )
        },
    )
    result = await registry.call_tool("mcp42", "analyze-progression", {"chords": ["C"]})
    assert result == '{"key": "C major"}'  # call_tool 面向 LLM：dict 序列化为 JSON 文本


@pytest.mark.anyio
async def test_call_tool_error_result_raises(monkeypatch) -> None:
    registry = McpRegistry()
    registry.register(McpServerConfig(name="mcp42", url="http://mcp42/mcp"))
    patch_registry_sessions(
        monkeypatch,
        registry,
        {
            "http://mcp42": FakeSession(
                {
                    "tools": SimpleNamespace(tools=[]),
                    "calls": {
                        "analyze-progression": SimpleNamespace(
                            is_error=True, structured_content=None, content=[]
                        )
                    },
                }
            )
        },
    )
    with pytest.raises(RuntimeError, match="returned error"):
        await registry.call_tool("mcp42", "analyze-progression", {})


@pytest.mark.anyio
async def test_call_tool_unknown_server_raises() -> None:
    registry = McpRegistry()
    with pytest.raises(McpServerNotFoundError):
        await registry.call_tool("ghost", "t", {})


@pytest.mark.anyio
async def test_bridged_tool_round_trip(monkeypatch) -> None:
    """LangChain 桥接工具 _arun → registry.call_tool 全链路。"""
    registry = McpRegistry()
    registry.register(McpServerConfig(name="mcp42", url="http://mcp42/mcp"))
    patch_registry_sessions(
        monkeypatch,
        registry,
        {
            "http://mcp42": FakeSession(
                {
                    "tools": _fake_tools(),
                    "calls": {
                        "analyze-progression": SimpleNamespace(
                            is_error=False,
                            structured_content=None,
                            content=[SimpleNamespace(text="ok: I-V")],
                        )
                    },
                }
            )
        },
    )
    await registry.discover("mcp42")
    bridge = registry.get_langchain_tools()[0]
    text = await bridge.ainvoke({"chords": ["C", "G"]})
    assert text == "ok: I-V"


# ---------------------------------------------------------------------------
# stdio 传输（本地命令型 Server）
# ---------------------------------------------------------------------------


def test_parse_stdio_server_entry() -> None:
    raw = '[{"name": "audio", "transport": "stdio", "command": "/usr/bin/audio-mcp"}]'
    servers = parse_servers(raw)
    assert len(servers) == 1
    assert servers[0].transport == "stdio"
    assert servers[0].command == "/usr/bin/audio-mcp"


def test_stdio_without_command_rejected() -> None:
    servers = parse_servers('[{"name": "bad", "transport": "stdio"}]')
    assert servers == []  # 校验失败被跳过并告警，不让一个坏配置拖垮启动


def test_http_without_url_rejected() -> None:
    servers = parse_servers('[{"name": "bad", "transport": "http"}]')
    assert servers == []


@pytest.mark.anyio
async def test_stdio_server_discover_and_call(monkeypatch) -> None:
    registry = McpRegistry()
    registry.register(
        McpServerConfig(
            name="audio", transport="stdio", command="/opt/audio-analyzer", args=[]
        )
    )
    patch_registry_sessions(
        monkeypatch,
        registry,
        {
            "$cmd:/opt/audio-analyzer": FakeSession(
                {
                    "tools": _fake_tools(),
                    "calls": {
                        "analyze-progression": SimpleNamespace(
                            is_error=False,
                            structured_content=None,
                            content=[SimpleNamespace(text="stdio-ok")],
                        )
                    },
                }
            )
        },
    )
    assert await registry.discover("audio") == 2
    assert await registry.call_tool("audio", "analyze-progression", {}) == "stdio-ok"
