from collections.abc import AsyncIterator
from threading import Lock

import httpx
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_openai import ChatOpenAI
from openai import AuthenticationError

from app.agent.config import config
from app.agent.prompt_templates import get_system_prompt


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

    def _build_messages(
        self,
        user_input: str,
        images: list[str] | None = None,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> list[BaseMessage]:
        """组装发给模型的完整消息列表（系统提示 + 历史 + 当前输入）。"""
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
            SystemMessage(content=get_system_prompt(user_input))
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
            messages = self._build_messages(user_input, images, context, history)
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
    ) -> AsyncIterator[str]:
        """流式处理用户输入，逐块产出回答文本。

        参数含义与 process_request 一致。客户端中断时，调用方取消本
        异步生成器即可（取消会在 await 点传播）。
        """
        if not config.api_key:
            raise AgentNotConfiguredError("DASHSCOPE_API_KEY is not configured")

        try:
            messages = self._build_messages(user_input, images, context, history)
            async for chunk in self._get_llm().astream(messages):
                content = chunk.content
                if isinstance(content, list):
                    content = "".join(str(part) for part in content)
                text = str(content)
                if text:
                    yield text
        except AuthenticationError as exc:
            raise AgentAuthenticationError(
                "The configured DASHSCOPE_API_KEY was rejected by the provider"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Agent request failed: {exc}") from exc


# 全局单例，便于快速导入使用。
agent = Agent()
