"""基于 MCP 协议的工具集成框架 —— 客户端注册中心。

把多个外部 MCP Server（含本项目自发布的 /mcp、乐理校正 mcp42 等）
统一注册、发现工具、桥接为 LangChain 工具供 Agent 调用。

高内聚低耦合的落点：
- 每个 MCP Server 一份独立配置（名字/端点/超时/开关），互不影响；
- 注册中心是唯一的协议适配点：编排层只见 LangChain 工具，不见 MCP 细节；
- 任一 Server 掉线只影响自己的工具（超时降级 + 缓存），不拖垮全局。

测试注入点：``_open_session`` 是唯一传输入口，测试可整体替换为假会话。
"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from langchain_core.tools import BaseTool
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from app.agent.config import config as agent_config
from app.core.observability import metrics, traced_span

logger = logging.getLogger(__name__)

_TOOL_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


class McpServerConfig(BaseModel):
    """一个外部 MCP Server 的接入配置（远程 HTTP 或本地 stdio 命令）。"""

    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    transport: Literal["http", "stdio"] = "http"
    url: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list)
    timeout: int = Field(default=20, ge=1, le=120)
    enabled: bool = True
    description: str = ""

    @model_validator(mode="after")
    def _check_transport_params(self) -> McpServerConfig:
        if self.transport == "http" and not self.url:
            raise ValueError("http 传输必须提供 url")
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio 传输必须提供 command")
        return self


class McpServerNotFoundError(RuntimeError):
    """请求了未注册（或未启用）的 MCP Server。"""


def parse_servers(raw: str) -> list[McpServerConfig]:
    """解析 AGENT_MCP_SERVERS 环境变量（JSON 数组）；非法项跳过并告警。"""
    if not raw.strip():
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("AGENT_MCP_SERVERS is not valid JSON, ignored")
        return []
    servers: list[McpServerConfig] = []
    if not isinstance(items, list):
        return []
    for item in items:
        try:
            servers.append(McpServerConfig.model_validate(item))
        except Exception as exc:
            logger.warning("Invalid MCP server entry %s: %s", item, exc)
    return servers


def _schema_to_pydantic(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """把 MCP inputSchema（JSON Schema）保守转换为 Pydantic 模型。

    只取 properties 的字段名与 description，类型一律 Any —— 保证
    任意合法 MCP schema 都能桥接，不因类型映射缺口丢工具。
    """
    fields: dict[str, Any] = {}
    properties = schema.get("properties") if isinstance(schema, dict) else None
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    for field_name, spec in (properties or {}).items():
        clean = _TOOL_NAME_RE.sub("_", str(field_name)) or "field"
        description = str(spec.get("description", "")) if isinstance(spec, dict) else ""
        if clean in required:
            fields[clean] = (Any, Field(description=description))
        else:
            fields[clean] = (Any, Field(default=None, description=description))
    if not fields:
        fields["arguments"] = (dict, Field(default_factory=dict))
    return create_model(f"{name}_args", **fields)  # type: ignore[call-overload]


class McpTool(BaseTool):
    """远端 MCP 工具的 LangChain 桥接：Agent 工具循环无感调用 MCP。

    ``registry`` 指向创建本工具的注册中心实例（而非模块级单例），
    多实例互不串扰，测试也不会意外触网。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    server_name: str
    remote_name: str
    args_schema: type[BaseModel]
    registry: Any = Field(default=None, exclude=True, repr=False)
    response_format: str = "content"  # type: ignore[assignment]

    def _run(self, **kwargs: Any) -> str:
        raise NotImplementedError("MCP tools are async-only; use _arun")

    async def _arun(self, **kwargs: Any) -> str:
        registry = (
            self.registry if isinstance(self.registry, McpRegistry) else mcp_registry
        )
        args = {k: v for k, v in kwargs.items() if v is not None}
        return await registry.call_tool(self.server_name, self.remote_name, args)


