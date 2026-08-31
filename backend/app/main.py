from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from app.api.main import api_router
from app.agent.routes import router as agent_router   # 导入 agent 路由
from app.core.config import settings

FRONTEND_DIR = Path(__file__).parent / "frontend"


def custom_generate_unique_id(route: APIRoute) -> str:
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}-{route.name}"


if settings.SENTRY_DSN and settings.FASTAPI_ENV != "development":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_HOST],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载主路由（包含用户、商品等原有接口）
app.include_router(api_router, prefix=settings.API_V1_STR)

# Agent 主接口：POST /api/v1/agent/chat
app.include_router(agent_router, prefix=f"{settings.API_V1_STR}/agent", tags=["agent"])
# 保留旧接口：POST /api/v1/chat
app.include_router(agent_router, prefix=settings.API_V1_STR, tags=["agent-legacy"])

# 挂载前端静态文件（如需）
app.frontend("/", directory=FRONTEND_DIR)
