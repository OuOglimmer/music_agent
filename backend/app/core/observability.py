"""全链路可观测性矩阵日志系统（零第三方依赖，标准库实现）。

三个层次：
1. **追踪（Trace/Span）**：ContextVar 贯穿 trace_id 与父子 span，
   ``traced()`` 装饰器 / ``traced_span()`` 上下文管理器在任意层埋点
   （HTTP → Agent 节点 → LLM → 工具 → RAG → MCP → 音频），
   完整 span 树存入环形缓冲，可按 trace_id 回放整条请求链路。
2. **矩阵日志（Structured Logs）**：JSON 结构化日志，每行携带
   trace_id / 事件 / 耗时 / 结果等矩阵字段，机器可聚合、人可读。
3. **指标（Metrics）**：内存计数器 + 延迟直方图 + 路由×阶段耗时矩阵，
   ``/observability/metrics`` 暴露给监控系统抓取。

并发安全：span/指标写入用 ``threading.Lock`` 保护（FastAPI 线程池 +
asyncio 混合执行），锁从不跨 await 持有。
"""

import json
import logging
import threading
import time
import uuid
from asyncio import CancelledError
from collections import Counter, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import wraps
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
_current_span: ContextVar[Span | None] = ContextVar("current_span", default=None)

# ---------------------------------------------------------------------------
# Span 与 Trace 模型
# ---------------------------------------------------------------------------


@dataclass
class Span:
    """一个最小执行单元：有名字、有耗时、有结果，挂在某个 trace 下。"""

    name: str
    parent_id: str = ""
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: float = field(default_factory=time.perf_counter)
    wall_start: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    duration_ms: float = -1.0  # 结束时写入；-1 表示尚未结束
    outcome: str = "running"  # running / ok / error / cancelled
    attributes: dict[str, Any] = field(default_factory=dict)

    def finish(self, outcome: str = "ok") -> None:
        self.duration_ms = round((time.perf_counter() - self.started_at) * 1000, 2)
        self.outcome = outcome

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "started_at": self.wall_start,
            "duration_ms": self.duration_ms,
            "outcome": self.outcome,
            **self.attributes,
        }


@dataclass
class Trace:
    """一次请求的完整链路：入口信息 + 全部 span（按开始时间排序）。"""

    trace_id: str
    method: str = ""
    path: str = ""
    status_code: int = 0
    user_id: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    duration_ms: float = -1.0
    outcome: str = "running"
    spans: list[Span] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        stages = [
            {"name": s.name, "duration_ms": s.duration_ms, "outcome": s.outcome}
            for s in self.spans
        ]
        return {
            "trace_id": self.trace_id,
            "method": self.method,
            "path": self.path,
            "status_code": self.status_code,
            "user_id": self.user_id,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "outcome": self.outcome,
            "span_count": len(self.spans),
            "stages": stages,
        }

    def detail(self) -> dict[str, Any]:
        data = self.summary()
        data["spans"] = [s.to_dict() for s in self.spans]
        return data


class TraceStore:
    """trace 环形缓冲：只保留最近 max_traces 条完整链路。"""

    def __init__(self, max_traces: int = 512) -> None:
        self._buf: deque[Trace] = deque(maxlen=max_traces)
        self._index: dict[str, Trace] = {}
        self._lock = threading.Lock()

    def start(self, trace_id: str, method: str, path: str, user_id: str = "") -> Trace:
        trace = Trace(trace_id=trace_id, method=method, path=path, user_id=user_id)
        with self._lock:
            evicted = None
            if len(self._buf) == self._buf.maxlen:
                evicted = self._buf[0]
            self._buf.append(trace)
            self._index[trace_id] = trace
            if evicted is not None:
                self._index.pop(evicted.trace_id, None)
        return trace

    def finish(self, trace: Trace, status_code: int, outcome: str) -> None:
        trace.status_code = status_code
        trace.outcome = outcome
        started = datetime.fromisoformat(trace.started_at).timestamp()
        trace.duration_ms = round((time.time() - started) * 1000, 2)

    def get(self, trace_id: str) -> Trace | None:
        with self._lock:
            return self._index.get(trace_id)

    def recent(self, limit: int = 50) -> list[Trace]:
        with self._lock:
            items = list(self._buf)
        return list(reversed(items))[:limit]

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
            self._index.clear()


trace_store = TraceStore()


