from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
RAG_DIR = Path(__file__).resolve().parent


class RagConfig(BaseSettings):
    """RAG 配置，用于向量知识库的嵌入与检索。"""

    api_key: str = Field(
        default="",
        validation_alias="DASHSCOPE_API_KEY",
        description="DashScope API Key，用于调用向量化接口",
    )

    embedding_model: str = Field(
        default="text-embedding-v4",
        validation_alias="RAG_EMBEDDING_MODEL",
        description="DashScope 兼容模式的向量化模型",
    )

    embedding_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="RAG_EMBEDDING_BASE_URL",
        description="向量化接口的基础 URL",
    )

    chroma_dir: Path = Field(
        default=RAG_DIR / "chroma_db",
        validation_alias="RAG_CHROMA_DIR",
        description="Chroma 持久化目录",
    )

    collection_name: str = Field(
        default="agent_knowledge",
        validation_alias="RAG_COLLECTION_NAME",
        description="Chroma 集合名称",
    )

    chunk_size: int = Field(
        default=500,
        validation_alias="RAG_CHUNK_SIZE",
        ge=100,
        description="文档切块大小（字符）",
    )

    chunk_overlap: int = Field(
        default=50,
        validation_alias="RAG_CHUNK_OVERLAP",
        ge=0,
        description="文档切块重叠（字符）",
    )

    top_k: int = Field(
        default=4,
        validation_alias="RAG_TOP_K",
        ge=1,
        le=20,
        description="检索时返回的相关片段数量",
    )

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


config = RagConfig()
