"use client";

import { useQueryClient } from "@tanstack/react-query"
import {
  FileText,
  FolderPlus,
  Library,
  Music2,
  Paperclip,
  Plus,
  Trash2,
  Upload,
  X,
} from "lucide-react"
import { useRef } from "react"

import { useStudio } from "@/components/Studio/StudioContext"
import { formatRelativeTime, styleById } from "@/components/Studio/presets"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { useState } from "react"

export function ResourcePanel() {
  const {
    sessions,
    activeSessionId,
    selectSession,
    deleteSession,
    attachments,
    addAttachments,
    removeAttachment,
    tracks,
    removeTrack,
    loadTrack,
    currentTrack,
  } = useStudio()
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [deleteTrackTarget, setDeleteTrackTarget] = useState<string | null>(
    null,
  )

  const startNewProject = () => {
    selectSession(null)
    queryClient.invalidateQueries({ queryKey: ["agent", "sessions"] })
  }

  return (
    <aside className="hidden w-72 shrink-0 flex-col overflow-hidden border-r bg-sidebar text-sidebar-foreground lg:flex">
      {/* 项目 */}
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        <h2 className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          <FolderPlus className="size-3.5" />
          项目
        </h2>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 gap-1 px-2 text-xs"
          onClick={startNewProject}
        >
          <Plus className="size-3.5" />
          新建
        </Button>
      </div>
      <nav className="max-h-56 shrink-0 space-y-0.5 overflow-y-auto px-2">
        <button
          type="button"
          onClick={() => selectSession(null)}
          className={cn(
            "flex w-full cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-left text-sm transition-colors",
            activeSessionId === null
              ? "bg-primary/10 text-primary"
              : "hover:bg-accent/60",
          )}
        >
          <Plus className="size-3.5 shrink-0" />
          <span className="truncate">新的项目（未保存）</span>
        </button>
        {sessions.map((session) => (
          <div
            key={session.id}
            className={cn(
              "group flex w-full items-center rounded-lg transition-colors",
              session.id === activeSessionId
                ? "bg-primary/10 text-primary"
                : "hover:bg-accent/60",
            )}
          >
            <button
              type="button"
              onClick={() => selectSession(session.id)}
              className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 px-2 py-2 text-left"
            >
              <Music2 className="size-3.5 shrink-0 opacity-70" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm">
                  {session.title || "未命名项目"}
                </span>
                <span className="block text-[11px] text-muted-foreground">
                  {formatRelativeTime(session.updated_at)}
                </span>
              </span>
            </button>
            <button
              type="button"
              aria-label="删除项目"
              onClick={() => setDeleteTarget(session.id)}
              className="mr-1.5 cursor-pointer p-1.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:text-destructive"
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
        {sessions.length === 0 && (
          <p className="px-2 py-3 text-center text-xs text-muted-foreground">
            开始创作或对话后自动建立项目
          </p>
        )}
      </nav>

      {/* 素材 */}
      <div className="mt-4 shrink-0 border-t px-4 pt-3">
        <h2 className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          <Upload className="size-3.5" />
          素材上传
        </h2>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="mt-2 flex w-full cursor-pointer flex-col items-center gap-1 rounded-xl border border-dashed px-3 py-4 text-center text-xs text-muted-foreground transition-colors hover:border-primary/50 hover:bg-primary/5 hover:text-foreground"
        >
          <Paperclip className="size-4" />
          上传图片或 txt / md 文档
          <span className="text-[11px] opacity-70">
            最多 5 个，将随下一条消息发送
          </span>
        </button>
        {attachments.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5 pb-1">
            {attachments.map((att) => (
              <span
                key={att.id}
                className="flex items-center gap-1 rounded-full border bg-background px-2 py-0.5 text-[11px]"
              >
                {att.url ? (
                  <img
                    src={att.url}
                    alt={att.file.name}
                    className="size-3.5 rounded-full object-cover"
                  />
                ) : (
                  <FileText className="size-3" />
                )}
                <span className="max-w-24 truncate">{att.file.name}</span>
                <button
                  type="button"
                  aria-label="移除素材"
                  onClick={() => removeAttachment(att.id)}
                  className="cursor-pointer text-muted-foreground hover:text-destructive"
                >
                  <X className="size-3" />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 作品库 */}
      <div className="mt-4 flex min-h-0 flex-1 flex-col border-t px-2 pt-3">
        <h2 className="flex items-center gap-1.5 px-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          <Library className="size-3.5" />
          作品库（{tracks.length}）
        </h2>
        <div className="mt-1 min-h-0 flex-1 space-y-0.5 overflow-y-auto pb-3">
          {tracks.map((track) => {
            const preset = styleById(track.style)
            return (
              <div
                key={track.id}
                className={cn(
                  "group flex items-center rounded-lg transition-colors",
                  currentTrack?.id === track.id
                    ? "bg-primary/10"
                    : "hover:bg-accent/60",
                )}
              >
                <button
                  type="button"
                  onClick={() => loadTrack(track)}
                  className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 px-2 py-2 text-left"
                >
                  <span
                    className={cn(
                      "flex size-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br text-white",
                      preset?.gradient ?? "from-violet-500 to-fuchsia-500",
                    )}
                  >
                    <Music2 className="size-3.5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">{track.title}</span>
                    <span className="block text-[11px] text-muted-foreground">
                      {formatRelativeTime(track.createdAt)}
                      {track.mp3File ? " · MP3" : " · MIDI"}
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  aria-label="删除作品"
                  onClick={() => setDeleteTrackTarget(track.id)}
                  className="mr-1.5 cursor-pointer p-1.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:text-destructive"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            )
          })}
          {tracks.length === 0 && (
            <p className="px-2 py-6 text-center text-xs text-muted-foreground">
              还没有作品，去「创作」页生成第一首歌吧
            </p>
          )}
        </div>
      </div>

      {/* 删除项目确认 */}
      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除项目？</DialogTitle>
            <DialogDescription>
              项目的对话与创作记录将被移除，且无法恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">取消</Button>
            </DialogClose>
            <Button
              variant="destructive"
              onClick={() => {
                if (deleteTarget) deleteSession(deleteTarget)
                setDeleteTarget(null)
              }}
            >
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除作品确认 */}
      <Dialog
        open={deleteTrackTarget !== null}
        onOpenChange={(open) => !open && setDeleteTrackTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>从作品库移除？</DialogTitle>
            <DialogDescription>
              仅移除作品库记录，服务器上的乐谱文件不受影响。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">取消</Button>
            </DialogClose>
            <Button
              variant="destructive"
              onClick={() => {
                if (deleteTrackTarget) removeTrack(deleteTrackTarget)
                setDeleteTrackTarget(null)
              }}
            >
              移除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
    </aside>
  )
}
