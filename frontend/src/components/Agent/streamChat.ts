/**
 * 通过 SSE 调用 Agent /chat 端点，逐块消费流式回答。
 *
 * 事件序列：session -> meta -> delta* -> done | error
 * meta 携带意图识别结果与人工升级标记；done 携带落库后的 message_id
 * （用于消息反馈）。中断：传入 AbortSignal，前端停止时 abort 即可，
 * 后端会取消生成并回滚。
 */

export type StreamIntentMeta = {
  intent: string
  label: string
  confidence: number
  method: string
  sensitive: boolean
}

export type StreamChatMeta = {
  intent?: StreamIntentMeta
  escalated?: boolean
  prompt_version?: string
}

export type StreamChatVars = {
  text: string
  sessionId: string | null
  files: File[]
  signal?: AbortSignal
}

export type StreamChatHandlers = {
  onSession?: (sessionId: string) => void
  onMeta?: (meta: StreamChatMeta) => void
  onDelta?: (delta: string) => void
  onError?: (detail: string) => void
}

export type StreamChatResult = {
  session_id: string | null
  response: string
  message_id: string | null
  meta: StreamChatMeta
}

function parseSseBlock(block: string): {
  event: string
  data: string
} | null {
  const lines = block.split("\n")
  let event = "message"
  const dataLines: string[] = []
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim()
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim())
    }
  }
  if (dataLines.length === 0) {
    return null
  }
  return { event, data: dataLines.join("\n") }
}

export async function streamChat(
  vars: StreamChatVars,
  handlers: StreamChatHandlers = {},
): Promise<StreamChatResult> {
  const form = new FormData()
  form.append("user_input", vars.text)
  if (vars.sessionId) {
    form.append("session_id", vars.sessionId)
  }
  for (const file of vars.files) {
    form.append("files", file)
  }

  const token = localStorage.getItem("access_token") ?? ""
  const response = await fetch("/api/v1/agent/chat", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
    signal: vars.signal,
  })

  if (!response.ok || !response.body) {
    let detail = `请求失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) {
        detail = body.detail
      }
    } catch {
      // 非 JSON 错误体，保留默认文案
    }
    throw new Error(detail)
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ""
  let sessionId: string | null = null
  let full = ""
  let messageId: string | null = null
  let meta: StreamChatMeta = {}

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }
      buffer += value
      buffer = buffer.replace(/\r\n?/g, "\n")
      const blocks = buffer.split("\n\n")
      buffer = blocks.pop() ?? ""

      for (const block of blocks) {
        const parsed = parseSseBlock(block)
        if (!parsed) {
          continue
        }
        const { event, data } = parsed
        let payload: {
          session_id?: string
          intent?: StreamIntentMeta
          escalated?: boolean
          prompt_version?: string
          delta?: string
          response?: string
          message_id?: string
          detail?: string
        }
        try {
          payload = JSON.parse(data) as typeof payload
        } catch {
          continue
        }
        if (event === "session" && payload.session_id) {
          sessionId = payload.session_id
          handlers.onSession?.(payload.session_id)
        } else if (event === "meta") {
          meta = {
            intent: payload.intent,
            escalated: payload.escalated,
            prompt_version: payload.prompt_version,
          }
          handlers.onMeta?.(meta)
        } else if (event === "delta" && typeof payload.delta === "string") {
          full += payload.delta
          handlers.onDelta?.(payload.delta)
        } else if (event === "done" && typeof payload.response === "string") {
          full = payload.response
          if (payload.message_id) {
            messageId = payload.message_id
          }
        } else if (event === "error") {
          throw new Error(payload.detail ?? "生成失败")
        }
      }
    }
  } finally {
    reader.releaseLock()
  }

  return { session_id: sessionId, response: full, message_id: messageId, meta }
}
