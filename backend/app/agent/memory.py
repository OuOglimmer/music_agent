"""上下文工程：会话记忆与上下文装配。

模型可见的上下文窗口有限（且越长越贵），本模块负责"该给模型看什么"：

1. **滚动摘要（SessionMemory.summary）**：超出近期窗口的旧对话压成一段
   摘要，保留主线（主题、已确认的需求、结论），避免长对话"失忆"；
2. **用户偏好（SessionMemory.preferences）**：规则抽取用户表达过的
   喜欢/不喜欢、提到的风格等稳定信息，跨轮次注入，不用用户反复说明；
3. **预算装配（build_context_block）**：人工坐席指导 > 会话摘要 >
   用户偏好 > RAG 检索资料 的优先级，按 token 预算裁剪后拼成
   参考资料块，交给 Agent 的 context 参数。

摘要更新在每轮对话落库后以后台任务执行（summarize_and_store），
LLM 调用失败只影响摘要新鲜度，不影响对话本身。
"""

import logging
import re
from datetime import UTC, datetime

from sqlmodel import Session as SQLModelSession
from sqlmodel import col, select

from app.agent.config import config
from app.core.db import engine
from app.models import AgentMessage, SessionMemory

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中英文混排按字符数近似（略保守，够用）。"""
    return len(text or "")


def _fit(text: str, budget: int) -> str:
    """把文本裁剪到预算内：保留开头，超出部分截断并标注。"""
    if estimate_tokens(text) <= budget:
        return text
    return text[: max(0, budget - 3)] + "…"


def get_or_create_memory(db: SQLModelSession, session_id: object) -> SessionMemory:
    row = db.exec(
        select(SessionMemory).where(SessionMemory.session_id == session_id)
    ).first()
    if row is None:
        row = SessionMemory(session_id=session_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# 用户偏好：规则抽取（零成本，每轮后台运行）
# ---------------------------------------------------------------------------

_LIKE_RE = re.compile(r"(?:喜欢|偏好|想要|希望|最好是)[^。！？!?\n，,]{1,30}")
_DISLIKE_RE = re.compile(r"(?:不喜欢|讨厌|不要|别用)[^。！？!?\n，,]{1,30}")


def extract_preferences(user_contents: list[str], existing: str = "") -> list[str]:
    """从用户消息中抽取偏好语句，去重合并到已有偏好列表。

    避免/不喜欢优先："不喜欢电子鼓点"也会命中"喜欢…"的正向规则，
    被避免项包含的正向匹配一律剔除。
    """
    found: list[str] = []
    for content in user_contents:
        dislikes = _DISLIKE_RE.findall(content)
        for match in dislikes:
            found.append("（避免）" + match.strip())
        for match in _LIKE_RE.findall(content):
            if any(match.strip() in dislike for dislike in dislikes):
                continue
            found.append(match.strip())
    lines = [line for line in existing.splitlines() if line.strip()]
    for item in found:
        if not any(item in line or line in item for line in lines):
            lines.append(item)
    return lines[:20]


# ---------------------------------------------------------------------------
# 上下文装配：按优先级与预算拼装参考资料块
# ---------------------------------------------------------------------------

_CONTEXT_HEADER = "以下是本对话的背景信息，与问题相关时请优先依据它们回答："


def build_context_block(
    memory: SessionMemory | None,
    rag_context: str = "",
    reviewer_guidance: str = "",
    budget: int | None = None,
) -> str:
    """装配注入对话的上下文块（不含系统提示词）。

    优先级：人工坐席指导 > 会话摘要 > 用户偏好 > RAG 资料。
    budget 为 None 时使用配置的 token 预算。
    """
    if budget is None:
        budget = config.context_token_budget

    sections: list[str] = []
    used = estimate_tokens(_CONTEXT_HEADER)

    def _take(title: str, body: str, weight: float) -> None:
        nonlocal used
        if not body.strip():
            return
        allowance = max(0, int((budget - used) * weight))
        trimmed = _fit(body.strip(), allowance)
        if not trimmed:
            return
        sections.append(f"[{title}] {trimmed}")
        used += estimate_tokens(trimmed)

    if reviewer_guidance.strip():
        sections.append(f"[人工坐席指导（请遵循）] {reviewer_guidance.strip()}")
        used += estimate_tokens(reviewer_guidance)
    if memory is not None and memory.summary.strip():
        _take("会话摘要（更早的对话）", memory.summary, weight=0.25)
    if memory is not None and memory.preferences.strip():
        _take("用户偏好（历史表达）", memory.preferences, weight=0.2)
    if rag_context.strip():
        _take("知识库检索资料", rag_context, weight=1.0)

    if not sections:
        return ""
    return _CONTEXT_HEADER + "\n" + "\n".join(sections)


# ---------------------------------------------------------------------------
# 滚动摘要：对话超过近期窗口后，把旧消息压成摘要（后台任务执行）
# ---------------------------------------------------------------------------

_SUMMARY_PROMPT = """\
请把以下音乐创作对话的旧记录压缩成一段摘要（300 字以内），保留：
1. 用户的核心需求与已确认的决策（风格/主题/用途/调式 BPM 等）；
2. 已经给出的关键结论（歌名、歌词主题、和弦进行等）；
3. 未解决的遗留问题。
只输出摘要正文，不要客套话。

