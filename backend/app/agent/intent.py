"""对话意图识别：规则快通道 + LLM 精分类的双通道设计。

设计目标：
1. **规则通道（必经）**：关键词命中即可给出高置信结果，零成本、零延迟，
   任何情况下都可用（包括未配置 API Key 时）；
2. **LLM 通道（可选）**：规则置信度不足时，用一次低成本 LLM 调用做精细
   分类，超时/失败/解析失败一律回退规则结果 —— 意图识别永不阻塞对话；
3. **敏感检测独立于意图**：命中敏感词直接标记 sensitive 并触发人工介入，
   不依赖意图分类结果。

分类结果用于三处：提示词注入（prompt_templates.INTENT_GUIDES）、
人工升级（低置信/敏感 → review_case）、评估聚合（按意图统计反馈与评分）。
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

from app.agent.config import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 意图分类体系：key 是稳定标识（落库/聚合用），label 是中文展示名
# ---------------------------------------------------------------------------

INTENT_LABELS: dict[str, str] = {
    "lyric_writing": "歌词创作",
    "arrangement": "编曲与乐理",
    "compose_pipeline": "完整创作",
    "music_qa": "乐理问答",
    "human_handoff": "转人工",
    "feedback": "反馈与投诉",
    "chat": "闲聊与其他",
}

# 各意图的触发关键词（小写匹配；命中数越多置信度越高）
INTENT_KEYWORDS: dict[str, list[str]] = {
    "lyric_writing": [
        "写歌词",
        "写词",
        "歌词",
        "改词",
        "填词",
        "作词",
        "verse",
        "chorus",
        "主歌",
        "副歌",
        "hook",
        "押韵",
        "金句",
        "歌词润色",
        "词写",
    ],
    "arrangement": [
        "编曲",
        "和弦",
        "配器",
        "乐器",
        "bpm",
        "节奏",
        "调式",
        "调性",
        "转调",
        "和声",
        "进行",
        "style tags",
        "风格标签",
        "混音",
        "音色",
    ],
    "compose_pipeline": [
        "做一首",
        "写一首",
        "创作一首",
        "作一首",
        "生成一首",
        "完整创作",
        "写个歌",
        "写首歌",
        "帮我做歌",
        "做歌",
        "创作歌",
        "compose",
    ],
    "music_qa": [
        "什么是",
        "是什么意思",
        "为什么",
        "怎么理解",
        "区别",
        "乐理",
        "科普",
        "解释",
        "知识",
        "如何入门",
        "怎么学",
        "推荐学习",
        "五度圈",
    ],
    "human_handoff": [
        "转人工",
        "人工客服",
        "人工坐席",
        "找人",
        "找个真人",
        "客服",
        "真人回复",
        "人工服务",
    ],
    "feedback": [
        "不好用",
        "太难用",
        "不满意",
        "投诉",
        "差评",
        "胡说",
        "答非所问",
        " errors",
        "bug",
        "出错了",
        "报错",
        "垃圾",
        "什么破",
    ],
    "chat": [],
}

# 敏感词：命中即升级人工，独立于意图分类（覆盖违规请求与危机干预两类）
SENSITIVE_KEYWORDS: list[str] = [
    "自杀",
    "自残",
    "轻生",
    "不想活",
    "伤害自己",
    "暴力",
    "杀人",
    "恐怖",
    "炸弹",
    "枪械",
    "赌博",
    "毒品",
    "冰毒",
    "大麻",
    "裸体",
    "色情",
    "淫秽",
    "领导人",
    "政治敏感",
    "颠覆",
    "分裂国家",
    "洗钱",
    "诈骗教程",
    "黑客攻击",
]


@dataclass
class IntentResult:
    """一次意图识别的完整产出（可序列化后随 meta 事件下发）。"""

    name: str
    confidence: float
    method: str = "rules"  # rules / llm / fallback
    matched: list[str] = field(default_factory=list)
    sensitive: bool = False
    reason: str = ""  # 升级人工时的原因说明

    @property
    def label(self) -> str:
        return INTENT_LABELS.get(self.name, self.name)

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.name,
            "label": self.label,
            "confidence": round(self.confidence, 2),
            "method": self.method,
            "sensitive": self.sensitive,
        }


def _match_keywords(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    return [kw for kw in keywords if kw.lower() in lowered]


def _is_sensitive(text: str) -> list[str]:
    return _match_keywords(text, SENSITIVE_KEYWORDS)


def classify_by_rules(text: str) -> IntentResult:
    """规则通道：关键词计分，未命中任何意图时返回低置信的 chat。"""
    sensitive_hits = _is_sensitive(text)
    best_name, best_hits = "chat", 0
    for name, keywords in INTENT_KEYWORDS.items():
        if name == "chat":
            continue
        hits = _match_keywords(text, keywords)
        if len(hits) > best_hits:
            best_name, best_hits = name, len(hits)

    if best_hits == 0:
        result = IntentResult(
            name="chat",
            confidence=0.35,
            method="rules",
        )
    else:
        # 命中 1 个词 0.75，之后每多命中 1 个 +0.05，封顶 0.95
        result = IntentResult(
            name=best_name,
            confidence=min(0.95, 0.75 + 0.05 * (best_hits - 1)),
            method="rules",
        )

    result.matched = _match_keywords(text, INTENT_KEYWORDS.get(result.name, []))
    if sensitive_hits:
        result.sensitive = True
        result.confidence = 1.0
        result.reason = f"命中敏感词：{'、'.join(sensitive_hits[:5])}"
    return result


# ---------------------------------------------------------------------------
# LLM 通道：仅在规则置信度不足且已配置 API Key 时启用
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """\
你是音乐创作助手的意图分类器。把用户输入分类到以下意图之一：
- lyric_writing：写歌词、改歌词、润色歌词
- arrangement：编曲、和弦、配器、节奏、调式等乐理操作类问题
- compose_pipeline：要求完整创作一首歌（含曲风、结构、编曲的整体产出）
- music_qa：乐理知识问答、概念解释、学习建议
- human_handoff：用户明确要求转人工/找真人
- feedback：对产品或回答的抱怨、投诉、负反馈
- chat：闲聊或无法归入上述类别的输入

