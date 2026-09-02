"""Agent 治理域表模型与 DTO：会话记忆 / 用户反馈 / 人工干预 / 改进项 / 评估运行。"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import StringConstraints
from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel

from app.models.base import get_datetime_utc


# 会话记忆：滚动摘要 + 用户偏好，供上下文工程按预算装配
class SessionMemory(SQLModel, table=True):
    __tablename__ = "agent_session_memory"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(
        foreign_key="agent_session.id",
        nullable=False,
        ondelete="CASCADE",
        unique=True,
        index=True,
    )
    summary: str = Field(default="", sa_column=Column(Text, nullable=False))
    preferences: str = Field(default="", sa_column=Column(Text, nullable=False))
    summarized_count: int = Field(default=0)
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )


# 用户对单条助手消息的反馈（点赞/点踩 + 可选评语），是评估闭环的数据源
class AgentFeedback(SQLModel, table=True):
    __tablename__ = "agent_feedback"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    message_id: uuid.UUID = Field(
        foreign_key="agent_message.id", nullable=False, ondelete="CASCADE", index=True
    )
    session_id: uuid.UUID = Field(
        foreign_key="agent_session.id", nullable=False, ondelete="CASCADE", index=True
    )
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    rating: str = Field(max_length=8)  # up / down
    comment: str = Field(default="", sa_column=Column(Text, nullable=False))
    intent: str | None = Field(default=None, max_length=40)
    prompt_version: str | None = Field(default=None, max_length=20)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )


class AgentFeedbackCreate(SQLModel):
    message_id: uuid.UUID
    # SQLModel.Field 不支持 pattern，用 Annotated+StringConstraints 表达正则约束
    rating: Annotated[str, StringConstraints(max_length=8, pattern="^(up|down)$")]
    comment: str = Field(default="", max_length=2000)


class AgentFeedbackPublic(SQLModel):
    id: uuid.UUID
    message_id: uuid.UUID
    rating: str
    comment: str
    created_at: datetime | None = None


# 人工干预工单：低置信/敏感/负反馈/用户主动请求时进入队列，由人工坐席处理
class ReviewCase(SQLModel, table=True):
    __tablename__ = "review_case"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(
        foreign_key="agent_session.id", nullable=False, ondelete="CASCADE", index=True
    )
    message_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_message.id", ondelete="CASCADE"
    )
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    source: str = Field(max_length=20)  # auto_intent / auto_feedback / manual
    reason: str = Field(default="", sa_column=Column(Text, nullable=False))
    status: str = Field(default="pending", max_length=20, index=True)
    intent: str | None = Field(default=None, max_length=40)
    # 坐席接管指令：status 为 in_progress 时注入后续对话的系统提示
    guidance: str = Field(default="", sa_column=Column(Text, nullable=False))
    resolution_note: str = Field(default="", sa_column=Column(Text, nullable=False))
    handled_by_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )


class ReviewCaseHandle(SQLModel):
    action: Annotated[
        str, StringConstraints(pattern="^(in_progress|resolved|dismissed)$")
    ]
    guidance: str | None = Field(default=None, max_length=4000)
    resolution_note: str | None = Field(default=None, max_length=4000)


# 用户主动请求人工介入的入参
class ReviewCaseCreate(SQLModel):
    session_id: uuid.UUID
    note: str = Field(default="", max_length=2000)


class ReviewCasePublic(SQLModel):
    id: uuid.UUID
    session_id: uuid.UUID
    message_id: uuid.UUID | None = None
    user_id: uuid.UUID
    source: str
    reason: str
    status: str
    intent: str | None = None
    guidance: str
    resolution_note: str
    handled_by_id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReviewCasesPublic(SQLModel):
    data: list[ReviewCasePublic]
    count: int


# 人工坐席沉淀的改进项：激活后自动注入系统提示，构成"反馈→提示词"闭环
class ImprovementNote(SQLModel, table=True):
    __tablename__ = "agent_improvement_note"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    content: str = Field(sa_column=Column(Text, nullable=False))
    source: str = Field(default="manual", max_length=20)  # review / eval / manual
    active: bool = Field(default=True, index=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )


class ImprovementNoteCreate(SQLModel):
    content: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="manual", max_length=20)


class ImprovementNotePublic(SQLModel):
    id: uuid.UUID
    content: str
    source: str
    active: bool
    created_at: datetime | None = None


# LLM-as-judge 离线评估的一次运行结果
class EvaluationRun(SQLModel, table=True):
    __tablename__ = "agent_evaluation_run"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    sample_count: int
    average_score: float
    aggregates: str = Field(default="", sa_column=Column(Text, nullable=False))
    results: str = Field(default="", sa_column=Column(Text, nullable=False))
    triggered_by_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )


class EvaluationRunPublic(SQLModel):
    id: uuid.UUID
    sample_count: int
    average_score: float
    aggregates: dict[str, object] = {}
    results: list[dict[str, object]] = []
    created_at: datetime | None = None


class EvaluationRunsPublic(SQLModel):
    data: list[EvaluationRunPublic]
    count: int


class EvaluationRunRequest(SQLModel):
    limit: int = Field(default=10, ge=1, le=50)
