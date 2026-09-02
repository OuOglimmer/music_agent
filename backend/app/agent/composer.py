"""创作流水线的多 Agent 编排（LangGraph supervisor 模式）。

对应四阶段方法论中的第 3、4 阶段：
和声 Agent（定骨架）→ 旋律 Agent（写主脑）→ [贝斯 ∥ 鼓]（填肉，可并行）
→ 评审 Agent（生成-批评循环）→ 渲染节点（总谱合成，不创造新内容）。

通信约定（多 Agent 协作理念）：
- 进程内用共享状态板：MusicBlueprint 是唯一媒介，各节点只读写自己负责的字段；
- 大产物（MIDI 文件）落盘，状态里只传路径；
- 编排者不发对话历史，节点间只传结构化的蓝图字段；
- LLM 节点通过 _call_specialist 调用（可整体替换/注入），失败时回退到
  确定性模板，保证流水线在没有 API Key 的环境也能跑通。
"""

import json
import logging
from typing import Any, TypedDict

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import SecretStr

from app.agent.audio_render import render_mp3
from app.agent.config import config as agent_config
from app.agent.mcp_clients import (
    analyze_progression,
    find_off_scale_chords,
    theory_summary,
)
from app.agent.tools.composition import (
    fallback_melody_data,
    generate_bass_line_data,
    generate_chord_progression_data,
    generate_drum_pattern_data,
    render_midi_data,
    validate_melody_data,
)

logger = logging.getLogger(__name__)


class MusicBlueprint(TypedDict, total=False):
    """创作蓝图：贯穿全流水线的共享状态板，各 Agent 节点读写各自负责的字段。"""

    features: dict[str, Any]  # 第 1、2 阶段产物：特征清单 + 检索到的参考资料
    references: str
    chords: list[str]  # 和声骨架（每和弦一小节）
    harmony_rationale: str
    melody: list[dict[str, Any]]  # 旋律音符
    melody_summary: str
    bass: list[dict[str, Any]]
    bass_summary: str
    drums: list[dict[str, Any]]
    drums_summary: str
    review: dict[str, Any] | None  # 评审结论 {"passed": bool, "notes": list[str]}
    revision_round: int
    theory: dict[str, Any] | None  # mcp42 乐理校正结果（罗马数字/终止式）
    theory_summary: str
    midi_path: str
    mp3_path: str
    render_summary: str


# ---------------------------------------------------------------------------
# Specialist LLM：按角色分温度调用，可整体注入替换（测试/换模型）
# ---------------------------------------------------------------------------

_SPECIALIST_TEMPERATURES = {"harmony": 0.4, "melody": 0.9, "review": 0.2}

_llm_cache: dict[str, ChatOpenAI] = {}


def _get_specialist_llm(role: str) -> ChatOpenAI:
    if role in _llm_cache:
        return _llm_cache[role]
    if not agent_config.api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not configured")
    llm = ChatOpenAI(
        model=agent_config.model_name,
        api_key=SecretStr(agent_config.api_key),
        base_url=agent_config.base_url,
        temperature=_SPECIALIST_TEMPERATURES.get(role, 0.5),
        # langchain-openai 运行时支持 max_tokens（agent_core 同款用法），但类型桩未收录
        max_tokens=agent_config.max_tokens,  # type: ignore[call-arg]  # ty: ignore[unknown-argument]
        http_client=httpx.Client(trust_env=False),
        http_async_client=httpx.AsyncClient(trust_env=False),
    )
    _llm_cache[role] = llm
    return llm


async def _call_specialist(role: str, system_prompt: str, task: str) -> str:
    """调用一个 Specialist LLM，返回原始文本。测试可 monkeypatch 本函数。"""
    response = await _get_specialist_llm(role).ainvoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=task)]
    )
    content = response.content
    if isinstance(content, list):
        content = "".join(str(part) for part in content)
    return str(content).strip()


def _extract_json(text: str) -> dict[str, Any] | None:
    """从 LLM 回复中提取第一个 JSON 对象（容忍 markdown 代码围栏）。"""
    text = text.strip()
    if "```" in text:
        for segment in text.split("```"):
            candidate = segment.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                text = candidate
                break
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# Specialist 提示词：每个角色的系统提示词短而专，只看自己该看的东西
# ---------------------------------------------------------------------------