def set_trace_user(user_id: str) -> None:
    """认证完成后把 user_id 关联到当前 trace（排障时按用户回放链路）。"""
    trace = trace_store.get(trace_id_var.get())
    if trace is not None and not trace.user_id:
        trace.user_id = user_id


# ---------------------------------------------------------------------------
# span 埋点：上下文管理器 + 装饰器
# ---------------------------------------------------------------------------


@contextmanager
def traced_span(name: str, **attributes: Any) -> Iterator[Span]:
    """记录一个子 span：自动挂到当前 trace 与父 span，结束写耗时与结果。

    注意：这是**同步**上下文管理器（内部无 await），在 async 函数里
    也用 ``with``（而非 ``async with``），内部照常可以 await。

    用法::

        with traced_span("rag.retrieve", top_k=6) as span:
            docs = retrieve(...)
            span.attributes["chunks"] = len(docs)
    """
    parent = _current_span.get()
    span = Span(name=name, parent_id=parent.span_id if parent else "")
    span.attributes.update(attributes)
    trace = trace_store.get(trace_id_var.get())
    if trace is not None:
        trace.spans.append(span)
    token: Token[Span | None] = _current_span.set(span)
    try:
        yield span
    except BaseException as exc:
        span.finish(
            "cancelled"
            if isinstance(exc, (KeyboardInterrupt, CancelledError))
            else "error"
        )
        metrics.incr("errors")
        raise
    else:
        span.finish("ok")
    finally:
        _current_span.reset(token)


def traced(name: str, **attributes: Any) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """同步/异步函数埋点装饰器，记录耗时、结果与异常。"""

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        if _is_coroutine(func):

            @wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                with traced_span(name, **attributes):
                    return await func(*args, **kwargs)

            async_wrapper.__wrapped_span__ = name  # type: ignore[attr-defined]
            return async_wrapper  # type: ignore[return-value]

        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            with traced_span(name, **attributes):
                return func(*args, **kwargs)

        sync_wrapper.__wrapped_span__ = name  # type: ignore[attr-defined]
        return sync_wrapper  # type: ignore[return-value]

    return decorator


def _is_coroutine(func: Callable[..., Any]) -> bool:
    import inspect

    return inspect.iscoroutinefunction(func)


# ---------------------------------------------------------------------------
# 指标：计数器 / 延迟直方图 / 路由×阶段矩阵
# ---------------------------------------------------------------------------

_METRIC_SAMPLES_CAP = 512


class Metrics:
    """进程内指标注册表：counters + 直方图 + 矩阵（route×stage）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: Counter[str] = Counter()
        self.counter_dims: dict[str, Counter[str]] = {}
        self.histograms: dict[str, list[float]] = {}
        self.matrix: dict[str, dict[str, dict[str, Any]]] = {}

    def incr(
        self, name: str, value: int = 1, dim: tuple[str, str] | None = None
    ) -> None:
        with self._lock:
            self.counters[name] += value
            if dim:
                key, label = dim
                self.counter_dims.setdefault(key, Counter())[label] += value

    def observe(self, name: str, duration_ms: float) -> None:
        with self._lock:
            samples = self.histograms.setdefault(name, [])
            samples.append(duration_ms)
            if len(samples) > _METRIC_SAMPLES_CAP:
                del samples[: len(samples) - _METRIC_SAMPLES_CAP]

    def observe_matrix(
        self, route: str, stage: str, duration_ms: float, error: bool
    ) -> None:
        with self._lock:
            row = self.matrix.setdefault(route, {})
            cell = row.setdefault(
                stage,
                {
                    "count": 0,
                    "errors": 0,
                    "total_ms": 0.0,
                    "p50_ms": 0.0,
                    "p95_ms": 0.0,
                },
            )
            cell["count"] += 1
            cell["errors"] += int(error)
            cell["total_ms"] = round(cell["total_ms"] + duration_ms, 2)
            samples = cell.setdefault("_samples", [])
            samples.append(duration_ms)
            if len(samples) > _METRIC_SAMPLES_CAP:
                del samples[: len(samples) - _METRIC_SAMPLES_CAP]
            cell["p50_ms"] = _percentile(samples, 50)
            cell["p95_ms"] = _percentile(samples, 95)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self.counters)
            dims = {k: dict(v) for k, v in self.counter_dims.items()}
            latency = {
                name: {
                    "count": len(samples),
                    "p50_ms": _percentile(samples, 50),
                    "p95_ms": _percentile(samples, 95),
                    "max_ms": round(max(samples), 2) if samples else 0.0,
                }
                for name, samples in self.histograms.items()
            }
            matrix = {
                route: {
                    stage: {k: v for k, v in cell.items() if k != "_samples"}
                    for stage, cell in row.items()
                }
                for route, row in self.matrix.items()
            }
        return {
            "counters": counters,
            "counter_dims": dims,
            "latency": latency,
            "matrix": matrix,
        }


def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return round(ordered[rank], 2)


metrics = Metrics()


# ---------------------------------------------------------------------------
# 矩阵日志：JSON 结构化 + trace_id 注入
# ---------------------------------------------------------------------------

_RESERVED_LOG_KEYS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
    "asctime",
}


class TraceIdFilter(logging.Filter):
    """把 trace_id / 当前 span 塞进每条 LogRecord，供 Formatter 输出。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_var.get()  # type: ignore[attr-defined]
        span = _current_span.get()
        record.span = span.name if span else ""  # type: ignore[attr-defined]
        return True


