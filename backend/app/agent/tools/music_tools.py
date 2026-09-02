"""音乐创作场景的实用工具集。

每个工具用 @tool 装饰器暴露 JSON Schema，由 agent_core 的
工具调用循环按需调用；全部为本地确定性计算，不依赖外部服务。
"""

import asyncio

from langchain_core.tools import BaseTool, tool
from music21 import chord as m21chord
from music21 import harmony, roman
from music21 import key as m21key


@tool
def analyze_chord_progression(chords: str) -> str:
    """分析和弦进行的调性与级数（罗马数字）。

    输入用空格或逗号分隔的和弦名称，例如 "C G Am F" 或 "Cmaj7, A7, Dm7"。
    返回推测的调性、每个和弦的级数与组成音，用于编曲前的和声分析。
    """
    names = [
        c.strip()
        for c in chords.replace("，", " ").replace(",", " ").split()
        if c.strip()
    ]
    if not names:
        return "未解析到和弦，请按 'C G Am F' 这样的格式提供和弦名称。"

    chord_objects: list[m21chord.Chord] = []
    for name in names:
        try:
            chord_objects.append(harmony.ChordSymbol(name))
        except Exception:
            chord_objects.append(None)  # type: ignore[list-item]

    valid = [c for c in chord_objects if c is not None]
    if not valid:
        return f"无法解析这些和弦名称: {', '.join(names)}。请使用常见写法，如 Am、Cmaj7、G/B。"

    key_obj: m21key.Key
    try:
        key_obj = _detect_key(valid)
    except Exception:
        key_obj = m21key.Key("C")

    lines = [f"推测调性: {key_obj.tonicPitchNameWithCase} {key_obj.mode}", ""]
    for name, c in zip(names, chord_objects, strict=False):
        if c is None:
            lines.append(f"{name}: 无法解析")
            continue
        try:
            rn = roman.romanNumeralFromChord(c, key_obj)
            pitches = "-".join(p.name for p in c.pitches)
            lines.append(f"{name}: {rn.figure}（{pitches}）")
        except Exception:
            pitches = "-".join(p.name for p in c.pitches)
            lines.append(f"{name}: 级数分析失败（{pitches}）")
    return "\n".join(lines)


@tool
def get_scale_notes(key_name: str, scale_type: str = "major") -> str:
    """查询某个调的音阶组成音与顺阶三和弦，写旋律/配和声时快速速查。

    key_name 是调名（如 "C"、"A"、"F#"）；scale_type 可选 "major"（大调）或
    "minor"（小调）。返回音阶各音与每个音级上可用的三和弦及其性质。
    """
    try:
        k = m21key.Key(key_name, scale_type)
    except Exception:
        return f"无法识别调 '{key_name}'（{scale_type}），请用类似 C、a、F# 的写法。"

    scale_obj = k.getScale()
    pitches = scale_obj.getPitches(f"{k.tonic.name}4", f"{k.tonic.name}6")
    note_names = [p.name for p in pitches[:8]]

    lines = [f"{k} 调音阶: {' '.join(note_names)}", "", "顺阶三和弦:"]
    names = [p.name for p in pitches]
    qualities_cn = {
        "major": "大三和弦",
        "minor": "小三和弦",
        "diminished": "减三和弦",
        "augmented": "增三和弦",
    }
    for degree in range(7):
        if degree + 4 >= len(names):
            break
        triad_pitches = [names[degree], names[degree + 2], names[degree + 4]]
        try:
            triad = m21chord.Chord(triad_pitches)
            quality = qualities_cn.get(trad_quality(triad), trad_quality(triad))
            numeral = ["I", "ii", "iii", "IV", "V", "vi", "vii°"][degree]
            if scale_type == "minor":
                numeral = ["i", "ii°", "III", "iv", "v", "VI", "VII"][degree]
            lines.append(f"{numeral}: {' '.join(triad_pitches)}（{quality}）")
        except Exception:
            continue
    return "\n".join(lines)


def trad_quality(triad: m21chord.Chord) -> str:
    quality = triad.quality
    return str(quality) if quality else "未知"


def _detect_key(chords: list[m21chord.Chord]) -> m21key.Key:
    """用音级加权打分法推测调性：和弦音出现越多、且落在主音/属音上的权重越高。"""
    from collections import Counter

    pc_weights: Counter[int] = Counter()
    for c in chords:
        for p in c.pitches:
            pc_weights[p.pitchClass] += 1

    best: m21key.Key | None = None
    best_score = -1.0
    for name in ("C", "C#", "D", "E-", "E", "F", "F#", "G", "A-", "A", "B-", "B"):
        for mode in ("major", "minor"):
            k = m21key.Key(name, mode)
            scale_pcs = {p.pitchClass for p in k.getScale().getPitches("C4", "C6")}
            score = (
                sum(pc_weights.get(pc, 0) for pc in scale_pcs)
                + 1.5 * pc_weights.get(k.tonic.pitchClass, 0)
                + 0.8 * pc_weights.get((k.tonic.pitchClass + 7) % 12, 0)
            )
            if score > best_score:
                best_score = score
                best = k
    assert best is not None
    return best


