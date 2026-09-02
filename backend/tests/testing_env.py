"""pytest 环境隔离：必须在导入 app.core.db 之前导入本模块。

测试 conftest 在会话结束时会清空 User/Item 表。任何入口直接运行 pytest
（不经 scripts/test.sh）都必须使用独立 *_test 数据库，防止误清开发库。
"""

import os

from app.core.config import settings

_db_name = str(settings.DATABASE_URL).rsplit("/", 1)[-1].split("?")[0]
if not _db_name.endswith("_test"):
    _test_url = str(settings.DATABASE_URL).rsplit("/", 1)[0] + "/app_test"
    os.environ["DATABASE_URL"] = _test_url
    # settings 实例已按开发库 URL 创建（engine 将读取该实例），需同步改写；
    # 若驱动前缀尚未由字段校验器改写，这里一并处理
    settings.DATABASE_URL = _test_url.replace(  # type: ignore[assignment]
        "postgresql://", "postgresql+psycopg://", 1
    )
