"""人工干预岗位的服务层：升级规则、工单创建与坐席指导注入。

升级触发来源（source）：
- auto_intent：意图识别判定敏感内容或置信度过低（chat 流程自动创建）；
- auto_feedback：用户对回复点踩（负反馈自动进入人工复核）；
- manual：用户主动请求转人工。

工单状态机：pending（待处理）→ in_progress（坐席接管，guidance 注入对话）
→ resolved / dismissed（结单，guidance 不再注入）。
"""

import logging

from sqlmodel import Session, col, select

from app.core.observability import log_event, metrics
from app.models import ReviewCase

logger = logging.getLogger(__name__)

OPEN_STATUSES = ("pending", "in_progress")
GUIDANCE_STATUS = "in_progress"


def get_open_case(db: Session, session_id: object) -> ReviewCase | None:
    """取该会话当前未结的人工干预工单（每会话同时最多一张）。"""
    return db.exec(
        select(ReviewCase)
        .where(
            ReviewCase.session_id == session_id,
            col(ReviewCase.status).in_(OPEN_STATUSES),
        )
        .order_by(col(ReviewCase.created_at).desc())
    ).first()


def create_review_case(
    db: Session,
    *,
    session_id: object,
    user_id: object,
    source: str,
    reason: str,
    intent: str | None = None,
    message_id: object | None = None,
) -> ReviewCase | None:
    """创建人工干预工单；该会话已有未结工单时跳过（避免刷屏）。

    返回新建的工单；已存在未结工单时返回 None。
    """
    if get_open_case(db, session_id) is not None:
        return None
    case = ReviewCase(
        session_id=session_id,
        user_id=user_id,
        source=source,
        reason=reason,
        intent=intent,
        message_id=message_id,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    metrics.incr("agent.review.escalations", dim=("agent.review.source", source))
    log_event(30, "agent.review.escalated", session_id=str(session_id), source=source)
    return case


def get_active_guidance(db: Session, session_id: object) -> str:
    """坐席接管中的指导内容：注入该会话后续对话的系统提示。"""
    case = db.exec(
        select(ReviewCase).where(
            ReviewCase.session_id == session_id,
            ReviewCase.status == GUIDANCE_STATUS,
        )
    ).first()
    if case is None or not case.guidance.strip():
        return ""
    return case.guidance.strip()
