"""Agent 治理接口：用户反馈、人工干预工单、改进项与评估闭环。

路由分组：
- **用户侧**（登录即可）：
  - POST /feedback          对单条助手消息点赞/点踩（点踩自动升级人工）；
  - POST /interventions     主动请求转人工。
- **人工坐席侧**（superuser）：
  - GET  /interventions               干预工单队列；
  - GET  /interventions/{id}          工单详情；
  - GET  /interventions/{id}/messages 工单对应会话的完整消息记录；
  - POST /interventions/{id}/handle   接管（guidance 注入后续对话）/结单；
  - POST /improvement-notes           沉淀改进项（激活后自动注入系统提示）；
  - POST /evaluations/run             触发一次 LLM-as-judge 评估；
  - GET  /evaluations                 历史评估运行；
  - GET  /evaluations/summary         反馈×意图×提示词版本聚合报表。

本 router 被 agent 路由 include，实际前缀为 /api/v1/agent（另有
/api/v1 旧前缀的等价镜像，与既有 agent 端点保持一致）。
"""

import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import col, select

from app.agent import evaluation, review
from app.agent.prompt_templates import PROMPT_VERSION
from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.core.observability import metrics
from app.models import (
    AgentFeedback,
    AgentFeedbackCreate,
    AgentFeedbackPublic,
    AgentMessage,
    AgentMessagePublic,
    AgentMessagesPublic,
    AgentSession,
    EvaluationRun,
    EvaluationRunPublic,
    EvaluationRunRequest,
    EvaluationRunsPublic,
    ImprovementNote,
    ImprovementNoteCreate,
    ImprovementNotePublic,
    ReviewCase,
    ReviewCaseCreate,
    ReviewCaseHandle,
    ReviewCasePublic,
    ReviewCasesPublic,
    User,
)

router = APIRouter()

ReviewerDep = Annotated[User, Depends(get_current_active_superuser)]

_MAX_FEEDBACK_SCAN = 5000


