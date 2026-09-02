"""治理闭环 API 测试：反馈 → 升级人工 → 坐席接管 → 指导注入 → 改进项。"""

import json
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.agent import routes as agent_routes
from app.agent.routes import agent, router
from app.api.deps import get_current_active_superuser, get_current_user, get_db
from app.core.config import settings
from app.models import (
    AgentFeedback,
    AgentMessage,
    AgentSession,
    EvaluationRun,
    ImprovementNote,
    ReviewCase,
    SessionMemory,
    User,
)


@pytest.fixture(autouse=True)
def _clean_agent_tables(db: Session):
    """每个用例前清空治理相关表（会话级共享 DB，防止跨用例残留）。"""
    from sqlmodel import delete

    for model in (
        AgentFeedback,
        ReviewCase,
        EvaluationRun,
        ImprovementNote,
        SessionMemory,
        AgentMessage,
        AgentSession,
    ):
        db.exec(delete(model))
    db.commit()
    yield


@pytest.fixture(autouse=True)
def _rules_only_intent(monkeypatch):
    """意图识别仅走规则通道：测试环境不发起 LLM 分类调用。"""
    from app.agent import intent as intent_module

    monkeypatch.setattr(intent_module.config, "enable_llm_intent", False)


def create_client(db: Session, superuser: bool = True) -> TestClient:
    """superuser=True 覆盖为超管用户；False 用随机普通用户（坐席接口应 403）。"""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/agent")
    user = db.exec(select(User).where(User.email == settings.FIRST_SUPERUSER)).one()
    if not superuser:
        from tests.utils.user import create_random_user

        user = create_random_user(db)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    if superuser:
        app.dependency_overrides[get_current_active_superuser] = lambda: user
    return TestClient(app)


def stream_mock(chunks: list[str] | None = None, captured: dict | None = None):
    async def fake_stream(*_args, **_kwargs) -> AsyncIterator[str]:
        if captured is not None:
            captured.update(_kwargs)
        for chunk in chunks or ["Agent response"]:
            yield chunk

    return fake_stream


def chat_once(client: TestClient, text: str, session_id: str | None = None) -> dict:
    """跑一轮对话（流被 mock），返回 session/message_id 等信息。"""
    data = {"user_input": text}
    if session_id:
        data["session_id"] = session_id
    response = client.post("/api/v1/agent/chat", data=data)
    assert response.status_code == 200
    events: dict[str, dict] = {}
    for block in response.text.strip().split("\n\n"):
        lines = block.split("\n")
        event = next(
            (
                line[len("event:") :].strip()
                for line in lines
                if line.startswith("event:")
            ),
            "message",
        )
        data_line = next(
            (line[len("data:") :] for line in lines if line.startswith("data:")), ""
        )
        events[event] = json.loads(data_line) if data_line else {}
    return {
        "session_id": events["session"]["session_id"],
        "meta": events.get("meta", {}),
        "done": events.get("done", {}),
    }