HARMONY_SYSTEM = """\
你是【编曲师】，只负责一件事：根据特征清单确定完整的和弦进行。
要求：
- chords 数组长度必须等于指定小节数，每个和弦持续一小节；
- 和弦用常见记号（如 C、Am、G7、Cmaj7、F、Bdim），优先调内三和弦，允许少量借用；
- 情绪与风格要求要体现在进行的选择上（如温暖用 1-5-6-4 类，遗憾用 6-4-1-5 类）；
- 只输出 JSON：{"chords": ["C", "G", ...], "rationale": "一句话说明选择理由"}。"""

MELODY_SYSTEM = """\
你是【主旋律创作者】，只负责一件事：在和弦骨架上写出有记忆点的主旋律。
要求：
- 旋律跟着和声走：强拍尽量落在当前小节和弦的和弦音上，弱拍可用经过音；
- 每个小节的音符 pitch 用 "音名+八度"（如 C5），音区建议 C4 到 A5；
- start 为相对全曲开头的拍位置，duration 为持续拍数，velocity 是 1-127 的力度；
- 有明确的最高点与重复动机，让副歌容易被记住；
- 只输出 JSON：{"notes": [{"pitch": "C5", "start": 0, "duration": 1, "velocity": 90}, ...],
"summary": "一句话说明旋律设计思路"}。"""

REVIEW_SYSTEM = """\
你是【听众评审】，以普通听众视角检验这份创作蓝图。
要求：
- passed 为是否通过：旋律是否有记忆点、情绪是否匹配、结构是否完整；
- 通过也请给出 1-2 条优点；不通过时给出最多 3 条具体、可执行的修改建议；
- 只输出 JSON：{"passed": true/false, "notes": ["...", "..."]}。"""


def _parse_chord_list(candidate: Any, bars: int) -> list[str] | None:
    """校验 LLM 给出的和弦列表：可解析、按 bars 截断/循环补齐；不可用返回 None。"""
    from music21 import harmony

    if not isinstance(candidate, list):
        return None
    names = [str(c).strip() for c in candidate if str(c).strip()]
    if not names:
        return None
    for name in names:
        try:
            harmony.ChordSymbol(name)
        except Exception:
            return None
    while len(names) < bars:
        names.extend(names)
    return names[:bars]


# ---------------------------------------------------------------------------
# 节点实现：每个节点只读写自己负责的字段
# ---------------------------------------------------------------------------


async def harmony_node(state: MusicBlueprint) -> dict[str, Any]:
    """和声 Agent：把特征清单扩展为完整和弦进行（定骨架）。"""
    features = state.get("features", {})
    key_name = str(features.get("key", "C"))
    mode = "minor" if str(features.get("mode", "major")) == "minor" else "major"
    mood = str(features.get("mood", "温暖"))
    bars = int(features.get("bars", 8))
    chords: list[str] | None = None
    rationale = ""
    try:
        task = json.dumps(
            {
                "key": key_name,
                "mode": mode,
                "mood": mood,
                "bars": bars,
                "user_notes": str(features.get("user_notes", "")),
                "references": state.get("references", "")[:1500],
            },
            ensure_ascii=False,
        )
        data = (
            _extract_json(await _call_specialist("harmony", HARMONY_SYSTEM, task)) or {}
        )
        chords = _parse_chord_list(data.get("chords"), bars)
        rationale = str(data.get("rationale", ""))[:500]
    except Exception as exc:
        logger.warning("Harmony agent fallback: %s", exc)
    if not chords:
        fallback = generate_chord_progression_data(key_name, mode, mood, bars)
        chords = fallback["chords"]
        rationale = (
            f"（和声 Agent 输出无效，按 {fallback['mood']} 模板生成："
            f"{'-'.join(fallback['degrees'])}）"
        )
    return {"chords": chords, "harmony_rationale": rationale}


async def theory_node(state: MusicBlueprint) -> dict[str, Any]:
    """乐理校正节点（mcp42 外脑）：确认调性、罗马数字与终止式；服务不可达时静默跳过。"""
    chords = state.get("chords") or []
    features = state.get("features", {})
    mode = "minor" if str(features.get("mode", "major")) == "minor" else "major"
    key_hint = f"{str(features.get('key', 'C'))} {mode}"
    analysis = await analyze_progression(chords, key_hint=key_hint)
    summary = theory_summary(analysis)
    off_scale = find_off_scale_chords(analysis, chords)
    if off_scale:
        summary += f"；调外和弦：{'、'.join(off_scale)}（评审时可关注）"
    return {"theory": analysis, "theory_summary": summary}


