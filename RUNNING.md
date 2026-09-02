# 音乐 Agent — 项目运行方式

本项目是基于 Full Stack FastAPI Template 二次开发的音乐创作 Agent 平台：
**FastAPI 后端（含多 Agent 创作流水线）+ Next.js 前端（音乐创作工作台）+ PostgreSQL**。

> 仓库根目录提供了 `./start.sh` 一键启动脚本，推荐直接使用；本文档同时给出手动分步方式，
> 便于理解各环节或单独排障。

---

## 1. 项目结构与技术栈

| 部分 | 目录 | 技术 | 默认端口 |
| --- | --- | --- | --- |
| 后端 API | `backend/` | FastAPI + SQLModel + Alembic + LangGraph（uv 管理依赖） | 8000 |
| 前端 | `frontend/` | Next.js 16 (App Router) + React 19 + Tailwind v4 + shadcn/ui（pnpm 管理依赖） | 3000 |
| 数据库 | docker compose `db` 服务 | PostgreSQL 18 | 5432 |
| 邮件测试 | docker compose `mailpit` 服务 | Mailpit（捕获本地开发邮件） | 8025 / 1025 |

开发模式下前端通过 `frontend/next.config.ts` 的 rewrites 把 `/api/v1/*` 同源代理到
`http://localhost:8000`，浏览器只与 3000 端口交互即可。

核心能力入口：

- 创作工作台：`http://localhost:3000/dashboard`（三栏布局，多 Agent 流水线 + 播放器）
- AI 对话：`http://localhost:3000/chat`（流式 SSE，支持语音输入/输出）
- 接口文档：`http://localhost:8000/docs`
- MCP Server：后端启动后在 `http://localhost:8000/mcp` 暴露乐理/创作工具

## 2. 环境要求

| 工具 | 版本要求 | 安装方式 |
| --- | --- | --- |
| uv | 任意较新版本 | `curl -LsSf https://astral.sh/uv/install.sh | sh` |
| Node.js | >= 20 | nvm 或系统包管理器 |
| pnpm | >= 10 | `npm i -g pnpm` 或 `corepack enable` |
| Docker + Compose | Compose v2+ | Docker Desktop / apt 安装 |
| fluidsynth + lame（可选） | — | MP3 音频渲染需要；缺 lame 输出 WAV，缺 fluidsynth 跳过渲染 |

本项目在 WSL2 下开发。`git clone` 后不需要额外配置 Python 环境，`uv sync` 会自动创建
根目录 `.venv`（uv workspace，含 backend 成员）。

## 3. 快速开始（一键脚本，推荐）

```bash
./start.sh              # 全部启动：docker 数据库 → 后端迁移+启动 → 前端启动
./start.sh stop         # 停止本脚本启动的前后端进程（不动 docker 容器）
./start.sh status       # 查看数据库 / 后端 / 前端状态
./start.sh logs         # 跟踪前后端日志（logs/backend.log、logs/frontend.log）
```

脚本行为说明：

- 自动完成：启动并等待 PostgreSQL 健康 → `uv sync` 同步依赖 → `prestart`（等待数据库、
  Alembic 迁移、创建初始超级用户）→ 后台启动 `fastapi dev`（8000）→ 前端依赖检查 →
  后台启动 `pnpm dev`（3000）→ 健康检查后打印访问地址。
- 日志写入 `logs/`，PID 记录在 `.run/`（均已加入 `.gitignore`）。
- 端口被占用时会跳过对应服务并提示，不会误杀你手动启动的进程；`./start.sh stop`
  也只停止由本脚本启动的进程。
- 常用变体：

```bash
./start.sh backend      # 只启动后端（含数据库准备）
./start.sh frontend     # 只启动前端
./start.sh --no-db      # 不启动 docker 数据库（例如用 Windows 宿主机 PostgreSQL）
./start.sh --reinstall  # 前端强制重新 pnpm install
BACKEND_PORT=8001 ./start.sh backend   # 自定义端口
```

启动成功后可访问：

| 地址 | 说明 |
| --- | --- |
| http://localhost:3000/dashboard | 音乐创作工作台 |
| http://localhost:3000/chat | AI 对话 |
| http://localhost:8000/docs | Swagger 接口文档 |
| http://localhost:8025 | Mailpit 本地邮件预览 |

初始超级用户与密码在根目录 `.env` 的 `FIRST_SUPERUSER` / `FIRST_SUPERUSER_PASSWORD`
（默认 `admin@example.com` / `abc123456`，仅本地开发默认值，部署前必须更换）。

## 4. 手动分步启动（等价于脚本做的事）

**第 1 步：启动数据库和邮件服务**（任选其一）

```bash
# 方式 A：Docker Compose（推荐）
docker compose up -d db mailpit

# 方式 B：使用 Windows 宿主机的 PostgreSQL（WSL2 场景）
# 先把 .env 里 DATABASE_URL 指向 Windows 主机地址（见 backend/README.md 的 WSL2 章节），然后：
bash scripts/setup-db-wsl.sh   # 建库（如不存在），可重复执行
```

