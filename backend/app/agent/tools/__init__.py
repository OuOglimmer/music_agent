from langchain_core.tools import BaseTool

from app.agent.config import config as agent_config
from app.agent.tools.composition import TOOLS as COMPOSITION_TOOLS
from app.agent.tools.music_tools import TOOLS as MUSIC_TOOLS
from app.agent.tools.music_tools import run_tool

# 本地工具：乐理查询 + 分角色创作/渲染
TOOLS = MUSIC_TOOLS + COMPOSITION_TOOLS


def get_effective_tools() -> list[BaseTool]:
    """Agent 工具循环的最终工具集 = 本地工具 + 已发现的 MCP 工具（可开关）。"""
    tools: list[BaseTool] = list(TOOLS)
    if agent_config.enable_mcp_tools:
        from app.agent.mcp_registry import mcp_registry

        tools.extend(mcp_registry.get_langchain_tools())
    return tools


__all__ = ["TOOLS", "get_effective_tools", "run_tool"]
