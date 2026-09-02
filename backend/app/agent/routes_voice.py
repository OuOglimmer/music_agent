"""语音接口：捕捉处理（上传录音 → 文本）与语音输出（文本 → 音频）。

独立于 routes.py 的路由文件，只依赖 speech.py：
- POST /agent/voice/transcriptions  multipart 上传音频，返回转写文本；
- POST /agent/voice/speech          JSON {text, voice?, format?}，返回音频流；
- GET  /agent/voice/config          前端能力协商（允许的格式/默认音色/上限）。
"""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.agent import speech
from app.agent.config import config as agent_config
from app.api.deps import CurrentUser
from app.core.observability import traced_span

logger = logging.getLogger(__name__)

router = APIRouter()


class SpeechRequest(BaseModel):
    """TTS 请求：待朗读文本 + 可选音色/格式。"""

    text: str = Field(min_length=1, max_length=8000)
    voice: str | None = Field(default=None, max_length=64)
    response_format: str = Field(default="mp3", pattern=r"^(mp3|wav|opus|flac|aac)$")


class VoiceConfigPublic(BaseModel):
    """前端录音/TTS 能力协商。"""

    allowed_suffixes: list[str]
    default_voice: str
    tts_max_chars: int
    max_upload_size_mb: int


@router.get("/voice/config", summary="语音能力协商（录音格式/音色/上限）")
def voice_config(_current_user: CurrentUser) -> VoiceConfigPublic:
    return VoiceConfigPublic(
        allowed_suffixes=sorted(agent_config.allowed_voice_suffixes),
        default_voice=agent_config.tts_voice,
        tts_max_chars=agent_config.tts_max_chars,
        max_upload_size_mb=agent_config.max_upload_size_mb,
    )


@router.post(
    "/voice/transcriptions",
    summary="语音转文字（麦克风录音或音频文件）",
)
async def transcriptions(
    _current_user: CurrentUser,
    audio: Annotated[UploadFile, File(description="录音或音频文件")],
    language: str = Form(default=""),
) -> dict:
    suffix = Path(audio.filename or "").suffix.lower()
    if suffix not in agent_config.allowed_voice_suffixes:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的音频格式：{audio.filename}"
            f"（支持 {'/'.join(sorted(agent_config.allowed_voice_suffixes))}）",
        )
    data = await audio.read()
    max_bytes = agent_config.max_upload_size_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"音频超过 {agent_config.max_upload_size_mb}MB 限制",
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="音频内容为空"
        )

    try:
        text = await speech.transcribe(
            data,
            audio.filename or "audio.wav",
            audio.content_type or "audio/wav",
            language=language.strip() or None,
        )
    except speech.SpeechNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except speech.SpeechProviderError as exc:
        logger.warning("STT failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="没能识别出语音内容，请靠近麦克风重试",
        )
    return {"text": text}


@router.post("/voice/speech", summary="文字转语音（朗读回复）")
async def speech_synthesis(
    _current_user: CurrentUser, request_in: SpeechRequest
) -> Response:
    with traced_span("voice.speech_endpoint", chars=len(request_in.text)):
        try:
            audio, media_type = await speech.synthesize(
                request_in.text,
                voice=request_in.voice,
                response_format=request_in.response_format,
            )
        except speech.SpeechNotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        except speech.SpeechProviderError as exc:
            logger.warning("TTS failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
    return Response(content=audio, media_type=media_type)
