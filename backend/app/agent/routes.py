import asyncio
import base64
import json
import logging
import mimetypes
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError
from sqlmodel import col, func, select

from app.agent import mcp_audio
from app.agent import memory as session_memory
from app.agent import review as review_service
from app.agent.agent_core import (
    AgentAuthenticationError,
    AgentNotConfiguredError,
    agent,
)
from app.agent.composer import STAGE_LABELS, build_compose_graph, stage_detail
from app.agent.config import config as agent_config
from app.agent.governance import router as governance_router
from app.agent.intent import (
    IntentResult,
    classify_intent,
    escalation_reason,
    needs_escalation,
)
from app.agent.prompt_templates import PROMPT_VERSION, build_system_prompt
from app.api.deps import CurrentUser, SessionDep
from app.models import (
    AgentMessage,
    AgentMessagePublic,
    AgentMessagesPublic,
    AgentSession,
    AgentSessionCreate,
    AgentSessionPublic,
    AgentSessionsPublic,
    ComposeRequest,
    ImprovementNote,
)
from app.rag.retriever import ingest_documents, retrieve_context

logger = logging.getLogger(__name__)

router = APIRouter()
router.include_router(governance_router)

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


@router.get("/sessions/{session_id}/messages", response_model=AgentMessagesPublic)
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
        suffix = (mimetypes.guess_extension(upload.content_type or "") or "").lower()
    return suffix


@router.post(
    "/chat",
    status_code=status.HTTP_200_OK,
    summary="调用 Agent（SSE 流式）",
)
async def chat(
    session: SessionDep,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
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
        if suffix in IMAGE_SUFFIXES and (upload.content_type or "").startswith(
            "image/"
        ):
            mime = (
                upload.content_type
                or mimetypes.guess_type(upload.filename or "")[0]
                or "image/png"
            )
            image_urls.append(f"data:{mime};base64,{base64.b64encode(data).decode()}")
            file_names.append(upload.filename or "图片")
        elif suffix in TEXT_SUFFIXES:
            text = data.decode("utf-8", errors="replace").strip()
            if text:
                documents.append((upload.filename or "document.txt", text))
                file_names.append(upload.filename or "document.txt")
        else:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"不支持的文件类型：{upload.filename}（支持图片和 txt/md 文档）",
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
            await asyncio.to_thread(ingest_documents, str(agent_session.id), documents)
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

    # ------------------------------------------------------------------
    # 意图识别（规则 + 可选 LLM）：结果驱动提示词注入与人工升级
    # ------------------------------------------------------------------
    if user_input.strip():
        intent_result = await classify_intent(user_input)
        if needs_escalation(intent_result):
            review_service.create_review_case(
                session,
                session_id=agent_session.id,
                user_id=current_user.id,
                source="auto_intent",
                reason=escalation_reason(intent_result),
                intent=intent_result.name,
            )
    else:
        # 纯文件上传无文字可分类：不升级、按默认意图处理
        intent_result = IntentResult(name="chat", confidence=1.0, method="rules")
    escalated = review_service.get_open_case(session, agent_session.id) is not None

    # ------------------------------------------------------------------
    # 上下文工程：坐席指导 > 会话摘要 > 用户偏好 > RAG 资料，按预算装配
    # ------------------------------------------------------------------
    memory_row = session_memory.get_or_create_memory(session, agent_session.id)
    reviewer_guidance = review_service.get_active_guidance(session, agent_session.id)
    context_block = session_memory.build_context_block(
        memory_row,
        rag_context=context,
        reviewer_guidance=reviewer_guidance,
    )

    # ------------------------------------------------------------------
    # 提示词工程：角色设定 + 风格参数 + 意图指南 + 运营改进项（反馈闭环回流）
    # ------------------------------------------------------------------
    active_notes = session.exec(
        select(ImprovementNote)
        .where(ImprovementNote.active)
        .order_by(col(ImprovementNote.created_at))
        .limit(10)
    ).all()
    system_prompt = build_system_prompt(
        user_input,
        intent_result.name,
        [note.content for note in active_notes],
    )

    history = _load_history(session, agent_session.id)
    user_content = user_input.strip() or (
        "、".join(file_names) if file_names else "请分析上传的内容"
    )

    def _discard_empty_session() -> None:
        if created_session:
            session.delete(agent_session)
            session.commit()

    async def event_generator() -> AsyncIterator[str]:
        """SSE 事件流：session -> meta -> delta* -> done / error。

        客户端断开时本生成器被取消（CancelledError），届时回滚未落库
        的消息并清理新建的空会话；正常结束才把消息成对落库，并在响应
        结束后由后台任务刷新会话记忆（滚动摘要 + 偏好提取）。
        """
        try:
            yield _sse_event("session", {"session_id": str(agent_session.id)})
            yield _sse_event(
                "meta",
                {
                    "intent": intent_result.to_dict(),
                    "escalated": escalated,
                    "prompt_version": PROMPT_VERSION,
                },
            )

            full = ""
            async for chunk in agent.process_request_stream(
                user_input,
                images=image_urls or None,
                context=context_block or None,
                history=history,
                system_prompt=system_prompt,
            ):
                full += chunk
                yield _sse_event("delta", {"delta": chunk})

            if not full.strip():
                raise RuntimeError("模型返回了空回复，请重试")

            agent_session.updated_at = datetime.now(UTC)
            # 用户消息打上意图标记，助手消息打上提示词版本：评估按此聚合
            user_msg = AgentMessage(
                session_id=agent_session.id,
                role="user",
                content=user_content,
                intent=intent_result.name,
                intent_confidence=intent_result.confidence,
            )
            assistant_msg = AgentMessage(
                session_id=agent_session.id,
                role="assistant",
                content=full,
                intent=intent_result.name,
                intent_confidence=intent_result.confidence,
                prompt_version=PROMPT_VERSION,
            )
            session.add(user_msg)
            session.add(assistant_msg)
            session.add(agent_session)
            session.commit()

            total_messages = session.exec(
                select(func.count())
                .select_from(AgentMessage)
                .where(AgentMessage.session_id == agent_session.id)
            ).one()
            if session_memory.should_refresh_memory(
                memory_row, int(total_messages), agent_config.history_window
            ):
                # 响应结束后执行：滚动摘要 + 偏好合并（失败只影响记忆新鲜度）
                background_tasks.add_task(
                    session_memory.summarize_and_store,
                    str(agent_session.id),
                    agent_config.history_window,
                )

            yield _sse_event(
                "done",
                {
                    "response": full,
                    "message_id": str(assistant_msg.id),
                    "intent": intent_result.name,
                },
            )
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
        background=background_tasks,
    )


