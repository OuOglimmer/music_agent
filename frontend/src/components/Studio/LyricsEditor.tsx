"use client";

import {
  Bot,
  Copy,
  FileText,
  Loader2,
  Plus,
  Wand2,
} from "lucide-react"
import { useState } from "react"

import { streamChat } from "@/components/Agent/streamChat"
import { LYRIC_TAGS } from "@/components/Studio/presets"
import { useStudio } from "@/components/Studio/StudioContext"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import useCustomToast from "@/hooks/useCustomToast"

export function LyricsEditor() {
  const { lyrics, setLyrics, activeSessionId } = useStudio()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const [theme, setTheme] = useState("")
  const [aiOutput, setAiOutput] = useState("")
  const [isGenerating, setIsGenerating] = useState(false)

  const insertTag = (tag: string) => {
    const el = document.activeElement
    const block = `\n[${tag}]\n`
    setLyrics(lyrics + block)
    el?.dispatchEvent(new KeyboardEvent("keydown", { key: "End" }))
  }

  const generateLyrics = () => {
    if (isGenerating) return
    const prompt = [
      "请为我的歌曲创作歌词。",
      `风格主题：${theme || "由你发挥"}`,
      "要求：使用 [Verse]、[Pre-Chorus]、[Chorus]、[Bridge]、[Outro] 等结构标签分段，",
      "每段之间留一个空行，语言自然有画面感，先输出歌词正文。",
    ].join("\n")
    setAiOutput("")
    setIsGenerating(true)
    streamChat(
      { text: prompt, sessionId: activeSessionId, files: [] },
      {
        onSession: () => {
          // 歌词生成同样挂到当前项目会话，刷新项目列表
        },
        onDelta: (delta) => setAiOutput((prev) => prev + delta),
      },
    )
      .then((result) => setAiOutput(result.response))
      .catch((err: unknown) =>
        showErrorToast(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setIsGenerating(false))
  }

  const lineCount = lyrics ? lyrics.split("\n").length : 0

  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col gap-3 overflow-hidden p-4 md:p-6">
      {/* 结构标签工具栏 */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 flex items-center gap-1 text-xs text-muted-foreground">
          <FileText className="size-3.5" />
          插入段落：
        </span>
        {LYRIC_TAGS.map((tag) => (
          <button
            key={tag}
            type="button"
            onClick={() => insertTag(tag)}
            className="cursor-pointer rounded-full border px-2.5 py-1 font-mono text-[11px] text-muted-foreground transition-colors hover:border-primary/50 hover:bg-primary/5 hover:text-primary"
          >
            [{tag}]
          </button>
        ))}
      </div>

      {/* 编辑器 */}
      <div className="flex min-h-0 flex-1 flex-col rounded-2xl border bg-card focus-within:border-primary/50">
        <Textarea
          value={lyrics}
          onChange={(e) => setLyrics(e.target.value)}
          placeholder={
            "在这里写下你的歌词…\n\n[Intro]\n[Verse]\n夜色穿过未关的窗\n[Chorus]\n…\n\n提示：点击上方标签快速插入段落结构，内容按项目自动保存。"
          }
          className="min-h-0 flex-1 resize-none rounded-none border-0 bg-transparent font-mono text-sm leading-relaxed focus-visible:ring-0"
        />
        <div className="flex items-center justify-between border-t px-3 py-1.5 text-[11px] text-muted-foreground">
          <span>
            {lyrics.length} 字 · {lineCount} 行 · 按项目自动保存
          </span>
          <button
            type="button"
            onClick={() => {
              navigator.clipboard.writeText(lyrics)
              showSuccessToast("歌词已复制")
            }}
            disabled={!lyrics}
            className="flex cursor-pointer items-center gap-1 hover:text-foreground disabled:opacity-40"
          >
            <Copy className="size-3" />
            复制全文
          </button>
        </div>
      </div>

      {/* AI 生成 */}
      <div className="shrink-0 space-y-2">
        <div className="flex gap-2">
          <input
            value={theme}
            onChange={(e) => setTheme(e.target.value)}
            placeholder="歌词主题，例如：夏夜、离别、追梦…"
            maxLength={100}
            className="h-9 flex-1 rounded-xl border bg-card px-3 text-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-primary/50"
          />
          <Button
            size="sm"
            onClick={generateLyrics}
            disabled={isGenerating}
            className="h-9 cursor-pointer gap-1.5 rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-500 px-3 text-xs font-semibold text-white hover:opacity-90"
          >
            {isGenerating ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Wand2 className="size-3.5" />
            )}
            AI 写歌词
          </Button>
        </div>
        {(aiOutput || isGenerating) && (
          <div className="max-h-48 space-y-2 overflow-y-auto rounded-2xl border bg-card p-3">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
              <Bot className="size-3.5 text-primary" />
              AI 歌词草稿
            </p>
            <pre className="font-mono text-xs leading-relaxed whitespace-pre-wrap">
              {aiOutput || "生成中…"}
            </pre>
            {!isGenerating && aiOutput && (
              <div className="flex gap-2 pt-1">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 cursor-pointer gap-1 rounded-lg px-2.5 text-[11px]"
                  onClick={() => {
                    setLyrics(`${lyrics ? `${lyrics}\n\n` : ""}${aiOutput}`)
                    showSuccessToast("已插入到编辑器")
                  }}
                >
                  <Plus className="size-3" />
                  插入到编辑器
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 cursor-pointer gap-1 rounded-lg px-2.5 text-[11px]"
                  onClick={() => setLyrics(aiOutput)}
                >
                  替换编辑器内容
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
