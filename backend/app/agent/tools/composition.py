"""分角色创作与总谱渲染工具（四阶段流水线的第 3、4 阶段）。

核心函数接受/返回结构化数据（list[dict]），供多 Agent 编排层直接调用；
@tool 包装层把参数做成 LLM 友好的字符串形式，注册进 TOOLS 供工具调用循环使用。
音符统一格式：{"pitch": "C4", "start": 拍, "duration": 拍, "velocity": 1-127}，
start 相对全曲开头、以拍为单位；鼓组事件用 {"drum": kick/snare/hihat/clap/crash, ...}。
"""

import json
import re
import uuid
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool
from music21 import chord as m21chord
from music21 import converter, harmony
from music21 import instrument as m21instrument
from music21 import key as m21key
from music21 import metadata as m21metadata
from music21 import note as m21note
from music21 import pitch as m21pitch
from music21 import stream as m21stream
from music21 import tempo as m21tempo
from music21 import volume as m21volume

from app.agent.config import config as agent_config

Note = dict[str, Any]

_QUALITY_SUFFIX = {"major": "", "minor": "m", "diminished": "dim", "augmented": "aug"}

# 和弦进行模板库（度数，从 1 开始），与知识库 music-basics.md 的进行速查一致
PROGRESSION_TEMPLATES: dict[str, dict[str, list[int]]] = {
    "major": {
        "warm": [1, 5, 6, 4],  # 卡农进行：温暖、正向
        "emotional": [6, 4, 1, 5],  # 遗憾感情歌
        "nostalgic": [1, 6, 4, 5],  # 50 年代进行：明朗怀旧
        "bright": [4, 5, 3, 6],  # 4536：日系与华语流行副歌
    },
    "minor": {
        "sad": [1, 6, 3, 7],  # i-VI-III-VII：伤感中带开阔
        "powerful": [1, 6, 7, 5],  # i-VI-VII-v：力量感
        "dark": [1, 4, 1, 7],  # i-iv-i-VII：阴郁压抑
    },
}

# 中文情绪词 → 模板键
MOOD_ALIASES: dict[str, str] = {
    "温暖": "warm",
    "正向": "warm",
    "治愈": "warm",
    "开心": "warm",
    "轻快": "warm",
    "抒情": "emotional",
    "遗憾": "emotional",
    "深情": "emotional",
    "分手": "emotional",
    "怀念": "nostalgic",
    "怀旧": "nostalgic",
    "回忆": "nostalgic",
    "明亮": "bright",
    "日系": "bright",
    "青春": "bright",
    "伤感": "sad",
    "难过": "sad",
    "告别": "sad",
    "力量": "powerful",
    "燃": "powerful",
    "热血": "powerful",
    "阴郁": "dark",
    "压抑": "dark",
    "紧张": "dark",
}

ROMAN_NUMERALS = {
    "major": ["I", "ii", "iii", "IV", "V", "vi", "vii°"],
    "minor": ["i", "ii°", "III", "iv", "v", "VI", "VII"],
}

# GM 鼓谱音高（MIDI 音符号）
DRUM_PITCHES = {
    "kick": "C2",  # 36 底鼓
    "snare": "D2",  # 38 军鼓
    "hihat": "F#2",  # 42 闭镲
    "clap": "D#2",  # 39 拍手
    "crash": "C#3",  # 49 吊镲
}


def _resolve_key(key_name: str, mode: str) -> m21key.Key:
    """解析调名；把音频解析常见的 'Bb' 降号写法归一为 music21 的 'B-'。"""
    name = key_name.strip()
    if len(name) >= 2 and name.endswith("b") and name[-2] != "-":
        name = name[:-1] + "-"
    return m21key.Key(name, "minor" if mode == "minor" else "major")


