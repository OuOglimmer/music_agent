"""语音接口测试：上传录音转文字 / 文本朗读（mock speech 模块，不触网）。"""

import io
from unittest.mock import patch as mock_patch

import pytest
from fastapi.testclient import TestClient

from app.agent import speech as speech_module
from app.agent.speech import SpeechNotConfiguredError, SpeechProviderError


def _wav(filename: str = "clip.wav", content: bytes = b"RIFFfake") -> dict:
    return {"files": {"audio": (filename, io.BytesIO(content), "audio/wav")}}


@pytest.fixture
def _auth(superuser_token_headers):
    return superuser_token_headers


def test_voice_config_lists_capabilities(
    client: TestClient, superuser_token_headers: dict
) -> None:
    response = client.get("/api/v1/agent/voice/config", headers=superuser_token_headers)
    assert response.status_code == 200
    body = response.json()
    assert ".wav" in body["allowed_suffixes"]
    assert body["default_voice"]
    assert body["tts_max_chars"] >= 100


def test_transcriptions_rejects_bad_suffix(
    client: TestClient, superuser_token_headers: dict
) -> None:
    response = client.post(
        "/api/v1/agent/voice/transcriptions",
        headers=superuser_token_headers,
        files={"audio": ("evil.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
    )
    assert response.status_code == 415


def test_transcriptions_rejects_empty_audio(
    client: TestClient, superuser_token_headers: dict
) -> None:
    response = client.post(
        "/api/v1/agent/voice/transcriptions",
        headers=superuser_token_headers,
        **_wav(content=b""),
    )
    assert response.status_code == 422


def test_transcriptions_returns_text(
    client: TestClient, superuser_token_headers: dict
) -> None:
    async def fake_transcribe(*_args: object, **_kwargs: object) -> str:
        return "帮我写一段旋律"

    with mock_patch.object(speech_module, "transcribe", fake_transcribe):
        response = client.post(
            "/api/v1/agent/voice/transcriptions",
            headers=superuser_token_headers,
            **_wav(filename="recording.webm"),
        )
    assert response.status_code == 200
    assert response.json()["text"] == "帮我写一段旋律"


def test_transcriptions_maps_not_configured_to_503(
    client: TestClient, superuser_token_headers: dict
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> str:
        raise SpeechNotConfiguredError("DASHSCOPE_API_KEY is not configured")

    with mock_patch.object(speech_module, "transcribe", broken):
        response = client.post(
            "/api/v1/agent/voice/transcriptions",
            headers=superuser_token_headers,
            **_wav(),
        )
    assert response.status_code == 503


def test_transcriptions_maps_provider_error_to_502(
    client: TestClient, superuser_token_headers: dict
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> str:
        raise SpeechProviderError("upstream 500")

    with mock_patch.object(speech_module, "transcribe", broken):
        response = client.post(
            "/api/v1/agent/voice/transcriptions",
            headers=superuser_token_headers,
            **_wav(),
        )
    assert response.status_code == 502


def test_speech_returns_audio(
    client: TestClient, superuser_token_headers: dict
) -> None:
    async def fake_synth(*_args: object, **_kwargs: object) -> tuple[bytes, str]:
        return b"MP3AUDIO", "audio/mpeg"

    with mock_patch.object(speech_module, "synthesize", fake_synth):
        response = client.post(
            "/api/v1/agent/voice/speech",
            headers=superuser_token_headers,
            json={"text": "你好，欢迎来到音乐工坊"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"MP3AUDIO"


def test_speech_validates_input(
    client: TestClient, superuser_token_headers: dict
) -> None:
    response = client.post(
        "/api/v1/agent/voice/speech",
        headers=superuser_token_headers,
        json={"text": "   "},
    )
    assert response.status_code == 422
    response = client.post(
        "/api/v1/agent/voice/speech",
        headers=superuser_token_headers,
        json={"text": "hi", "response_format": "wma"},
    )
    assert response.status_code == 422


def test_voice_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/agent/voice/config").status_code == 401
