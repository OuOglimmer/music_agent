import asyncio
import base64
import json
import logging
import mimetypes
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.agent.agent_core import (
    AgentAuthenticationError,
    AgentNotConfiguredError,
    agent,
)
from app.agent.config import config as agent_config
from app.models import (
    AgentMessage,
    AgentMessagePublic,
    AgentMessagesPublic,
    AgentSession,
    AgentSessionCreate,
    AgentSessionPublic,
    AgentSessionsPublic,
)
from app.rag.retriever import ingest_documents, retrieve_context

logger = logging.getLogger(__name__)

router = APIRouter()

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
TEXT_SUFFIXES = {".txt", ".md"}


def _get_owned_session(
    session: SessionDep, current_user: CurrentUser, session_id: uuid.UUID
) -> AgentSession:
    agent_session = session.exec(
        select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.owner_id == current_user.id,
        )
    ).first()
    if not agent_session:
        raise HTTPException(status_code=404, detail="Session not found")
    return agent_session


@router.get("/sessions", response_model=AgentSessionsPublic)
def list_sessions(
    session: SessionDep, current_user: CurrentUser
) -> AgentSessionsPublic:
    statement = (
        select(AgentSession)
        .where(AgentSession.owner_id == current_user.id)
        .order_by(col(AgentSession.updated_at).desc())
    )
    sessions = session.exec(statement).all()
    count = session.exec(
        select(func.count())
        .select_from(AgentSession)
        .where(AgentSession.owner_id == current_user.id)
    ).one()
    return AgentSessionsPublic(
        data=[AgentSessionPublic.model_validate(item) for item in sessions],
        count=count,
    )


@router.post(
    "/sessions",
    response_model=AgentSessionPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_session(
    session: SessionDep,
    current_user: CurrentUser,
    session_in: AgentSessionCreate,
) -> AgentSession:
    agent_session = AgentSession.model_validate(
        session_in, update={"owner_id": current_user.id}
    )
    session.add(agent_session)
    session.commit()
    session.refresh(agent_session)
    return agent_session


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session: SessionDep, current_user: CurrentUser, session_id: uuid.UUID
) -> None:
    agent_session = _get_owned_session(session, current_user, session_id)
    session.delete(agent_session)
    session.commit()


def _load_history(db: SessionDep, agent_session_id: uuid.UUID) -> list[dict[str, str]]:
    """读取会话内已落库的对话记录，供模型做多轮上下文。"""
    rows = db.exec(
        select(AgentMessage)
        .where(AgentMessage.session_id == agent_session_id)
        .order_by(col(AgentMessage.created_at))
    ).all()
    window = rows[-agent_config.history_window :]
    return [{"role": row.role, "content": row.content} for row in window]


@router.get(
    "/sessions/{session_id}/messages", response_model=AgentMessagesPublic
)
def list_session_messages(
    session: SessionDep, current_user: CurrentUser, session_id: uuid.UUID
) -> AgentMessagesPublic:
    """返回某个会话的完整消息记录，按时间升序。"""
    agent_session = _get_owned_session(session, current_user, session_id)
    statement = (
        select(AgentMessage)
        .where(AgentMessage.session_id == agent_session.id)
        .order_by(col(AgentMessage.created_at))
    )
    messages = session.exec(statement).all()
    return AgentMessagesPublic(
        data=[AgentMessagePublic.model_validate(item) for item in messages],
        count=len(messages),
    )