def _diatonic_triad_names(k: m21key.Key) -> list[str]:
    """返回调内七个顺阶三和弦的和弦名（如 C 调 → ['C','Dm','Em','F','G','Am','Bdim']）。"""
    pitches = k.getScale().getPitches(f"{k.tonic.name}4", f"{k.tonic.name}6")  # type: ignore[no-untyped-call]
    names: list[str] = []
    for degree in range(7):
        triad = m21chord.Chord(
            [pitches[degree].name, pitches[degree + 2].name, pitches[degree + 4].name]
        )
        suffix = _QUALITY_SUFFIX.get(triad.quality, "")
        names.append(f"{triad.root().name}{suffix}")
    return names


def _resolve_mood(mode: str, mood: str) -> str:
    """把自由文本情绪归一到模板键；未命中时按调式给默认值。"""
    for word, template in MOOD_ALIASES.items():
        if word in mood:
            # 中文情绪词归一后仍需匹配当前调式下的模板
            candidate = template
            if candidate in PROGRESSION_TEMPLATES[mode]:
                return candidate
    return "warm" if mode == "major" else "sad"


def generate_chord_progression_data(
    key_name: str, mode: str, mood: str, bars: int, beats_per_bar: int = 4
) -> dict[str, Any]:
    """按调式与情绪从模板库生成和弦进行（每个和弦持续一小节）。"""
    k = _resolve_key(key_name, mode)
    triads = _diatonic_triad_names(k)
    template_key = _resolve_mood(mode, mood)
    degrees = PROGRESSION_TEMPLATES[mode][template_key]
    chords = [triads[degree - 1] for degree in degrees]
    while len(chords) < bars:
        chords.extend([triads[d - 1] for d in degrees])
    chords = chords[:bars]
    numerals = [ROMAN_NUMERALS[mode][d - 1] for d in degrees]
    return {
        "key": k.tonicPitchNameWithCase,
        "mode": mode,
        "mood": template_key,
        "chords": chords,
        "degrees": numerals,
        "beats_per_bar": beats_per_bar,
    }


# ---------------------------------------------------------------------------
# 贝斯线（律动底层）：按风格给出根音节奏型
# ---------------------------------------------------------------------------


def _fifth_name(root: str) -> str:
    return m21pitch.Pitch(root).transpose("P5").name


def generate_bass_line_data(
    chords: list[str], style: str = "octave", beats_per_bar: int = 4
) -> list[Note]:
    """按风格为每个小节的和弦生成贝斯音符，音区固定在低音谱表（八度 2-3）。"""
    styles = ("whole", "root_fifth", "octave", "eighth")
    if style not in styles:
        style = "octave"
    notes: list[Note] = []
    for bar, chord_name in enumerate(chords):
        try:
            root = harmony.ChordSymbol(chord_name).root().name
        except Exception:
            root = chord_name.rstrip("m0123456789") or "C"
        base = bar * beats_per_bar
        if style == "whole":
            notes.append(
                {
                    "pitch": f"{root}2",
                    "start": base,
                    "duration": beats_per_bar,
                    "velocity": 80,
                }
            )
        elif style == "root_fifth":
            notes.append(
                {
                    "pitch": f"{root}2",
                    "start": base,
                    "duration": beats_per_bar / 2,
                    "velocity": 82,
                }
            )
            notes.append(
                {
                    "pitch": f"{_fifth_name(root)}2",
                    "start": base + beats_per_bar / 2,
                    "duration": beats_per_bar / 2,
                    "velocity": 78,
                }
            )
        elif style == "octave":
            notes.append(
                {"pitch": f"{root}2", "start": base, "duration": 1.0, "velocity": 88}
            )
            notes.append(
                {
                    "pitch": f"{root}3",
                    "start": base + 2,
                    "duration": 1.0,
                    "velocity": 84,
                }
            )
        else:  # eighth：八分音符律动
            for i in range(beats_per_bar * 2):
                notes.append(
                    {
                        "pitch": f"{root}2",
                        "start": base + i * 0.5,
                        "duration": 0.45,
                        "velocity": 86 if i % 2 == 0 else 70,
                    }
                )
    return notes