async def melody_node(state: MusicBlueprint) -> dict[str, Any]:
    """旋律 Agent：基于和弦骨架创作主旋律（写主脑）；返工时吸收评审意见。"""
    features = state.get("features", {})
    key_name = str(features.get("key", "C"))
    mode = "minor" if str(features.get("mode", "major")) == "minor" else "major"
    bars = int(features.get("bars", 8))
    beats_per_bar = int(features.get("beats_per_bar", 4))
    bpm = int(features.get("bpm", 100))
    chords = (
        state.get("chords")
        or generate_chord_progression_data(key_name, mode, "warm", bars)["chords"]
    )

    review = state.get("review") or {}
    feedback: list[str] = []
    if state.get("revision_round", 0) > 0 and not review.get("passed", True):
        feedback = [str(n) for n in (review.get("notes") or [])[:3]]

    notes: list[dict[str, Any]] | None = None
    summary = ""
    try:
        task = json.dumps(
            {
                "chords": chords,
                "key": key_name,
                "mode": mode,
                "bpm": bpm,
                "mood": str(features.get("mood", "")),
                "bars": bars,
                "beats_per_bar": beats_per_bar,
                "revision_feedback": feedback,
            },
            ensure_ascii=False,
        )
        data = (
            _extract_json(await _call_specialist("melody", MELODY_SYSTEM, task)) or {}
        )
        candidate = data.get("notes")
        if isinstance(candidate, list) and candidate:
            notes = [n for n in candidate if isinstance(n, dict)]
            summary = str(data.get("summary", ""))[:500]
    except Exception as exc:
        logger.warning("Melody agent fallback: %s", exc)

    if notes:
        notes, problems = validate_melody_data(key_name, mode, notes)
        if problems:
            summary = f"（校验提示：{'；'.join(problems)}）{summary}"
    if not notes:
        notes = fallback_melody_data(chords, beats_per_bar)
        summary = f"（旋律 Agent 输出无效，已按和弦琶音兜底生成）{summary}"
    return {"melody": notes, "melody_summary": summary or "旋律已生成"}


def _bass_style_for(style: str) -> str:
    mapping = {
        "ballad": "whole",
        "folk": "root_fifth",
        "pop": "octave",
        "rock": "eighth",
        "edm": "eighth",
        "electronic": "eighth",
    }
    return mapping.get(style, "octave")


async def bass_node(state: MusicBlueprint) -> dict[str, Any]:
    """贝斯手节点：确定性生成律动底层（配器跟着旋律走，无需 LLM）。"""
    features = state.get("features", {})
    beats_per_bar = int(features.get("beats_per_bar", 4))
    chords = state.get("chords") or []
    style = _bass_style_for(str(features.get("style", "pop")))
    notes = generate_bass_line_data(chords, style, beats_per_bar)
    return {"bass": notes, "bass_summary": f"{style} 风格贝斯线，{len(notes)} 个音符"}


async def drums_node(state: MusicBlueprint) -> dict[str, Any]:
    """鼓手节点：确定性生成节奏型（与贝斯并行，互不依赖）。"""
    features = state.get("features", {})
    beats_per_bar = int(features.get("beats_per_bar", 4))
    bars = len(state.get("chords") or []) or int(features.get("bars", 8))
    style = str(features.get("style", "pop"))
    if style not in ("ballad", "pop", "rock", "edm"):
        style = "pop"
    events = generate_drum_pattern_data(style, bars, beats_per_bar)
    return {"drums": events, "drums_summary": f"{style} 风格鼓组，{len(events)} 个事件"}


async def review_node(state: MusicBlueprint) -> dict[str, Any]:
    """听众评审节点：生成-批评循环；LLM 不可用时默认放行（不阻塞流水线）。"""
    melody = state.get("melody") or []
    result: dict[str, Any] = {
        "passed": True,
        "notes": ["（评审 Agent 不可用，默认放行）"],
    }
    try:
        task = json.dumps(
            {
                "chords": state.get("chords", []),
                "melody_notes": len(melody),
                "melody_summary": state.get("melody_summary", ""),
                "harmony_rationale": state.get("harmony_rationale", ""),
                "theory_summary": state.get("theory_summary", ""),
                "mood": (state.get("features") or {}).get("mood", ""),
                "revision_round": state.get("revision_round", 0),
            },
            ensure_ascii=False,
        )
        data = (
            _extract_json(await _call_specialist("review", REVIEW_SYSTEM, task)) or {}
        )
        result = {
            "passed": bool(data.get("passed", True)),
            "notes": [str(n) for n in (data.get("notes") or [])[:3]],
        }
    except Exception as exc:
        logger.warning("Review agent fallback: %s", exc)
    return {"review": result, "revision_round": state.get("revision_round", 0) + 1}