async def _save_upload_temp(upload: UploadFile) -> str:
    """校验并把上传音频落到临时文件，返回路径；非法类型/超限抛 HTTPException。"""
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in mcp_audio.AUDIO_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的音频格式：{upload.filename}"
            f"（支持 {'/'.join(sorted(mcp_audio.AUDIO_SUFFIXES))}）",
        )
    data = await upload.read()
    max_bytes = agent_config.max_upload_size_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"音频 {upload.filename} 超过 {agent_config.max_upload_size_mb}MB 限制",
        )
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
        return tmp.name


@router.post(
    "/compose",
    status_code=status.HTTP_200_OK,
    summary="多 Agent 协作创作（SSE 流式，支持上传音频做听觉解析）",
)
async def compose(
    session: SessionDep,
    current_user: CurrentUser,
    request_json: str = Form(default="{}"),
    audio: UploadFile | None = File(default=None),
) -> StreamingResponse:
    """多 Agent 创作流水线：听觉解析 → 和声 → 乐理校正 → 旋律 → [贝斯 ∥ 鼓] → 评审 → 渲染。

    request_json 是 ComposeRequest 的 JSON 字符串；audio 为可选音频文件
    （wav/mp3/flac/ogg/aac），上传时先跑听觉解析，检测到的 BPM/调性自动
    填入特征清单（请求里显式给出的值优先）。
    SSE 事件流：session -> stage* -> done / error。
    """
    try:
        request_in = ComposeRequest.model_validate_json(request_json or "{}")
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=json.dumps(exc.errors(include_url=False), ensure_ascii=False),
        ) from exc

    upload = audio if audio is not None and audio.filename else None

    if request_in.session_id:
        agent_session = _get_owned_session(session, current_user, request_in.session_id)
        created_session = False
    else:
        title_source = request_in.mood or request_in.style or "创作"
        agent_session = AgentSession(
            title=f"创作：{title_source} {request_in.key}调 {request_in.bpm}BPM"[:255],
            owner_id=current_user.id,
        )
        session.add(agent_session)
        session.commit()
        session.refresh(agent_session)
        created_session = True

    # 第 2 阶段：知识匹配（RAG 检索风格/编曲参考资料），失败不阻塞创作
    query = " ".join(
        part
        for part in (request_in.mood, request_in.style, request_in.user_notes)
        if part
    )
    references = ""
    if query:
        try:
            references = await asyncio.to_thread(
                retrieve_context, str(agent_session.id), query
            )
        except Exception:
            logger.warning("Knowledge base retrieval failed", exc_info=True)

    # 第 1 阶段：听觉解析（audio-analyzer MCP），上传音频时提取节奏/和声骨架
    analysis: dict[str, object] | None = None
    analysis_detail = mcp_audio.analysis_summary(None)
    upload_path: str | None = None
    if upload:
        upload_path = await _save_upload_temp(upload)
        try:
            analysis = await mcp_audio.analyze_audio_file(upload_path)
            analysis_detail = mcp_audio.analysis_summary(analysis)
        finally:
            Path(upload_path).unlink(missing_ok=True)

    # 特征清单：解析结果打底，请求里显式给出的字段优先
    explicit = request_in.model_fields_set - {"session_id", "user_notes"}
    features: dict[str, object] = dict(analysis) if analysis else {}
    features.update(
        {
            "mood": request_in.mood or request_in.style,
            "style": request_in.style,
            "bars": request_in.bars,
            "beats_per_bar": request_in.beats_per_bar,
            "user_notes": request_in.user_notes,
            "title": f"{request_in.mood or request_in.style}-{request_in.key}{request_in.bpm}",
        }
    )
    for field in ("key", "mode", "bpm"):
        if field in explicit:
            features[field] = getattr(request_in, field)
    if "key" not in features:
        features["key"] = request_in.key
    if "mode" not in features:
        features["mode"] = request_in.mode
    if "bpm" not in features:
        features["bpm"] = request_in.bpm
    inputs: dict[str, object] = {"features": features, "references": references}
    user_content = (
        f"创作请求：{request_in.key} "
        f"{'小调' if request_in.mode == 'minor' else '大调'} {request_in.bpm}BPM "
        f"{request_in.bars} 小节｜风格 {request_in.style}"
        + (f"｜{request_in.mood}" if request_in.mood else "")
        + (f"｜附音频（{analysis_detail}）" if upload else "")
    )

    def _discard_empty_session() -> None:
        if created_session:
            session.delete(agent_session)
            session.commit()

    async def event_generator() -> AsyncIterator[str]:
        try:
            yield _sse_event("session", {"session_id": str(agent_session.id)})
            if upload:
                yield _sse_event(
                    "stage",
                    {
                        "node": "analysis",
                        "stage": STAGE_LABELS["analysis"],
                        "detail": analysis_detail,
                    },
                )

            graph = build_compose_graph()
            final: dict[str, object] = {}
            async for stream_mode, payload in graph.astream(
                inputs, stream_mode=["updates", "values"]
            ):
                if stream_mode == "values":
                    final = payload if isinstance(payload, dict) else {}
                    continue
                for node, patch in (payload or {}).items():
                    if node == "__end__" or not isinstance(patch, dict):
                        continue
                    yield _sse_event(
                        "stage",
                        {
                            "node": node,
                            "stage": STAGE_LABELS.get(node, node),
                            "detail": stage_detail(node, patch),
                        },
                    )

            summary = str(final.get("render_summary") or "创作完成")
            agent_session.updated_at = datetime.now(UTC)
            session.add(
                AgentMessage(
                    session_id=agent_session.id, role="user", content=user_content
                )
            )
            session.add(
                AgentMessage(
                    session_id=agent_session.id, role="assistant", content=summary
                )
            )
            session.add(agent_session)
            session.commit()
            yield _sse_event(
                "done",
                {
                    "summary": summary,
                    "midi_path": str(final.get("midi_path", "")),
                    "chords": final.get("chords", []),
                },
            )
        except asyncio.CancelledError:
            _discard_empty_session()
            raise
        except Exception as exc:
            logger.exception("Compose pipeline failed")
            _discard_empty_session()
            yield _sse_event("error", {"detail": f"Compose failed: {exc}"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _safe_score_path(filename: str) -> Path:
    """把请求的文件名限制在产物目录内，拒绝路径穿越。"""
    from app.agent.config import config as agent_cfg

    scores_dir = Path(agent_cfg.scores_dir).resolve()
    candidate = (scores_dir / filename).resolve()
    if candidate.parent != scores_dir or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Score file not found")
    return candidate


@router.get(
    "/scores/{filename}",
    summary="下载/播放总谱产物（mp3 或 midi）",
)
async def download_score(_current_user: CurrentUser, filename: str) -> FileResponse:
    """返回渲染产物文件（音频由前端 <audio> 播放，midi 供音乐软件使用）。

    _current_user 仅用于鉴权（未登录不可访问产物文件）。
    """
    path = _safe_score_path(filename)
    media_type = "audio/mpeg" if path.suffix == ".mp3" else "audio/midi"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
    )
