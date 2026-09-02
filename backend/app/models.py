import uuid
from datetime import UTC, datetime
from typing import Annotated

from pydantic import EmailStr, StringConstraints
from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    items: list[Item] = Relationship(back_populates="owner", cascade_delete=True)
    agent_sessions: list[AgentSession] = Relationship(
        back_populates="owner", cascade_delete=True
    )


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(SQLModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


class AgentSessionBase(SQLModel):
    title: str = Field(default="New Chat", min_length=1, max_length=255)


class AgentSessionCreate(AgentSessionBase):
    pass


class AgentSession(AgentSessionBase, table=True):
    __tablename__ = "agent_session"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )
    owner: User | None = Relationship(back_populates="agent_sessions")


class AgentSessionPublic(AgentSessionBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AgentSessionsPublic(SQLModel):
    data: list[AgentSessionPublic]
    count: int


class AgentMessage(SQLModel, table=True):
    __tablename__ = "agent_message"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(
        foreign_key="agent_session.id", nullable=False, ondelete="CASCADE", index=True
    )
    role: str = Field(nullable=False)
    content: str = Field(default="", sa_column=Column(Text, nullable=False))
    intent: str | None = Field(default=None, max_length=40, index=True)
    intent_confidence: float | None = None
    prompt_version: str | None = Field(default=None, max_length=20)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )


class AgentMessagePublic(SQLModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    intent: str | None = None
    intent_confidence: float | None = None
    prompt_version: str | None = None
    created_at: datetime | None = None


class AgentMessagesPublic(SQLModel):
    data: list[AgentMessagePublic]
    count: int


# ---------------------------------------------------------------------------
# Agent 治理：会话记忆 / 用户反馈 / 人工干预 / 改进项 / 评估运行
# ---------------------------------------------------------------------------


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


# 多 Agent 创作请求：特征清单（第 1 阶段产物）+ 风格参数
class ComposeRequest(SQLModel):
    key: str = Field(default="C", max_length=4)
    mode: str = Field(default="major", max_length=8)  # major / minor
    bpm: int = Field(default=100, ge=40, le=220)
    mood: str = Field(default="", max_length=200)
    style: str = Field(default="pop", max_length=40)  # ballad/pop/rock/edm/folk
    bars: int = Field(default=8, ge=4, le=64)
    beats_per_bar: int = Field(default=4)
    user_notes: str = Field(default="", max_length=2000)
    session_id: uuid.UUID | None = None


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
