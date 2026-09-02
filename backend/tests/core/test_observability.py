"""全链路可观测性矩阵日志系统测试：span 树、指标矩阵、JSON 日志、中间件。"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.core import observability as obs
from app.core.observability import (
    JsonFormatter,
    Metrics,
    configure_logging,
    trace_id_var,
    trace_store,
    traced_span,
)


@pytest.fixture(autouse=True)
def _clean_store():
    trace_store.clear()
    yield
    trace_store.clear()


def test_spans_nest_under_trace_with_parent_ids() -> None:
    with obs.manual_trace("t1", "root") as root_span:
        with traced_span("child", k=1) as child:
            with traced_span("grandchild"):
                pass
        child.finish("ok")  # 幂等：重复 finish 不崩溃（重复调用只覆盖）

    trace = trace_store.get("t1")
    assert trace is not None
    names = [s.name for s in trace.spans]
    assert names == ["root", "child", "grandchild"]
    by_name = {s.name: s for s in trace.spans}
    assert by_name["child"].parent_id == root_span.span_id
    assert by_name["grandchild"].parent_id == child.span_id
    assert all(s.duration_ms >= 0 for s in trace.spans)
    assert trace.outcome == "ok"
    assert trace.duration_ms >= 0


def test_span_error_records_outcome_and_metric() -> None:
    before = obs.metrics.counters["errors"]
    with pytest.raises(ValueError, match="boom"):
        with obs.manual_trace("t2", "root"):
            with traced_span("failing"):
                raise ValueError("boom")
    trace = trace_store.get("t2")
    assert trace is not None
    failing = next(s for s in trace.spans if s.name == "failing")
    assert failing.outcome == "error"
    root = next(s for s in trace.spans if s.name == "root")
    assert root.outcome == "error"
    # 每个失败的 span 各计一次错误（failing + root）
    assert obs.metrics.counters["errors"] == before + 2


def test_metrics_percentiles_and_matrix() -> None:
    m = Metrics()
    for value in [10.0, 20.0, 30.0, 40.0, 100.0]:
        m.observe("agent.llm", value)
        m.observe_matrix("/chat", "llm", value, error=value > 90)
    snap = m.snapshot()
    assert snap["latency"]["agent.llm"]["count"] == 5
    assert snap["latency"]["agent.llm"]["max_ms"] == 100.0
    cell = snap["matrix"]["/chat"]["llm"]
    assert cell["count"] == 5
    assert cell["errors"] == 1
    assert cell["p50_ms"] == 30.0
    assert cell["p95_ms"] == 100.0
    assert "_samples" not in cell  # 内部采样数组不外泄


def test_json_log_carries_trace_and_matrix_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_logging()
    with caplog.at_level(logging.INFO):
        with obs.manual_trace("t3", "op", route="/x"):
            logging.getLogger("app.test").info(
                "hello", extra={"event": "rag.hit", "chunks": 3}
            )
    record = next(r for r in caplog.records if r.getMessage() == "hello")
    assert record.trace_id == "t3"  # type: ignore[attr-defined]
    assert record.span == "op"  # type: ignore[attr-defined]
    assert record.event == "rag.hit"  # type: ignore[attr-defined]
    assert record.chunks == 3  # type: ignore[attr-defined]

    # 整行 JSON 输出也应携带矩阵字段（JsonFormatter 的最终形态）
    import json

    formatter = JsonFormatter()
    payload = json.loads(formatter.format(record))
    assert payload["trace_id"] == "t3"
    assert payload["event"] == "rag.hit"
    assert payload["chunks"] == 3


def test_json_formatter_is_json_safe() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "msg", None, None)
    record.obj = object()  # 不可 JSON 序列化的对象应降级为 str
    import json

    payload = json.loads(formatter.format(record))
    assert isinstance(payload["obj"], str)


def test_middleware_adds_trace_header_and_records_trace(client: TestClient) -> None:
    response = client.get("/docs")
    assert response.status_code == 200
    trace_id = response.headers.get("x-trace-id")
    assert trace_id
    trace = trace_store.get(trace_id)
    assert trace is not None
    assert trace.path == "/docs"
    assert trace.outcome == "ok"


def test_middleware_honors_incoming_request_id(client: TestClient) -> None:
    response = client.get("/docs", headers={"X-Request-ID": "gw-abc-123"})
    assert response.headers.get("x-trace-id") == "gw-abc-123"
    assert trace_store.get("gw-abc-123") is not None


def test_manual_trace_outcome_error() -> None:
    with pytest.raises(RuntimeError):
        async_bg = obs.manual_trace("t9", "bg")
        with async_bg:
            raise RuntimeError("x")
    assert trace_store.get("t9").outcome == "error"  # type: ignore[union-attr]


def test_trace_id_var_default_empty() -> None:
    assert trace_id_var.get() == ""