class JsonFormatter(logging.Formatter):
    """把日志行输出为单行 JSON：标准字段 + 矩阵字段（extra 透传）。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "trace_id", ""),
            "span": getattr(record, "span", ""),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_KEYS and key not in payload:
                payload[key] = _json_safe(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
    except TypeError, ValueError:
        return str(value)
    return value


def configure_logging(level: int = logging.INFO) -> None:
    """root logger 切换为 JSON 结构化输出（幂等，可重复调用）。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_observability_json", False):
            return  # 已初始化
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler._observability_json = True  # type: ignore[attr-defined]
    handler.addFilter(TraceIdFilter())
    root.handlers = [handler]
    root.setLevel(level)


def log_event(level: int, event: str, **fields: Any) -> None:
    """带矩阵字段的结构化事件日志：log_event(logging.INFO, "rag.hit", chunks=3)。"""
    logging.getLogger("app.observability").log(
        level, event, extra={"event": event, **fields}
    )


# ---------------------------------------------------------------------------
# HTTP 中间件：trace 入口 + 出口
# ---------------------------------------------------------------------------


class ObservabilityMiddleware:
    """纯 ASGI 中间件：生成/接续 trace_id，记录入口 span 与请求指标。

    客户端可携带 ``X-Request-ID`` 接续链路（网关/前端透传），
    响应头回传 ``X-Trace-ID`` 便于排障关联。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        incoming = headers.get("x-request-id", "")
        trace_id = incoming[:64] or uuid.uuid4().hex
        token = trace_id_var.set(trace_id)
        method = scope.get("method", "")
        path = scope.get("path", "")
        user_id = str(scope.get("state", {}).get("user_id", "") or "")

        trace = trace_store.start(trace_id, method, path, user_id)
        status_holder = {"status": 0}
        start = time.perf_counter()
        outcome = "ok"

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers_list = list(message.get("headers", []))
                headers_list.append((b"x-trace-id", trace_id.encode()))
                message["headers"] = headers_list
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            outcome = "error"
            raise
        finally:
            trace_id_var.reset(token)
            status = status_holder["status"]
            trace_store.finish(trace, status or 500, outcome)
            duration_ms = (time.perf_counter() - start) * 1000
            metrics.incr("http.requests")
            metrics.observe("http.request.duration", duration_ms)
            if outcome == "error" or status >= 500:
                metrics.incr("http.errors")
            log_event(
                logging.INFO if status and status < 500 else logging.ERROR,
                "http.request",
                method=method,
                path=path,
                status=status,
                duration_ms=round(duration_ms, 2),
                outcome=outcome,
            )


# ---------------------------------------------------------------------------
# 后台任务便捷入口（供 SSE 等无 HTTP 入口的链路手动开 trace）
# ---------------------------------------------------------------------------


@contextmanager
def manual_trace(
    trace_id: str, name: str = "background", **attributes: Any
) -> Iterator[Span]:
    """为无 HTTP 上下文的后台任务开一个临时 trace（如流式创作收尾）。

    内部无 await，同步上下文管理器即可同时服务 sync/async 场景。
    """
    trace = trace_store.start(trace_id, "TASK", name)
    token = trace_id_var.set(trace_id)
    try:
        with traced_span(name, **attributes) as span:
            yield span
    except BaseException:
        trace_store.finish(trace, 0, "error")
        raise
    else:
        trace_store.finish(trace, 0, "ok")
    finally:
        trace_id_var.reset(token)
