"use client";

import {
  Download,
  Music2,
  Pause,
  Play,
  Repeat,
  SkipBack,
  SkipForward,
  Volume2,
} from "lucide-react"
import { type RefObject, useEffect, useRef, useState } from "react"

import {
  formatRelativeTime,
  styleById,
} from "@/components/Studio/presets"
import { useStudio } from "@/components/Studio/StudioContext"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import useCustomToast from "@/hooks/useCustomToast"

const fmtTime = (seconds: number) => {
  if (!Number.isFinite(seconds)) return "0:00"
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, "0")}`
}

/** 伪随机（确定性）波形，用于 MIDI-only 作品的占位可视化 */
function pseudoPeaks(seed: string, count = 120): number[] {
  let h = 2166136261
  for (const ch of seed) {
    h ^= ch.charCodeAt(0)
    h = Math.imul(h, 16777619)
  }
  const peaks: number[] = []
  for (let i = 0; i < count; i++) {
    h = Math.imul(h ^ (h >>> 13), 1274126177)
    const v = ((h >>> 0) % 1000) / 1000
    const envelope = 0.35 + 0.65 * Math.abs(Math.sin((i / count) * Math.PI * 3))
    peaks.push(0.15 + v * 0.85 * envelope)
  }
  return peaks
}

function Waveform({
  peaks,
  progress,
  interactive,
  onSeek,
  canvasRef,
}: {
  peaks: number[]
  progress: number
  interactive: boolean
  onSeek: (ratio: number) => void
  canvasRef: RefObject<HTMLCanvasElement | null>
}) {
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return

    const draw = () => {
      const dpr = window.devicePixelRatio || 1
      const width = wrap.clientWidth
      const height = 88
      canvas.width = width * dpr
      canvas.height = height * dpr
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      const ctx = canvas.getContext("2d")
      if (!ctx) return
      ctx.scale(dpr, dpr)
      ctx.clearRect(0, 0, width, height)

      const gap = 2
      const barWidth = Math.max(2, width / peaks.length - gap)
      const playedX = width * progress
      for (let i = 0; i < peaks.length; i++) {
        const x = (i * width) / peaks.length
        const barH = Math.max(3, peaks[i] * (height - 8))
        const y = (height - barH) / 2
        const played = x < playedX
        if (played) {
          const gradient = ctx.createLinearGradient(0, y, 0, y + barH)
          gradient.addColorStop(0, "#8b5cf6")
          gradient.addColorStop(1, "#d946ef")
          ctx.fillStyle = gradient
        } else {
          ctx.fillStyle = "oklch(0.55 0 0 / 0.35)"
        }
        const r = Math.min(barWidth / 2, 2)
        ctx.beginPath()
        ctx.roundRect(x, y, barWidth, barH, r)
        ctx.fill()
      }
    }

    draw()
    const observer = new ResizeObserver(draw)
    observer.observe(wrap)
    return () => observer.disconnect()
  }, [peaks, progress, canvasRef])

  const handlePointer = (e: React.PointerEvent) => {
    if (!interactive) return
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect) return
    onSeek((e.clientX - rect.left) / rect.width)
  }

  return (
    <div
      ref={wrapRef}
      onPointerDown={handlePointer}
      onPointerMove={(e) => e.buttons === 1 && handlePointer(e)}
      className={cn(
        "relative w-full select-none",
        interactive ? "cursor-pointer" : "cursor-default",
      )}
    >
      <canvas ref={canvasRef} className="block w-full" />
    </div>
  )
}

export function PlayerPanel() {
  const {
    currentTrack,
    isPlaying,
    togglePlay,
    position,
    duration,
    seek,
    volume,
    setVolume,
    loop,
    toggleLoop,
    peaks,
    playNext,
    tracks,
  } = useStudio()
  const { showErrorToast, showSuccessToast } = useCustomToast()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [hoverVolume, setHoverVolume] = useState(false)

  const preset = currentTrack ? styleById(currentTrack.style) : undefined
  const progress = duration > 0 ? position / duration : 0
  const displayPeaks = peaks ?? (currentTrack ? pseudoPeaks(currentTrack.id) : [])
  const hasRealAudio = Boolean(currentTrack?.mp3File)

  const download = async (file: string | null, ext: string) => {
    if (!file) return
    try {
      const token = localStorage.getItem("access_token") ?? ""
      const res = await fetch(
        `/api/v1/agent/scores/${encodeURIComponent(file)}`,
        { headers: token ? { Authorization: `Bearer ${token}` } : undefined },
      )
      if (!res.ok) throw new Error(`下载失败（HTTP ${res.status}）`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = file
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      showErrorToast(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <aside className="hidden w-80 shrink-0 flex-col overflow-hidden border-l bg-sidebar text-sidebar-foreground xl:flex">
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        <h2 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          播放器
        </h2>
        {tracks.length > 0 && (
          <span className="text-xs text-muted-foreground">
            {tracks.length} 个作品
          </span>
        )}
      </div>

      {currentTrack ? (
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
          {/* 封面 */}
          <div
            className={cn(
              "bg-mesh relative flex aspect-square w-full items-center justify-center overflow-hidden rounded-2xl border bg-gradient-to-br",
              preset?.gradient ?? "from-violet-500 to-fuchsia-500",
            )}
          >
            <Music2
              className={cn(
                "size-14 text-white/90 transition-transform",
                isPlaying && "animate-pulse",
              )}
            />
            {isPlaying && (
              <div className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-end gap-0.5">
                {[0, 1, 2, 3, 4, 5, 6].map((i) => (
                  <span
                    key={i}
                    className="w-1 animate-eq rounded-full bg-white/80"
                    style={{
                      height: 16,
                      animationDelay: `${i * 110}ms`,
                      animationDuration: `${0.9 + (i % 3) * 0.25}s`,
                    }}
                  />
                ))}
              </div>
            )}
          </div>

          {/* 信息 */}
          <div className="space-y-1.5">
            <h3 className="truncate text-base font-semibold">
              {currentTrack.title}
            </h3>
            <p className="text-xs text-muted-foreground">
              {preset?.label ?? currentTrack.style} ·{" "}
              {currentTrack.key}
              {currentTrack.mode === "minor" ? " 小调" : " 大调"} ·{" "}
              {currentTrack.bpm} BPM · {currentTrack.bars} 小节 ·{" "}
              {formatRelativeTime(currentTrack.createdAt)}
            </p>
            {!hasRealAudio && (
              <p className="text-[11px] text-amber-600 dark:text-amber-400">
                当前作品仅有 MIDI 总谱，以下为示意波形
              </p>
            )}
          </div>

          {/* 和弦 */}
          {currentTrack.chords.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                和弦进行
              </p>
              <div className="flex flex-wrap gap-1.5">
                {currentTrack.chords.map((chord, i) => (
                  <span
                    key={`${chord}-${i}`}
                    className="rounded-lg border bg-background px-2 py-0.5 font-mono text-xs"
                  >
                    {chord}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* 波形 */}
          <div className="rounded-2xl border bg-background p-2">
            <Waveform
              peaks={displayPeaks}
              progress={progress}
              interactive={hasRealAudio && duration > 0}
              onSeek={seek}
              canvasRef={canvasRef}
            />
            <div className="flex justify-between px-1 pt-1 font-mono text-[11px] text-muted-foreground">
              <span>{fmtTime(position)}</span>
              <span>{fmtTime(duration)}</span>
            </div>
          </div>

          {/* 控制 */}
          <div className="space-y-3">
            <div className="flex items-center justify-center gap-3">
              <Button
                variant="ghost"
                size="icon"
                aria-label="上一个"
                onClick={() => playNext(-1)}
                className="cursor-pointer"
              >
                <SkipBack className="size-4.5" />
              </Button>
              <button
                type="button"
                aria-label={isPlaying ? "暂停" : "播放"}
                onClick={togglePlay}
                disabled={!hasRealAudio}
                className="flex size-12 cursor-pointer items-center justify-center rounded-full bg-gradient-to-r from-violet-600 to-fuchsia-500 text-white shadow-lg shadow-violet-500/25 transition-all hover:opacity-90 hover:shadow-violet-500/35 disabled:opacity-40"
              >
                {isPlaying ? (
                  <Pause className="size-5" fill="currentColor" />
                ) : (
                  <Play className="ml-0.5 size-5" fill="currentColor" />
                )}
              </button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="下一个"
                onClick={() => playNext(1)}
                className="cursor-pointer"
              >
                <SkipForward className="size-4.5" />
              </Button>
            </div>

            <div className="flex items-center justify-between">
              <Button
                variant="ghost"
                size="icon"
                aria-label="循环播放"
                onClick={toggleLoop}
                className={cn(
                  "size-8 cursor-pointer",
                  loop && "text-primary",
                )}
              >
                <Repeat className="size-4" />
              </Button>
              <div
                className="flex items-center gap-2"
                onMouseEnter={() => setHoverVolume(true)}
                onMouseLeave={() => setHoverVolume(false)}
              >
                <Volume2 className="size-4 text-muted-foreground" />
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={volume}
                  onChange={(e) => setVolume(Number(e.target.value))}
                  aria-label="音量"
                  className={cn(
                    "h-1 cursor-pointer appearance-none rounded-full bg-muted accent-violet-600 transition-all [&::-webkit-slider-thumb]:size-3 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-violet-600",
                    "w-20",
                  )}
                  style={{
                    background: `linear-gradient(to right, var(--color-violet-600) ${volume * 100}%, var(--color-muted) ${volume * 100}%)`,
                  }}
                />
              </div>
            </div>

            {/* 下载 */}
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                className="h-8 flex-1 cursor-pointer gap-1.5 rounded-lg text-xs"
                disabled={!currentTrack.mp3File}
                onClick={() => download(currentTrack.mp3File, "mp3")}
              >
                <Download className="size-3.5" />
                MP3
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-8 flex-1 cursor-pointer gap-1.5 rounded-lg text-xs"
                disabled={!currentTrack.midiFile}
                onClick={() => {
                  download(currentTrack.midiFile, "mid")
                  showSuccessToast("MIDI 已开始下载")
                }}
              >
                <Download className="size-3.5" />
                MIDI
              </Button>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
          <div className="bg-mesh flex size-16 items-center justify-center rounded-3xl border">
            <Music2 className="size-7 text-primary" />
          </div>
          <h3 className="text-sm font-semibold">还没有选择作品</h3>
          <p className="text-xs leading-relaxed text-muted-foreground">
            从左侧作品库选择一首，或在「创作」页
            <br />
            生成你的第一段音乐，这里将展示
            <br />
            波形、和弦与下载。
          </p>
        </div>
      )}
    </aside>
  )
}