def _get_owned_message(
    session: SessionDep, current_user: CurrentUser, message_id: UUID
) -> AgentMessage:
    """校验消息存在且属于当前用户的会话（防越权反馈）。"""
    message = session.get(AgentMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    agent_session = session.get(AgentSession, message.session_id)
    if agent_session is None or agent_session.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


# ---------------------------------------------------------------------------
# 用户侧：反馈与转人工
# ---------------------------------------------------------------------------


@router.post(
    "/feedback",
    response_model=AgentFeedbackPublic,
    status_code=status.HTTP_201_CREATED,
    summary="对单条助手消息提交反馈（点赞/点踩）",
)
def submit_feedback(
    session: SessionDep,
    current_user: CurrentUser,
    feedback_in: AgentFeedbackCreate,
) -> AgentFeedback:
    """反馈写入 agent_feedback 表，并按消息的意图/提示词版本降维聚合。

    点踩会自动创建人工干预工单（source=auto_feedback），进入坐席队列。
    """
    message = _get_owned_message(session, current_user, feedback_in.message_id)

    existing = session.exec(
        select(AgentFeedback).where(
            AgentFeedback.message_id == message.id,
            AgentFeedback.user_id == current_user.id,
        )
    ).first()
    if existing is not None:
        existing.rating = feedback_in.rating
        existing.comment = feedback_in.comment
        existing.intent = message.intent
        existing.prompt_version = message.prompt_version
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    feedback = AgentFeedback(
        message_id=message.id,
        session_id=message.session_id,
        user_id=current_user.id,
        rating=feedback_in.rating,
        comment=feedback_in.comment,
        intent=message.intent,
        prompt_version=message.prompt_version,
    )
    session.add(feedback)
    session.commit()
    session.refresh(feedback)
    metrics.incr("agent.feedback", dim=("agent.feedback.rating", feedback.rating))

    if feedback.rating == "down":
        review.create_review_case(
            session,
            session_id=message.session_id,
            user_id=current_user.id,
            source="auto_feedback",
            reason=f"用户对回复点踩：{feedback.comment or '（无评语）'}",
            intent=message.intent,
            message_id=message.id,
        )
    return feedback


@router.post(
    "/interventions",
    response_model=ReviewCasePublic,
    status_code=status.HTTP_201_CREATED,
    summary="主动请求人工介入",
)
def request_intervention(
    session: SessionDep,
    current_user: CurrentUser,
    request_in: ReviewCaseCreate,
) -> ReviewCase:
    agent_session = session.get(AgentSession, request_in.session_id)
    if agent_session is None or agent_session.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    case = review.create_review_case(
        session,
        session_id=agent_session.id,
        user_id=current_user.id,
        source="manual",
        reason=request_in.note.strip() or "用户主动请求转人工",
        intent=None,
    )
    if case is None:
        existing = review.get_open_case(session, agent_session.id)
        if existing is None:  # 理论上不可达，防御一下
            raise HTTPException(status_code=500, detail="Failed to create case")
        return existing
    return case


# ---------------------------------------------------------------------------
# 人工坐席侧：干预工单
# ---------------------------------------------------------------------------


@router.get(
    "/interventions",
    response_model=ReviewCasesPublic,
    summary="人工干预工单队列（坐席）",
)
def list_interventions(
    session: SessionDep,
    _reviewer: ReviewerDep,
    status_filter: str | None = None,
    limit: int = 50,
) -> ReviewCasesPublic:
    statement = (
        select(ReviewCase)
        .order_by(col(ReviewCase.created_at).desc())
        .limit(min(max(limit, 1), 200))
    )
    if status_filter:
        statement = statement.where(ReviewCase.status == status_filter)
    cases = session.exec(statement).all()
    return ReviewCasesPublic(
        data=[ReviewCasePublic.model_validate(case) for case in cases],
        count=len(cases),
    )


def _get_case(session: SessionDep, case_id: UUID) -> ReviewCase:
    case = session.get(ReviewCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Review case not found")
    return case


@router.get(
    "/interventions/{case_id}",
    response_model=ReviewCasePublic,
    summary="工单详情（坐席）",
)
def get_intervention(
    session: SessionDep, _reviewer: ReviewerDep, case_id: UUID
) -> ReviewCase:
    return _get_case(session, case_id)


@router.get(
    "/interventions/{case_id}/messages",
    response_model=AgentMessagesPublic,
    summary="工单对应会话的完整消息（坐席）",
)
def get_intervention_messages(
    session: SessionDep, _reviewer: ReviewerDep, case_id: UUID
) -> AgentMessagesPublic:
    case = _get_case(session, case_id)
    messages = session.exec(
        select(AgentMessage)
        .where(AgentMessage.session_id == case.session_id)
        .order_by(col(AgentMessage.created_at))
    ).all()
    return AgentMessagesPublic(
        data=[AgentMessagePublic.model_validate(m) for m in messages],
        count=len(messages),
    )


@router.post(
    "/interventions/{case_id}/handle",
    response_model=ReviewCasePublic,
    summary="处理工单：接管/结单（坐席）",
)
def handle_intervention(
    session: SessionDep,
    current_user: CurrentUser,
    case_id: UUID,
    handle_in: ReviewCaseHandle,
    _reviewer: ReviewerDep,
) -> ReviewCase:
    """action=in_progress 时坐席接管，guidance（接管指令）会注入该会话
    后续每次对话的系统提示；resolved/dismissed 结单后不再注入。"""
    case = _get_case(session, case_id)
    case.status = handle_in.action
    if handle_in.guidance is not None:
        case.guidance = handle_in.guidance
    if handle_in.resolution_note is not None:
        case.resolution_note = handle_in.resolution_note
    case.handled_by_id = current_user.id
    case.updated_at = datetime.now(UTC)
    session.add(case)
    session.commit()
    session.refresh(case)
    metrics.incr("agent.review.handled", dim=("agent.review.status", case.status))
    return case


# ---------------------------------------------------------------------------
# 人工坐席侧：改进项（反馈闭环的"提示词回流"环节）
# ---------------------------------------------------------------------------


@router.post(
    "/improvement-notes",
    response_model=ImprovementNotePublic,
    status_code=status.HTTP_201_CREATED,
    summary="新增改进项（坐席）",
)
def create_improvement_note(
    session: SessionDep,
    _reviewer: ReviewerDep,
    note_in: ImprovementNoteCreate,
) -> ImprovementNote:
    note = ImprovementNote(
        content=note_in.content.strip(),
        source=note_in.source,
        active=True,
    )
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


@router.get(
    "/improvement-notes",
    response_model=list[ImprovementNotePublic],
    summary="改进项列表（坐席）",
)
def list_improvement_notes(
    session: SessionDep, _reviewer: ReviewerDep
) -> list[ImprovementNote]:
    return list(
        session.exec(
            select(ImprovementNote).order_by(col(ImprovementNote.created_at).desc())
        ).all()
    )


@router.post(
    "/improvement-notes/{note_id}/toggle",
    response_model=ImprovementNotePublic,
    summary="启用/停用改进项（坐席）",
)
def toggle_improvement_note(
    session: SessionDep, _reviewer: ReviewerDep, note_id: UUID
) -> ImprovementNote:
    note = session.get(ImprovementNote, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Improvement note not found")
    note.active = not note.active
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


@router.delete(
    "/improvement-notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除改进项（坐席）",
)
def delete_improvement_note(
    session: SessionDep, _reviewer: ReviewerDep, note_id: UUID
) -> None:
    note = session.get(ImprovementNote, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Improvement note not found")
    session.delete(note)
    session.commit()


# ---------------------------------------------------------------------------
# 人工坐席侧：评估闭环
# ---------------------------------------------------------------------------


@router.post(
    "/evaluations/run",
    summary="触发一次 LLM-as-judge 评估（坐席）",
)
async def run_evaluation_endpoint(
    session: SessionDep,
    current_user: CurrentUser,
    request_in: EvaluationRunRequest,
    _reviewer: ReviewerDep,
) -> dict[str, Any]:
    """抽样最近的助手回复，由评审模型按维度打分并落库。"""
    report = await evaluation.run_evaluation(
        session, request_in.limit, triggered_by_id=current_user.id
    )
    metrics.incr("agent.evaluation.api_runs")
    return report


@router.get(
    "/evaluations",
    response_model=EvaluationRunsPublic,
    summary="历史评估运行（坐席）",
)
def list_evaluations(
    session: SessionDep, _reviewer: ReviewerDep, limit: int = 20
) -> EvaluationRunsPublic:
    runs = session.exec(
        select(EvaluationRun)
        .order_by(col(EvaluationRun.created_at).desc())
        .limit(min(max(limit, 1), 100))
    ).all()

    def _public(run: EvaluationRun) -> EvaluationRunPublic:
        return EvaluationRunPublic(
            id=run.id,
            sample_count=run.sample_count,
            average_score=run.average_score,
            aggregates=json.loads(run.aggregates or "{}"),
            results=json.loads(run.results or "[]"),
            created_at=run.created_at,
        )

    return EvaluationRunsPublic(data=[_public(run) for run in runs], count=len(runs))


@router.get(
    "/evaluations/summary",
    summary="治理总览报表（坐席）：反馈×意图×提示词版本",
)
def governance_summary(session: SessionDep, _reviewer: ReviewerDep) -> dict[str, Any]:
    """聚合用户反馈与干预工单，回答"哪个意图/哪个提示词版本体验最差"。

    该报表是闭环的起点：定位薄弱意图 → 坐席沉淀改进项/接管指导 →
    注入提示词 → 再次评估对比。
    """
    feedback_rows = session.exec(
        select(AgentFeedback)
        .order_by(col(AgentFeedback.created_at).desc())
        .limit(_MAX_FEEDBACK_SCAN)
    ).all()

    by_intent: dict[str, dict[str, int]] = {}
    by_version: dict[str, dict[str, int]] = {}
    up = down = 0
    for row in feedback_rows:
        if row.rating == "up":
            up += 1
        elif row.rating == "down":
            down += 1
        for bucket, key in ((by_intent, row.intent), (by_version, row.prompt_version)):
            name = key or "unknown"
            cell = bucket.setdefault(name, {"up": 0, "down": 0, "total": 0})
            cell[row.rating] = cell.get(row.rating, 0) + 1
            cell["total"] += 1

    open_cases = session.exec(
        select(ReviewCase).where(col(ReviewCase.status).in_(review.OPEN_STATUSES))
    ).all()
    active_notes = session.exec(
        select(ImprovementNote).where(ImprovementNote.active)
    ).all()
    latest_run = session.exec(
        select(EvaluationRun).order_by(col(EvaluationRun.created_at).desc())
    ).first()

    return {
        "feedback": {
            "total": up + down,
            "up": up,
            "down": down,
            "satisfaction": round(up / (up + down), 4) if up + down else None,
            "by_intent": by_intent,
            "by_prompt_version": by_version,
        },
        "review_queue": {
            "open_cases": len(open_cases),
            "pending": sum(1 for c in open_cases if c.status == "pending"),
            "in_progress": sum(1 for c in open_cases if c.status == "in_progress"),
        },
        "improvement_notes": {
            "active": len(active_notes),
            "prompt_version": PROMPT_VERSION,
        },
        "latest_evaluation": (
            {
                "id": str(latest_run.id),
                "average_score": latest_run.average_score,
                "sample_count": latest_run.sample_count,
                "created_at": (latest_run.created_at or datetime.now(UTC)).isoformat(),
            }
            if latest_run
            else None
        ),
    }