**第 2 步：后端**（在 `backend/` 目录）

```bash
uv sync                          # 同步依赖（首次或依赖变更后）
uv run bash scripts/prestart.sh  # 等待数据库 + Alembic 迁移 + 创建超级用户
uv run fastapi dev               # 开发服务器，http://localhost:8000
```

**第 3 步：前端**（在 `frontend/` 目录，另开一个终端）

```bash
pnpm install    # 首次或依赖变更后
pnpm dev        # http://localhost:3000，/api/v1 自动代理到 8000
```

生产方式（本地模拟）：后端 `uv run fastapi run`；前端 `pnpm build && pnpm start`。

## 5. Docker Compose 全栈方式（当前不可用，待更新）

`compose.yml` + `docker compose watch` 的全容器模式来自原模板，其中 backend 镜像
（`backend/Dockerfile`）仍按旧的 **bun + Vite 静态导出** 流程构建前端；前端迁移到
Next.js（SSR 服务器）后该 Dockerfile 尚未同步更新，因此目前**无法用 Compose 跑完整栈**。

Compose 里的基础设施服务仍然可用：

```bash
docker compose up -d db mailpit
```

## 6. `.env` 关键配置（仓库根目录）

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | PostgreSQL 连接串，本地进程用 `localhost:5432`（docker db） |
| `FIRST_SUPERUSER` / `FIRST_SUPERUSER_PASSWORD` | 初始管理员账号，prestart 时创建 |
| `SECRET_KEY` | JWT 签名密钥，本地默认 `changethis`，**部署必须更换** |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | LLM 服务（当前指向 DashScope 兼容端点） |
| `AGENT_MODEL_NAME` / `AGENT_MAX_TOKENS` | Agent 对话/创作使用的模型 |
| `SMTP_HOST` / `SMTP_PORT` | 本地指向 Mailpit（`localhost:1025`） |
| `AGENT_SCORES_DIR` | 生成的 MIDI/MP3 输出目录（默认 `backend/generated_scores/`） |

未配置 LLM Key 时流水线自动回退确定性模板，仍可跑通；语音（STT/TTS）未配置则对应接口返回 503。

## 7. 数据库迁移

模型变更后（`backend/app/models.py`），在 `backend/` 目录：

```bash
uv run alembic revision --autogenerate -m "描述变更"
uv run alembic upgrade head
```

## 8. 测试与静态检查

```bash
# 后端测试（pytest + 覆盖率报告 htmlcov/）
cd backend && uv run bash scripts/test.sh

# 前端 lint
cd frontend && pnpm lint

# 全仓库格式/拼写检查（提交前会自动跑）
uv run prek run --all-files
```

## 9. 常见问题（FAQ）

**Q：`next dev` 报 “Another next dev server is already running”？**
Next.js 16 同一项目目录同时只允许一个 dev 实例（跨端口也不行）。先停掉旧实例
（可用 `./start.sh stop`，或按提示 `kill <PID>`）再启动。

**Q：端口 8000 / 3000 被占用？**
`./start.sh` 会检测并跳过，提示端口占用者。排查：`ss -ltnp | grep -E ':8000|:3000'`。
换端口启动：`BACKEND_PORT=8001 FRONTEND_PORT=3001 ./start.sh`（注意前端代理目标
`BACKEND_URL` 也要对应调整）。

**Q：后端连不上数据库？**
确认 docker 容器健康：`docker compose ps db`；确认 `.env` 的 `DATABASE_URL` 与数据库
实际位置一致（docker db 用 `localhost`，Windows 宿主机 PG 用对应地址，见
`backend/README.md` 的 WSL2 章节）。

**Q：忘记密码邮件收不到？**
本地邮件全部进 Mailpit：http://localhost:8025 。Mailpit 未启动时先
`docker compose up -d mailpit`（`./start.sh` 会自动尝试拉起）。

**Q：生成的作品没有 MP3？**
MP3 渲染依赖 `fluidsynth` + `lame`（FluidR3_GM 音色库）。缺 lame 输出 WAV，
缺 fluidsynth 则跳过渲染只有 MIDI。

**Q：前端调不通后端接口？**
开发模式前端把 `/api/v1` 代理到 `BACKEND_URL`（默认 `http://localhost:8000`）。
确认后端已启动、`http://localhost:8000/docs` 可打开。前端 API client 由 OpenAPI
自动生成，后端接口变更后重新生成：后端运行中执行 `cd frontend && pnpm generate-client`。

**Q：`development.md` 里的 `bun run dev` / 5173 端口是什么？**
那是模板旧版（Vite）前端留下的文档，当前前端已是 Next.js + pnpm（3000 端口），
以本文档与 `frontend/README.md` 为准。
