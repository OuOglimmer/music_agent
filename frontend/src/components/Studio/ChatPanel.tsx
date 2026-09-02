"use client";

import {
  ArrowUp,
  Bot,
  FileText,
  Paperclip,
  Square,
  X,
} from "lucide-react"
import { type KeyboardEvent, useEffect, useRef, useState } from "react"

import { Markdown } from "@/components/Agent/Markdown"
import { useStudio } from "@/components/Studio/StudioContext"
import { Button } from "@/components/ui/button"

const MAX_INPUT_LENGTH = 10000

function PendingDots() {
  return (
    <div className="flex items-center gap-1.5 py-2 text-sm text-muted-foreground">
      <span>正在思考</span>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1.5 animate-bounce rounded-full bg-muted-foreground/70"
          style={{ animationDelay: `${i * 150}ms` }}
        />
      ))}
    </div>
  )
}

export function ChatPanel() {
  const {
    messages,
    isStreaming,
    sendMessage,
    retryMessage,
    stopStreaming,
    attachments,
    addAttachments,
    removeAttachment,
  } = useStudio()

  const [input, setInput] = useState("")
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const composingRef = useRef(false)

  const lastMessage = messages[messages.length - 1]
  useEffect(() => {
    const el = scrollRef.current
    if (el && lastMessage) {
      el.scrollTop = el.scrollHeight
    }
  }, [lastMessage])

  const handleTextareaInput = (value: string) => {
    setInput(value)
    const el = textareaRef.current
    if (el) {
      el.style.height = "auto"
      el.style.height = `${Math.min(el.scrollHeight, 160)}px`
    }
  }

  const handleSend = () => {
    if ((!input.trim() && attachments.length === 0) || isStreaming) return
    sendMessage(input)
    setInput("")
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto"
    }
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !composingRef.current) {
      event.preventDefault()
      handleSend()
    }
  }

  const isEmpty = messages.length === 0

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {isEmpty ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
            <div className="bg-mesh flex size-16 items-center justify-center rounded-3xl border shadow-inner">
              <Bot className="size-7 text-primary" />
            </div>
            <h2 className="text-xl font-semibold">与 AI 聊聊你的想法</h2>
            <p className="max-w-sm text-sm text-muted-foreground">
              讨论创作思路、上传参考图片或文档，
              <br />
              Agent 会结合项目上下文给你灵感。
            </p>
          </div>
        ) : (
          <div className="mx-auto max-w-2xl space-y-5 px-4 py-5">
            {messages.map((message) =>
              message.role === "user" ? (
                <div key={message.id} className="flex justify-end">
                  <div className="max-w-[85%] space-y-2">
                    {message.attachments &&
                      message.attachments.length > 0 && (
                        <div className="flex flex-wrap justify-end gap-2">
                          {message.attachments.map((att) =>
                            att.url ? (
                              <img
                                key={att.id}
                                src={att.url}
                                alt={att.file.name}
                                className="max-h-36 rounded-xl border object-cover"
                              />
                            ) : (
                              <div
                                key={att.id}
                                className="flex items-center gap-1.5 rounded-xl border bg-card px-2.5 py-1.5 text-xs"
                              >
                                <FileText
                                  size={13}
                                  className="shrink-0 text-muted-foreground"
                                />
                                <span className="max-w-36 truncate">
                                  {att.file.name}
                                </span>
                              </div>
                            ),
                          )}
                        </div>
                      )}
                    {message.content && (
                      <div className="rounded-2xl rounded-br-sm bg-primary/10 px-4 py-2.5 text-sm whitespace-pre-wrap break-words">
                        {message.content}
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div key={message.id} className="flex items-start gap-3">
                  <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                    <Bot size={16} />
                  </div>
                  <div className="min-w-0 flex-1 pt-1 text-sm">
                    {message.status === "pending" ? (
                      message.content ? (
                        <Markdown>{message.content}</Markdown>
                      ) : (
                        <PendingDots />
                      )
                    ) : message.status === "error" ? (
                      <div className="space-y-2">
                        <p className="text-destructive">
                          请求失败：{message.content}
                        </p>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => retryMessage(message)}
                          disabled={isStreaming || !message.retryText}
                        >
                          重试
                        </Button>
                      </div>
                    ) : (
                      <Markdown>{message.content}</Markdown>
                    )}
                  </div>
                </div>
              ),
            )}
          </div>
        )}
      </div>

      {/* 输入区 */}
      <div className="shrink-0 px-4 pb-4">
        <div className="mx-auto max-w-2xl rounded-3xl border bg-card p-3 shadow-sm transition-colors focus-within:border-primary/50">
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-2 px-1 pb-2">
              {attachments.map((att) => (
                <span
                  key={att.id}
                  className="relative flex h-8 max-w-40 items-center gap-1.5 rounded-lg border bg-muted/50 px-2 text-xs"
                >
                  {att.url ? (
                    <img
                      src={att.url}
                      alt={att.file.name}
                      className="size-5 rounded object-cover"
                    />
                  ) : (
                    <FileText size={13} className="shrink-0" />
                  )}
                  <span className="truncate">{att.file.name}</span>
                  <button
                    type="button"
                    aria-label="移除附件"
                    onClick={() => removeAttachment(att.id)}
                    className="cursor-pointer text-muted-foreground hover:text-destructive"
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <textarea
            ref={textareaRef}
            value={input}
            rows={1}
            maxLength={MAX_INPUT_LENGTH}
            placeholder="和 Agent 聊聊创作灵感…（Enter 发送）"
            onChange={(e) => handleTextareaInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onCompositionStart={() => {
              composingRef.current = true
            }}
            onCompositionEnd={() => {
              composingRef.current = false
            }}
            className="max-h-40 w-full resize-none bg-transparent px-2 text-sm outline-none placeholder:text-muted-foreground"
          />
          <div className="flex items-center justify-between pt-1">
            <div className="flex items-center gap-1 pl-1">
              <button
                type="button"
                aria-label="添加附件"
                onClick={() => fileInputRef.current?.click()}
                className="flex size-8 cursor-pointer items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                <Paperclip size={15} />
              </button>
              <span className="text-xs text-muted-foreground">
                Shift + Enter 换行
              </span>
            </div>
            {isStreaming ? (
              <button
                type="button"
                aria-label="停止生成"
                onClick={stopStreaming}
                className="flex size-8 cursor-pointer items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90"
              >
                <Square size={13} fill="currentColor" />
              </button>
            ) : (
              <button
                type="button"
                aria-label="发送"
                disabled={!input.trim() && attachments.length === 0}
                onClick={handleSend}
                className="flex size-8 cursor-pointer items-center justify-center rounded-full bg-gradient-to-r from-violet-600 to-fuchsia-500 text-white transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                <ArrowUp size={15} />
              </button>
            )}
          </div>
        </div>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/png,image/jpeg,image/webp,image/gif,.txt,.md"
        className="hidden"
        onChange={(e) => {
          addAttachments(e.target.files)
          e.target.value = ""
        }}
      />
    </div>
  )
}
