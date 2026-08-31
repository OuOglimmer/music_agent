from pydantic import BaseModel
from uuid import UUID


class AgentResponse(BaseModel):
    """Agent 响应模型。"""

    response: str
    session_id: UUID
