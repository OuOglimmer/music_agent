/**
 * 通过 SSE 调用 /agent/compose 多 Agent 创作流水线。
 *
 * 事件序列：session -> stage* -> done | error
 * 中断：传入 AbortSignal，前端停止时 abort 即可。
 */

export type ComposePayload = {
  style: string
  mood: string
  key: string
  mode: string
  bpm: number
  bars: number
  beats_per_bar: number
  user_notes: string
  session_id: string | null
}

export type ComposeStageEvent = {
  node: string
  label: string
  detail: string
}

export type ComposeDonePayload = {
  summary: string
  midi_path: string
  mp3_path: string
  chords: string[]
}

export type ComposeHandlers = {
  onSession?: (sessionId: string) => void
  onStage?: (stage: ComposeStageEvent) => void
  onDone?: (payload: ComposeDonePayload) => void
}

function parseSseBlock(block: string): { event: string; data: string } | null {
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

export async function streamCompose(
  payload: ComposePayload,
  handlers: ComposeHandlers = {},
  signal?: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem("access_token") ?? ""
  const response = await fetch("/api/v1/agent/compose", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
    signal,
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
        let data: Record<string, unknown>
        try {
          data = JSON.parse(parsed.data) as Record<string, unknown>
        } catch {
          continue
        }
        if (parsed.event === "session" && typeof data.session_id === "string") {
          handlers.onSession?.(data.session_id)
        } else if (parsed.event === "stage") {
          handlers.onStage?.({
            node: String(data.node ?? ""),
            label: String(data.stage ?? data.node ?? ""),
            detail: String(data.detail ?? ""),
          })
        } else if (parsed.event === "done") {
          handlers.onDone?.({
            summary: String(data.summary ?? "创作完成"),
            midi_path: String(data.midi_path ?? ""),
            mp3_path: String(data.mp3_path ?? ""),
            chords: Array.isArray(data.chords) ? data.chords.map(String) : [],
          })
        } else if (parsed.event === "error") {
          throw new Error(String(data.detail ?? "创作失败"))
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}
