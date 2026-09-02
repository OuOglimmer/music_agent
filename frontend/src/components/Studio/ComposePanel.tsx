"use client";

import {
  Check,
  CircleAlert,
  Loader2,
  Sparkles,
  Square,
} from "lucide-react"

import {
  BEATS_PER_BAR_OPTIONS,
  KEY_OPTIONS,
  MOOD_PRESETS,
  STYLE_PRESETS,
} from "@/components/Studio/presets"
import { useStudio } from "@/components/Studio/StudioContext"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

/** 自定义轨道滑杆：原生 range + 主题渐变轨道 */
function ParamSlider({
  value,
  min,
  max,
  step,
  onChange,
}: {
  value: number
  min: number
  max: number
  step: number
  onChange: (v: number) => void
}) {
  const pct = ((value - min) / (max - min)) * 100
  return (
    <input
      type="range"
      value={value}
      min={min}
      max={max}
      step={step}
      onChange={(e) => onChange(Number(e.target.value))}
      className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-muted accent-violet-600 outline-none [&::-webkit-slider-thumb]:size-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-white [&::-webkit-slider-thumb]:bg-violet-600 [&::-webkit-slider-thumb]:shadow [&::-moz-range-thumb]:size-4 [&::-moz-range-thumb]:appearance-none [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-white [&::-moz-range-thumb]:bg-violet-600"
      style={{
        background: `linear-gradient(to right, var(--color-violet-600) ${pct}%, var(--color-fuchsia-500) ${pct}%, var(--color-muted) ${pct}%)`,
      }}
    />
  )
}

export function ComposePanel() {
  const {
    params,
    setParams,
    stages,
    isComposing,
    composeError,
    startCompose,
    stopCompose,
    activeSessionTitle,
  } = useStudio()

  const toggleMode = (mode: "major" | "minor") => setParams({ mode })

  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col gap-5 overflow-y-auto p-4 md:p-6">
      {/* 风格选择 */}
      <section>
        <div className="mb-2.5 flex items-center justify-between">
          <h3 className="text-sm font-semibold">音乐风格</h3>
          <span className="text-xs text-muted-foreground">
            选择一种风格，参数将自动匹配
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
          {STYLE_PRESETS.map((preset) => {
            const selected = params.style === preset.id
            const Icon = preset.icon
            return (
              <button
                key={preset.id}
                type="button"
                onClick={() => setParams({ style: preset.id })}
                className={cn(
                  "group relative cursor-pointer overflow-hidden rounded-2xl border bg-card p-3 text-left transition-all hover:shadow-md",
                  selected &&
                    "border-primary ring-primary/30 ring-2 shadow-md",
                )}
              >
                <div
                  className={cn(
                    "mb-2 flex size-9 items-center justify-center rounded-xl bg-gradient-to-br text-white transition-transform group-hover:scale-105",
                    preset.gradient,
                  )}
                >
                  <Icon className="size-4.5" />
                </div>
                <p className="text-sm font-medium">
                  {preset.label}
                  <span className="ml-1 text-xs font-normal text-muted-foreground">
                    {preset.en}
                  </span>
                </p>
                <p className="mt-0.5 text-xs leading-snug text-muted-foreground">
                  {preset.desc}
                </p>
                {selected && (
                  <span className="bg-primary text-primary-foreground absolute top-2 right-2 flex size-4.5 items-center justify-center rounded-full">
                    <Check className="size-3" />
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </section>

      {/* 情绪 */}
      <section>
        <h3 className="mb-2.5 text-sm font-semibold">音乐情绪</h3>
        <div className="flex flex-wrap gap-1.5">
          {MOOD_PRESETS.map((mood) => (
            <button
              key={mood}
              type="button"
              onClick={() => setParams({ mood })}
              className={cn(
                "cursor-pointer rounded-full border px-3 py-1 text-xs transition-colors",
                params.mood === mood
                  ? "border-primary bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent",
              )}
            >
              {mood}
            </button>
          ))}
        </div>
      </section>

      {/* 参数 */}
      <section className="grid grid-cols-2 gap-x-6 gap-y-4">
        <div className="col-span-2 grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1.5 block text-xs text-muted-foreground">
              调性
            </label>
            <Select
              value={params.key}
              onValueChange={(v) => setParams({ key: v })}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KEY_OPTIONS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {k} 调
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="mb-1.5 block text-xs text-muted-foreground">
              调式
            </label>
            <div className="grid grid-cols-2 gap-1 rounded-lg border p-1">
              <button
                type="button"
                onClick={() => toggleMode("major")}
                className={cn(
                  "cursor-pointer rounded-md px-2 py-1 text-xs transition-colors",
                  params.mode === "major"
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent",
                )}
              >
                大调 明亮
              </button>
              <button
                type="button"
                onClick={() => toggleMode("minor")}
                className={cn(
                  "cursor-pointer rounded-md px-2 py-1 text-xs transition-colors",
                  params.mode === "minor"
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent",
                )}
              >
                小调 黯淡
              </button>
            </div>
          </div>
        </div>

        <div className="col-span-2">
          <div className="mb-1.5 flex items-center justify-between">
            <label className="text-xs text-muted-foreground">
              速度（BPM）
            </label>
            <span className="text-primary font-mono text-xs font-semibold">
              {params.bpm}
            </span>
          </div>
          <ParamSlider
            value={params.bpm}
            min={40}
            max={220}
            step={1}
            onChange={(v) => setParams({ bpm: v })}
          />
          <div className="mt-1.5 flex justify-between text-[11px] text-muted-foreground">
            <button
              type="button"
              className="cursor-pointer hover:text-foreground"
              onClick={() => setParams({ bpm: 72 })}
            >
              慢板 72
            </button>
            <button
              type="button"
              className="cursor-pointer hover:text-foreground"
              onClick={() => setParams({ bpm: 100 })}
            >
              中速 100
            </button>
            <button
              type="button"
              className="cursor-pointer hover:text-foreground"
              onClick={() => setParams({ bpm: 128 })}
            >
              快板 128
            </button>
          </div>
        </div>

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <label className="text-xs text-muted-foreground">小节数</label>
            <span className="text-primary font-mono text-xs font-semibold">
              {params.bars}
            </span>
          </div>
          <ParamSlider
            value={params.bars}
            min={4}
            max={64}
            step={4}
            onChange={(v) => setParams({ bars: v })}
          />
        </div>

        <div>
          <label className="mb-1.5 block text-xs text-muted-foreground">
            拍号
          </label>
          <Select
            value={String(params.beatsPerBar)}
            onValueChange={(v) => setParams({ beatsPerBar: Number(v) })}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {BEATS_PER_BAR_OPTIONS.map((b) => (
                <SelectItem key={b} value={String(b)}>
                  {b}/4 拍
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="col-span-2">
          <label className="mb-1.5 block text-xs text-muted-foreground">
            创作备注（可选）
          </label>
          <Textarea
            value={params.userNotes}
            onChange={(e) => setParams({ userNotes: e.target.value })}
            placeholder="例如：副歌部分希望更开阔，加入弦乐铺底…"
            className="min-h-16 resize-none text-sm"
            maxLength={2000}
          />
        </div>
      </section>

      {/* 流水线进度 */}
      {(stages.length > 0 || isComposing || composeError) && (
        <section className="rounded-2xl border bg-card p-3">
          <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            <Sparkles className="size-3.5 text-primary" />
            多 Agent 创作流水线
          </h3>
          <ol className="space-y-1.5">
            {stages.map((stage, i) => (
              <li key={`${stage.node}-${i}`} className="flex items-start gap-2">
                <Check className="mt-0.5 size-3.5 shrink-0 text-emerald-500" />
                <div className="min-w-0">
                  <span className="text-xs font-medium">{stage.label}</span>
                  {stage.detail && (
                    <span className="ml-2 text-xs break-words text-muted-foreground">
                      {stage.detail}
                    </span>
                  )}
                </div>
              </li>
            ))}
            {isComposing && (
              <li className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" />
                正在推进下一步…
              </li>
            )}
          </ol>
          {composeError && (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-destructive">
              <CircleAlert className="size-3.5" />
              {composeError}
            </p>
          )}
        </section>
      )}

      {/* 开始创作 */}
      <div className="sticky bottom-0 -mx-4 border-t bg-background/80 px-4 py-3 backdrop-blur md:-mx-6 md:px-6">
        <div className="flex items-center gap-3">
          <Button
            onClick={startCompose}
            disabled={isComposing}
            className="h-11 flex-1 cursor-pointer rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-500 text-sm font-semibold text-white shadow-lg shadow-violet-500/20 transition-all hover:opacity-90 hover:shadow-violet-500/30 disabled:opacity-60"
          >
            {isComposing ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                创作中…（和声 → 旋律 → 贝斯 → 鼓 → 评审 → 渲染）
              </>
            ) : (
              <>
                <Sparkles className="size-4" />
                开始创作「{activeSessionTitle}」
              </>
            )}
          </Button>
          {isComposing && (
            <Button
              variant="outline"
              size="icon"
              aria-label="停止创作"
              onClick={stopCompose}
              className="size-11 rounded-xl"
            >
              <Square className="size-4" fill="currentColor" />
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