# ---------------------------------------------------------------------------
# 鼓组（节奏驱动）：GM 鼓谱音高，按风格给出每小节节奏型
# ---------------------------------------------------------------------------


def generate_drum_pattern_data(
    style: str = "pop", bars: int = 8, beats_per_bar: int = 4
) -> list[Note]:
    """按风格生成鼓组事件（kick/snare/hihat/clap/crash），每小节重复同一节奏型。"""
    styles = ("ballad", "pop", "rock", "edm")
    if style not in styles:
        style = "pop"
    hits: list[tuple[str, float]] = []
    if style == "ballad":
        hits = [
            ("kick", 0),
            ("snare", 2),
            ("hihat", 0),
            ("hihat", 1),
            ("hihat", 2),
            ("hihat", 3),
        ]
    elif style == "pop":
        hits = [("kick", 0), ("kick", 2), ("snare", 1), ("snare", 3)]
        hits += [("hihat", i * 0.5) for i in range(beats_per_bar * 2)]
    elif style == "rock":
        hits = [("kick", 0), ("kick", 2.5), ("snare", 1), ("snare", 3)]
        hits += [("hihat", i * 0.5) for i in range(beats_per_bar * 2)]
    else:  # edm：四踩底鼓 + 反拍开镲
        hits = [("kick", i) for i in range(beats_per_bar)]
        hits += [("clap", 1), ("clap", 3)]
        hits += [("hihat", i + 0.5) for i in range(beats_per_bar)]

    events: list[Note] = []
    for bar in range(bars):
        offset = bar * beats_per_bar
        for drum, beat in hits:
            if beat >= beats_per_bar:
                continue
            events.append({"drum": drum, "start": offset + beat, "duration": 0.25})
        if bar == 0:
            events.append({"drum": "crash", "start": 0, "duration": 1.0})
    return events


# ---------------------------------------------------------------------------
# 旋律校验与兜底（旋律主脑由 LLM 担任，这里只校验，不创作）
# ---------------------------------------------------------------------------


def validate_melody_data(
    key_name: str, mode: str, notes: list[Note]
) -> tuple[list[Note], list[str]]:
    """校验旋律：音高是否在调内、时值是否重叠、音区是否合理。返回 (合法音符, 问题列表)。"""
    k = _resolve_key(key_name, mode)
    scale_pcs = {p.pitchClass for p in k.getScale().getPitches("C4", "C6")}  # type: ignore[no-untyped-call]
    problems: list[str] = []
    off_scale = [
        n
        for n in notes
        if m21pitch.Pitch(str(n.get("pitch", "C4"))).pitchClass not in scale_pcs
    ]
    if notes and len(off_scale) / len(notes) > 0.2:
        problems.append(
            f"{len(off_scale)}/{len(notes)} 个音符不在调内（如 "
            f"{', '.join(str(n.get('pitch')) for n in off_scale[:3])}），旋律会显得跑调"
        )
    if any(
        not isinstance(n.get("start"), (int, float))
        or not isinstance(n.get("duration"), (int, float))
        for n in notes
    ):
        problems.append("存在缺少 start/duration 或类型不对的音符")
    ordered = sorted(notes, key=lambda n: float(n.get("start", 0)))
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        if (
            float(prev.get("start", 0)) + float(prev.get("duration", 0))
            > float(cur.get("start", 0)) + 1e-6
        ):
            problems.append("存在音符时值重叠，请检查 start/duration")
            break
    return notes, problems


