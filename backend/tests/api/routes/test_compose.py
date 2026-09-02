"""/api/v1/agent/compose 多 Agent 创作端点测试（SSE stage 事件流）。"""

import json
from typing import Any

from app.models import AgentSession
from tests.api.routes.test_agent import create_client, parse_sse

FAKE_UPDATES: list[dict[str, dict[str, Any]]] = [
    {"harmony": {"chords": ["C", "Am", "F", "G"], "harmony_rationale": "温暖正向"}},
    {"theory": {"theory": None, "theory_summary": "C major｜I-vi-IV-V"}},
    {"melody": {"melody": [], "melody_summary": "平静开场"}},
    {"bass": {"bass": [], "bass_summary": "octave 风格"}},
    {"drums": {"drums": [], "drums_summary": "pop 风格"}},
    {
        "review": {
            "review": {"passed": True, "notes": ["有记忆点"]},
            "revision_round": 1,
        }
    },
    {"render": {"midi_path": "/tmp/blueprint.mid", "render_summary": "总谱已生成"}},
]
FAKE_FINAL: dict[str, Any] = {
    "chords": ["C", "Am", "F", "G"],
    "midi_path": "/tmp/blueprint.mid",
    "render_summary": "总谱已生成",
}


class FakeGraph:
    def __init__(self, updates=None, final=None, error: Exception | None = None):
        self.updates = updates if updates is not None else FAKE_UPDATES
        self.final = final if final is not None else FAKE_FINAL
        self.error = error
        self.inputs: dict[str, Any] | None = None

    async def astream(self, inputs, stream_mode=None):
        self.inputs = inputs
        if self.error is not None:
            raise self.error
        for update in self.updates:
            yield ("updates", update)
        yield ("values", self.final)


def test_compose_streams_stage_events(monkeypatch, db) -> None:
    import app.agent.routes as agent_routes

    graph = FakeGraph()
    monkeypatch.setattr(agent_routes, "build_compose_graph", lambda: graph)
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")

    response = create_client(db).post(
        "/api/v1/agent/compose",
        data={
            "request_json": json.dumps(
                {
                    "key": "C",
                    "mode": "major",
                    "bpm": 96,
                    "mood": "温暖",
                    "style": "pop",
                    "bars": 4,
                }
            )
        },
    )

    assert response.status_code == 200
    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[0] == "session"
    assert names[-1] == "done"
    assert "error" not in names
    stage_events = [data for name, data in events if name == "stage"]
    assert [s["node"] for s in stage_events] == [
        "harmony",
        "theory",
        "melody",
        "bass",
        "drums",
        "review",
        "render",
    ]
    assert stage_events[0]["detail"] == "温暖正向"
    assert stage_events[1]["detail"] == "C major｜I-vi-IV-V"
    assert stage_events[0]["stage"].startswith("🎹")

    done = events[-1][1]
    assert done["midi_path"] == "/tmp/blueprint.mid"
    assert done["chords"] == ["C", "Am", "F", "G"]
    assert done["summary"] == "总谱已生成"

    # 特征清单进入 graph 输入，参考资料字段存在
    assert graph.inputs is not None
    assert graph.inputs["features"]["key"] == "C"
    assert graph.inputs["features"]["bars"] == 4

    # 会话落库：用户请求 + 最终摘要
    session_id = events[0][1]["session_id"]
    messages = (
        create_client(db).get(f"/api/v1/agent/sessions/{session_id}/messages").json()
    )
    assert messages["count"] == 2
    assert messages["data"][0]["role"] == "user"
    assert "创作请求" in messages["data"][0]["content"]
    assert messages["data"][1]["content"] == "总谱已生成"


def test_compose_error_discards_new_session(monkeypatch, db) -> None:
    import app.agent.routes as agent_routes

    graph = FakeGraph(error=RuntimeError("boom"))
    monkeypatch.setattr(agent_routes, "build_compose_graph", lambda: graph)
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")

    client = create_client(db)
    before = client.get("/api/v1/agent/sessions").json()["count"]
    response = client.post(
        "/api/v1/agent/compose", data={"request_json": json.dumps({"key": "C"})}
    )

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1][0] == "error"
    assert "boom" in events[-1][1]["detail"]
    # 失败后新建的空会话应被清理
    after = client.get("/api/v1/agent/sessions").json()
    assert after["count"] == before


def test_compose_rejects_invalid_params(db) -> None:
    response = create_client(db).post(
        "/api/v1/agent/compose",
        data={"request_json": json.dumps({"bpm": 10, "bars": 100})},
    )
    assert response.status_code == 422


def test_compose_requires_owned_session(monkeypatch, db) -> None:
    import app.agent.routes as agent_routes
    from tests.utils.user import create_random_user

    monkeypatch.setattr(agent_routes, "build_compose_graph", lambda: FakeGraph())
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")

    other_user = create_random_user(db)
    other_session = AgentSession(title="别人的会话", owner_id=other_user.id)
    db.add(other_session)
    db.commit()

    response = create_client(db).post(
        "/api/v1/agent/compose",
        data={
            "request_json": json.dumps(
                {"key": "C", "session_id": str(other_session.id)}
            )
        },
    )
    assert response.status_code == 404


