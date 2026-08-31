from threading import Lock

import httpx
from langchain_openai import OpenAIEmbeddings

from app.rag.config import config


class RagNotConfiguredError(RuntimeError):
    """Raised when the RAG API key is missing."""


_embeddings: OpenAIEmbeddings | None = None
_lock = Lock()


def get_embeddings() -> OpenAIEmbeddings:
    """返回全局单例的向量化模型客户端。"""
    global _embeddings
    if _embeddings is not None:
        return _embeddings

    with _lock:
        if _embeddings is None:
            if not config.api_key:
                raise RagNotConfiguredError(
                    "DASHSCOPE_API_KEY is not configured"
                )
            _embeddings = OpenAIEmbeddings(
                model=config.embedding_model,
                api_key=config.api_key,
                base_url=config.embedding_base_url,
                check_embedding_ctx_length=False,
                # 直连 DashScope，避免环境中的代理变量干扰
                http_client=httpx.Client(trust_env=False),
                http_async_client=httpx.AsyncClient(trust_env=False),
            )
    return _embeddings
