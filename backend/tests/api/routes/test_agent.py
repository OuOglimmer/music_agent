import asyncio
import base64
import json
from collections.abc import AsyncIterator
from unittest.mock import MagicMock
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.agent import routes as agent_routes
from app.api.deps import get_current_user, get_db
from app.agent.agent_core import AgentAuthenticationError, AgentNotConfiguredError
from app.agent.routes import agent, router
from app.core.config import settings
from app.models import AgentSession, User


def create_client(db: Session) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/agent")
    user = db.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).one()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 响应体解析为 [(event, data), ...]。"""
    events: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        events.append((event_name, json.loads("\n".join(data_lines))))
    return events


def stream_mock(
    chunks: list[str] | None = None,
    error: Exception | None = None,
    captured: dict | None = None,
):
    """构造一个可注入的 process_request_stream 假实现。"""

    async def fake_stream(*args, **kwargs) -> AsyncIterator[str]:
        if captured is not None:
            captured.update(kwargs)
        if error is not None:
            raise error
        for chunk in chunks or ["Agent response"]:
            yield chunk

    return fake_stream


def test_chat_returns_sse_stream(monkeypatch, db: Session) -> None:
    monkeypatch.setattr(
        agent,
        "process_request_stream",
        stream_mock(chunks=["Agent ", "response"]),
    )
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")

    response = create_client(db).post(
        "/api/v1/agent/chat",
        data={"user_input": "hello"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert [name for name, _ in events] == [
        "session",
        "delta",
        "delta",
        "done",
    ]
    UUID(events[0][1]["session_id"])
    assert events[-1][1]["response"] == "Agent response"


def test_chat_stream_emits_error_without_api_key(
    monkeypatch, db: Session
) -> None:
    monkeypatch.setattr(
        agent,
        "process_request_stream",
        stream_mock(
            error=AgentNotConfiguredError("DASHSCOPE_API_KEY is not configured")
        ),
    )
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")

    response = create_client(db).post(
        "/api/v1/agent/chat",
        data={"user_input": "hello"},
    )

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["detail"] == "DASHSCOPE_API_KEY is not configured"


def test_chat_stream_emits_error_with_invalid_api_key(
    monkeypatch, db: Session
) -> None:
    monkeypatch.setattr(
        agent,
        "process_request_stream",
        stream_mock(
            error=AgentAuthenticationError(
                "The configured DASHSCOPE_API_KEY was rejected by the provider"
            )
        ),
    )
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")

    response = create_client(db).post(
        "/api/v1/agent/chat",
        data={"user_input": "hello"},
    )

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["detail"] == (
        "The configured DASHSCOPE_API_KEY was rejected by the provider"
    )


def test_chat_rejects_empty_user_input(db: Session) -> None:
    response = create_client(db).post(
        "/api/v1/agent/chat",
        data={"user_input": ""},
    )

    assert response.status_code == 422


def test_chat_with_image_passes_data_url(monkeypatch, db: Session) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        agent,
        "process_request_stream",
        stream_mock(chunks=["图里是红色"], captured=captured),
    )
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")

    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFBgI/2InF9gAAAABJRU5ErkJggg=="
    )
    response = create_client(db).post(
        "/api/v1/agent/chat",
        data={"user_input": "图片里是什么"},
        files={"files": ("red.png", png_bytes, "image/png")},
    )

    assert response.status_code == 200
    assert captured.get("images") is not None
    assert len(captured["images"]) == 1
    assert captured["images"][0].startswith("data:image/png;base64,")
    events = parse_sse(response.text)
    UUID(events[0][1]["session_id"])


def test_chat_with_document_ingests_and_retrieves(
    monkeypatch, db: Session
) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        agent,
        "process_request_stream",
        stream_mock(chunks=["答案是 42"], captured=captured),
    )
    ingest_mock = MagicMock(return_value=2)
    monkeypatch.setattr(agent_routes, "ingest_documents", ingest_mock)
    monkeypatch.setattr(
        agent_routes, "retrieve_context", lambda *a, **k: "知识片段"
    )

    response = create_client(db).post(
        "/api/v1/agent/chat",
        data={"user_input": "文档里说了什么"},
        files={"files": ("note.txt", b"hello doc", "text/plain")},
    )

    assert response.status_code == 200
    assert captured.get("context") == "知识片段"
    ingest_mock.assert_called_once()
    session_arg = ingest_mock.call_args.args[0]
    documents_arg = ingest_mock.call_args.args[1]
    UUID(session_arg)
    assert documents_arg == [("note.txt", "hello doc")]


def test_chat_rejects_unsupported_file_type(db: Session) -> None:
    response = create_client(db).post(
        "/api/v1/agent/chat",
        data={"user_input": "看看这个"},
        files={"files": ("evil.exe", b"MZ...", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert "不支持的文件类型" in response.json()["detail"]


def test_chat_rejects_oversized_file(monkeypatch, db: Session) -> None:
    from app.agent.config import config as agent_config

    monkeypatch.setattr(agent_config, "max_upload_size_mb", 0)

    response = create_client(db).post(
        "/api/v1/agent/chat",
        data={"user_input": "看看这个"},
        files={"files": ("big.txt", b"123456", "text/plain")},
    )

    assert response.status_code == 413
    assert "超过" in response.json()["detail"]


def test_chat_rejects_too_many_files(monkeypatch, db: Session) -> None:
    from app.agent.config import config as agent_config

    monkeypatch.setattr(agent_config, "max_upload_files", 1)

    response = create_client(db).post(
        "/api/v1/agent/chat",
        data={"user_input": "看看这些"},
        files=[
            ("files", ("a.txt", b"a", "text/plain")),
            ("files", ("b.txt", b"b", "text/plain")),
        ],
    )

    assert response.status_code == 413
    assert "最多上传" in response.json()["detail"]


def test_chat_persists_messages_and_lists_history(
    monkeypatch, db: Session
) -> None:
    monkeypatch.setattr(
        agent,
        "process_request_stream",
        stream_mock(chunks=["第一段回答"]),
    )
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")

    client = create_client(db)
    response = client.post("/api/v1/agent/chat", data={"user_input": "你好"})
    events = parse_sse(response.text)
    session_id = events[0][1]["session_id"]

    messages = client.get(f"/api/v1/agent/sessions/{session_id}/messages")
    assert messages.status_code == 200
    body = messages.json()
    assert body["count"] == 2
    assert [(m["role"], m["content"]) for m in body["data"]] == [
        ("user", "你好"),
        ("assistant", "第一段回答"),
    ]


def test_chat_passes_conversation_history_on_followup(
    monkeypatch, db: Session
) -> None:
    def make_stream(captured: dict):
        return stream_mock(chunks=["回答"], captured=captured)

    first_captured: dict = {}
    second_captured: dict = {}
    monkeypatch.setattr(
        agent, "process_request_stream", make_stream(first_captured)
    )
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")

    client = create_client(db)
    first_resp = client.post(
        "/api/v1/agent/chat", data={"user_input": "你好"}
    )
    first_session_id = parse_sse(first_resp.text)[0][1]["session_id"]

    monkeypatch.setattr(
        agent, "process_request_stream", make_stream(second_captured)
    )
    client.post(
        "/api/v1/agent/chat",
        data={"user_input": "继续说说", "session_id": first_session_id},
    )

    assert second_captured["history"] == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "回答"},
    ]
    messages = client.get(
        f"/api/v1/agent/sessions/{first_session_id}/messages"
    ).json()
    assert messages["count"] == 4


def test_session_messages_require_owner(db: Session) -> None:
    from tests.utils.user import create_random_user

    other_user = create_random_user(db)
    other_session = AgentSession(title="别人的会话", owner_id=other_user.id)
    db.add(other_session)
    db.commit()

    client = create_client(db)
    response = client.get(
        f"/api/v1/agent/sessions/{other_session.id}/messages"
    )
    assert response.status_code == 404


def test_chat_cancel_discards_new_session(monkeypatch, db: Session) -> None:
    """生成被中断（客户端断开会取消生成任务）时：不落库消息、清理新建的空会话。

    注：TestClient 断开流不会真正取消生成器（生产 uvicorn 才会），
    因此这里让流式源在生成中途抛出 CancelledError 来模拟中断。
    """

    async def interrupted_stream(*args, **kwargs) -> AsyncIterator[str]:
        yield "第一段"
        # 模拟客户端断开导致生成任务被取消
        raise asyncio.CancelledError

    monkeypatch.setattr(agent, "process_request_stream", interrupted_stream)
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")

    client = create_client(db)
    before = client.get("/api/v1/agent/sessions").json()["count"]
    with client.stream(
        "POST", "/api/v1/agent/chat", data={"user_input": "你好"}
    ) as response:
        assert response.status_code == 200
        # 消费完流：生成被取消后流即结束
        response.read()

    # 中断后，本次新建的空会话应被清理
    after = client.get("/api/v1/agent/sessions").json()
    assert after["count"] == before