def fallback_melody_data(chords: list[str], beats_per_bar: int = 4) -> list[Note]:
    """兜底旋律：按每小节和弦的根-三-五-三琶音生成，保证渲染管线永远有旋律可用。"""
    notes: list[Note] = []
    for bar, chord_name in enumerate(chords):
        try:
            chord = harmony.ChordSymbol(chord_name)
            tone_names = [p.name for p in chord.pitches][:3] or ["C", "E", "G"]
        except Exception:
            tone_names = ["C", "E", "G"]
        pattern = [0, 1, 2, 1]
        base = bar * beats_per_bar
        for i, tone_index in enumerate(pattern):
            name = tone_names[tone_index % len(tone_names)]
            pitch = m21pitch.Pitch(name)
            pitch.octave = 5
            notes.append(
                {
                    "pitch": pitch.nameWithOctave,
                    "start": base + i,
                    "duration": 1.0,
                    "velocity": 90 if i == 0 else 82,
                }
            )
    return notes


# ---------------------------------------------------------------------------
# 总谱渲染（第 4 阶段）：时间轴对齐 + 力度微调，不创造新内容
# ---------------------------------------------------------------------------


def _parse_notes(raw: str | list[Note] | None) -> list[Note]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _add_pitched_note(part: m21stream.Part, note_data: Note) -> bool:
    try:
        pitch = m21pitch.Pitch(str(note_data["pitch"]))
        offset = float(note_data["start"])
        duration = float(note_data["duration"])
        if duration <= 0 or offset < 0:
            return False
    except KeyError, TypeError, ValueError:
        return False
    note_obj = m21note.Note(pitch)
    note_obj.duration.quarterLength = duration
    note_obj.offset = offset
    velocity = note_data.get("velocity", 90)
    note_obj.volume = m21volume.Volume(
        velocity=int(velocity) if isinstance(velocity, (int, float)) else 90
    )
    part.insert(offset, note_obj)
    return True


def _add_drums(part: m21stream.Part, events: list[Note], beats_per_bar: int) -> int:
    """把鼓组事件写入打击乐轨（channel 10），小节首拍做力度重音微调。"""
    count = 0
    for event in events:
        drum = str(event.get("drum", "kick"))
        pitch_name = DRUM_PITCHES.get(drum, DRUM_PITCHES["kick"])
        try:
            offset = float(event["start"])
            duration = float(event.get("duration", 0.25))
        except KeyError, TypeError, ValueError:
            continue
        if offset < 0 or duration <= 0:
            continue
        note_obj = m21note.Note(pitch_name)
        note_obj.duration.quarterLength = duration
        note_obj.offset = offset
        in_bar_beat = offset % beats_per_bar
        velocity = 118 if in_bar_beat == 0 else 96
        note_obj.volume = m21volume.Volume(velocity=velocity)
        part.insert(offset, note_obj)
        count += 1
    return count