async def render_node(state: MusicBlueprint) -> dict[str, Any]:
    """渲染节点（第 4 阶段）：时间轴对齐合成总谱，不创造新内容，大产物落盘传路径。"""
    features = state.get("features", {})
    chords = state.get("chords") or []
    if not chords:
        return {"midi_path": "", "render_summary": "渲染失败：缺少和弦骨架"}
    try:
        result = render_midi_data(
            str(features.get("key", "C")),
            "minor" if str(features.get("mode", "major")) == "minor" else "major",
            int(features.get("bpm", 100)),
            chords,
            melody=state.get("melody"),
            bass=state.get("bass"),
            drums=state.get("drums"),
            beats_per_bar=int(features.get("beats_per_bar", 4)),
            title=str(features.get("title", "blueprint")),
        )
    except Exception as exc:
        logger.exception("Render failed")
        return {"midi_path": "", "mp3_path": "", "render_summary": f"渲染失败: {exc}"}
    summary = (
        f"总谱已生成（{result['parts']} 轨 / {result['bars']} 小节 / {result['bpm']} BPM）: "
        f"{result['path']}"
    )
    mp3_path = ""
    if agent_config.enable_mp3:
        mp3_path = await render_mp3(result["path"]) or ""
        if mp3_path:
            summary += f"；音频已渲染: {mp3_path}"
    return {
        "midi_path": result["path"],
        "mp3_path": mp3_path,
        "render_summary": summary,
    }


def route_after_review(state: MusicBlueprint) -> str:
    """评审不通过且还有返工额度（初始创作外最多返工 max_revisions 轮）→ 回旋律节点；否则渲染。"""
    review = state.get("review") or {}
    if (
        review.get("passed", True)
        or state.get("revision_round", 0) > agent_config.max_revisions
    ):
        return "render"
    return "melody"


def build_compose_graph() -> Any:
    """构建创作流水线 graph。melody 之后贝斯/鼓并行，评审汇合后再决定返工或渲染。"""
    # TypedDict(total=False) 是运行时验证过的合法 state schema，ty 桩未识别
    graph = StateGraph(MusicBlueprint)  # ty: ignore[invalid-argument-type]
    graph.add_node("harmony", harmony_node)
    graph.add_node("theory", theory_node)
    graph.add_node("melody", melody_node)
    graph.add_node("bass", bass_node)
    graph.add_node("drums", drums_node)
    graph.add_node("review", review_node)
    graph.add_node("render", render_node)
    graph.add_edge(START, "harmony")
    graph.add_edge("harmony", "theory")
    graph.add_edge("theory", "melody")
    graph.add_edge("melody", "bass")
    graph.add_edge("melody", "drums")
    graph.add_edge(["bass", "drums"], "review")
    graph.add_conditional_edges(
        "review", route_after_review, {"melody": "melody", "render": "render"}
    )
    graph.add_edge("render", END)
    return graph.compile()


# ---------------------------------------------------------------------------
# SSE 阶段事件辅助：把节点更新映射为前端可读的舞台进展
# ---------------------------------------------------------------------------

STAGE_LABELS: dict[str, str] = {
    "analysis": "🎧 听觉解析：提取节奏与和声骨架",
    "harmony": "🎹 和声师：确定和弦进行",
    "theory": "📐 乐理校正：mcp42 校验进行",
    "melody": "🎼 主旋律创作者：写作记忆点",
    "bass": "🎸 贝斯手：铺设律动底层",
    "drums": "🥁 鼓手：设计节奏驱动",
    "review": "👂 听众评审：检验作品",
    "render": "🎚️ 渲染：合成总谱",
}

_DETAIL_FIELDS = (
    "harmony_rationale",
    "theory_summary",
    "melody_summary",
    "bass_summary",
    "drums_summary",
    "render_summary",
)


def stage_detail(node: str, patch: dict[str, Any]) -> str:
    """从节点更新中提取一句给用户看的进展说明。"""
    for field in _DETAIL_FIELDS:
        if field in patch and str(patch[field]).strip():
            return str(patch[field])[:300]
    if node == "review":
        review = patch.get("review") or {}
        verdict = "通过" if review.get("passed") else "需修改"
        notes = "；".join(str(n) for n in (review.get("notes") or [])[:3])
        return f"评审结论：{verdict}" + (f"｜{notes}" if notes else "")
    return ""