def test_feedback_up_persists_with_denormalized_dims(monkeypatch, db: Session) -> None:
    monkeypatch.setattr(agent, "process_request_stream", stream_mock())
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")
    client = create_client(db)

    result = chat_once(client, "帮我写歌词")
    response = client.post(
        "/api/v1/agent/feedback",
        json={
            "message_id": result["done"]["message_id"],
            "rating": "up",
            "comment": "很好",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["rating"] == "up"
    feedback = db.exec(select(AgentFeedback)).one()
    assert feedback.rating == "up"
    assert feedback.intent == result["meta"]["intent"]["intent"]
    assert feedback.prompt_version == result["meta"]["prompt_version"]


def test_feedback_repeats_upsert_same_message(monkeypatch, db: Session) -> None:
    monkeypatch.setattr(agent, "process_request_stream", stream_mock())
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")
    client = create_client(db)
    result = chat_once(client, "你好")

    client.post(
        "/api/v1/agent/feedback",
        json={"message_id": result["done"]["message_id"], "rating": "up"},
    )
    client.post(
        "/api/v1/agent/feedback",
        json={
            "message_id": result["done"]["message_id"],
            "rating": "down",
            "comment": "不对",
        },
    )
    feedbacks = db.exec(select(AgentFeedback)).all()
    assert len(feedbacks) == 1
    assert feedbacks[0].rating == "down"


def test_feedback_unknown_message_404(db: Session) -> None:
    client = create_client(db)
    response = client.post(
        "/api/v1/agent/feedback",
        json={"message_id": str(UUID(int=1)), "rating": "up"},
    )
    assert response.status_code == 404


def test_down_rating_creates_review_case(monkeypatch, db: Session) -> None:
    monkeypatch.setattr(agent, "process_request_stream", stream_mock())
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")
    client = create_client(db)
    result = chat_once(client, "介绍一下编曲")

    client.post(
        "/api/v1/agent/feedback",
        json={
            "message_id": result["done"]["message_id"],
            "rating": "down",
            "comment": "答非所问",
        },
    )
    cases = db.exec(select(ReviewCase)).all()
    assert len(cases) == 1
    assert cases[0].source == "auto_feedback"
    assert cases[0].status == "pending"


def test_manual_intervention_dedupes_open_case(db: Session) -> None:
    client = create_client(db)

    created = client.post("/api/v1/agent/sessions", json={"title": "求助"})
    session_id = created.json()["id"]

    first = client.post(
        "/api/v1/agent/interventions", json={"session_id": session_id, "note": "转人工"}
    )
    assert first.status_code == 201
    assert first.json()["source"] == "manual"

    second = client.post(
        "/api/v1/agent/interventions",
        json={"session_id": session_id, "note": "再转一次"},
    )
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]  # 未结单去重


def test_review_queue_requires_superuser(db: Session) -> None:
    client = create_client(db, superuser=False)
    response = client.get("/api/v1/agent/interventions")
    assert response.status_code == 403


def test_full_loop_down_feedback_to_guidance_injection(
    monkeypatch, db: Session
) -> None:
    """点踩 → 工单 → 坐席接管（guidance）→ 下一轮对话注入坐席指导。"""
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")
    client = create_client(db, superuser=True)

    captured: dict = {}
    monkeypatch.setattr(agent, "process_request_stream", stream_mock(captured=captured))
    result = chat_once(client, "写一首毕业歌")

    client.post(
        "/api/v1/agent/feedback",
        json={
            "message_id": result["done"]["message_id"],
            "rating": "down",
            "comment": "没有考虑我是零基础",
        },
    )
    queue = client.get("/api/v1/agent/interventions").json()
    assert queue["count"] == 1
    case_id = queue["data"][0]["id"]
    assert queue["data"][0]["status"] == "pending"

    # 工单详情 + 会话消息可读
    detail = client.get(f"/api/v1/agent/interventions/{case_id}").json()
    assert detail["reason"]
    messages = client.get(f"/api/v1/agent/interventions/{case_id}/messages").json()
    assert messages["count"] == 2

    # 接管并给出指导
    handled = client.post(
        f"/api/v1/agent/interventions/{case_id}/handle",
        json={
            "action": "in_progress",
            "guidance": "用户是零基础，后续回答必须先解释术语再给建议",
        },
    )
    assert handled.status_code == 200
    assert handled.json()["status"] == "in_progress"

    # 下一轮对话：坐席指导进入上下文，meta 标记 escalated
    captured.clear()
    result2 = chat_once(client, "继续", session_id=result["session_id"])
    assert result2["meta"]["escalated"] is True
    assert "[人工坐席指导（请遵循）]" in (captured.get("context") or "")
    assert "零基础" in (captured.get("context") or "")

    # 结单后不再注入
    client.post(
        f"/api/v1/agent/interventions/{case_id}/handle",
        json={"action": "resolved", "resolution_note": "已按零基础口径重答"},
    )
    captured.clear()
    monkeypatch.setattr(agent, "process_request_stream", stream_mock(captured=captured))
    client.post(
        "/api/v1/agent/chat",
        data={"user_input": "再看看", "session_id": result["session_id"]},
    )
    assert "[人工坐席指导（请遵循）]" not in (captured.get("context") or "")


def test_improvement_note_injected_into_system_prompt(monkeypatch, db: Session) -> None:
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")
    client = create_client(db, superuser=True)

    created = client.post(
        "/api/v1/agent/improvement-notes",
        json={"content": "提到 AI 音乐工具时必须附上使用步骤", "source": "review"},
    )
    assert created.status_code == 201
    note_id = created.json()["id"]

    captured: dict = {}
    monkeypatch.setattr(agent, "process_request_stream", stream_mock(captured=captured))
    chat_once(client, "推荐个 AI 音乐工具")
    assert "运营改进项" in (captured.get("system_prompt") or "")
    assert "使用步骤" in (captured.get("system_prompt") or "")

    # 停用后不再注入
    client.post(f"/api/v1/agent/improvement-notes/{note_id}/toggle")
    captured.clear()
    chat_once(client, "再推荐一个")
    assert "运营改进项" not in (captured.get("system_prompt") or "")

    client.delete(f"/api/v1/agent/improvement-notes/{note_id}")


def test_governance_summary_aggregates(monkeypatch, db: Session) -> None:
    monkeypatch.setattr(agent, "process_request_stream", stream_mock())
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")
    client = create_client(db, superuser=True)

    result = chat_once(client, "写歌词")
    client.post(
        "/api/v1/agent/feedback",
        json={"message_id": result["done"]["message_id"], "rating": "up"},
    )

    summary = client.get("/api/v1/agent/evaluations/summary").json()
    assert summary["feedback"]["total"] == 1
    assert summary["feedback"]["up"] == 1
    assert summary["feedback"]["satisfaction"] == 1.0
    assert summary["review_queue"]["open_cases"] == 0
    assert summary["improvement_notes"]["prompt_version"]


def test_evaluation_run_without_api_key(monkeypatch, db: Session) -> None:
    from app.agent.config import config as agent_config

    monkeypatch.setattr(agent, "process_request_stream", stream_mock())
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")
    monkeypatch.setattr(agent_config, "api_key", "")  # 评审模型不可用 → 样本记为 failed
    client = create_client(db, superuser=True)

    chat_once(client, "写歌词")

    response = client.post("/api/v1/agent/evaluations/run", json={"limit": 5})
    assert response.status_code == 200
    report = response.json()
    assert report["sample_count"] == 1
    assert report["aggregates"]["failed_count"] == 1

    runs = client.get("/api/v1/agent/evaluations").json()
    assert runs["count"] == 1