def test_download_score_serves_audio(monkeypatch, db, tmp_path) -> None:
    from app.agent.config import config as agent_config

    (tmp_path / "blueprint-abc.mp3").write_bytes(b"ID3fakemp3")
    (tmp_path / "blueprint-abc.mid").write_bytes(b"MThdfake")
    monkeypatch.setattr(agent_config, "scores_dir", str(tmp_path))

    client = create_client(db)
    mp3 = client.get("/api/v1/agent/scores/blueprint-abc.mp3")
    assert mp3.status_code == 200
    assert mp3.headers["content-type"] == "audio/mpeg"

    midi = client.get("/api/v1/agent/scores/blueprint-abc.mid")
    assert midi.status_code == 200
    assert midi.headers["content-type"] == "audio/midi"


def test_download_score_rejects_traversal_and_missing(
    monkeypatch, db, tmp_path
) -> None:
    from app.agent.config import config as agent_config

    monkeypatch.setattr(agent_config, "scores_dir", str(tmp_path))
    client = create_client(db)
    # 路径穿越
    assert client.get("/api/v1/agent/scores/..%2F..%2Fetc%2Fpasswd").status_code == 404
    # 不存在的文件
    assert client.get("/api/v1/agent/scores/nope.mp3").status_code == 404


async def fake_analysis(_path: str) -> dict[str, Any] | None:
    return {
        "bpm": 120,
        "bpm_confidence": 0.82,
        "key": "Bb",
        "mode": "major",
        "key_confidence": 0.84,
        "tempo_stability": 0.99,
        "duration_seconds": 8.0,
    }


def test_compose_with_audio_uploads_analysis(monkeypatch, db) -> None:
    """上传音频：先跑听觉解析，检测到的 BPM/调性进入特征清单并广播 analysis 事件。"""
    import app.agent.routes as agent_routes

    graph = FakeGraph()
    monkeypatch.setattr(agent_routes, "build_compose_graph", lambda: graph)
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")
    monkeypatch.setattr(agent_routes.mcp_audio, "analyze_audio_file", fake_analysis)

    response = create_client(db).post(
        "/api/v1/agent/compose",
        data={"request_json": json.dumps({"style": "pop", "bars": 8})},
        files={"audio": ("hook.wav", b"RIFFfake", "audio/wav")},
    )

    assert response.status_code == 200
    events = parse_sse(response.text)
    stage_nodes = [d["node"] for n, d in events if n == "stage"]
    assert stage_nodes[0] == "analysis"
    analysis_event = next(
        d for n, d in events if n == "stage" and d["node"] == "analysis"
    )
    assert "120 BPM" in analysis_event["detail"]
    assert "大调" in analysis_event["detail"]

    # 解析结果填充特征清单：BPM 与调性来自音频
    assert graph.inputs is not None
    features = graph.inputs["features"]
    assert features["bpm"] == 120
    assert features["key"] == "Bb"
    assert features["mode"] == "major"


def test_compose_explicit_params_override_analysis(monkeypatch, db) -> None:
    """请求里显式给出的 BPM/调性优先于音频解析结果。"""
    import app.agent.routes as agent_routes

    graph = FakeGraph()
    monkeypatch.setattr(agent_routes, "build_compose_graph", lambda: graph)
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")
    monkeypatch.setattr(agent_routes.mcp_audio, "analyze_audio_file", fake_analysis)

    create_client(db).post(
        "/api/v1/agent/compose",
        data={"request_json": json.dumps({"key": "G", "bpm": 90})},
        files={"audio": ("hook.wav", b"RIFFfake", "audio/wav")},
    )

    features = graph.inputs["features"]
    assert features["key"] == "G"
    assert features["bpm"] == 90
    # 未显式给出的 mode 仍来自解析
    assert features["mode"] == "major"


def test_compose_analysis_failure_falls_back(monkeypatch, db) -> None:
    """解析失败（服务不可用/解析不出）→ 特征清单退回请求参数。"""
    import app.agent.routes as agent_routes

    async def broken(_path: str) -> dict[str, Any] | None:
        return None

    graph = FakeGraph()
    monkeypatch.setattr(agent_routes, "build_compose_graph", lambda: graph)
    monkeypatch.setattr(agent_routes, "retrieve_context", lambda *a, **k: "")
    monkeypatch.setattr(agent_routes.mcp_audio, "analyze_audio_file", broken)

    response = create_client(db).post(
        "/api/v1/agent/compose",
        data={"request_json": json.dumps({"key": "C", "bpm": 96})},
        files={"audio": ("hook.wav", b"RIFFfake", "audio/wav")},
    )

    assert response.status_code == 200
    features = graph.inputs["features"]
    assert features["bpm"] == 96
    assert features["key"] == "C"
    events = parse_sse(response.text)
    analysis_event = next(
        d for n, d in events if n == "stage" and d["node"] == "analysis"
    )
    assert "不可用" in analysis_event["detail"]


def test_compose_rejects_non_audio_upload(db) -> None:
    response = create_client(db).post(
        "/api/v1/agent/compose",
        data={"request_json": json.dumps({"key": "C"})},
        files={"audio": ("evil.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 415
    assert "不支持的音频格式" in response.json()["detail"]
