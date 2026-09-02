"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { AxiosError } from "axios"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"

import { AgentService } from "@/client"
import { streamChat } from "@/components/Agent/streamChat"
import {
  type ComposeDonePayload,
  type ComposeStageEvent,
  streamCompose,
} from "@/components/Studio/composeStream"
import {
  basename,
  styleById,
  type StyleId,
} from "@/components/Studio/presets"
import useCustomToast from "@/hooks/useCustomToast"

export type StudioAttachment = { id: string; file: File; url?: string }

export type StudioChatMessage = {
  id: string
  role: "user" | "assistant"
  content: string
  status: "done" | "pending" | "error"
  retryText?: string
  attachments?: StudioAttachment[]
}

export type StudioTrack = {
  id: string
  sessionId: string | null
  title: string
  style: StyleId
  key: string
  mode: string
  bpm: number
  bars: number
  chords: string[]
  mp3File: string | null
  midiFile: string | null
  createdAt: string
}

export type ComposeParams = {
  style: StyleId
  mood: string
  key: string
  mode: "major" | "minor"
  bpm: number
  bars: number
  beatsPerBar: number
  userNotes: string
}

const DEFAULT_PARAMS: ComposeParams = {
  style: "pop",
  mood: "明快",
  key: "C",
  mode: "major",
  bpm: 100,
  bars: 8,
  beatsPerBar: 4,
  userNotes: "",
}

const DRAFT_KEY = "__draft__"
const TRACKS_STORAGE_KEY = "music-studio:tracks"
const LYRICS_STORAGE_PREFIX = "music-studio:lyrics"
const MAX_FILES = 5

const newId = () => crypto.randomUUID()
const keyOf = (id: string | null) => id ?? DRAFT_KEY

const getErrorMessage = (err: unknown) => {
  if (err instanceof AxiosError) {
    const detail = (err.response?.data as { detail?: string } | undefined)
      ?.detail
    return detail ?? err.message
  }
  return err instanceof Error ? err.message : String(err)
}

type StudioContextValue = {
  // 项目（会话）
  sessions: {
    id: string
    title?: string
    updated_at?: string | null
  }[]
  activeSessionId: string | null
  activeSessionTitle: string
  selectSession: (id: string | null) => void
  deleteSession: (id: string) => void

  // 对话
  messages: StudioChatMessage[]
  isStreaming: boolean
  sendMessage: (text: string) => void
  retryMessage: (message: StudioChatMessage) => void
  stopStreaming: () => void

  // 素材
  attachments: StudioAttachment[]
  addAttachments: (files: FileList | null) => void
  removeAttachment: (id: string) => void

  // 创作
  params: ComposeParams
  setParams: (patch: Partial<ComposeParams>) => void
  stages: ComposeStageEvent[]
  isComposing: boolean
  composeError: string | null
  startCompose: () => void
  stopCompose: () => void

  // 作品库
  tracks: StudioTrack[]
  addTrack: (track: StudioTrack) => void
  removeTrack: (id: string) => void

  // 播放器
  currentTrack: StudioTrack | null
  loadTrack: (track: StudioTrack) => void
  isPlaying: boolean
  togglePlay: () => void
  position: number
  duration: number
  seek: (ratio: number) => void
  volume: number
  setVolume: (v: number) => void
  loop: boolean
  toggleLoop: () => void
  audioUrl: string | null
  peaks: number[] | null
  playNext: (dir: 1 | -1) => void

  // 歌词
  lyrics: string
  setLyrics: (text: string) => void
}

const StudioContext = createContext<StudioContextValue | null>(null)

export function useStudio() {
  const ctx = useContext(StudioContext)
  if (!ctx) {
    throw new Error("useStudio must be used within StudioProvider")
  }
  return ctx
}

