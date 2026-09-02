#!/usr/bin/env bash

set -e
set -x

# 测试跑在独立的 app_test 数据库上：conftest 在会话结束时会清空 User/Item 表，
# 若直接连开发库会连带清掉超级用户与会话数据。数据库不存在时自动创建。
export DATABASE_URL="$(uv run python -c '
from app.core.config import settings
print(str(settings.DATABASE_URL).rsplit("/", 1)[0] + "/app_test")
')"

# 连接维护库 postgres 创建测试库（已存在则跳过；非 PostgreSQL 后端交由应用自行处理）
uv run python - <<'PY'
import os

import sqlalchemy as sa
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import ProgrammingError

url = make_url(os.environ["DATABASE_URL"])
if url.get_backend_name() != "postgresql":
    print(f"backend {url.get_backend_name()!r} 不需要预建数据库")
else:
    admin = url.set(database="postgres")
    engine = sa.create_engine(admin, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{url.database}"'))
        print(f"已创建测试数据库 {url.database}")
    except ProgrammingError:
        print(f"测试数据库 {url.database} 已存在")
PY

# 测试库执行迁移（conftest 的 init_db 只负责创建超级用户，不建表）
uv run alembic upgrade head

FASTAPI_ENV=development coverage run -m pytest tests/
coverage report
coverage html --title "${@-coverage}"
