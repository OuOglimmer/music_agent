"use client";

import { Bot, FileText, Sparkles } from "lucide-react"
import { useState } from "react"

import { ChatPanel } from "@/components/Studio/ChatPanel"
import { ComposePanel } from "@/components/Studio/ComposePanel"
import { LyricsEditor } from "@/components/Studio/LyricsEditor"
import { PlayerPanel } from "@/components/Studio/PlayerPanel"
import { ResourcePanel } from "@/components/Studio/ResourcePanel"
import { StudioProvider, useStudio } from "@/components/Studio/StudioContext"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

const TABS = [
  { value: "compose", label: "创作", icon: Sparkles },
  { value: "chat", label: "对话", icon: Bot },
  { value: "lyrics", label: "歌词", icon: FileText },
] as const

function CenterTabs() {
  const { attachments, isComposing } = useStudio()
  const [tab, setTab] = useState<string>("compose")

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col gap-0">
        <div className="shrink-0 border-b bg-background/60 px-4 pt-2 backdrop-blur">
          <TabsList className="bg-muted/60 h-9 rounded-xl p-1">
            {TABS.map(({ value, label, icon: Icon }) => (
              <TabsTrigger
                key={value}
                value={value}
                className="cursor-pointer gap-1.5 rounded-lg px-3.5 text-sm data-[state=active]:bg-background data-[state=active]:shadow-sm"
              >
                <Icon className="size-3.5" />
                {label}
                {value === "chat" && attachments.length > 0 && (
                  <span className="bg-primary ml-0.5 flex size-4 items-center justify-center rounded-full text-[10px] font-semibold text-white">
                    {attachments.length}
                  </span>
                )}
                {value === "compose" && isComposing && (
                  <span className="size-1.5 animate-pulse rounded-full bg-violet-500" />
                )}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        <TabsContent
          value="compose"
          className="min-h-0 flex-1 overflow-hidden data-[state=inactive]:hidden"
        >
          <ComposePanel />
        </TabsContent>
        <TabsContent
          value="chat"
          className="min-h-0 flex-1 overflow-hidden data-[state=inactive]:hidden"
        >
          <ChatPanel />
        </TabsContent>
        <TabsContent
          value="lyrics"
          className="min-h-0 flex-1 overflow-hidden data-[state=inactive]:hidden"
        >
          <LyricsEditor />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function StudioHeader() {
  const { activeSessionTitle } = useStudio()
  return (
    <div className="pointer-events-none absolute top-3 right-4 z-10 hidden items-center gap-2 text-xs text-muted-foreground md:flex">
      <span className="size-1.5 rounded-full bg-emerald-500" />
      当前项目：{activeSessionTitle}
    </div>
  )
}

function Studio() {
  return (
    <div className="relative flex h-[calc(100dvh-4rem)] overflow-hidden">
      <StudioHeader />
      <ResourcePanel />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <CenterTabs />
      </div>
      <PlayerPanel />
    </div>
  )
}

export default function DashboardPage() {
  return (
    <StudioProvider>
      <Studio />
    </StudioProvider>
  )
}
