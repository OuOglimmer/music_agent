from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class AgentConfig(BaseSettings):
    """
    配置类，用于加载环境变量
    """
    
    api_key: str = Field(
        default="",
        validation_alias="DASHSCOPE_API_KEY",
        description="DASHSCOPE API Key，用于访问 DashScope API",
    )

    model_name: str = Field(
        default="deepseek-v4-pro-0813",
        validation_alias="AGENT_MODEL_NAME",
        description="使用的模型名称",
    )

    base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="AGENT_BASE_URL",
        description="DashScope API 的基础 URL",
    )

    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        validation_alias="AGENT_TEMPERATURE",
        description="生成文本的温度参数",
    )

    max_tokens: int = Field(
        default=4096,
        ge=1,
        validation_alias="AGENT_MAX_TOKENS",
        description="生成文本的最大 token 数量（歌词与编曲建议较长，不宜低于 2048）",
    )

    max_iterations: int = Field(
        default=10,
        ge=1,
        validation_alias="AGENT_MAX_ITERATIONS",
        description="最大交互次数，防止无限循环",
    )

    enable_tools: bool = Field(
        default=False,
        validation_alias="AGENT_ENABLE_TOOLS",
        description="是否启用工具功能",
    )

    max_upload_files: int = Field(
        default=5,
        ge=1,
        le=20,
        validation_alias="AGENT_MAX_UPLOAD_FILES",
        description="单次消息最多上传的文件数量",
    )

    max_upload_size_mb: int = Field(
        default=10,
        ge=1,
        le=100,
        validation_alias="AGENT_MAX_UPLOAD_SIZE_MB",
        description="单个文件的大小上限（MB）",
    )

    history_window: int = Field(
        default=12,
        ge=0,
        validation_alias="AGENT_HISTORY_WINDOW",
        description="多轮对话时携带的最近消息条数（0 表示不携带历史）",
    )

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

config = AgentConfig()
