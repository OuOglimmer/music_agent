from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def test_health_check(client: TestClient) -> None:
    response = client.get(f"{settings.API_V1_STR}/utils/health-check/")
    assert response.status_code == 200
    assert response.json() is True


def test_trailing_slash_redirect(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """无尾斜杠请求带尾斜杠路由时应 307 补齐（Next.js 代理会剥掉尾斜杠）。"""
    no_follow = TestClient(app, follow_redirects=False)
    response = no_follow.get(
        f"{settings.API_V1_STR}/utils/health-check",
        headers=superuser_token_headers,
    )
    assert response.status_code == 307
    assert response.headers["location"].endswith("/utils/health-check/")

    followed = client.get(f"{settings.API_V1_STR}/utils/health-check")
    assert followed.status_code == 200
    assert followed.json() is True


def test_no_slash_route_not_redirected() -> None:
    """本身不带尾斜杠的路由不受补斜杠中间件影响。"""
    no_follow = TestClient(app, follow_redirects=False)
    response = no_follow.post(
        f"{settings.API_V1_STR}/login/access-token",
        data={"username": "", "password": ""},
    )
    assert response.status_code != 307
