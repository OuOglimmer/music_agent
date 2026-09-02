from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录的 .env（backend 的上一级）
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


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
        default=8192,
        ge=1,
        validation_alias="AGENT_MAX_TOKENS",
        description="单次回复的最大 token 数（8192 覆盖长歌词/编曲方案等场景，可通过 .env 调整）",
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

    scores_dir: str = Field(
        default=str(Path(__file__).resolve().parents[2] / "generated_scores"),
        validation_alias="AGENT_SCORES_DIR",
        description="总谱渲染产物（MIDI）的输出目录",
    )

    max_revisions: int = Field(
        default=2,
        ge=0,
        le=5,
        validation_alias="AGENT_MAX_REVISIONS",
        description="创作流水线中评审不通过时允许的最大返工轮数",
    )

    mcp42_url: str = Field(
        default="https://mcp42.jackjakarta.xyz/mcp",
        validation_alias="AGENT_MCP42_URL",
        description="mcp42 乐理校正 MCP 服务的 Streamable HTTP 端点",
    )

    mcp42_timeout: int = Field(
        default=20,
        ge=1,
        le=120,
        validation_alias="AGENT_MCP42_TIMEOUT",
        description="mcp42 乐理校正调用的超时秒数，超时按服务不可用处理",
    )

    audio_analyzer_command: str = Field(
        default="/home/glimmer/tools/mcp-server",
        validation_alias="AGENT_AUDIO_ANALYZER_COMMAND",
        description="audio-analyzer MCP 服务（stdio）的可执行文件路径，留空禁用听觉解析",
    )

    audio_analyzer_timeout: int = Field(
        default=60,
        ge=1,
        le=600,
        validation_alias="AGENT_AUDIO_ANALYZER_TIMEOUT",
        description="音频解析的超时秒数，超时按解析失败处理",
    )

    enable_mp3: bool = Field(
        default=True,
        validation_alias="AGENT_ENABLE_MP3",
        description="渲染总谱后是否自动用 fluidsynth 转 MP3（需要本机安装）",
    )

    enable_mcp_server: bool = Field(
        default=True,
        validation_alias="AGENT_ENABLE_MCP_SERVER",
        description="是否把本项目的工具经 FastMCP 发布为 MCP Server（挂载在 /mcp）",
    )

    enable_mcp_tools: bool = Field(
        default=False,
        validation_alias="AGENT_ENABLE_MCP_TOOLS",
        description="对话 Agent 是否把发现的 MCP 工具并入工具调用循环",
    )

    mcp_servers_json: str = Field(
        default="",
        validation_alias="AGENT_MCP_SERVERS",
        description=(
            "额外 MCP Server 列表（JSON 数组），每项形如 "
            '{"name": "music-wiki", "url": "https://.../mcp", "timeout": 10, "enabled": true}'
        ),
    )

    mcp_discover_timeout: int = Field(
        default=8,
        ge=1,
        le=60,
        validation_alias="AGENT_MCP_DISCOVER_TIMEOUT",
        description="MCP 工具发现（list_tools）的单服务超时秒数",
    )

    # -- 治理：意图识别 / 人工干预 / 上下文工程 ---------------------------------

    enable_llm_intent: bool = Field(
        default=True,
        validation_alias="AGENT_ENABLE_LLM_INTENT",
        description="意图识别是否启用 LLM 精分类（规则置信度不足时触发；关闭则仅用规则）",
    )

    intent_llm_timeout: int = Field(
        default=8,
        ge=1,
        le=60,
        validation_alias="AGENT_INTENT_LLM_TIMEOUT",
        description="LLM 意图分类的超时秒数，超时回退规则结果",
    )

    intent_confidence_threshold: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        validation_alias="AGENT_INTENT_CONFIDENCE_THRESHOLD",
        description="意图置信度阈值：低于该值触发 LLM 精分类与人工升级",
    )

    enable_auto_escalation: bool = Field(
        default=True,
        validation_alias="AGENT_ENABLE_AUTO_ESCALATION",
        description="是否自动升级人工（敏感内容/置信度过低时创建干预工单）",
    )

    context_token_budget: int = Field(
        default=3000,
        ge=200,
        le=32000,
        validation_alias="AGENT_CONTEXT_TOKEN_BUDGET",
        description="上下文工程注入资料的 token 预算（坐席指导+摘要+偏好+RAG）",
    )

    # -- 语音：捕捉处理（STT）与输出（TTS），走 OpenAI 兼容端点 ----------------

    stt_model: str = Field(
        default="qwen3-asr-flash",
        validation_alias="AUDIO_STT_MODEL",
        description="语音转文字模型（OpenAI 兼容 /audio/transcriptions）",
    )

    stt_timeout: int = Field(
        default=60,
        ge=5,
        le=300,
        validation_alias="AUDIO_STT_TIMEOUT",
        description="语音转文字请求超时秒数",
    )

    stt_language: str = Field(
        default="",
        validation_alias="AUDIO_STT_LANGUAGE",
        description="识别语言提示（如 zh/en），留空交给模型自动判断",
    )

    tts_model: str = Field(
        default="qwen-tts-latest",
        validation_alias="AUDIO_TTS_MODEL",
        description="文字转语音模型（OpenAI 兼容 /audio/speech）",
    )

    tts_voice: str = Field(
        default="Cherry",
        validation_alias="AUDIO_TTS_VOICE",
        description="TTS 默认音色（如 Cherry/Serena/Ethan）",
    )

    tts_timeout: int = Field(
        default=60,
        ge=5,
        le=300,
        validation_alias="AUDIO_TTS_TIMEOUT",
        description="文字转语音请求超时秒数",
    )

    tts_max_chars: int = Field(
        default=2000,
        ge=100,
        le=8000,
        validation_alias="AUDIO_TTS_MAX_CHARS",
        description="单次语音合成的文本长度上限（字符）",
    )

    voice_suffixes: str = Field(
        default=".wav,.mp3,.m4a,.webm,.ogg,.flac,.aac",
        validation_alias="AUDIO_VOICE_SUFFIXES",
        description="允许上传的音频扩展名（逗号分隔）",
    )

    @property
    def allowed_voice_suffixes(self) -> set[str]:
        return {s.strip().lower() for s in self.voice_suffixes.split(",") if s.strip()}

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


config = AgentConfig()