def render_midi_data(
    key_name: str,
    mode: str,
    bpm: int,
    chords: list[str],
    melody: list[Note] | None = None,
    bass: list[Note] | None = None,
    drums: list[Note] | None = None,
    beats_per_bar: int = 4,
    title: str = "untitled",
) -> dict[str, Any]:
    """把四轨（旋律/和声/贝斯/鼓）按时间轴对齐合成为总谱并导出 MIDI 文件。

    旋律缺失时用兜底琶音补齐（渲染层不创造新内容，只保证总谱完整可播放）。
    """
    bars = max(1, len(chords))
    melody_notes = melody or fallback_melody_data(chords, beats_per_bar)
    bass_notes = bass or generate_bass_line_data(chords, "octave", beats_per_bar)
    drum_events = drums or generate_drum_pattern_data("pop", bars, beats_per_bar)

    score = m21stream.Score()
    meta = m21metadata.Metadata()
    meta.title = title
    score.insert(0, meta)

    melody_part = m21stream.Part(id="melody")  # type: ignore[no-untyped-call]
    melody_part.insert(0, m21instrument.Vocalist())  # type: ignore[no-untyped-call]
    melody_part.insert(0, m21tempo.MetronomeMark(number=bpm))
    melody_count = sum(_add_pitched_note(melody_part, n) for n in melody_notes)
    score.insert(0, melody_part)

    chord_part = m21stream.Part(id="harmony")  # type: ignore[no-untyped-call]
    chord_part.insert(0, m21instrument.Piano())  # type: ignore[no-untyped-call]
    chord_part.insert(0, m21tempo.MetronomeMark(number=bpm))
    chord_count = 0
    for bar, chord_name in enumerate(chords):
        try:
            symbol = harmony.ChordSymbol(chord_name)
        except Exception:
            continue
        symbol.duration.quarterLength = beats_per_bar
        symbol.offset = bar * beats_per_bar
        chord_part.insert(bar * beats_per_bar, symbol)
        chord_count += 1
    score.insert(0, chord_part)

    bass_part = m21stream.Part(id="bass")  # type: ignore[no-untyped-call]
    bass_part.insert(0, m21instrument.ElectricBass())  # type: ignore[no-untyped-call]
    bass_part.insert(0, m21tempo.MetronomeMark(number=bpm))
    bass_count = sum(_add_pitched_note(bass_part, n) for n in bass_notes)
    score.insert(0, bass_part)

    drum_part = m21stream.Part(id="drums")  # type: ignore[no-untyped-call]
    drum_instrument = m21instrument.BassDrum()  # type: ignore[no-untyped-call]
    drum_instrument.midiChannel = 9  # channel 10（0 起算为 9）：GM 打击乐通道
    drum_part.insert(0, drum_instrument)
    drum_part.insert(0, m21tempo.MetronomeMark(number=bpm))
    drum_count = _add_drums(drum_part, drum_events, beats_per_bar)
    score.insert(0, drum_part)

    scores_dir = Path(agent_config.scores_dir)
    scores_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w-]+", "-", title.lower()).strip("-") or "score"
    filename = f"{slug}-{uuid.uuid4().hex[:8]}.mid"
    file_path = scores_dir / filename
    fp = score.write("midi", str(file_path))  # type: ignore[no-untyped-call]

    parsed = converter.parse(fp)
    parts = len(parsed.parts) if isinstance(parsed, m21stream.Score) else 0
    return {
        "path": str(fp),
        "parts": parts,
        "melody_notes": melody_count,
        "chord_bars": chord_count,
        "bass_notes": bass_count,
        "drum_hits": drum_count,
        "bars": bars,
        "bpm": bpm,
        "key": key_name,
        "mode": mode,
    }


# ---------------------------------------------------------------------------
# LLM 友好的 @tool 包装层
# ---------------------------------------------------------------------------


@tool
def generate_chord_progression(
    key_name: str, mode: str = "major", mood: str = "warm", bars: int = 8
) -> str:
    """按调式与情绪生成完整和弦进行（每个和弦一小节），编曲前先定和声骨架。

    key_name 是调名（如 "C"、"A"、"bB"）；mode 为 "major"（大调）或 "minor"（小调）；
    mood 可用中文或英文描述情绪（如 "温暖"、"伤感"、"遗憾"、"燃"）；
    bars 是小节数。返回和弦序列与对应级数。
    """
    data = generate_chord_progression_data(key_name, mode, mood, bars)
    lines = [
        f"{data['key']} {mode}｜情绪 {mood} → 模板 {data['mood']}（{'-'.join(data['degrees'])}）",
        f"和弦进行（共 {len(data['chords'])} 小节）: {' | '.join(data['chords'])}",
    ]
    return "\n".join(lines)


