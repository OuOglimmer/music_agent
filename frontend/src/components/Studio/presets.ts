import type { LucideIcon } from "lucide-react"
import { AudioLines, Disc3, Guitar, Piano, Zap } from "lucide-react"

export type StyleId = "ballad" | "pop" | "rock" | "edm" | "folk"

export type StylePreset = {
  id: StyleId
  label: string
  en: string
  desc: string
  icon: LucideIcon
  /** 卡片渐变（与全局主题呼应） */
  gradient: string
  /** 选中该风格时的推荐参数 */
  bpm: number
  mode: "major" | "minor"
  mood: string
}

export const STYLE_PRESETS: StylePreset[] = [
  {
    id: "pop",
    label: "流行",
    en: "Pop",
    desc: "明快旋律，抓耳副歌",
    icon: AudioLines,
    gradient: "from-violet-500 to-fuchsia-500",
    bpm: 100,
    mode: "major",
    mood: "明快",
  },
  {
    id: "ballad",
    label: "抒情",
    en: "Ballad",
    desc: "慢板叙事，情感细腻",
    icon: Piano,
    gradient: "from-sky-500 to-indigo-500",
    bpm: 72,
    mode: "major",
    mood: "温柔",
  },
  {
    id: "rock",
    label: "摇滚",
    en: "Rock",
    desc: "强劲节拍，失真张力",
    icon: Zap,
    gradient: "from-rose-500 to-orange-500",
    bpm: 128,
    mode: "minor",
    mood: "激昂",
  },
  {
    id: "edm",
    label: "电音",
    en: "EDM",
    desc: "合成器律动，舞池能量",
    icon: Disc3,
    gradient: "from-cyan-500 to-blue-600",
    bpm: 126,
    mode: "minor",
    mood: "梦幻",
  },
  {
    id: "folk",
    label: "民谣",
    en: "Folk",
    desc: "木吉他质感，质朴诗意",
    icon: Guitar,
    gradient: "from-emerald-500 to-teal-600",
    bpm: 88,
    mode: "major",
    mood: "清新",
  },
]

export const styleById = (id: string): StylePreset | undefined =>
  STYLE_PRESETS.find((s) => s.id === id)

export const MOOD_PRESETS = [
  "温暖",
  "忧郁",
  "激昂",
  "梦幻",
  "宁静",
  "神秘",
  "浪漫",
  "治愈",
] as const

export const KEY_OPTIONS = [
  "C",
  "C#",
  "D",
  "D#",
  "E",
  "F",
  "F#",
  "G",
  "G#",
  "A",
  "A#",
  "B",
] as const

export const BEATS_PER_BAR_OPTIONS = [2, 3, 4, 6] as const

export const LYRIC_TAGS = [
  "Intro",
  "Verse",
  "Pre-Chorus",
  "Chorus",
  "Bridge",
  "Outro",
] as const

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ""
  const diff = Date.now() - then
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return "刚刚"
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`
  return new Date(iso).toLocaleDateString("zh-CN")
}

export function basename(path: string | null | undefined): string | null {
  if (!path) return null
  const name = path.split(/[\\/]/).pop()
  return name || null
}
