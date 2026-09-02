/**
 * 治理接口的前端辅助：消息反馈（点赞/点踩）与主动转人工。
 *
 * 这两个端点尚未进入自动生成的 SDK，直接用 fetch + Bearer 调用，
 * 错误处理与 streamChat 保持一致（抛出后端 detail 文案）。
 */

const agentBase = "/api/v1/agent"

const headers = () => {
  const token = localStorage.getItem("access_token") ?? ""
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
}

async function postJson(path: string, body: unknown) {
  const response = await fetch(`${agentBase}${path}`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`
    try {
      const data = (await response.json()) as { detail?: string }
      if (data.detail) {
        detail = data.detail
      }
    } catch {
      // 非 JSON 错误体，保留默认文案
    }
    throw new Error(detail)
  }
}

/** 对单条助手消息提交反馈；点踩会自动进入人工坐席复核队列。 */
export function submitFeedback(
  messageId: string,
  rating: "up" | "down",
  comment = "",
): Promise<void> {
  return postJson("/feedback", { message_id: messageId, rating, comment })
}

/** 主动请求人工介入（每会话同时最多一张未结工单）。 */
export function requestIntervention(
  sessionId: string,
  note = "",
): Promise<void> {
  return postJson("/interventions", { session_id: sessionId, note })
}
