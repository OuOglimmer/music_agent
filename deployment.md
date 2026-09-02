# 项目部署(FastAPI Cloud)

通过仓库内置的 GitHub Actions workflow,将项目部署到 [FastAPI Cloud](https://fastapicloud.com)。

> **注意**:当前 workflow 中「构建前端」一步仍按旧 Vite 前端(bun)编写;前端已改为
> Next.js,需要在 workflow 中改为 `pnpm install && pnpm build`,并按 Next.js 的托管方式
> 调整(或由 FastAPI 同域托管构建产物)后再启用自动部署。

## 创建 FastAPI Cloud 应用

在 FastAPI Cloud 上创建应用,并将 [Application Directory](https://fastapicloud.com/docs/builds-and-deployments/application-directory/) 设为 `backend`。

通过 [Neon](https://fastapicloud.com/docs/integrations/neon-integration/) 或
[Supabase](https://fastapicloud.com/docs/integrations/supabase-integration/) 集成连接
PostgreSQL 数据库——两种集成都自动配置 `DATABASE_URL` secret;也可以手动为其他
PostgreSQL 服务商配置 `DATABASE_URL`。

## 配置应用

### 环境变量

在 FastAPI Cloud 应用上添加以下必需的[环境变量](https://fastapicloud.com/docs/builds-and-deployments/environment-variables/):

* `PROJECT_NAME`:项目名称,用于 API 文档与邮件。
* `FIRST_SUPERUSER`:首个超级用户的邮箱。
* `FRONTEND_HOST`:应用公网地址,例如生成的 `https://your-app.fastapicloud.dev` 或自定义域名。

启用邮件功能时,按邮件服务商提供的值添加以下可选环境变量:

* `SMTP_HOST`
* `SMTP_USER`
* `EMAILS_FROM_EMAIL`

启用 Sentry 时,配置 `SENTRY_DSN`。

### Secrets

添加以下必需值并标记为 secret:

* `SECRET_KEY`:用于签名安全令牌的密钥。
* `FIRST_SUPERUSER_PASSWORD`:首个超级用户的密码。
* `DATABASE_URL`:PostgreSQL 连接 URL(使用数据库集成时自动配置)。

使用需要鉴权的邮件服务商时,添加 `SMTP_PASSWORD` secret。

用以下命令生成 `SECRET_KEY` 与 `FIRST_SUPERUSER_PASSWORD` 的安全随机值:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 配置持续部署

仓库内置的 `.github/workflows/deploy.yml` 会在代码推送到 `master` 时构建前端、
准备数据库并部署应用;也可以在 **Actions** 页签手动触发。

登录 FastAPI Cloud,把 [deploy token](https://fastapicloud.com/docs/advanced-features/deploy-tokens/)
与应用 ID 配置为 GitHub 仓库 secrets:

```bash
uv run fastapi login
uv run fastapi cloud setup-ci --secrets-only --app-id <your-app-id>
```

如果已安装并登录 GitHub CLI,该命令会自动配置 `FASTAPI_CLOUD_TOKEN` 与
`FASTAPI_CLOUD_APP_ID`;否则会打印值,需要手动到仓库
**Settings** > **Secrets and variables** > **Actions** 添加。

workflow 会在部署前执行数据库迁移并创建首个超级用户。在仓库
**Settings** > **Secrets and variables** > **Actions** 页面添加以下仓库变量:

* `PROJECT_NAME`
* `FIRST_SUPERUSER`

添加以下仓库 secrets:

* `DATABASE_URL`
* `SECRET_KEY`
* `FIRST_SUPERUSER_PASSWORD`

取值与 FastAPI Cloud 上配置的一致。`DATABASE_URL` 使用数据库服务商提供的连接 URL;
数据库必须能被 GitHub 托管的 runner 访问,准备步骤才能连上。

部署 workflow 执行以下步骤:

1. 安装并构建前端到 `backend/app/frontend`(旧 Vite 方案,需按上述提示调整)。
2. 运行 `backend/scripts/prestart.sh` 执行数据库迁移并创建首个超级用户。
3. 用 `uv run fastapi deploy` 部署。

## 地址

把 `your-app.fastapicloud.dev` 替换为你的 FastAPI Cloud 应用地址。

应用(前端 + API):`https://your-app.fastapicloud.dev`

交互式 API 文档:`https://your-app.fastapicloud.dev/docs`

## Docker Compose

部署到自有服务器请参阅 [Docker Compose 部署指南](./deployment-docker-compose.md)。

## GitHub 仓库自动化

安装以下 GitHub App 以启用内置的仓库自动化:

* [Latest Changes](https://github.com/apps/latest-changes):PR 合并时更新 `release-notes.md`。
* [PR Push](https://github.com/apps/pr-push):允许 pre-commit workflow 向 PR 分支推送自动修复。
* [PR Submit](https://github.com/apps/pr-submit):允许「Bump pre-commit hooks」「Prepare Release」workflow 创建 PR。

要使用 [Smokeshow](https://github.com/samuelcolvin/smokeshow) 发布代码覆盖率,
添加 `SMOKESHOW_AUTH_KEY` 仓库 secret。