class McpRegistry:
    """MCP Server 注册中心：注册 → 发现 → 调用 → 桥接。"""

    def __init__(self) -> None:
        self._servers: dict[str, McpServerConfig] = {}
        self._tools: dict[str, list[McpTool]] = {}
        self._lock = asyncio.Lock()

    # -- 注册 ---------------------------------------------------------------

    def register(self, server: McpServerConfig) -> None:
        self._servers[server.name] = server
        self._tools.pop(server.name, None)

    def load_from_config(self) -> None:
        """按配置重建注册表：内置 mcp42 / audio-analyzer + 环境变量里的额外 Server。"""
        self._servers.clear()
        self._tools.clear()
        if agent_config.mcp42_url:
            self.register(
                McpServerConfig(
                    name="mcp42",
                    url=agent_config.mcp42_url,
                    timeout=agent_config.mcp42_timeout,
                    description="乐理校正：和弦进行分析（罗马数字/终止式/调性）",
                )
            )
        if agent_config.audio_analyzer_command:
            self.register(
                McpServerConfig(
                    name="audio-analyzer",
                    transport="stdio",
                    command=agent_config.audio_analyzer_command,
                    timeout=agent_config.audio_analyzer_timeout,
                    description=(
                        "听觉解析：audio_info / rhythm_analysis（BPM/拍位）/ "
                        "harmonic_analysis（调性/音级分布）"
                    ),
                )
            )
        for server in parse_servers(agent_config.mcp_servers_json):
            self.register(server)

    def get_server(self, name: str) -> McpServerConfig | None:
        """按名查询注册条目（含 enabled 状态），供能力探测使用。"""
        return self._servers.get(name)

    # -- 传输（测试注入点）--------------------------------------------------

    @asynccontextmanager
    async def _open_session(
        self, server: McpServerConfig
    ) -> AsyncIterator[ClientSession]:
        """打开一个 MCP 会话（按 transport 分流）。测试 monkeypatch 本方法。

        - http：Streamable HTTP（远程服务，如 mcp42、本项目自发布的 /mcp）；
        - stdio：本地子进程命令（如 audio-analyzer 二进制）。
        """
        if server.transport == "stdio":
            params = StdioServerParameters(command=server.command, args=server.args)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
            return
        async with streamable_http_client(server.url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    # -- 发现 ---------------------------------------------------------------

    async def discover(self, server_name: str) -> int:
        """拉取某 Server 的工具列表并缓存为 LangChain 工具，返回工具数。"""
        server = self._servers.get(server_name)
        if server is None or not server.enabled:
            raise McpServerNotFoundError(
                f"MCP server '{server_name}' is not registered/enabled"
            )
        async with self._lock:
            existing = {t.name for tools in self._tools.values() for t in tools}
            with traced_span("mcp.discover", server=server_name):
                async with asyncio.timeout(server.timeout):
                    async with self._open_session(server) as session:
                        result = await session.list_tools()
                bridged: list[McpTool] = []
                for tool in result.tools:
                    lc_name = self._unique_name(tool.name, existing)
                    existing.add(lc_name)
                    bridged.append(
                        McpTool(
                            name=lc_name,
                            description=(tool.description or tool.name)[:1024],
                            server_name=server_name,
                            remote_name=tool.name,
                            args_schema=_schema_to_pydantic(
                                tool.name, tool.inputSchema
                            ),
                            registry=self,
                        )
                    )
            self._tools[server_name] = bridged
        metrics.incr("mcp.discoveries")
        logger.info("MCP server %s: discovered %s tools", server_name, len(bridged))
        return len(bridged)

    async def discover_all(self) -> dict[str, int]:
        """发现所有启用的 Server（单个失败不影响其他），返回 {server: 工具数}。"""
        results: dict[str, int] = {}
        for name, server in self._servers.items():
            if not server.enabled:
                continue
            try:
                results[name] = await self.discover(name)
            except Exception as exc:
                self._tools.pop(name, None)
                results[name] = 0
                logger.warning("MCP discovery failed for %s: %s", name, exc)
        return results

    def _unique_name(self, tool_name: str, existing: set[str]) -> str:
        clean = _TOOL_NAME_RE.sub("_", tool_name)[:56] or "tool"
        name = clean
        while name in existing:
            name = f"{clean}_{len(existing)}"
        return name

    # -- 调用 ---------------------------------------------------------------

    async def call_tool_result(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        """调用远端工具，返回结构化内容（优先）或文本；失败抛异常由调用方降级。"""
        server = self._servers.get(server_name)
        if server is None or not server.enabled:
            raise McpServerNotFoundError(
                f"MCP server '{server_name}' is not registered/enabled"
            )
        with traced_span("mcp.call", server=server_name, tool=tool_name):
            async with asyncio.timeout(server.timeout):
                async with self._open_session(server) as session:
                    result = await session.call_tool(tool_name, arguments or {})
        metrics.incr("mcp.calls")
        if result.is_error:
            raise RuntimeError(f"MCP tool {server_name}/{tool_name} returned error")
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            return structured
        return "\n".join(
            str(getattr(block, "text", ""))
            for block in (result.content or [])
            if getattr(block, "text", "")
        ).strip()

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        """调用远端工具并返回文本结果（LangChain 工具桥接用）。"""
        result = await self.call_tool_result(server_name, tool_name, arguments)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)

    async def call_many(
        self, server_name: str, calls: list[tuple[str, dict[str, Any]]]
    ) -> list[Any]:
        """同一会话内连续调用多个工具：一次连接开销，多次调用。

        适用 stdio 型 Server（避免每个工具各拉起一个子进程）。
        任一调用报错即抛异常，由调用方降级。
        """
        server = self._servers.get(server_name)
        if server is None or not server.enabled:
            raise McpServerNotFoundError(
                f"MCP server '{server_name}' is not registered/enabled"
            )
        tool_names = [name for name, _args in calls]
        with traced_span("mcp.call_many", server=server_name, tools=tool_names):
            async with asyncio.timeout(server.timeout):
                async with self._open_session(server) as session:
                    results = [
                        await session.call_tool(name, args or {})
                        for name, args in calls
                    ]
        metrics.incr("mcp.calls", len(calls))
        outputs: list[Any] = []
        for result in results:
            if result.is_error:
                raise RuntimeError(f"MCP tool {server_name}/{tool_names} returned error")
            structured = getattr(result, "structured_content", None)
            if isinstance(structured, dict):
                outputs.append(structured)
                continue
            outputs.append(
                "\n".join(
                    str(getattr(block, "text", ""))
                    for block in (result.content or [])
                    if getattr(block, "text", "")
                ).strip()
            )
        return outputs

    # -- 桥接 / 状态 --------------------------------------------------------

    def get_langchain_tools(self) -> list[McpTool]:
        """返回已发现工具的 LangChain 桥接（供 Agent 工具循环合并）。"""
        return [tool for tools in self._tools.values() for tool in tools]

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "name": server.name,
                "transport": server.transport,
                "url": server.url,
                "command": server.command,
                "timeout": server.timeout,
                "enabled": server.enabled,
                "description": server.description,
                "discovered_tools": [
                    t.remote_name for t in self._tools.get(server.name, [])
                ],
            }
            for server in self._servers.values()
        ]


mcp_registry = McpRegistry()
mcp_registry.load_from_config()
