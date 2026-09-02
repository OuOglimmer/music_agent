from uuid import UUID

from pydantic import BaseModel


class AgentResponse(BaseModel):
    """Agent 响应模型。"""

    response: str
    session_id: UUID
