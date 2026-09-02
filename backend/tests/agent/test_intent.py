"""意图识别模块测试：规则通道、敏感检测、LLM 输出解析与升级判定。"""

import asyncio

from app.agent.intent import (
    INTENT_LABELS,
    IntentResult,
    _parse_llm_verdict,
    classify_by_rules,
    classify_intent,
    escalation_reason,
    needs_escalation,
)


def test_rules_hit_lyric_writing() -> None:
    result = classify_by_rules("帮我写一段歌词，要有押韵")
    assert result.name == "lyric_writing"
    assert result.confidence >= 0.75
    assert not result.sensitive
    assert result.matched


def test_rules_multiple_hits_raise_confidence() -> None:
    single = classify_by_rules("我想写歌词")
    multi = classify_by_rules("写歌词 押韵 主歌 副歌 hook")
    assert multi.confidence > single.confidence


def test_rules_no_hit_returns_low_confidence_chat() -> None:
    result = classify_by_rules("今天天气不错")
    assert result.name == "chat"
    assert result.confidence < 0.55


def test_sensitive_detection_flags_and_boosts_confidence() -> None:
    result = classify_by_rules("我心情很差，不想活了")
    assert result.sensitive
    assert result.confidence == 1.0
    assert "不想活" in result.reason


def test_parse_llm_verdict_valid_json() -> None:
    verdict = _parse_llm_verdict(
        '{"intent": "arrangement", "confidence": 0.9, "sensitive": false}'
    )
    assert verdict is not None
    assert verdict.name == "arrangement"
    assert verdict.method == "llm"


def test_parse_llm_verdict_rejects_unknown_intent() -> None:
    assert _parse_llm_verdict('{"intent": "dancing", "confidence": 0.9}') is None
    assert _parse_llm_verdict("不是 JSON") is None


def test_parse_llm_verdict_clamps_confidence() -> None:
    verdict = _parse_llm_verdict('{"intent": "chat", "confidence": 9.9}')
    assert verdict is not None
    assert verdict.confidence == 1.0


def test_intent_label_fallback() -> None:
    assert IntentResult(name="chat", confidence=0.5).label == INTENT_LABELS["chat"]
    assert IntentResult(name="nope", confidence=0.5).label == "nope"


def test_needs_escalation_policy(monkeypatch) -> None:
    from app.agent.config import config

    monkeypatch.setattr(config, "intent_confidence_threshold", 0.55)
    monkeypatch.setattr(config, "enable_auto_escalation", True)

    # 敏感内容一律升级
    sensitive = IntentResult(name="chat", confidence=0.9, sensitive=True)
    assert needs_escalation(sensitive)
    assert escalation_reason(sensitive)

    # 明确转人工一律升级
    handoff = IntentResult(name="human_handoff", confidence=0.9)
    assert needs_escalation(handoff)
    assert escalation_reason(handoff) == "用户明确要求转人工"

    # 低置信闲聊是正常现象，不升级
    low_chat = IntentResult(name="chat", confidence=0.35)
    assert not needs_escalation(low_chat)

    # 非闲聊意图置信度过低才升级
    low_arrangement = IntentResult(name="arrangement", confidence=0.35)
    assert needs_escalation(low_arrangement)
    assert "置信度过低" in escalation_reason(low_arrangement)

    high = IntentResult(name="lyric_writing", confidence=0.9)
    assert not needs_escalation(high)

    # 关闭自动升级后，低置信不再触发（敏感/转人工仍升级）
    monkeypatch.setattr(config, "enable_auto_escalation", False)
    assert not needs_escalation(low_arrangement)
    assert needs_escalation(sensitive)
    assert needs_escalation(handoff)


def test_classify_intent_falls_back_to_rules_without_llm(monkeypatch) -> None:
    from app.agent.config import config

    monkeypatch.setattr(config, "enable_llm_intent", False)
    result = asyncio.run(classify_intent("帮我写歌词"))
    assert result.name == "lyric_writing"
    assert result.method == "rules"


def test_classify_intent_empty_text() -> None:
    result = asyncio.run(classify_intent("   "))
    assert result.name == "chat"
    assert result.confidence == 0.0
