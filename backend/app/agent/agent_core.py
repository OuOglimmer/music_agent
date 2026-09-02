import json
from collections.abc import AsyncIterator
from threading import Lock
from typing import Any

import httpx
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from openai import AuthenticationError

from app.agent.config import config
from app.agent.prompt_templates import get_system_prompt
from app.agent.tools import get_effective_tools, run_tool


class AgentNotConfiguredError(RuntimeError):
    """Raised when the agent API key is missing."""


class AgentAuthenticationError(RuntimeError):
    """Raised when the configured agent API key is rejected by the provider."""


class Agent:
    """Agent 核心类，负责 LLM 调用和业务逻辑"""

    def __init__(self) -> None:
        self._llm: ChatOpenAI | None = None
        self._lock = Lock()

    def _get_llm(self) -> ChatOpenAI:
        if self._llm is not None:
            return self._llm

        with self._lock:
            if self._llm is None:
                self._llm = ChatOpenAI(
                    model=config.model_name,
                    openai_api_key=config.api_key,
                    base_url=config.base_url,
                    temperature=config.temperature,
                    max_tokens=config.max_tokens,
                    http_client=httpx.Client(trust_env=False),
                    http_async_client=httpx.AsyncClient(trust_env=False),
                )

        return self._llm

    def get_llm(
        self, temperature: float | None = None, max_tokens: int | None = None
    ) -> Runnable[Any, Any]:
        """获取 LLM 客户端，供意图分类、会话摘要、评估打分等辅助调用复用。

        传入 temperature/max_tokens 时返回参数覆盖后的绑定视图，
        不影响主对话链路使用的默认实例。
        """
        base = self._get_llm()
        if temperature is None and max_tokens is None:
            return base
        kwargs: dict[str, float | int] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return base.bind(**kwargs)

    @staticmethod
    def _parse_tool_args(raw: str) -> dict[str, object]:
        try:
            parsed = json.loads(raw or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _build_messages(
        self,
        user_input: str,
        images: list[str] | None = None,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
    ) -> list[BaseMessage]:
        """组装发给模型的完整消息列表（系统提示 + 历史 + 当前输入）。

        system_prompt 由路由层经 build_system_prompt 组装后传入（含意图
        指南与运营改进项）；不传时退回默认组装，保持独立调用兼容。
        """
        content: list[dict[str, object]] = []
        if context:
            content.append(
                {
                    "type": "text",
                    "text": f"以下是与问题相关的知识库片段，与问题相关时请优先依据它们回答：\n{context}",
                }
            )
        if user_input.strip():
            content.append({"type": "text", "text": user_input})
        for image_url in images or []:
            content.append({"type": "image_url", "image_url": {"url": image_url}})
        if not content:
            content.append({"type": "text", "text": "请分析上传的内容"})

        messages: list[BaseMessage] = [
            SystemMessage(content=system_prompt or get_system_prompt(user_input))
        ]
        for turn in history or []:
            if not turn.get("content"):
                continue
            if turn.get("role") == "assistant":
                messages.append(AIMessage(content=turn["content"]))
            else:
                messages.append(HumanMessage(content=turn["content"]))
        messages.append(HumanMessage(content=content))
        return messages

    async def process_request(
        self,
        user_input: str,
        images: list[str] | None = None,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
    ) -> str:
        """处理用户输入，返回回答。

        images: base64 data URL 列表，交给多模态模型识别。
        context: 知识库检索到的参考资料片段。
        history: 同一会话内先前的对话记录，格式为
            [{"role": "user" | "assistant", "content": "..."}]，
            按时间顺序排列，用于多轮上下文。
        """
        if not config.api_key:
            raise AgentNotConfiguredError("DASHSCOPE_API_KEY is not configured")

        try:
            messages = self._build_messages(
                user_input, images, context, history, system_prompt
            )
            response = await self._get_llm().ainvoke(messages)
            content = response.content
            if isinstance(content, list):
                content = "".join(str(part) for part in content)
            text = str(content).strip()
            if not text:
                finish = response.response_metadata.get("finish_reason", "")
                if finish == "length":
                    raise RuntimeError(
                        "回复被 max_tokens 截断，请调大 AGENT_MAX_TOKENS"
                    )
                raise RuntimeError("模型返回了空回复，请重试")
            return text
        except AuthenticationError as exc:
            raise AgentAuthenticationError(
                "The configured DASHSCOPE_API_KEY was rejected by the provider"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Agent request failed: {exc}") from exc

    async def process_request_stream(
        self,
        user_input: str,
        images: list[str] | None = None,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
    ) -> AsyncIterator[str]:
        """流式处理用户输入，逐块产出回答文本。

        参数含义与 process_request 一致。客户端中断时，调用方取消本
        异步生成器即可（取消会在 await 点传播）。
        """
        if not config.api_key:
            raise AgentNotConfiguredError("DASHSCOPE_API_KEY is not configured")

        tools = get_effective_tools() if config.enable_tools else None
        try:
            llm = self._get_llm().bind_tools(tools) if tools else self._get_llm()
            messages = self._build_messages(
                user_input, images, context, history, system_prompt
            )
            for _ in range(config.max_iterations):
                finish_reason = ""
                text_parts: list[str] = []
                tool_calls: dict[int, dict[str, object]] = {}
                async for chunk in llm.astream(messages):
                    content = chunk.content
                    if isinstance(content, list):
                        content = "".join(str(part) for part in content)
                    text = str(content)
                    if text:
                        text_parts.append(text)
                        yield text
                    for tc in chunk.tool_call_chunks or []:
                        slot = tool_calls.setdefault(
                            tc.get("index") or 0,
                            {"name": "", "args": "", "id": ""},
                        )
                        if tc.get("name"):
                            slot["name"] = tc["name"]
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        slot["args"] += tc.get("args") or ""
                    finish_reason = (
                        chunk.response_metadata.get("finish_reason", "")
                        or finish_reason
                    )

                full_text = "".join(text_parts)
                if not tool_calls:
                    if finish_reason == "length":
                        yield "\n\n（回复已达单次输出上限，可继续提问让它接着写，或调大 AGENT_MAX_TOKENS）"
                    return

                messages.append(
                    AIMessage(
                        content=full_text,
                        tool_calls=[
                            {
                                "name": slot["name"],
                                "args": self._parse_tool_args(str(slot["args"])),
                                "id": slot["id"] or f"call_{index}",
                                "type": "tool_call",
                            }
                            for index, slot in sorted(tool_calls.items())
                        ],
                    )
                )
                for index, slot in sorted(tool_calls.items()):
                    result = await run_tool(
                        str(slot["name"]),
                        self._parse_tool_args(str(slot["args"])),
                    )
                    messages.append(
                        ToolMessage(
                            content=result,
                            tool_call_id=str(slot["id"] or f"call_{index}"),
                        )
                    )
            yield "\n\n（已达到最大工具调用轮数，回答可能不完整，请重试或简化问题）"
        except AuthenticationError as exc:
            raise AgentAuthenticationError(
                "The configured DASHSCOPE_API_KEY was rejected by the provider"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Agent request failed: {exc}") from exc


# 全局单例，便于快速导入使用。
agent = Agent()
