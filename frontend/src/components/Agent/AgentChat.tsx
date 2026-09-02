import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import Link from "next/link"
import { AxiosError } from "axios"
import {
  ArrowUp,
  Bot,
  FileText,
  LifeBuoy,
  Paperclip,
  Plus,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  X,
} from "lucide-react"
import { type KeyboardEvent, useEffect, useRef, useState } from "react"
import { AgentService } from "@/client"
import { Markdown } from "@/components/Agent/Markdown"
import { streamChat } from "@/components/Agent/streamChat"
import {
  requestIntervention,
  submitFeedback,
} from "@/components/Agent/governance"
import { Button } from "@/components/ui/button"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"

type Attachment = {
  id: string
  file: File
  url?: string
}

type ChatMessage = {
  id: string
  role: "user" | "assistant"
  content: string
  status: "done" | "pending" | "error"
  retryText?: string
  attachments?: Attachment[]
  // 后端落库后的消息 ID（历史加载的消息 id 即服务端 id），反馈用
  serverId?: string
  // 意图识别结果（meta 事件或历史消息携带）
  intent?: { key: string; label: string } | null
  escalated?: boolean
  // 本地反馈状态：提交成功后标记，避免重复提交
  feedback?: "up" | "down"
}

// 历史消息只有意图 key，用本地映射补展示名（与后端 INTENT_LABELS 一致）
const INTENT_LABELS: Record<string, string> = {
  lyric_writing: "歌词创作",
  arrangement: "编曲与乐理",
  compose_pipeline: "完整创作",
  music_qa: "乐理问答",
  human_handoff: "转人工",
  feedback: "反馈与投诉",
  chat: "闲聊与其他",
}

type ChatVars = {
  text: string
  sessionId: string | null
  files: File[]
  replyId: string
  signal?: AbortSignal
}

const DRAFT_KEY = "__draft__"
const MAX_INPUT_LENGTH = 10000
const MAX_FILES = 5
const ACCEPT = "image/png,image/jpeg,image/webp,image/gif,.txt,.md"

const keyOf = (id: string | null) => id ?? DRAFT_KEY
const newId = () => crypto.randomUUID()

const getErrorMessage = (err: Error) =>
  err instanceof AxiosError
    ? ((err.response?.data as { detail?: string } | undefined)?.detail ??
      err.message)
    : err.message

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

function AssistantAvatar() {
  return (
    <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
      <Bot size={16} />
    </div>
  )
}

