"""语音模块测试：STT 转写 / TTS 合成 / 缓存（mock httpx，不触网）。"""

import httpx
import pytest

from app.agent import speech
from app.agent.config import config as agent_config
from app.agent.speech import (
    SpeechNotConfiguredError,
    SpeechProviderError,
    _TtsCache,
)


class FakeAsyncClient:
    """替换 speech._client()：按路径返回预置响应。"""

    status_code = 200
    response_map: dict[str, httpx.Response] = {}

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, path: str, **kwargs: object) -> httpx.Response:
        self.last_call = (path, kwargs)  # type: ignore[attr-defined]
        response = self.response_map[path]
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def _clean_tts_cache():
    """TTS 缓存是模块级单例：每个测试前清空，避免用例间相互命中。"""
    speech._tts_cache._data.clear()
    yield
    speech._tts_cache._data.clear()


@pytest.fixture
def fake_client(monkeypatch):
    def _install(
        responses: dict[str, httpx.Response | Exception], status_code: int = 200
    ):
        cls = type(
            "C",
            (FakeAsyncClient,),
            {"response_map": responses, "status_code": status_code},
        )
        calls: list[tuple] = []

        async def post(self, path, **kwargs):
            calls.append((path, kwargs))
            response = self.response_map[path]
            if isinstance(response, Exception):
                raise response
            return response

        cls.post = post  # type: ignore[method-assign]
        monkeypatch.setattr(speech, "_client", lambda: cls())
        return calls

    return _install


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_transcribe_returns_text(fake_client, monkeypatch) -> None:
    monkeypatch.setattr(agent_config, "api_key", "sk-test")
    calls = fake_client(
        {
            "/audio/transcriptions": httpx.Response(
                200,
                json={"text": "  你好世界  "},
                request=httpx.Request("POST", "http://x"),
            )
        }
    )
    text = await speech.transcribe(b"audio-bytes", "clip.webm", "audio/webm")
    assert text == "你好世界"
    path, kwargs = calls[0]
    assert path == "/audio/transcriptions"
    assert kwargs["data"]["model"] == agent_config.stt_model
    assert "language" not in kwargs["data"]


@pytest.mark.anyio
async def test_transcribe_passes_language_hint(fake_client, monkeypatch) -> None:
    monkeypatch.setattr(agent_config, "api_key", "sk-test")
    calls = fake_client(
        {
            "/audio/transcriptions": httpx.Response(
                200, json={"text": "hi"}, request=httpx.Request("POST", "http://x")
            )
        }
    )
    await speech.transcribe(b"a", "a.wav", "audio/wav", language="en")
    assert calls[0][1]["data"]["language"] == "en"


@pytest.mark.anyio
async def test_transcribe_upstream_error_raises_provider_error(
    fake_client, monkeypatch
) -> None:
    monkeypatch.setattr(agent_config, "api_key", "sk-test")
    fake_client(
        {
            "/audio/transcriptions": httpx.Response(
                503, text="busy", request=httpx.Request("POST", "http://x")
            )
        }
    )
    with pytest.raises(SpeechProviderError, match="503"):
        await speech.transcribe(b"a", "a.wav", "audio/wav")


@pytest.mark.anyio
async def test_transcribe_network_error_raises_provider_error(
    fake_client, monkeypatch
) -> None:
    monkeypatch.setattr(agent_config, "api_key", "sk-test")
    fake_client({"/audio/transcriptions": httpx.ConnectError("down")})
    with pytest.raises(SpeechProviderError):
        await speech.transcribe(b"a", "a.wav", "audio/wav")


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_synthesize_returns_audio_and_caches(fake_client, monkeypatch) -> None:
    monkeypatch.setattr(agent_config, "api_key", "sk-test")
    calls = fake_client(
        {
            "/audio/speech": httpx.Response(
                200, content=b"MP3DATA", request=httpx.Request("POST", "http://x")
            )
        }
    )
    audio, media_type = await speech.synthesize("你好", voice="Cherry")
    assert audio == b"MP3DATA"
    assert media_type == "audio/mpeg"

    # 第二次相同请求命中缓存，不再发起网络调用
    audio2, _ = await speech.synthesize("你好", voice="Cherry")
    assert audio2 == b"MP3DATA"
    assert len(calls) == 1


@pytest.mark.anyio
async def test_synthesize_empty_text_rejected(fake_client, monkeypatch) -> None:
    monkeypatch.setattr(agent_config, "api_key", "sk-test")
    fake_client({})
    with pytest.raises(ValueError, match="不能为空"):
        await speech.synthesize("   ")


@pytest.mark.anyio
async def test_synthesize_too_long_rejected(fake_client, monkeypatch) -> None:
    monkeypatch.setattr(agent_config, "api_key", "sk-test")
    monkeypatch.setattr(agent_config, "tts_max_chars", 100)
    fake_client({})
    with pytest.raises(ValueError, match="上限"):
        await speech.synthesize("长" * 101)


@pytest.mark.anyio
async def test_synthesize_upstream_error(fake_client, monkeypatch) -> None:
    monkeypatch.setattr(agent_config, "api_key", "sk-test")
    fake_client(
        {
            "/audio/speech": httpx.Response(
                500, text="boom", request=httpx.Request("POST", "http://x")
            )
        }
    )
    with pytest.raises(SpeechProviderError):
        await speech.synthesize("你好")


def test_tts_cache_lru_eviction() -> None:
    cache = _TtsCache()
    for i in range(40):
        cache.put(f"文本{i}", "Cherry", "mp3", b"x" * 1000)
    assert len(cache._data) <= 32
    assert cache.get("文本0", "Cherry", "mp3") is None  # 最旧的被逐出
    cache.put("最新", "Cherry", "mp3", b"y" * (9 * 1024 * 1024))  # 超大条目触发字节上限
    assert cache._total <= 8 * 1024 * 1024 + 9 * 1024 * 1024


def test_not_configured_when_no_key(monkeypatch) -> None:
    monkeypatch.setattr(agent_config, "api_key", "")
    with pytest.raises(SpeechNotConfiguredError):
        speech._headers()