@tool
def calculate_song_timing(bpm: int, bars: int, beats_per_bar: int = 4) -> str:
    """按 BPM 计算歌曲/段落时长，用于规划曲式结构。

    bpm 是速度（拍每分钟），bars 是小节数，beats_per_bar 是每小节拍数
    （4/4 拍传 4，3/4 拍传 3）。返回总时长以及常见段落长度的对照表。
    """
    if bpm <= 0 or bars <= 0:
        return "bpm 和 bars 必须为正数。"
    sec_per_bar = beats_per_bar * 60 / bpm
    total_seconds = round(sec_per_bar * bars)
    minutes, seconds = divmod(total_seconds, 60)

    lines = [
        f"速度 {bpm} BPM、{beats_per_bar}/4 拍下：",
        f"{bars} 小节 ≈ {total_seconds} 秒（{minutes} 分 {seconds:02d} 秒）",
        f"每小节 ≈ {sec_per_bar:.1f} 秒",
        "",
        "常见段落时长参考:",
    ]
    for section_bars in (4, 8, 16, 24, 32):
        s = round(sec_per_bar * section_bars)
        m, sec = divmod(s, 60)
        duration = f"{m} 分 {sec:02d} 秒" if m else f"{sec} 秒"
        lines.append(f"  {section_bars} 小节 ≈ {duration}")
    return "\n".join(lines)


@tool
def check_lyrics_structure(lyrics: str) -> str:
    """检查歌词的结构完整度：识别段落标记、统计行数与每行字数。

    会识别 [Verse]、[Chorus]、[Bridge]、[Pre-Chorus]、主歌、副歌、桥段等
    常见标记，并报告缺失的结构和过长的行，帮助歌词符合流行歌曲曲式。
    """
    marker_prefixes = (
        "[verse",
        "[chorus",
        "[bridge",
        "[pre-chorus",
        "[intro",
        "[outro",
        "[hook",
        "[refrain",
        "主歌",
        "副歌",
        "桥段",
        "预副歌",
        "前奏",
        "尾奏",
        "-hook",
    )
    section_aliases = {
        "主歌": "Verse",
        "副歌": "Chorus",
        "桥段": "Bridge",
        "预副歌": "Pre-Chorus",
        "前奏": "Intro",
        "尾奏": "Outro",
    }

    lines = [line.strip() for line in lyrics.splitlines() if line.strip()]
    sections: list[str] = []
    lyric_lines: list[str] = []
    too_long: list[str] = []

    for line in lines:
        low = line.lower()
        if any(low.startswith(prefix) for prefix in marker_prefixes) or (
            any(line.startswith(a) for a in section_aliases) and len(line) <= 12
        ):
            name = line.lstrip("[").rstrip("]")
            sections.append(section_aliases.get(name, name))
        else:
            lyric_lines.append(line)
            if len(line) > 16:
                too_long.append(line)

    if not lyric_lines:
        return "没有识别到歌词内容，请粘贴完整的歌词文本。"

    avg_len = sum(len(line) for line in lyric_lines) / len(lyric_lines)
    report = [
        f"共 {len(lyric_lines)} 行歌词，平均每行 {avg_len:.1f} 字。",
    ]
    if sections:
        report.append(f"识别到的段落标记: {' → '.join(sections)}")
    else:
        report.append(
            "⚠️ 未识别到任何段落标记（如 [Verse]、[Chorus]），AI 音乐工具将无法区分段落，建议补上。"
        )

    has_chorus = any("chorus" in s.lower() or "副歌" in s for s in sections)
    has_verse = any("verse" in s.lower() or "主歌" in s for s in sections)
    if not has_verse:
        report.append("⚠️ 缺少主歌（Verse）。")
    if not has_chorus:
        report.append("⚠️ 缺少副歌（Chorus），流行歌曲通常至少需要一段记忆点。")

    if avg_len > 14:
        report.append(
            "⚠️ 平均每行超过 14 字，作为歌词偏长，演唱时容易赶拍子，建议拆行或精简。"
        )
    if too_long:
        report.append(
            f"过长的行（>16 字）: {too_long[:3]}{'…' if len(too_long) > 3 else ''}"
        )

    return "\n".join(report)


TOOLS: list[BaseTool] = [
    analyze_chord_progression,
    get_scale_notes,
    calculate_song_timing,
    check_lyrics_structure,
]
_TOOLS_BY_NAME = {t.name: t for t in TOOLS}


async def run_tool(name: str, args: dict) -> str:
    """执行一个工具；出错时返回错误文本，让模型有机会自行纠正。"""
    tool_obj = _TOOLS_BY_NAME.get(name)
    if tool_obj is None:
        return f"未知工具: {name}"
    try:
        return str(await asyncio.to_thread(tool_obj.invoke, args))
    except Exception as exc:
        return f"工具执行出错: {exc}"
