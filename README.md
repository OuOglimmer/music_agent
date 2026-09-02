# Music Agent · 多 Agent 音乐创作平台

基于 [Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template) 二次开发的音乐创作项目:
前端是一个 Suno 风格的音乐创作工作台,后端提供**对话创作 Agent** 与 **LangGraph 多 Agent 创作流水线**,
支持从自然语言描述到多轨 MIDI / MP3 成品的自动生成,并配套语音交互、音乐知识库检索(RAG)、
MCP 工具生态、反馈评估闭环与全链路可观测性。

## 核心功能

- 🤖 **对话创作 Agent**(`POST /api/v1/agent/chat`):流式 SSE 对话 + LangChain 工具调用,
  内置乐理查询与创作类工具(和弦进行、贝斯线、鼓点、旋律校验、MIDI 渲染)。
- 🎼 **多 Agent 创作流水线**(`POST /api/v1/agent/compose`):LangGraph 按角色编排——
  和声 Agent → 乐理校正(mcp42)→ 旋律 Agent → 贝斯 ∥ 鼓(并行)→ 评审 Agent(不通过返工)→ 渲染,
  产出四轨总谱(MIDI)并自动渲染为 MP3;可上传音频自动检测调性/BPM。
- 🎙️ **语音交互**:浏览器录音 → 语音识别(DashScope ASR),文本 → 语音合成(TTS,带 LRU 缓存)。
- 📚 **音乐知识库 RAG**:向量 + BM25 混合检索、召回率自动调参,音乐乐理语料开箱可用。
- 🔌 **MCP 工具生态**:本项目工具通过 FastMCP 以 Streamable HTTP 发布在 `/mcp`;
  同时支持作为 MCP 客户端接入任意远程 HTTP / 本地 stdio 服务。
- 🧭 **Agent 治理体系**:意图识别、提示词/上下文工程、用户反馈(点赞点踩)、
  人工坐席接管、LLM-as-judge 离线评估,形成"评估 → 改进 → 再评估"闭环。
- 📊 **全链路可观测性**:JSON 矩阵日志 + trace/span 链路追踪 + 延迟指标,
  超级管理员可通过 `/api/v1/observability/*` 查询。
- 🔒 **开箱即用的账号体系**:JWT 认证、密码哈希、邮箱找回密码、管理员面板。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 API | [FastAPI](https://fastapi.tiangolo.com) + [SQLModel](https://sqlmodel.tiangolo.com) + [Pydantic](https://docs.pydantic.dev) |
| 数据库 | [PostgreSQL](https://www.postgresql.org) + [Alembic](https://alembic.sqlalchemy.org) 迁移 |
| Agent 编排 | [LangGraph](https://langchain-ai.github.io/langgraph) / [LangChain](https://python.langchain.com),LLM 走 [DashScope](https://dashscope.aliyuncs.com)(OpenAI 兼容接口) |
| RAG | ChromaDB 向量库 + 自实现 Okapi BM25 混合检索 |
| 前端 | [Next.js 16(App Router)](https://nextjs.org) + [React 19](https://react.dev) + [Tailwind CSS v4](https://tailwindcss.com) + [shadcn/ui](https://ui.shadcn.com) + [TanStack Query/Table](https://tanstack.com) |
| 音频渲染 | fluidsynth + FluidR3_GM 音色库,MIDI → MP3(lame) |
| 邮件 | SMTP + [Mailpit](https://mailpit.axllent.org) 本地测试 |
| 部署/运维 | [Docker Compose](https://docs.docker.com/compose)、[Traefik](https://traefik.io) 自动 HTTPS、Sentry(可选)、GitHub Actions |

## 快速启动(本地开发)

需要:Docker、[uv](https://docs.astral.sh/uv/)、Node.js + [pnpm](https://pnpm.io)。

一键启动(推荐,自动完成数据库、迁移、前后端启动与健康检查):

```bash
./start.sh          # 全部启动;./start.sh stop 停止;./start.sh status 查看状态
```

或手动分步:

```bash
# 1. 启动数据库与邮件服务
docker compose up -d db mailpit

# 2. 后端(backend 目录)
uv sync
uv run bash scripts/prestart.sh   # 建库迁移 + 创建首个超级用户
uv run fastapi dev

# 3. 前端(新终端,frontend 目录)
pnpm install
pnpm dev
```

启动后:

| 地址 | 说明 |
| --- | --- |
| <http://localhost:3000> | 前端音乐工作台(`/api/v1` 自动代理到后端) |
| <http://localhost:8000> | 后端 API |
| <http://localhost:8000/docs> | Swagger API 文档 |
| <http://localhost:8025> | Mailpit(查看开发环境邮件) |

首个超级用户的邮箱与密码来自根目录 `.env` 的 `FIRST_SUPERUSER` / `FIRST_SUPERUSER_PASSWORD`。

> 完整运行方式说明(配置、迁移、测试、FAQ)见 [RUNNING.md](./RUNNING.md);
> Windows + WSL2 直连 PostgreSQL 等开发细节见 [development.md](./development.md)。

## 前端页面

| 路由 | 说明 |
| --- | --- |
| `/dashboard` | 音乐创作工作台(三栏式:项目资源库 / 创作中心 / 波形播放器) |
| `/chat` | 独立 AI 对话页(流式 SSE + 附件) |
| `/items` `/admin` `/settings` | 数据管理 / 用户管理 / 账号设置 |
| `/login` `/signup` `/recover-password` `/reset-password` | 认证相关页面 |

## API 概览

均挂载在 `/api/v1` 下,详见 Swagger 文档:

- `agent/chat` 对话 Agent(SSE)、`agent/compose` 创作流水线(SSE)、`agent/scores/{filename}` 作品下载
- `agent/voice/transcriptions` 语音识别、`agent/voice/speech` 语音合成、`agent/voice/config` 能力协商
- `agent/mcp/servers` MCP 服务状态、`agent/mcp/refresh` 手动发现工具
- `agent/feedback` 用户反馈、`agent/interventions` 人工接管、`agent/evaluations` 离线评估
- `observability/metrics`、`observability/traces` 可观测性查询(仅超级管理员)
- `login`、`users`、`items`、`utils` 账号与基础接口

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [development.md](./development.md) | 本地开发、Docker Compose、`.env` 配置、代码规范 |
| [backend/README.md](./backend/README.md) | 后端架构、Agent 流水线、MCP / RAG / 语音 / 治理 / 可观测性详解、测试与迁移 |
| [frontend/README.md](./frontend/README.md) | 前端页面结构与开发说明 |
| [deployment.md](./deployment.md) | FastAPI Cloud 部署 |
| [deployment-docker-compose.md](./deployment-docker-compose.md) | 自有服务器 Docker Compose 部署 |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 参与贡献 |
| [release-notes.md](./release-notes.md) | 版本更新记录 |

## License

本项目基于 [MIT License](./LICENSE) 开源(继承上游模板)。
