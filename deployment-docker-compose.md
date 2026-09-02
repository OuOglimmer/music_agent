# 项目部署(Docker Compose)

可以用 Docker Compose 把项目部署到自己的远程服务器。部署配置内置
[Traefik](https://traefik.io) 处理 HTTPS 并把流量路由到应用。

> **注意**:当前后端镜像中「构建前端」一步仍按旧 Vite 前端(bun)编写;前端已改为
> Next.js,需要先调整后端 Dockerfile / 构建流程(或在服务器上单独以 `pnpm build &&
> pnpm start` 运行前端)再使用本指南。

## 准备

* 准备好一台可用的远程服务器。
* 为应用域名及需要暴露的支撑服务子域名配置 DNS 解析,例如
  `fastapi-project.example.com` 与 `adminer.fastapi-project.example.com`。
* 在远程服务器安装并配置 [Docker](https://docs.docker.com/engine/install/)
  (Docker Engine,不是 Docker Desktop)。

## 复制代码

```bash
rsync -av --exclude=".git/" --filter=":- .gitignore" ./ root@your-server.example.com:/root/code/app/
```

`--filter=":- .gitignore"` 让 rsync 复用与 Git 相同的忽略规则,排除 Python 虚拟环境等文件。

## 配置应用

### 环境变量

设置应用域名、项目名称与首个超级用户邮箱:

```bash
export DOMAIN=fastapi-project.example.com
export PROJECT_NAME="Music Agent"
export FIRST_SUPERUSER=admin@example.com
```

也可以按需配置:

* `SMTP_HOST`:邮件服务商的 SMTP 主机。
* `SMTP_USER`:SMTP 用户名。
* `EMAILS_FROM_EMAIL`:发件邮箱。
* `SENTRY_DSN`:Sentry DSN。

### Secrets

生成并设置数据库密码、令牌签名密钥与首个超级用户密码:

```bash
export POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export FIRST_SUPERUSER_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

使用需要鉴权的邮件服务商时,同时设置 `SMTP_PASSWORD`。

## 部署

```bash
cd /root/code/app/
docker compose -f compose.yml -f compose.deploy.yml build
docker compose -f compose.yml -f compose.deploy.yml run --rm backend bash scripts/prestart.sh
docker compose -f compose.yml -f compose.deploy.yml up -d
```

`compose.deploy.yml` 在共享的 `compose.yml` 基础上追加了 HTTPS 与自动证书处理。
显式列出这两个文件可以排除 `compose.override.yml` 中的本地开发配置。

后端 Docker 镜像会构建前端,因此服务器无需安装 bun 或预构建的前端文件
(改用 Next.js 后需按上述提示调整)。

## 用 GitHub Actions 部署

内置的 `.github/workflows/deploy-docker-compose.yml` workflow 会在 Actions 页面手动触发时,
在服务器上执行部署命令。

只有在你信任仓库的贡献者与 workflow 代码时才使用 self-hosted runner。
GitHub 建议私有仓库使用 self-hosted runner,因为 workflow 会直接在 runner 机器上执行。

### 配置仓库变量与 Secrets

在仓库中进入 **Settings** > **Secrets and variables** > **Actions**,添加以下仓库变量:

* `DOMAIN`
* `PROJECT_NAME`
* `FIRST_SUPERUSER`

启用邮件功能时,添加以下可选仓库变量:

* `SMTP_HOST`
* `SMTP_USER`
* `EMAILS_FROM_EMAIL`

启用 Sentry 时,添加可选仓库变量 `SENTRY_DSN`。

添加以下仓库 secrets:

* `POSTGRES_PASSWORD`
* `SECRET_KEY`
* `FIRST_SUPERUSER_PASSWORD`

使用需要鉴权的邮件服务商时,添加可选 secret `SMTP_PASSWORD`。

### 安装 Self-Hosted Runner

在服务器上创建专用用户并授予 Docker 权限:

```bash
sudo adduser github
sudo usermod -aG docker github
sudo su - github
```

在 GitHub 仓库中进入 **Settings** > **Actions** > **Runners**,选择 **New self-hosted
runner**,选 Linux,按 GitHub 给出的命令下载、配置并注册 runner,安装到
`/home/github/actions-runner`。

注册完成后,退出 `github` 用户会话,把 runner 安装为系统服务:

```bash
exit
cd /home/github/actions-runner
sudo ./svc.sh install github
sudo ./svc.sh start
sudo ./svc.sh status
```

参见 GitHub 指南:[添加 self-hosted runner](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)
与[将 runner 配置为服务](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application?platform=linux)。

### 执行部署

runner 上线后,打开仓库 **Actions** 页签,选择 **Deploy with Docker Compose**,
点击 **Run workflow**。

## 地址

把 `fastapi-project.example.com` 替换为你的域名。

应用(前端 + API):`https://fastapi-project.example.com`

交互式 API 文档:`https://fastapi-project.example.com/docs`

Adminer:`https://adminer.fastapi-project.example.com`
