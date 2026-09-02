"use client";

import { useRouter } from "next/navigation"
import { usePathname } from "next/navigation"
import { useEffect, useState } from "react"

import { Footer } from "@/components/Common/Footer"
import AppSidebar from "@/components/Sidebar/AppSidebar"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { isLoggedIn } from "@/hooks/useAuth"

// 全屏沉浸页面：隐藏 Footer，让内容占满视口剩余高度
const FULLSCREEN_PATHS = ["/dashboard", "/chat"]

function PagePlaceholder() {
  return (
    <div className="flex flex-1 items-center justify-center gap-1.5 pb-24">
      {[0, 1, 2, 3, 4].map((i) => (
        <span
          key={i}
          className="w-1.5 animate-eq rounded-full bg-gradient-to-t from-violet-600 to-fuchsia-500"
          style={{ height: 26, animationDelay: `${i * 120}ms` }}
        />
      ))}
    </div>
  )
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const pathname = usePathname() ?? ""
  const fullscreen = FULLSCREEN_PATHS.some((p) => pathname.startsWith(p))
  // 页面依赖浏览器 API（localStorage / 音频等），客户端挂载后再渲染，
  // 避免服务端预渲染期执行浏览器查询
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
    if (!isLoggedIn()) {
      router.replace("/login")
    }
  }, [router])

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2 border-b bg-background px-4">
          <SidebarTrigger className="-ml-1 text-muted-foreground" />
        </header>
        <main className="flex flex-1 flex-col">
          {mounted ? children : <PagePlaceholder />}
        </main>
        {!fullscreen && <Footer />}
      </SidebarInset>
    </SidebarProvider>
  )
}
