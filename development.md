# 项目开发指南(Music Agent)

## 本地开发

本地开发时,用 Docker Compose 启动 PostgreSQL 和 Mailpit
(或者使用 [backend/README.md](./backend/README.md#postgresql-on-windows-搭配-wsl2) 中描述的
Windows PostgreSQL + WSL2 直连方案),再在本地运行 FastAPI 与 Next.js 开发服务器。

启动支撑服务:

```bash
docker compose up -d db mailpit
```

然后,在 `backend` 目录安装依赖并准备数据库:

```bash
uv sync
uv run bash scripts/prestart.sh
```

如果使用 Windows 上安装的 PostgreSQL 而非 Docker Compose,则在仓库根目录运行:

```bash
bash scripts/setup-db-wsl.sh
```

启动 FastAPI 开发服务器:

```bash
uv run fastapi dev
```

另开一个终端,在 `frontend` 目录安装依赖并启动 Next.js 开发服务器:

```bash
pnpm install
pnpm dev
```

现在可以访问:

- 前端开发服务器:<http://localhost:3000>
- 后端 API:<http://localhost:8000>
- 自动交互式 API 文档(Swagger UI):<http://localhost:8000/docs>
- Mailpit:<http://localhost:8025>

前端开发服务器通过 `next.config.ts` 中的 rewrites 把 `/api/v1` 请求代理到后端
`http://localhost:8000`(可用环境变量 `BACKEND_URL` 覆盖)。

> **注意**:本文档对应当前的前端(Next.js + pnpm)。旧版文档中的 Vite + bun + 端口 5173
> 说明已过时;`.github/workflows/deploy.yml` 等 CI/CD 流程仍按旧 Vite 前端编写,
> 改用 Next.js 后需要相应调整(见 [deployment.md](./deployment.md) 中的提示)。

## Docker Compose 整栈运行

用 Docker Compose 同时运行后端与构建好的前端:

```bash
docker compose run --rm backend bash scripts/prestart.sh
docker compose watch
```

访问地址:

- 应用(前端 + API,由 FastAPI 托管):<http://localhost:8000>
- 交互式 API 文档:<http://localhost:8000/docs>
- Adminer(数据库管理):<http://localhost:8080>
- Traefik UI(查看路由分发):<http://localhost:8090>
- Mailpit:<http://localhost:8025>

> **注意**:compose 中前端的构建方式仍沿用旧 Vite 方案;前端改为 Next.js 后,
> 该流程需要调整 Dockerfile 与构建产物路径。在此之前,推荐使用上面的本地开发模式,
> 前端单独用 `pnpm dev` / `pnpm build && pnpm start` 运行。

启动整栈前,请先停掉本地运行的 FastAPI 服务器,因为两者都占用 8000 端口。

首次启动整栈时,所有服务就绪可能需要一分钟。可以用 `docker compose logs`
(或 `docker compose logs backend`)观察启动进度。

## Mailpit

[Mailpit](https://mailpit.axllent.org) 会截获本地开发时发送的邮件而不真正投递。
本地后端连接 `localhost:1025`,Compose 内的后端连接 `mailpit` 服务;
截获的邮件在 <http://localhost:8025> 查看。

## Docker Compose 文件与环境变量

- `compose.yml`:整栈共享配置,Docker Compose 自动加载。
- `compose.override.yml`:本地开发配置(端口发布、源码挂载、自动重建、后端热重载),
  Docker Compose 自动加载并叠加在 `compose.yml` 之上。
- `compose.deploy.yml`:部署专用配置(HTTPS 与自动证书),部署时与 `compose.yml`
  显式组合使用。

后端从根目录 `.env` 文件读取本地配置;Docker Compose 也用它做变量插值,
并把需要的变量传给各容器。修改环境变量后,记得重启整栈:

```bash
docker compose watch
```

## `.env` 文件

仓库跟踪的 `.env` 包含本地开发的默认值、密码等配置,主机名使用 `localhost`,
适用于本机进程;Docker Compose 会把数据库、SMTP 等主机名覆盖为 Compose 服务名。

不要把部署密钥放进 `.env`。生产部署请按
[FastAPI Cloud 部署指南](./deployment.md) 或
[Docker Compose 部署指南](./deployment-docker-compose.md) 的方式配置。

与 Agent 相关的常用环境变量(均在 `.env` 或后端配置中设置):

- `DASHSCOPE_API_KEY`:LLM / 语音服务的 API Key(必配,否则 Agent 与语音接口不可用)
- `AGENT_MODEL_NAME`:对话与流水线使用的模型(默认 `deepseek-v4-pro-0813`)
- `AGENT_SCORES_DIR` / `AGENT_MAX_REVISIONS`:MIDI 输出目录 / 评审返工轮数上限
- `AGENT_MCP42_URL` / `AGENT_MCP_SERVERS`:乐理校正服务与 MCP 客户端接入列表

完整清单见 [backend/README.md](./backend/README.md)。

## 前端生产构建

在 `frontend` 目录构建:

```bash
pnpm build
pnpm start   # 生产模式运行,默认 http://localhost:3000
```

如需由 FastAPI 同域托管前端,需要把 Next.js 构建产物同步到 `backend/app/frontend`
并调整后端静态托管逻辑(当前 CI/CD 中"构建前端到 backend/app/frontend"的步骤
是旧 Vite 方案的遗留,需要相应更新)。

## 代码检查与 Git 提交钩子

项目使用 [prek](https://prek.j178.dev/)(pre-commit 的现代替代品)做代码检查与格式化。
配置在仓库根目录的 `.pre-commit-config.yaml`。

### 安装 prek 使其自动运行

prek 已是项目依赖。在项目根目录安装 Git 钩子,让每次提交前自动运行:

```bash
uv run prek install -f
```

`-f` 表示强制安装(覆盖已有的 pre-commit 钩子)。此后每次 `git commit` 时,
prek 会检查并格式化要提交的代码;如果有文件被修改,需要重新 `git add` 后再提交。

### 手动运行 prek

在项目根目录对全部文件手动运行:

```bash
uv run prek run --all-files
```
