"""Agent 会话与消息表模型、创作请求 DTO。"""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, Relationship, SQLModel

from app.models.base import get_datetime_utc
from app.models.user import User


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
