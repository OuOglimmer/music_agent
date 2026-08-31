"""Add agent_message table for persisted chat history

Revision ID: c5d1e8a3f7b2
Revises: 7b2c9f1a4d10
Create Date: 2026-08-31

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c5d1e8a3f7b2"
down_revision = "7b2c9f1a4d10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_message",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["agent_session.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_message_session_id"),
        "agent_message",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_agent_message_session_id"), table_name="agent_message"
    )
    op.drop_table("agent_message")