def _sse_event(event: str, data: dict[str, object]) -> str:
    """格式化一条 SSE 事件帧。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _detect_suffix(upload: UploadFile) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if not suffix:
        suffix = (
            mimetypes.guess_extension(upload.content_type or "") or ""
        ).lower()
    return suffix


@router.post(
    "/chat",
    status_code=status.HTTP_200_OK,
    summary="调用 Agent（SSE 流式）",
)
async def chat(
    session: SessionDep,
    current_user: CurrentUser,
    user_input: str = Form(default="", max_length=10000),
    session_id: uuid.UUID | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
) -> StreamingResponse:
    uploads = files or []
    if not user_input.strip() and not uploads:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="消息不能为空：请输入文字或上传文件",
        )
    if len(uploads) > agent_config.max_upload_files:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"最多上传 {agent_config.max_upload_files} 个文件",
        )

    max_bytes = agent_config.max_upload_size_mb * 1024 * 1024
    image_urls: list[str] = []
    documents: list[tuple[str, str]] = []
    file_names: list[str] = []
    for upload in uploads:
        suffix = _detect_suffix(upload)
        data = await upload.read()
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"文件 {upload.filename} 超过 "
                f"{agent_config.max_upload_size_mb}MB 限制",
            )
        if suffix in IMAGE_SUFFIXES and (
            upload.content_type or ""
        ).startswith("image/"):
            mime = (
                upload.content_type
                or mimetypes.guess_type(upload.filename or "")[0]
                or "image/png"
            )
            image_urls.append(
                f"data:{mime};base64,{base64.b64encode(data).decode()}"
            )
            file_names.append(upload.filename or "图片")
        elif suffix in TEXT_SUFFIXES:
            text = data.decode("utf-8", errors="replace").strip()
            if text:
                documents.append((upload.filename or "document.txt", text))
                file_names.append(upload.filename or "document.txt")
        else:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"不支持的文件类型：{upload.filename}"
                "（支持图片和 txt/md 文档）",
            )

    if session_id:
        agent_session = _get_owned_session(session, current_user, session_id)
    else:
        title_source = (
            user_input.strip()
            or (uploads[0].filename if uploads and uploads[0].filename else "")
            or "新对话"
        )
        agent_session = AgentSession(
            title=title_source[:255],
            owner_id=current_user.id,
        )
        session.add(agent_session)
        session.commit()
        session.refresh(agent_session)
    created_session = session_id is None

    if documents:
        try:
            # 向量入库是同步阻塞调用（含 embedding HTTP），放线程池避免卡死事件循环
            await asyncio.to_thread(
                ingest_documents, str(agent_session.id), documents
            )
        except Exception as exc:
            logger.exception("Document ingestion failed")
            if created_session:
                session.delete(agent_session)
                session.commit()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"文档入库失败：{exc}",
            ) from exc

    context = ""
    if user_input.strip():
        try:
            # 向量检索同样含同步 embedding 调用，放线程池执行
            context = await asyncio.to_thread(
                retrieve_context, str(agent_session.id), user_input
            )
        except Exception:
            # 检索失败不阻塞对话，仅作为无参考资料处理
            logger.warning("Knowledge base retrieval failed", exc_info=True)

    history = _load_history(session, agent_session.id)
    user_content = user_input.strip() or (
        "、".join(file_names) if file_names else "请分析上传的内容"
    )

    def _discard_empty_session() -> None:
        if created_session:
            session.delete(agent_session)
            session.commit()

    async def event_generator() -> AsyncIterator[str]:
        """SSE 事件流：session -> delta* -> done / error。

        客户端断开时本生成器被取消（CancelledError），届时回滚未落库
        的消息并清理新建的空会话；正常结束才把消息成对落库。
        """
        try:
            yield _sse_event("session", {"session_id": str(agent_session.id)})

            full = ""
            async for chunk in agent.process_request_stream(
                user_input,
                images=image_urls or None,
                context=context or None,
                history=history,
            ):
                full += chunk
                yield _sse_event("delta", {"delta": chunk})

            if not full.strip():
                raise RuntimeError("模型返回了空回复，请重试")

            agent_session.updated_at = datetime.now(UTC)
            session.add(
                AgentMessage(
                    session_id=agent_session.id,
                    role="user",
                    content=user_content,
                )
            )
            session.add(
                AgentMessage(
                    session_id=agent_session.id,
                    role="assistant",
                    content=full,
                )
            )
            session.add(agent_session)
            session.commit()
            yield _sse_event("done", {"response": full})
        except asyncio.CancelledError:
            # 客户端主动中断：不落库、清理本次新建的空会话
            _discard_empty_session()
            raise
        except AgentNotConfiguredError as exc:
            logger.warning("Agent not configured: %s", exc)
            _discard_empty_session()
            yield _sse_event("error", {"detail": str(exc)})
        except AgentAuthenticationError as exc:
            logger.warning("Agent authentication failed: %s", exc)
            _discard_empty_session()
            yield _sse_event("error", {"detail": str(exc)})
        except Exception as exc:
            logger.exception("Agent request failed")
            _discard_empty_session()
            yield _sse_event("error", {"detail": f"Agent request failed: {exc}"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
