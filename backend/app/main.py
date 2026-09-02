from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import RedirectResponse

from app.agent.config import config as agent_config
from app.agent.mcp_registry import mcp_registry
from app.agent.routes import router as agent_router  # 导入 agent 路由
from app.agent.routes_voice import router as voice_router  # 语音路由
from app.api.main import api_router
from app.core.config import settings
from app.core.observability import ObservabilityMiddleware, configure_logging

FRONTEND_DIR = Path(__file__).parent / "frontend"


def custom_generate_unique_id(route: APIRoute) -> str:
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}-{route.name}"


if settings.SENTRY_DSN and settings.FASTAPI_ENV != "development":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)

# MCP Server 子应用：把本项目工具以 Streamable HTTP 发布在 /mcp
_mcp_http_app: Any | None = None
if agent_config.enable_mcp_server:
    from app.mcp.server import create_http_app

    _mcp_http_app = create_http_app()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动时做一次 MCP 工具发现（只影响对话工具集，失败不阻塞启动）。"""
    import asyncio

    from app.core.observability import log_event

    if agent_config.enable_mcp_tools:
        try:
            discovered = await asyncio.wait_for(mcp_registry.discover_all(), timeout=30)
            log_event(20, "mcp.discover_all", servers=discovered)
        except Exception as exc:
            log_event(30, "mcp.discover_all_failed", detail=str(exc))
    yield


# 全链路可观测性：JSON 矩阵日志 + trace 贯穿（中间件在 CORS 之后添加，最先执行）
configure_logging()

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
    lifespan=lifespan,
)

# FastAPI 0.141 对 include_router 采用惰性解析，app.routes 里只有 _IncludedRouter
# 包装器、拿不到展开后的真实路径；openapi() 会完整解析，启动时收集一次路径集合，
# 供补尾斜杠中间件判断"带斜杠版本"的路由是否存在。
_api_route_paths: set[str] = set()


def collect_api_route_paths() -> None:
    global _api_route_paths
    try:
        _api_route_paths = set(app.openapi().get("paths", {}).keys())
    except Exception:  # pragma: no cover - schema 构建失败时放弃补斜杠
        _api_route_paths = set()


class APITrailingSlashRedirectMiddleware:
    """补齐 /api/v1 下缺失的尾斜杠（307，保留方法与查询串）。

    部分路由以尾斜杠定义（如 /items/）。MCP 子应用挂载在 "/" 上，
    FastAPI 自带的 redirect_slashes 兜底不会触发；Next.js 开发代理还会剥掉
    尾斜杠，导致这类请求 404。仅当"带斜杠版本"确实命中已注册路由时才重定向，
    不影响无尾斜杠路由（如 /agent/chat）。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            if (
                path.startswith(settings.API_V1_STR)
                and not path.endswith("/")
                and path + "/" in _api_route_paths
            ):
                query = scope.get("query_string", b"")
                location = path + "/" + (f"?{query.decode()}" if query else "")
                await RedirectResponse(location, status_code=307)(scope, receive, send)
                return
        await self.app(scope, receive, send)


# 先注册补斜杠中间件再注册 CORS/可观测性：Starlette 后添加的在外层，
# 这样重定向响应仍会经过可观测性记录与 CORS 头处理
app.add_middleware(APITrailingSlashRedirectMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_HOST],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ObservabilityMiddleware)

# 挂载主路由（包含用户、商品、可观测性等接口）
app.include_router(api_router, prefix=settings.API_V1_STR)

# Agent 主接口：POST /api/v1/agent/chat
app.include_router(agent_router, prefix=f"{settings.API_V1_STR}/agent", tags=["agent"])
# 语音接口：POST /api/v1/agent/voice/transcriptions | /voice/speech
app.include_router(voice_router, prefix=f"{settings.API_V1_STR}/agent", tags=["voice"])
# 保留旧接口：POST /api/v1/chat
app.include_router(agent_router, prefix=settings.API_V1_STR, tags=["agent-legacy"])

# MCP Server：本项目工具的协议化出口（Mount 排在 API 路由之后，不遮蔽既有路径）
if _mcp_http_app is not None:
    app.mount("/", _mcp_http_app)

# 挂载前端静态文件（如需）
app.frontend("/", directory=FRONTEND_DIR)

# 全部路由就绪后收集一次 API 路径集合，供补尾斜杠中间件使用
collect_api_route_paths()
