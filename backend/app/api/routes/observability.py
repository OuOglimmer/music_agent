"""全链路可观测性查询接口（仅超级管理员）。

- GET /observability/metrics   指标快照：计数器、延迟直方图、路由×阶段矩阵
- GET /observability/traces    最近请求链路摘要
- GET /observability/traces/{trace_id}  单条链路完整 span 树
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_active_superuser
from app.core.observability import metrics, trace_store

router = APIRouter(dependencies=[Depends(get_current_active_superuser)])


@router.get("/metrics", summary="指标快照（计数器/延迟/路由×阶段矩阵）")
def get_metrics() -> dict:
    return metrics.snapshot()


@router.get("/traces", summary="最近请求链路（环形缓冲内，新的在前）")
def list_traces(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    traces = trace_store.recent(limit)
    return {"count": len(traces), "data": [t.summary() for t in traces]}


@router.get("/traces/{trace_id}", summary="单条链路完整 span 树")
def get_trace(trace_id: str) -> dict:
    trace = trace_store.get(trace_id)
    if trace is None:
        raise HTTPException(
            status_code=404, detail="Trace not found (expired or unknown)"
        )
    return trace.detail()
