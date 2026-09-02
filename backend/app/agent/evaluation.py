"""评估闭环：LLM-as-judge 对助手回复做离线质量评估。

评估对象：最近落库的助手消息（连同其对应的用户提问与意图标记）。
评分维度（各 0-10）：
- relevance：相关性——是否回答了用户的问题；
- professionalism：专业性——音乐知识/建议是否正确、内行；
- actionability：可执行性——用户能否据此直接行动（投喂 AI 音乐工具等）；
- clarity：表达清晰度——零基础用户能否看懂。

产出：单样本评分 + 聚合报告（总体均分、按意图/提示词版本分组），
持久化到 agent_evaluation_run 表。聚合结果配合反馈统计
（/evaluations/summary）定位薄弱意图，由人工坐席沉淀为改进项注入
系统提示词，形成"评估 → 改进 → 再评估"的闭环。
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, select

from app.core.observability import metrics
from app.models import AgentMessage, EvaluationRun

logger = logging.getLogger(__name__)

DIMENSIONS = ("relevance", "professionalism", "actionability", "clarity")

JUDGE_SYSTEM_PROMPT = """\
你是音乐创作助手回复的质量评审员。对照用户提问，从四个维度给助手回复打分（0-10 整数）：
- relevance（相关性）：是否针对用户的问题与需求作答；
- professionalism（专业性）：音乐知识、参数与建议是否正确、内行；
- actionability（可执行性）：用户能否直接照做（如把风格提示词粘贴到 AI 音乐工具）；
- clarity（清晰度）：面向零基础用户是否通俗、结构清楚。

同时给出 overall（综合分 0-10）与一句 comment（指出最主要的问题或优点）。

只输出 JSON，格式：
{"relevance": <int>, "professionalism": <int>, "actionability": <int>, "clarity": <int>, "overall": <int>, "comment": "<一句话>"}\
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_judge_output(raw: str) -> dict[str, object] | None:
    """解析评审 JSON 输出；非法或缺失维度时返回 None。"""
    match = _JSON_RE.search(raw or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    scores: dict[str, int] = {}
    for key in (*DIMENSIONS, "overall"):
        raw_value = data.get(key)
        if raw_value is None:
            return None
        try:
            value = int(float(raw_value))
        except TypeError, ValueError:
            return None
        scores[key] = max(0, min(10, value))
    comment = str(data.get("comment", ""))[:300]
    return {**scores, "comment": comment}


async def judge_sample(
    llm: Any, user_text: str, assistant_text: str, intent: str | None
) -> dict[str, Any] | None:
    """对单个样本打分；LLM 失败/输出非法返回 None（计入 failed）。"""
    user_block = user_text or "（无文字输入，用户上传了文件）"
    payload = (
        f"【用户意图】{intent or '未知'}\n"
        f"【用户提问】{user_block[:1500]}\n"
        f"【助手回复】{assistant_text[:3000]}"
    )
    try:
        response = await llm.ainvoke(
            [("system", JUDGE_SYSTEM_PROMPT), ("human", payload)]
        )
    except Exception as exc:
        logger.warning("Judge LLM call failed: %s", exc)
        return None
    content = response.content
    if isinstance(content, list):
        content = "".join(str(part) for part in content)
    return parse_judge_output(str(content))


def _load_samples(db: Session, limit: int) -> list[dict[str, Any]]:
    """取最近的助手消息及其对应的用户提问，作为评估样本。"""
    assistant_rows = db.exec(
        select(AgentMessage)
        .where(AgentMessage.role == "assistant")
        .order_by(col(AgentMessage.created_at).desc())
        .limit(limit)
    ).all()
    samples: list[dict[str, Any]] = []
    for row in reversed(assistant_rows):
        user_row = db.exec(
            select(AgentMessage)
            .where(
                AgentMessage.session_id == row.session_id,
                AgentMessage.role == "user",
                col(AgentMessage.created_at) <= (row.created_at or datetime.now(UTC)),
            )
            .order_by(col(AgentMessage.created_at).desc())
        ).first()
        samples.append(
            {
                "message_id": str(row.id),
                "session_id": str(row.session_id),
                "intent": row.intent,
                "prompt_version": row.prompt_version,
                "assistant_text": row.content,
                "user_text": user_row.content if user_row else "",
            }
        )
    return samples


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总单样本评分为聚合报告（总体 + 按意图 + 按提示词版本）。"""
    judged = [r for r in results if r.get("scores") is not None]

    def _avg(values: list[float]) -> float:
        return round(sum(values) / len(values), 2) if values else 0.0

    dimensions = {
        dim: _avg([float(r["scores"][dim]) for r in judged])
        for dim in (*DIMENSIONS, "overall")
    }

    def _group_by(key: str) -> dict[str, float]:
        groups: dict[str, list[float]] = {}
        for r in judged:
            group = str(r.get(key) or "unknown")
            score = float(r["scores"]["overall"])
            groups.setdefault(group, []).append(score)
        return {name: _avg(scores) for name, scores in sorted(groups.items())}

    return {
        "sample_count": len(results),
        "judged_count": len(judged),
        "failed_count": len(results) - len(judged),
        "dimensions_avg": dimensions,
        "overall_avg": dimensions["overall"],
        "by_intent": _group_by("intent"),
        "by_prompt_version": _group_by("prompt_version"),
    }


async def run_evaluation(
    db: Session, limit: int, triggered_by_id: object | None = None
) -> dict[str, Any]:
    """执行一次评估：抽样 → 打分 → 聚合 → 落库，返回完整报告。"""
    from app.agent.agent_core import agent
    from app.agent.config import config

    samples = _load_samples(db, limit)
    results: list[dict[str, Any]] = []
    if samples and config.api_key:
        llm = agent.get_llm(temperature=0.0, max_tokens=500)
        for sample in samples:
            intent_value = sample["intent"]
            scores = await judge_sample(
                llm,
                str(sample["user_text"]),
                str(sample["assistant_text"]),
                str(intent_value) if intent_value else None,
            )
            results.append({**sample, "scores": scores})
    else:
        results = [{**sample, "scores": None} for sample in samples]

    aggregates = _aggregate(results)
    run = EvaluationRun(
        sample_count=len(results),
        average_score=float(aggregates["overall_avg"]),
        aggregates=json.dumps(aggregates, ensure_ascii=False),
        results=json.dumps(results, ensure_ascii=False),
        triggered_by_id=triggered_by_id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    metrics.incr("agent.evaluation.runs", value=1)
    return _run_report(run)


def _run_report(run: EvaluationRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "sample_count": run.sample_count,
        "average_score": run.average_score,
        "aggregates": json.loads(run.aggregates or "{}"),
        "results": json.loads(run.results or "[]"),
        "created_at": (run.created_at or datetime.now(UTC)).isoformat(),
    }
