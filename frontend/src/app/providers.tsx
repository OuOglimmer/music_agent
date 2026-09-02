"use client";

import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query"
import { AxiosError } from "axios"
import { useState } from "react"

import { client } from "@/client/client.gen"
import { ThemeProvider } from "@/components/theme-provider"
import { Toaster } from "@/components/ui/sonner"

// 与旧版 main.tsx 一致：同源 /api/v1（由 next.config.ts 代理到 FastAPI），
// token 存 localStorage，由 hey-api client 的 auth 回调自动附加 Bearer 头。
client.setConfig({
  baseURL: process.env.NEXT_PUBLIC_API_URL ?? "",
  auth: () =>
    typeof window === "undefined"
      ? ""
      : localStorage.getItem("access_token") || "",
})

const handleApiError = (error: Error) => {
  if (!(error instanceof AxiosError)) {
    return
  }
  // 404 "User not found" 表示 token 对应的用户已不存在（如数据库重置），会话无法恢复
  const detail = (error.response?.data as { detail?: string } | undefined)
    ?.detail
  const sessionInvalid =
    [401, 403].includes(error.response?.status ?? 0) ||
    (error.response?.status === 404 && detail === "User not found")
  if (sessionInvalid) {
    localStorage.removeItem("access_token")
    window.location.href = "/login"
  }
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({
          onError: handleApiError,
        }),
        mutationCache: new MutationCache({
          onError: handleApiError,
        }),
      }),
  )

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        {children}
        <Toaster richColors closeButton position="top-center" />
      </ThemeProvider>
    </QueryClientProvider>
  )
}
