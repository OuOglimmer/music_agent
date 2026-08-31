import { createFileRoute, redirect } from "@tanstack/react-router"

import AgentChat from "@/components/Agent/AgentChat"
import { isLoggedIn } from "@/hooks/useAuth"

export const Route = createFileRoute("/")({
  component: AgentChat,
  beforeLoad: async () => {
    if (!isLoggedIn()) {
      throw redirect({
        to: "/login",
      })
    }
  },
  head: () => ({
    meta: [
      {
        title: "Agent 对话 - FastAPI Template",
      },
    ],
  }),
})