@tool
def generate_bass_line(
    chords: str, style: str = "octave", beats_per_bar: int = 4
) -> str:
    """根据和弦进行生成贝斯线（音符 JSON），负责律动底层。

    chords 用逗号或空格分隔（每个和弦一小节，如 "C, G, Am, F"）；
    style 可选 whole（全音符长音，抒情）、root_fifth（根音五音，民谣）、
    octave（根音加八度点缀，流行）、eighth（八分音符律动，电子/摇滚）。
    返回音符 JSON：{"pitch","start","duration","velocity"}，单位为拍。
    """
    names = [c.strip() for c in re.split(r"[,，\s]+", chords) if c.strip()]
    if not names:
        return "未解析到和弦，请按 'C, G, Am, F' 的格式提供。"
    notes = generate_bass_line_data(names, style, beats_per_bar)
    return f"贝斯线（{style} 风格，{len(names)} 小节，{len(notes)} 个音符）:\n{json.dumps(notes, ensure_ascii=False)}"


@tool
def generate_drum_pattern(
    style: str = "pop", bars: int = 8, beats_per_bar: int = 4
) -> str:
    """按风格生成鼓组节奏型（事件 JSON），负责节奏驱动。

    style 可选 ballad（轻打击）、pop（流行）、rock（摇滚）、edm（电子四踩）。
    返回事件 JSON：{"drum": kick/snare/hihat/clap/crash, "start", "duration"}，单位为拍。
    """
    events = generate_drum_pattern_data(style, bars, beats_per_bar)
    return f"鼓组节奏（{style} 风格，{bars} 小节，{len(events)} 个事件）:\n{json.dumps(events, ensure_ascii=False)}"


@tool
def validate_melody(key_name: str, mode: str, melody_json: str) -> str:
    """校验旋律是否贴合和声骨架：检查音高是否在调内、时值是否重叠。

    melody_json 是音符数组 JSON：{"pitch": "C5", "start": 0, "duration": 1, "velocity": 90}，
    start/duration 单位为拍。返回问题清单，为空表示旋律可以进入编曲阶段。
    """
    try:
        parsed = json.loads(melody_json)
        notes = parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return "melody_json 不是合法的 JSON 数组。"
    if not notes:
        return "旋律为空，请先创作主旋律。"
    _, problems = validate_melody_data(key_name, mode, notes)
    if not problems:
        return f"校验通过：{len(notes)} 个音符均在 {key_name} {mode} 调内且时值无重叠。"
    return "发现问题:\n- " + "\n- ".join(problems)


@tool
def render_midi(
    key_name: str,
    mode: str,
    bpm: int,
    chords: str,
    melody_json: str = "[]",
    bass_json: str = "[]",
    drums_json: str = "[]",
    beats_per_bar: int = 4,
    title: str = "untitled",
) -> str:
    """把旋律/和声/贝斯/鼓四轨按时间轴对齐，合成为完整总谱并导出 MIDI 文件。

    chords 为逗号分隔的和弦名（每个一小节）；melody_json/bass_json 是音符数组 JSON
    （{"pitch","start","duration","velocity"}，单位拍）；drums_json 是鼓组事件数组 JSON。
    返回 MIDI 文件路径与各轨统计，可用任何音乐软件直接打开。
    """
    chord_names = [c.strip() for c in re.split(r"[,，\s]+", chords) if c.strip()]
    if not chord_names:
        return "未提供和弦进行，无法渲染总谱。"
    if bpm <= 0 or bpm > 300:
        return "bpm 必须在 1-300 之间。"
    result = render_midi_data(
        key_name,
        mode,
        bpm,
        chord_names,
        melody=_parse_notes(melody_json),
        bass=_parse_notes(bass_json),
        drums=_parse_notes(drums_json),
        beats_per_bar=beats_per_bar,
        title=title,
    )
    return (
        f"总谱已生成: {result['path']}\n"
        f"共 {result['parts']} 轨｜{result['bars']} 小节｜{result['bpm']} BPM｜"
        f"旋律 {result['melody_notes']} 音｜和声 {result['chord_bars']} 小节｜"
        f"贝斯 {result['bass_notes']} 音｜鼓 {result['drum_hits']} 击"
    )


TOOLS: list[BaseTool] = [
    generate_chord_progression,
    generate_bass_line,
    generate_drum_pattern,
    validate_melody,
    render_midi,
]