export function AgentChat() {
  const { user: currentUser } = useAuth()
  const { showErrorToast, showSuccessToast } = useCustomToast()
  const queryClient = useQueryClient()

  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState("")
  const [attachments, setAttachments] = useState<Attachment[]>([])

  const activeIdRef = useRef<string | null>(null)
  const cacheRef = useRef<Record<string, ChatMessage[]>>({})
  const abortRef = useRef<AbortController | null>(null)
  // 正在流式生成的会话 key（新会话首条消息可能从 DRAFT_KEY 迁移到真实会话）
  const pendingTargetRef = useRef<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const composingRef = useRef(false)

  const addFiles = (fileList: FileList | null) => {
    const incoming = Array.from(fileList ?? [])
    if (incoming.length === 0) {
      return
    }
    const room = MAX_FILES - attachments.length
    if (incoming.length > room) {
      showErrorToast(`最多上传 ${MAX_FILES} 个文件`)
    }
    const next = incoming.slice(0, Math.max(room, 0)).map((file) => ({
      id: newId(),
      file,
      url: file.type.startsWith("image/")
        ? URL.createObjectURL(file)
        : undefined,
    }))
    setAttachments((prev) => [...prev, ...next])
    if (fileInputRef.current) {
      fileInputRef.current.value = ""
    }
  }

  const removeAttachment = (id: string) => {
    setAttachments((prev) => {
      const target = prev.find((a) => a.id === id)
      if (target?.url) {
        URL.revokeObjectURL(target.url)
      }
      return prev.filter((a) => a.id !== id)
    })
  }

  const sessionsQuery = useQuery({
    queryKey: ["agent", "sessions"],
    queryFn: async () => (await AgentService.listSessions()).data,
  })

  const updateMessages = (
    key: string,
    updater: (prev: ChatMessage[]) => ChatMessage[],
  ) => {
    const next = updater(cacheRef.current[key] ?? [])
    cacheRef.current[key] = next
    if (keyOf(activeIdRef.current) === key) {
      setMessages(next)
    }
  }

  const switchConversation = (id: string | null) => {
    if (id === activeIdRef.current) {
      return
    }
    cacheRef.current[keyOf(activeIdRef.current)] = messages
    activeIdRef.current = id
    setActiveId(id)
    const cached = cacheRef.current[keyOf(id)]
    setMessages(cached ?? [])
    setInput("")
    textareaRef.current?.focus()
    if (id !== null && cached === undefined) {
      loadSessionMessages(id)
    }
  }

  const loadSessionMessages = (sessionId: string) => {
    queryClient
      .fetchQuery({
        queryKey: ["agent", "messages", sessionId],
        queryFn: async () =>
          (
            await AgentService.listSessionMessages({
              path: { session_id: sessionId },
            })
          ).data,
        staleTime: Infinity,
      })
      .then((data) => {
        const loaded: ChatMessage[] = data.data.map((m) => ({
          id: m.id,
          role: m.role === "user" ? "user" : "assistant",
          content: m.content,
          status: "done",
          intent:
            m.intent && m.role !== "user"
              ? { key: m.intent, label: INTENT_LABELS[m.intent] ?? m.intent }
              : null,
        }))
        cacheRef.current[sessionId] = loaded
        if (activeIdRef.current === sessionId) {
          setMessages(loaded)
        }
      })
      .catch((err: Error) => {
        showErrorToast(getErrorMessage(err))
      })
  }

  const startNewChat = () => {
    if (activeIdRef.current === null) {
      cacheRef.current[DRAFT_KEY] = []
      setMessages([])
      setInput("")
      textareaRef.current?.focus()
      return
    }
    switchConversation(null)
  }

  const deleteSessionMutation = useMutation({
    mutationFn: (sessionId: string) =>
      AgentService.deleteSession({ path: { session_id: sessionId } }),
    onSuccess: (_data, sessionId) => {
      delete cacheRef.current[sessionId]
      if (activeIdRef.current === sessionId) {
        activeIdRef.current = null
        setActiveId(null)
        setMessages(cacheRef.current[DRAFT_KEY] ?? [])
      }
      queryClient.invalidateQueries({ queryKey: ["agent", "sessions"] })
    },
    onError: (err) => showErrorToast(getErrorMessage(err as Error)),
  })

  const runChat = (
    text: string,
    sessionId: string | null,
    replyId: string,
    files: File[] = [],
    sentAttachments: Attachment[] = [],
    signal?: AbortSignal,
  ) => {
    pendingTargetRef.current = keyOf(sessionId)
    sendMutation.mutate(
      { text, sessionId, files, replyId, signal },
      {
        onSuccess: (data, vars) => {
          const targetId = vars.sessionId ?? data.session_id
          updateMessages(keyOf(targetId), (prev) =>
            prev.map((m) =>
              m.id === vars.replyId
                ? {
                    ...m,
                    content: data.response,
                    status: "done",
                    serverId: data.message_id ?? undefined,
                  }
                : m,
            ),
          )
          queryClient.invalidateQueries({ queryKey: ["agent", "sessions"] })
        },
        onError: (err, vars) => {
          const canceled = (err as Error)?.name === "AbortError"
          const message = canceled
            ? "已停止生成"
            : getErrorMessage(err as Error)
          updateMessages(keyOf(pendingTargetRef.current), (prev) =>
            prev.map((m) =>
              m.id === vars.replyId
                ? {
                    ...m,
                    content: message,
                    status: "error",
                    retryText: vars.text,
                    attachments: sentAttachments,
                  }
                : m,
            ),
          )
          if (!canceled) {
            showErrorToast(message)
          }
          // 中断时后端可能已回滚新建的空会话，刷新列表
          queryClient.invalidateQueries({ queryKey: ["agent", "sessions"] })
        },
      },
    )
  }

  const sendMutation = useMutation({
    mutationFn: async (vars: ChatVars) => {
      const result = await streamChat(vars, {
        onSession: (sid) => {
          if (!vars.sessionId) {
            // 新会话首条消息：后端已创建会话，把草稿挂到真实会话上
            pendingTargetRef.current = sid
            cacheRef.current[sid] = cacheRef.current[DRAFT_KEY] ?? []
            delete cacheRef.current[DRAFT_KEY]
            if (activeIdRef.current === null) {
              activeIdRef.current = sid
              setActiveId(sid)
            }
          }
        },
        onMeta: (meta) => {
          // 意图识别与人工升级标记：挂到正在生成的回复消息上
          const key = keyOf(pendingTargetRef.current)
          updateMessages(key, (prevMsgs) =>
            prevMsgs.map((m) =>
              m.id === vars.replyId
                ? {
                    ...m,
                    intent: meta.intent
                      ? { key: meta.intent.intent, label: meta.intent.label }
                      : m.intent,
                    escalated: meta.escalated,
                  }
                : m,
            ),
          )
        },
        onDelta: (delta) => {
          const key = keyOf(pendingTargetRef.current)
          updateMessages(key, (prevMsgs) =>
            prevMsgs.map((m) =>
              m.id === vars.replyId ? { ...m, content: m.content + delta } : m,
            ),
          )
        },
      })
      return result
    },
  })

  // 点踩时展开评语输入框（消息 id + 草稿）
  const [feedbackDraft, setFeedbackDraft] = useState<{
    id: string
    comment: string
  } | null>(null)

  const feedbackMutation = useMutation({
    mutationFn: async (vars: {
      message: ChatMessage
      rating: "up" | "down"
      comment: string
    }) => {
      const targetId = vars.message.serverId ?? vars.message.id
      await submitFeedback(targetId, vars.rating, vars.comment)
      return vars
    },
    onSuccess: (vars) => {
      updateMessages(keyOf(activeIdRef.current), (prev) =>
        prev.map((m) =>
          m.id === vars.message.id ? { ...m, feedback: vars.rating } : m,
        ),
      )
      setFeedbackDraft(null)
      if (vars.rating === "up") {
        showSuccessToast("感谢反馈！")
      } else {
        showSuccessToast("已收到反馈，会话已转交人工复核")
      }
    },
    onError: (err) => showErrorToast(getErrorMessage(err as Error)),
  })

  const requestInterventionMutation = useMutation({
    mutationFn: async () => {
      const sessionId = activeIdRef.current
      if (!sessionId) {
        throw new Error("先开启一个对话再申请人工介入")
      }
      await requestIntervention(sessionId, "用户通过界面请求转人工")
    },
    onSuccess: () => {
      showSuccessToast("已提交人工介入申请，坐席会尽快处理")
    },
    onError: (err) => showErrorToast(getErrorMessage(err as Error)),
  })

  const lastMessage = messages[messages.length - 1]
  useEffect(() => {
    const el = scrollRef.current
    if (el && lastMessage) {
      el.scrollTop = el.scrollHeight
    }
  }, [lastMessage])

  const send = (raw: string) => {
    const text = raw.trim()
    if ((!text && attachments.length === 0) || sendMutation.isPending) {
      return
    }
    const sessionId = activeIdRef.current
    const replyId = newId()
    const sentAttachments = attachments
    const controller = new AbortController()
    abortRef.current = controller
    updateMessages(keyOf(sessionId), (prev) => [
      ...prev,
      {
        id: newId(),
        role: "user",
        content: text,
        status: "done",
        attachments: sentAttachments,
      },
      { id: replyId, role: "assistant", content: "", status: "pending" },
    ])
    setInput("")
    setAttachments([])
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto"
    }
    runChat(
      text,
      sessionId,
      replyId,
      sentAttachments.map((a) => a.file),
      sentAttachments,
      controller.signal,
    )
  }

  const stop = () => {
    abortRef.current?.abort()
    abortRef.current = null
  }

  const retry = (message: ChatMessage) => {
    if (!message.retryText || sendMutation.isPending) {
      return
    }
    const sessionId = activeIdRef.current
    updateMessages(keyOf(sessionId), (prev) =>
      prev.map((m) =>
        m.id === message.id ? { ...m, content: "", status: "pending" } : m,
      ),
    )
    const controller = new AbortController()
    abortRef.current = controller
    runChat(
      message.retryText,
      sessionId,
      message.id,
      (message.attachments ?? []).map((a) => a.file),
      message.attachments ?? [],
      controller.signal,
    )
  }

  const handleTextareaInput = (value: string) => {
    setInput(value)
    const el = textareaRef.current
    if (el) {
      el.style.height = "auto"
      el.style.height = `${Math.min(el.scrollHeight, 192)}px`
    }
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !composingRef.current) {
      event.preventDefault()
      send(input)
    }
  }

  const sessions = sessionsQuery.data?.data ?? []
  const isEmpty = messages.length === 0

  return (
    <div className="flex h-full w-full overflow-hidden bg-background text-foreground">
      {/* 会话侧栏 */}
      <aside className="hidden w-64 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground md:flex">
        <div className="p-3">
          <Button onClick={startNewChat} className="w-full rounded-xl py-2.5">
            <Plus size={16} />
            开启新对话
          </Button>
        </div>
        <p className="px-4 pb-1 text-xs text-muted-foreground">历史对话</p>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
          {sessions.map((session) => (
            <div
              key={session.id}
              className={`group flex w-full items-center rounded-lg transition-colors ${
                session.id === activeId
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-accent/60"
              }`}
            >
              <button
                type="button"
                onClick={() => switchConversation(session.id)}
                className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-left text-sm"
              >
                <Bot size={14} className="shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">
                  {session.title || "新对话"}
                </span>
              </button>
              <button
                type="button"
                aria-label="删除会话"
                disabled={deleteSessionMutation.isPending}
                onClick={() => deleteSessionMutation.mutate(session.id)}
                className="mr-1 cursor-pointer p-1.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:text-destructive disabled:opacity-40"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
          {sessions.length === 0 && (
            <p className="px-2 py-6 text-center text-xs text-muted-foreground">
              还没有对话记录
            </p>
          )}
        </nav>
        <div className="border-t p-3">
          <Button asChild variant="ghost" className="mb-2 w-full justify-start">
            <Link href="/dashboard">返回工作台</Link>
          </Button>
          <p className="truncate px-1 text-xs text-muted-foreground">
            {currentUser?.full_name || currentUser?.email}
          </p>
        </div>
      </aside>

      {/* 聊天主区域 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
          <Bot size={18} className="text-primary" />
          <span className="text-sm font-medium">Agent 对话</span>
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            disabled={activeId === null || requestInterventionMutation.isPending}
            onClick={() => requestInterventionMutation.mutate()}
          >
            <LifeBuoy size={14} />
            转人工
          </Button>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {isEmpty ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
              <div className="flex size-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                <Bot size={28} />
              </div>
              <h1 className="text-2xl font-semibold">你好，我是 Agent</h1>
              <p className="max-w-md text-sm text-muted-foreground">
                有什么可以帮你？输入问题，随时开始对话。
              </p>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
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
                                  className="max-h-40 rounded-xl border object-cover"
                                />
                              ) : (
                                <div
                                  key={att.id}
                                  className="flex items-center gap-1.5 rounded-xl border bg-card px-2.5 py-1.5 text-xs"
                                >
                                  <FileText
                                    size={14}
                                    className="shrink-0 text-muted-foreground"
                                  />
                                  <span className="max-w-40 truncate">
                                    {att.file.name}
                                  </span>
                                </div>
                              ),
                            )}
                          </div>
                        )}
                      {message.content && (
                        <div className="rounded-2xl bg-primary/10 px-4 py-2.5 text-sm whitespace-pre-wrap break-words">
                          {message.content}
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div key={message.id} className="flex items-start gap-3">
                    <AssistantAvatar />
                    <div className="min-w-0 flex-1 pt-1">
                      {message.status === "pending" ? (
                        message.content ? (
                          <Markdown>{message.content}</Markdown>
                        ) : (
                          <PendingDots />
                        )
                      ) : message.status === "error" ? (
                        <div className="space-y-2">
                          <p className="text-sm text-destructive">
                            请求失败：{message.content}
                          </p>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => retry(message)}
                            disabled={
                              sendMutation.isPending || !message.retryText
                            }
                          >
                            重试
                          </Button>
                        </div>
                      ) : (
                        <Markdown>{message.content}</Markdown>
                      )}

                      {/* 意图徽章 + 人工升级标记 + 反馈按钮 */}
                      {message.status === "done" && (
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          {message.intent && (
                            <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                              意图：{message.intent.label}
                            </span>
                          )}
                          {message.escalated && (
                            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-700 dark:bg-amber-950 dark:text-amber-300">
                              人工坐席处理中
                            </span>
                          )}
                          {message.feedback ? (
                            <span className="text-xs text-muted-foreground">
                              {message.feedback === "up" ? "已点赞" : "已点踩"}
                            </span>
                          ) : (
                            <div className="flex items-center gap-0.5">
                              <button
                                type="button"
                                aria-label="点赞"
                                disabled={feedbackMutation.isPending}
                                onClick={() =>
                                  feedbackMutation.mutate({
                                    message,
                                    rating: "up",
                                    comment: "",
                                  })
                                }
                                className="cursor-pointer rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
                              >
                                <ThumbsUp size={14} />
                              </button>
                              <button
                                type="button"
                                aria-label="点踩"
                                disabled={feedbackMutation.isPending}
                                onClick={() =>
                                  setFeedbackDraft({ id: message.id, comment: "" })
                                }
                                className="cursor-pointer rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
                              >
                                <ThumbsDown size={14} />
                              </button>
                            </div>
                          )}
                        </div>
                      )}

                      {feedbackDraft?.id === message.id && (
                        <div className="mt-2 flex items-center gap-2">
                          <input
                            value={feedbackDraft.comment}
                            placeholder="说说哪里不满意（可选）"
                            maxLength={2000}
                            onChange={(e) =>
                              setFeedbackDraft({
                                id: message.id,
                                comment: e.target.value,
                              })
                            }
                            className="min-w-0 flex-1 rounded-lg border bg-card px-3 py-1.5 text-sm outline-none focus:border-primary/50"
                          />
                          <Button
                            size="sm"
                            disabled={feedbackMutation.isPending}
                            onClick={() =>
                              feedbackMutation.mutate({
                                message,
                                rating: "down",
                                comment: feedbackDraft.comment,
                              })
                            }
                          >
                            提交
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setFeedbackDraft(null)}
                          >
                            取消
                          </Button>
                        </div>
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
          <div className="mx-auto max-w-3xl rounded-3xl border bg-card p-3 shadow-sm transition-colors focus-within:border-primary/50">
            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 px-1 pb-2">
                {attachments.map((att) => (
                  <div key={att.id} className="relative">
                    {att.url ? (
                      <img
                        src={att.url}
                        alt={att.file.name}
                        className="size-16 rounded-lg border object-cover"
                      />
                    ) : (
                      <div className="flex h-16 max-w-40 items-center gap-1.5 rounded-lg border bg-muted/50 px-2 text-xs">
                        <FileText
                          size={14}
                          className="shrink-0 text-muted-foreground"
                        />
                        <span className="truncate">{att.file.name}</span>
                      </div>
                    )}
                    <button
                      type="button"
                      aria-label="移除附件"
                      onClick={() => removeAttachment(att.id)}
                      className="absolute -top-1.5 -right-1.5 flex size-5 cursor-pointer items-center justify-center rounded-full bg-foreground text-background shadow"
                    >
                      <X size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <textarea
              ref={textareaRef}
              value={input}
              rows={1}
              maxLength={MAX_INPUT_LENGTH}
              placeholder="给 Agent 发送消息，可附带图片或文档…"
              onChange={(e) => handleTextareaInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onCompositionStart={() => {
                composingRef.current = true
              }}
              onCompositionEnd={() => {
                composingRef.current = false
              }}
              className="max-h-48 w-full resize-none bg-transparent px-2 text-sm outline-none placeholder:text-muted-foreground"
            />
            <div className="flex items-center justify-between pt-1">
              <div className="flex items-center gap-1 pl-1">
                <button
                  type="button"
                  aria-label="添加附件"
                  onClick={() => fileInputRef.current?.click()}
                  className="flex size-8 cursor-pointer items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <Paperclip size={16} />
                </button>
                <span className="text-xs text-muted-foreground">
                  Enter 发送，Shift + Enter 换行
                </span>
              </div>
              {sendMutation.isPending ? (
                <button
                  type="button"
                  aria-label="停止生成"
                  onClick={stop}
                  className="flex size-8 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90"
                >
                  <Square size={14} fill="currentColor" />
                </button>
              ) : (
                <button
                  type="button"
                  aria-label="发送"
                  disabled={!input.trim() && attachments.length === 0}
                  onClick={() => send(input)}
                  className="flex size-8 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
                >
                  <ArrowUp size={16} />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => addFiles(e.target.files)}
      />
    </div>
  )
}

export default AgentChat
