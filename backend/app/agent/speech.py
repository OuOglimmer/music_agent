"""语音捕捉处理（STT）与语音输出（TTS）。

统一走 OpenAI 兼容端点（DashScope compatible-mode）：
- 转写：``POST {base_url}/audio/transcriptions``（multipart 上传音频，
  返回 ``{"text": ...}``）——浏览器麦克风录音（webm/ogg）与常见音频
  （wav/mp3/m4a…）都能捕捉处理；
- 合成：``POST {base_url}/audio/speech``（JSON 请求，返回音频字节流），
  用于朗读 Agent 回复；相同 (文本, 音色, 格式) 命中内存 LRU 缓存。

设计要点：
- 全异步 httpx，直连不信任环境代理（与 agent_core 同款 trust_env=False）；
- 失败语义清晰：``SpeechNotConfiguredError``（缺 Key）→ 503，
  上游失败 → ``SpeechProviderError`` → 502；
- 每次调用记录 ``stt.transcribe`` / ``tts.synthesize`` span 与指标。
"""

import hashlib
import logging
import time
from collections import OrderedDict
from typing import Any

import httpx

from app.agent.config import config as agent_config
from app.core.observability import metrics, traced_span

logger = logging.getLogger(__name__)

_TTS_CACHE_MAX_ITEMS = 32
_TTS_CACHE_MAX_BYTES = 8 * 1024 * 1024


class SpeechNotConfiguredError(RuntimeError):
    """语音服务未配置 API Key。"""


class SpeechProviderError(RuntimeError):
    """上游语音服务返回错误或超时。"""


class _TtsCache:
    """小型 LRU：重复朗读同一段文本时免二次合成（也省 token 费用）。"""

    def __init__(self) -> None:
        self._data: OrderedDict[str, bytes] = OrderedDict()
        self._total = 0

    def _key(self, text: str, voice: str, response_format: str) -> str:
        return hashlib.sha256(f"{voice}|{response_format}|{text}".encode()).hexdigest()

    def get(self, text: str, voice: str, response_format: str) -> bytes | None:
        key = self._key(text, voice, response_format)
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, text: str, voice: str, response_format: str, audio: bytes) -> None:
        key = self._key(text, voice, response_format)
        if key in self._data:
            self._data.move_to_end(key)
            return
        self._data[key] = audio
        self._total += len(audio)
        while len(self._data) > _TTS_CACHE_MAX_ITEMS or (
            self._total > _TTS_CACHE_MAX_BYTES and len(self._data) > 1
        ):
            _, evicted = self._data.popitem(last=False)
            self._total -= len(evicted)


_tts_cache = _TtsCache()


def _headers() -> dict[str, str]:
    if not agent_config.api_key:
        raise SpeechNotConfiguredError("DASHSCOPE_API_KEY is not configured")
    return {"Authorization": f"Bearer {agent_config.api_key}"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=agent_config.base_url,
        timeout=agent_config.stt_timeout,
        trust_env=False,  # 直连，避免环境代理变量干扰
    )


async def transcribe(
    audio: bytes,
    filename: str,
    content_type: str,
    language: str | None = None,
) -> str:
    """把音频转写为文本（捕捉处理链路的出口）。

    language 为 ISO 639-1 提示（如 "zh"），None 表示自动判断。
    返回去掉首尾空白的识别文本；上游失败抛 SpeechProviderError。
    """
    with traced_span("stt.transcribe", filename=filename, bytes=len(audio)) as span:
        started = time.perf_counter()
        lang = language or agent_config.stt_language or None
        try:
            async with _client() as client:
                response = await client.post(
                    "/audio/transcriptions",
                    headers=_headers(),
                    files={"file": (filename, audio, content_type or "audio/wav")},
                    data={
                        "model": agent_config.stt_model,
                        **({"language": lang} if lang else {}),
                    },
                )
        except SpeechNotConfiguredError:
            raise
        except Exception as exc:
            metrics.incr("stt.errors")
            raise SpeechProviderError(f"语音识别请求失败：{exc}") from exc

        if response.status_code >= 400:
            metrics.incr("stt.errors")
            detail = response.text[:300]
            raise SpeechProviderError(
                f"语音识别服务返回 {response.status_code}：{detail}"
            )
        try:
            text = str(response.json().get("text", "")).strip()
        except Exception as exc:
            metrics.incr("stt.errors")
            raise SpeechProviderError("语音识别服务返回了无法解析的响应") from exc

        duration_ms = (time.perf_counter() - started) * 1000
        span.attributes["duration_ms"] = round(duration_ms, 2)
        span.attributes["chars"] = len(text)
        metrics.incr("stt.requests")
        metrics.observe("stt.duration", duration_ms)
        log = logging.getLogger(__name__)
        log.info("STT ok: %s chars from %s bytes", len(text), len(audio))
        return text


async def synthesize(
    text: str,
    voice: str | None = None,
    response_format: str = "mp3",
) -> tuple[bytes, str]:
    """把文本合成为语音（输出链路），返回 (音频字节, content_type)。

    命中缓存时直接返回，不打上游。
    """
    text = text.strip()
    if not text:
        raise ValueError("合成文本不能为空")
    if len(text) > agent_config.tts_max_chars:
        raise ValueError(f"合成文本超过 {agent_config.tts_max_chars} 字符上限")
    voice = voice or agent_config.tts_voice

    cached = _tts_cache.get(text, voice, response_format)
    if cached is not None:
        metrics.incr("tts.cache_hits")
        return cached, _media_type(response_format)

    with traced_span("tts.synthesize", voice=voice, chars=len(text)) as span:
        started = time.perf_counter()
        try:
            async with _client() as client:
                response = await client.post(
                    "/audio/speech",
                    headers={**_headers(), "Content-Type": "application/json"},
                    json={
                        "model": agent_config.tts_model,
                        "input": text,
                        "voice": voice,
                        "response_format": response_format,
                    },
                    timeout=agent_config.tts_timeout,
                )
        except SpeechNotConfiguredError:
            raise
        except Exception as exc:
            metrics.incr("tts.errors")
            raise SpeechProviderError(f"语音合成请求失败：{exc}") from exc

        if response.status_code >= 400:
            metrics.incr("tts.errors")
            raise SpeechProviderError(
                f"语音合成服务返回 {response.status_code}：{response.text[:300]}"
            )

        audio = response.content
        if not audio:
            metrics.incr("tts.errors")
            raise SpeechProviderError("语音合成服务返回了空音频")

        duration_ms = (time.perf_counter() - started) * 1000
        span.attributes["duration_ms"] = round(duration_ms, 2)
        span.attributes["bytes"] = len(audio)
        metrics.incr("tts.requests")
        metrics.observe("tts.duration", duration_ms)
        _tts_cache.put(text, voice, response_format, audio)
        return audio, _media_type(response_format)


def _media_type(response_format: str) -> str:
    return {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "opus": "audio/ogg",
        "flac": "audio/flac",
        "aac": "audio/aac",
        "pcm": "audio/pcm",
    }.get(response_format, "audio/mpeg")


def tts_cache_stats() -> dict[str, Any]:
    """缓存规模（可观测性/运维）。"""
    return {"items": len(_tts_cache._data), "bytes": _tts_cache._total}
