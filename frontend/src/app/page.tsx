"use client";

import { useRouter } from "next/navigation"
import { useEffect } from "react"

import { isLoggedIn } from "@/hooks/useAuth"

export default function Home() {
  const router = useRouter()

  useEffect(() => {
    router.replace(isLoggedIn() ? "/dashboard" : "/login")
  }, [router])

  return (
    <div className="flex min-h-svh items-center justify-center">
      <div className="flex items-end gap-1" aria-label="加载中">
        {[0, 1, 2, 3, 4].map((i) => (
          <span
            key={i}
            className="w-1.5 animate-eq rounded-full bg-gradient-to-t from-violet-600 to-fuchsia-500"
            style={{ height: 28, animationDelay: `${i * 120}ms` }}
          />
        ))}
      </div>
    </div>
  )
}