同时判断是否包含敏感内容（自残、暴力、违法、色情、政治敏感）。

只输出 JSON，格式：
{"intent": "<意图key>", "confidence": <0到1的数字>, "sensitive": <true|false>}\
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_llm_verdict(raw: str) -> IntentResult | None:
    """解析 LLM 的 JSON 裁决；非法输出返回 None（调用方回退规则结果）。"""
    match = _JSON_RE.search(raw or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    intent = str(data.get("intent", ""))
    if intent not in INTENT_LABELS:
        return None
    try:
        confidence = float(data.get("confidence", 0.5))
    except TypeError, ValueError:
        confidence = 0.5
    return IntentResult(
        name=intent,
        confidence=min(1.0, max(0.0, confidence)),
        method="llm",
        sensitive=bool(data.get("sensitive", False)),
    )


async def classify_with_llm(text: str) -> IntentResult | None:
    """LLM 精分类；内部不抛异常，任何失败返回 None。"""
    from app.agent.agent_core import agent

    try:
        llm = agent.get_llm(temperature=0.0, max_tokens=200)
        response = await llm.ainvoke(
            [
                ("system", _LLM_SYSTEM_PROMPT),
                ("human", text[:2000]),
            ]
        )
        content = response.content
        if isinstance(content, list):
            content = "".join(str(part) for part in content)
        return _parse_llm_verdict(str(content))
    except Exception as exc:
        logger.warning("LLM intent classification failed: %s", exc)
        return None


def _should_use_llm(rules: IntentResult) -> bool:
    if not (config.api_key and config.enable_llm_intent):
        return False
    if rules.sensitive:
        return False
    return rules.confidence < config.intent_confidence_threshold


async def classify_intent(text: str) -> IntentResult:
    """意图识别主入口：规则先行，置信度不足时走 LLM，失败回退规则。

    保证在任何配置/网络状态下都能返回结果，绝不阻塞或中断对话。
    """
    if not text.strip():
        return IntentResult(name="chat", confidence=0.0, method="rules")

    rules = classify_by_rules(text)
    if not _should_use_llm(rules):
        return rules

    try:
        verdict = await asyncio.wait_for(
            classify_with_llm(text), timeout=config.intent_llm_timeout
        )
    except TimeoutError:
        logger.warning(
            "LLM intent classification timed out after %ss", config.intent_llm_timeout
        )
        return rules
    if verdict is None:
        return rules
    if verdict.sensitive:
        verdict.reason = "LLM 判定含敏感内容"
        verdict.confidence = 1.0
    return verdict


def needs_escalation(result: IntentResult) -> bool:
    """根据意图结果判断是否应升级人工。

    升级条件：敏感内容、明确要求转人工，或非闲聊意图置信度过低。
    低置信的闲聊是正常现象，不升级（否则会灌爆人工队列）。
    """
    if result.sensitive:
        return True
    if result.name == "human_handoff":
        return True
    if not config.enable_auto_escalation:
        return False
    if result.name == "chat":
        return False
    return result.confidence < config.intent_confidence_threshold


def escalation_reason(result: IntentResult) -> str:
    if result.sensitive:
        return result.reason or "命中敏感内容"
    if result.name == "human_handoff":
        return "用户明确要求转人工"
    if result.confidence < config.intent_confidence_threshold:
        return (
            f"意图置信度过低（{result.confidence:.2f} < "
            f"{config.intent_confidence_threshold}），意图={result.name}"
        )
    return ""