对话记录：
"""


def _pending_turns(total_count: int, summarized_count: int, history_window: int) -> int:
    """需要新压缩的消息条数：把超出最近窗口、尚未摘要的部分纳入摘要。"""
    target = max(0, total_count - history_window)
    return max(0, target - summarized_count)


def should_refresh_memory(
    memory: SessionMemory | None, total_count: int, history_window: int
) -> bool:
    """是否需要刷新记忆：有消息被挤出近期窗口且尚未摘要时返回 True。"""
    summarized = memory.summarized_count if memory is not None else 0
    return _pending_turns(total_count, summarized, history_window) > 0


async def summarize_and_store(session_id: str, history_window: int) -> bool:
    """把超出窗口的旧对话滚动压缩进 SessionMemory（含偏好合并）。

    独立开数据库会话（设计为在后台任务中运行，请求会话此时可能已关闭）。
    任何失败都只记日志、返回 False，不影响主对话流程。
    """
    from app.agent.agent_core import agent

    try:
        with SQLModelSession(engine) as db:
            rows = db.exec(
                select(AgentMessage)
                .where(AgentMessage.session_id == session_id)
                .order_by(col(AgentMessage.created_at))
            ).all()
            if not rows:
                return False

            memory = get_or_create_memory(db, session_id)
            pending = _pending_turns(len(rows), memory.summarized_count, history_window)
            if pending <= 0:
                return False

            transcript = "\n".join(
                f"{'用户' if row.role == 'user' else '助手'}：{row.content[:500]}"
                for row in rows[: memory.summarized_count + pending]
            )

            summary = memory.summary
            if config.api_key:
                try:
                    llm = agent.get_llm(temperature=0.2, max_tokens=600)
                    response = await llm.ainvoke(
                        [("system", _SUMMARY_PROMPT + transcript[:6000])]
                    )
                    content = response.content
                    if isinstance(content, list):
                        content = "".join(str(part) for part in content)
                    if str(content).strip():
                        summary = str(content).strip()
                except Exception:
                    logger.warning("Session summary LLM call failed", exc_info=True)

            memory.summary = summary
            preference_source = [row.content for row in rows if row.role == "user"]
            memory.preferences = "\n".join(
                extract_preferences(preference_source, memory.preferences)
            )
            memory.summarized_count += pending
            memory.updated_at = datetime.now(UTC)
            db.add(memory)
            db.commit()
            return True
    except Exception:
        logger.warning("Session memory refresh failed", exc_info=True)
        return False
