"""上下文工程测试：偏好抽取、预算装配优先级、滚动摘要触发条件。"""

from app.agent.memory import (
    _pending_turns,
    build_context_block,
    estimate_tokens,
    extract_preferences,
    should_refresh_memory,
)
from app.models import SessionMemory


def test_estimate_tokens_counts_characters() -> None:
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("") == 0


def test_extract_preferences_merges_and_dedupes() -> None:
    first = extract_preferences(["我喜欢抒情的歌", "不喜欢电子鼓点"])
    assert any("喜欢抒情" in line for line in first)
    assert any("避免" in line and "电子鼓点" in line for line in first)

    merged = extract_preferences(["我喜欢抒情慢歌"], existing="\n".join(first))
    # 原有偏好保留，新增不重复
    assert len(merged) >= len(first)
    assert len({*merged}) == len(merged)


def test_extract_preferences_caps_at_20() -> None:
    # 用互不为子串的语句，避免去重逻辑吞掉合法条目
    contents = [f"我喜欢数字{i}号音符" for i in range(40)]
    assert len(extract_preferences(contents)) == 20


def test_build_context_block_empty() -> None:
    assert build_context_block(None) == ""


def test_build_context_block_priority_order() -> None:
    memory = SessionMemory(
        session_id="00000000-0000-0000-0000-000000000000",
        summary="早前聊了毕业歌需求",
        preferences="喜欢抒情",
    )
    block = build_context_block(
        memory,
        rag_context="钢琴和弦进行资料",
        reviewer_guidance="先安抚用户情绪",
    )
    positions = {
        key: block.find(key)
        for key in ("人工坐席指导", "会话摘要", "用户偏好", "知识库检索资料")
    }
    assert all(v >= 0 for v in positions.values())
    order = sorted(positions.items(), key=lambda kv: kv[1])
    assert [name for name, _ in order] == [
        "人工坐席指导",
        "会话摘要",
        "用户偏好",
        "知识库检索资料",
    ]


def test_build_context_block_respects_budget() -> None:
    memory = SessionMemory(
        session_id="00000000-0000-0000-0000-000000000000",
        summary="长" * 5000,
    )
    block = build_context_block(memory, budget=500)
    assert estimate_tokens(block) <= 500 + len("[会话摘要（更早的对话）] ") + 3
    assert "…" in block  # 截断标记存在


def test_pending_turns_and_should_refresh() -> None:
    # 14 条消息、窗口 12：2 条待摘要
    assert _pending_turns(14, 0, 12) == 2
    # 已摘要 2 条、总数未增长：无需刷新
    assert _pending_turns(14, 2, 12) == 0
    # 总数涨到 16：又有 2 条待摘要
    assert _pending_turns(16, 2, 12) == 2

    assert should_refresh_memory(None, 20, 12)
    assert not should_refresh_memory(None, 5, 12)
    memory = SessionMemory(
        session_id="00000000-0000-0000-0000-000000000000",
        summarized_count=8,
    )
    assert not should_refresh_memory(memory, 20, 12)
    assert should_refresh_memory(memory, 25, 12)