export function StudioProvider({ children }: { children: React.ReactNode }) {
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const queryClient = useQueryClient()

  // ---------- 项目 ----------
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const activeIdRef = useRef<string | null>(null)
  const [messages, setMessages] = useState<StudioChatMessage[]>([])
  const cacheRef = useRef<Record<string, StudioChatMessage[]>>({})

  const sessionsQuery = useQuery({
    queryKey: ["agent", "sessions"],
    queryFn: async () => (await AgentService.listSessions()).data,
  })
  const sessions = sessionsQuery.data?.data ?? []
  const activeSessionTitle =
    sessions.find((s) => s.id === activeSessionId)?.title || "新的项目"

  const updateMessages = useCallback(
    (key: string, updater: (prev: StudioChatMessage[]) => StudioChatMessage[]) => {
      const next = updater(cacheRef.current[key] ?? [])
      cacheRef.current[key] = next
      if (keyOf(activeIdRef.current) === key) {
        setMessages(next)
      }
    },
    [],
  )

  const loadSessionMessages = useCallback(
    (sessionId: string) => {
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
          const loaded: StudioChatMessage[] = data.data.map((m) => ({
            id: m.id,
            role: m.role === "user" ? "user" : "assistant",
            content: m.content,
            status: "done",
          }))
          cacheRef.current[sessionId] = loaded
          if (activeIdRef.current === sessionId) {
            setMessages(loaded)
          }
        })
        .catch((err: unknown) => showErrorToast(getErrorMessage(err)))
    },
    [queryClient, showErrorToast],
  )

  const selectSession = useCallback(
    (id: string | null) => {
      if (id === activeIdRef.current) return
      cacheRef.current[keyOf(activeIdRef.current)] = messages
      activeIdRef.current = id
      setActiveSessionId(id)
      const cached = cacheRef.current[keyOf(id)]
      setMessages(cached ?? [])
      if (id !== null && cached === undefined) {
        loadSessionMessages(id)
      }
    },
    [messages, loadSessionMessages],
  )

  const deleteSessionMutation = useMutation({
    mutationFn: (sessionId: string) =>
      AgentService.deleteSession({ path: { session_id: sessionId } }),
    onSuccess: (_data, sessionId) => {
      delete cacheRef.current[sessionId]
      if (activeIdRef.current === sessionId) {
        activeIdRef.current = null
        setActiveSessionId(null)
        setMessages(cacheRef.current[DRAFT_KEY] ?? [])
      }
      queryClient.invalidateQueries({ queryKey: ["agent", "sessions"] })
      showSuccessToast("项目已删除")
    },
    onError: (err) => showErrorToast(getErrorMessage(err)),
  })

  // ---------- 对话 ----------
  const [isStreaming, setIsStreaming] = useState(false)
  const abortChatRef = useRef<AbortController | null>(null)
  const pendingTargetRef = useRef<string | null>(null)

  const runChat = useCallback(
    (
      text: string,
      sessionId: string | null,
      replyId: string,
      files: File[],
      sentAttachments: StudioAttachment[],
      signal?: AbortSignal,
    ) => {
      setIsStreaming(true)
      pendingTargetRef.current = keyOf(sessionId)

      streamChat(
        { text, sessionId, files, signal },
        {
          onSession: (sid) => {
            if (!sessionId) {
              pendingTargetRef.current = sid
              cacheRef.current[sid] = cacheRef.current[DRAFT_KEY] ?? []
              delete cacheRef.current[DRAFT_KEY]
              if (activeIdRef.current === null) {
                activeIdRef.current = sid
                setActiveSessionId(sid)
              }
            }
          },
          onDelta: (delta) => {
            const key = keyOf(pendingTargetRef.current)
            updateMessages(key, (prev) =>
              prev.map((m) =>
                m.id === replyId ? { ...m, content: m.content + delta } : m,
              ),
            )
          },
        },
      )
        .then((result) => {
          const targetId = sessionId ?? result.session_id
          updateMessages(keyOf(targetId), (prev) =>
            prev.map((m) =>
              m.id === replyId
                ? { ...m, content: result.response, status: "done" }
                : m,
            ),
          )
          queryClient.invalidateQueries({ queryKey: ["agent", "sessions"] })
        })
        .catch((err: unknown) => {
          const canceled = (err as Error)?.name === "AbortError"
          const message = canceled ? "已停止生成" : getErrorMessage(err)
          updateMessages(keyOf(pendingTargetRef.current), (prev) =>
            prev.map((m) =>
              m.id === replyId
                ? {
                    ...m,
                    content: message,
                    status: "error",
                    retryText: text,
                    attachments: sentAttachments,
                  }
                : m,
            ),
          )
          if (!canceled) {
            showErrorToast(message)
          }
          queryClient.invalidateQueries({ queryKey: ["agent", "sessions"] })
        })
        .finally(() => setIsStreaming(false))
    },
    [queryClient, showErrorToast, updateMessages],
  )

  const [attachments, setAttachments] = useState<StudioAttachment[]>([])

  const addAttachments = useCallback(
    (fileList: FileList | null) => {
      const incoming = Array.from(fileList ?? [])
      if (incoming.length === 0) return
      setAttachments((prev) => {
        const room = MAX_FILES - prev.length
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
        return [...prev, ...next]
      })
    },
    [showErrorToast],
  )

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => {
      const target = prev.find((a) => a.id === id)
      if (target?.url) URL.revokeObjectURL(target.url)
      return prev.filter((a) => a.id !== id)
    })
  }, [])

  const sendMessage = useCallback(
    (raw: string) => {
      const text = raw.trim()
      if ((!text && attachments.length === 0) || isStreaming) return
      const sessionId = activeIdRef.current
      const replyId = newId()
      const sentAttachments = attachments
      const controller = new AbortController()
      abortChatRef.current = controller
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
      setAttachments([])
      runChat(
        text,
        sessionId,
        replyId,
        sentAttachments.map((a) => a.file),
        sentAttachments,
        controller.signal,
      )
    },
    [attachments, isStreaming, runChat, updateMessages],
  )

  const retryMessage = useCallback(
    (message: StudioChatMessage) => {
      if (!message.retryText || isStreaming) return
      const sessionId = activeIdRef.current
      updateMessages(keyOf(sessionId), (prev) =>
        prev.map((m) =>
          m.id === message.id ? { ...m, content: "", status: "pending" } : m,
        ),
      )
      const controller = new AbortController()
      abortChatRef.current = controller
      runChat(
        message.retryText,
        sessionId,
        message.id,
        (message.attachments ?? []).map((a) => a.file),
        message.attachments ?? [],
        controller.signal,
      )
    },
    [isStreaming, runChat, updateMessages],
  )

  const stopStreaming = useCallback(() => {
    abortChatRef.current?.abort()
    abortChatRef.current = null
  }, [])

  // ---------- 创作 ----------
  const [params, setParamsState] = useState<ComposeParams>(DEFAULT_PARAMS)
  const [stages, setStages] = useState<ComposeStageEvent[]>([])
  const [isComposing, setIsComposing] = useState(false)
  const [composeError, setComposeError] = useState<string | null>(null)
  const composeAbortRef = useRef<AbortController | null>(null)

  const setParams = useCallback((patch: Partial<ComposeParams>) => {
    setParamsState((prev) => ({ ...prev, ...patch }))
  }, [])

  const applyStyle = useCallback((style: StyleId) => {
    const preset = styleById(style)
    setParamsState((prev) => ({
      ...prev,
      style,
      bpm: preset?.bpm ?? prev.bpm,
      mode: preset?.mode ?? prev.mode,
      mood: preset?.mood ?? prev.mood,
    }))
  }, [])

  // ---------- 作品库 ----------
  const [tracks, setTracks] = useState<StudioTrack[]>([])

  useEffect(() => {
    try {
      const raw = localStorage.getItem(TRACKS_STORAGE_KEY)
      if (raw) setTracks(JSON.parse(raw) as StudioTrack[])
    } catch {
      // 忽略损坏的本地缓存
    }
  }, [])

  const persistTracks = (next: StudioTrack[]) => {
    try {
      localStorage.setItem(TRACKS_STORAGE_KEY, JSON.stringify(next))
    } catch {
      // 存储满等异常不阻塞界面
    }
  }

  const addTrack = useCallback(
    (track: StudioTrack) => {
      setTracks((prev) => {
        const next = [track, ...prev]
        persistTracks(next)
        return next
      })
    },
    [],
  )

  const removeTrack = useCallback((id: string) => {
    setTracks((prev) => {
      const next = prev.filter((t) => t.id !== id)
      persistTracks(next)
      return next
    })
  }, [])

  // ---------- 播放器 ----------
  const [currentTrack, setCurrentTrack] = useState<StudioTrack | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [position, setPosition] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolumeState] = useState(0.9)
  const [loop, setLoop] = useState(false)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [peaks, setPeaks] = useState<number[] | null>(null)

  const audioRef = useRef<HTMLAudioElement | null>(null)
  const objectUrlRef = useRef<string | null>(null)
  const audioCtxRef: { current: AudioContext | null } = useRef(null)

  const getAudio = useCallback(() => {
    if (!audioRef.current) {
      const audio = new Audio()
      audio.preload = "auto"
      audio.addEventListener("timeupdate", () =>
        setPosition(audio.currentTime),
      )
      audio.addEventListener("loadedmetadata", () =>
        setDuration(audio.duration || 0),
      )
      audio.addEventListener("play", () => setIsPlaying(true))
      audio.addEventListener("pause", () => setIsPlaying(false))
      audio.addEventListener("ended", () => setIsPlaying(false))
      audioRef.current = audio
    }
    return audioRef.current
  }, [])

  const togglePlay = useCallback(() => {
    const audio = getAudio()
    if (!audio.src) return
    if (audio.paused) {
      audio.play().catch(() => showErrorToast("音频播放失败"))
    } else {
      audio.pause()
    }
  }, [getAudio, showErrorToast])

  const seek = useCallback(
    (ratio: number) => {
      const audio = getAudio()
      if (!audio.duration) return
      audio.currentTime = Math.min(Math.max(ratio, 0), 1) * audio.duration
      setPosition(audio.currentTime)
    },
    [getAudio],
  )

  const setVolume = useCallback(
    (v: number) => {
      setVolumeState(v)
      getAudio().volume = v
    },
    [getAudio],
  )

  const toggleLoop = useCallback(() => {
    setLoop((prev) => {
      getAudio().loop = !prev
      return !prev
    })
  }, [getAudio])

  /** 用认证请求拉取音频产物，返回 blob URL，并解码波形峰值 */
  const loadTrack = useCallback(
    (track: StudioTrack) => {
      setCurrentTrack(track)
      setPosition(0)
      setDuration(0)
      setPeaks(null)

      const audio = getAudio()
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current)
        objectUrlRef.current = null
      }

      if (!track.mp3File) {
        audio.removeAttribute("src")
        setAudioUrl(null)
        return
      }

      const token = localStorage.getItem("access_token") ?? ""
      fetch(`/api/v1/agent/scores/${encodeURIComponent(track.mp3File)}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      })
        .then(async (res) => {
          if (!res.ok) throw new Error(`音频加载失败（HTTP ${res.status}）`)
          const blob = await res.blob()
          const url = URL.createObjectURL(blob)
          objectUrlRef.current = url
          audio.src = url
          setAudioUrl(url)
          audio.play().catch(() => setIsPlaying(false))
          // 解码波形
          try {
            audioCtxRef.current ??= new AudioContext()
            const arrayBuffer = await blob.arrayBuffer()
            const buffer =
              await audioCtxRef.current.decodeAudioData(arrayBuffer)
            const channel = buffer.getChannelData(0)
            const buckets = 120
            const size = Math.floor(channel.length / buckets)
            const nextPeaks: number[] = []
            for (let i = 0; i < buckets; i++) {
              let max = 0
              for (let j = 0; j < size; j++) {
                const v = Math.abs(channel[i * size + j] || 0)
                if (v > max) max = v
              }
              nextPeaks.push(max)
            }
            const peakMax = Math.max(...nextPeaks, 0.01)
            setPeaks(nextPeaks.map((p) => p / peakMax))
          } catch {
            setPeaks(null)
          }
        })
        .catch((err: unknown) => {
          setAudioUrl(null)
          showErrorToast(getErrorMessage(err))
        })
    },
    [getAudio, showErrorToast],
  )

  const playNext = useCallback(
    (dir: 1 | -1) => {
      if (tracks.length === 0) return
      const idx = tracks.findIndex((t) => t.id === currentTrack?.id)
      const nextIdx = (idx + dir + tracks.length) % tracks.length
      loadTrack(tracks[nextIdx])
    },
    [currentTrack, loadTrack, tracks],
  )

  // ---------- 歌词 ----------
  const [lyrics, setLyricsState] = useState("")
  const lyricsSessionRef = useRef<string | null>(null)

  useEffect(() => {
    // 切换项目时加载对应的歌词草稿
    if (lyricsSessionRef.current === activeSessionId) return
    lyricsSessionRef.current = activeSessionId
    try {
      const saved = localStorage.getItem(
        `${LYRICS_STORAGE_PREFIX}:${keyOf(activeSessionId)}`,
      )
      setLyricsState(saved ?? "")
    } catch {
      setLyricsState("")
    }
  }, [activeSessionId])

  const setLyrics = useCallback(
    (text: string) => {
      setLyricsState(text)
      try {
        localStorage.setItem(
          `${LYRICS_STORAGE_PREFIX}:${keyOf(activeSessionId)}`,
          text,
        )
      } catch {
        // 忽略存储异常
      }
    },
    [activeSessionId],
  )

  // ---------- 创作流水线 ----------
  const startComposeInner = useCallback(() => {
    if (isComposing) return
    setIsComposing(true)
    setComposeError(null)
    setStages([])
    const controller = new AbortController()
    composeAbortRef.current = controller

    streamCompose(
      {
        style: params.style,
        mood: params.mood,
        key: params.key,
        mode: params.mode,
        bpm: params.bpm,
        bars: params.bars,
        beats_per_bar: params.beatsPerBar,
        user_notes: params.userNotes,
        session_id: activeIdRef.current,
      },
      {
        onSession: (sid) => {
          if (!activeIdRef.current) {
            activeIdRef.current = sid
            setActiveSessionId(sid)
            cacheRef.current[sid] = []
            queryClient.invalidateQueries({ queryKey: ["agent", "sessions"] })
          }
        },
        onStage: (stage) => setStages((prev) => [...prev, stage]),
        onDone: (payload: ComposeDonePayload) => {
          const preset = styleById(params.style)
          const track: StudioTrack = {
            id: newId(),
            sessionId: activeIdRef.current,
            title: `${preset?.label ?? params.style} · ${params.key}${
              params.mode === "minor" ? "m" : ""
            } ${params.bpm}BPM`,
            style: params.style,
            key: params.key,
            mode: params.mode,
            bpm: params.bpm,
            bars: params.bars,
            chords: payload.chords,
            mp3File: basename(payload.mp3_path),
            midiFile: basename(payload.midi_path),
            createdAt: new Date().toISOString(),
          }
          addTrack(track)
          // 往当前会话的对话里补一条创作完成消息
          updateMessages(keyOf(activeIdRef.current), (prev) => [
            ...prev,
            {
              id: newId(),
              role: "user",
              content: `创作请求：${preset?.label ?? params.style} ${params.key} ${
                params.mode === "minor" ? "小调" : "大调"
              } ${params.bpm}BPM ${params.bars} 小节`,
              status: "done",
            },
            {
              id: newId(),
              role: "assistant",
              content: `🎵 创作完成\n\n${payload.summary}`,
              status: "done",
            },
          ])
          queryClient.invalidateQueries({ queryKey: ["agent", "sessions"] })
          showSuccessToast("创作完成，已加入作品库")
          loadTrack(track)
        },
      },
      controller.signal,
    )
      .catch((err: unknown) => {
        if ((err as Error)?.name !== "AbortError") {
          const message = getErrorMessage(err)
          setComposeError(message)
          showErrorToast(message)
        }
      })
      .finally(() => {
        setIsComposing(false)
        composeAbortRef.current = null
      })
  }, [
    activeIdRef,
    addTrack,
    isComposing,
    loadTrack,
    params,
    queryClient,
    showErrorToast,
    showSuccessToast,
    updateMessages,
  ])

  // applyStyle 是创作面板专用的小工具，合并进 setParams 语义里对外暴露
  const value = useMemo<StudioContextValue>(
    () => ({
      sessions,
      activeSessionId,
      activeSessionTitle,
      selectSession,
      deleteSession: (id: string) => deleteSessionMutation.mutate(id),
      messages,
      isStreaming,
      sendMessage,
      retryMessage,
      stopStreaming,
      attachments,
      addAttachments,
      removeAttachment,
      params,
      setParams: (patch) => {
        if ("style" in patch && patch.style !== params.style) {
          applyStyle(patch.style as StyleId)
          return
        }
        setParams(patch)
      },
      stages,
      isComposing,
      composeError,
      startCompose: startComposeInner,
      stopCompose: () => composeAbortRef.current?.abort(),
      tracks,
      addTrack,
      removeTrack,
      currentTrack,
      loadTrack,
      isPlaying,
      togglePlay,
      position,
      duration,
      seek,
      volume,
      setVolume,
      loop,
      toggleLoop,
      audioUrl,
      peaks,
      playNext,
      lyrics,
      setLyrics,
    }),
    [
      activeSessionId,
      activeSessionTitle,
      addAttachments,
      addTrack,
      applyStyle,
      audioUrl,
      composeError,
      currentTrack,
      deleteSessionMutation,
      duration,
      isComposing,
      isPlaying,
      isStreaming,
      loadTrack,
      loop,
      lyrics,
      messages,
      params,
      peaks,
      playNext,
      position,
      removeAttachment,
      removeTrack,
      retryMessage,
      seek,
      selectSession,
      sendMessage,
      sessions,
      setLyrics,
      setParams,
      setVolume,
      startComposeInner,
      stages,
      stopStreaming,
      toggleLoop,
      togglePlay,
      tracks,
      volume,
    ],
  )

  return <StudioContext.Provider value={value}>{children}</StudioContext.Provider>
}
