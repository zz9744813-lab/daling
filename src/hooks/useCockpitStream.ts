import { useEffect, useRef, useState, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { cockpitApi } from '../api/client'
import type {
  AgentRole,
  AgentStatus,
  ContinuousStatus,
  KnownSSEEventType,
  SSEEvent,
  SSEEventType,
} from '../types'

const CONTINUOUS_EVENT_TYPES: KnownSSEEventType[] = [
  'run_started',
  'policy_updated',
  'run_paused',
  'run_resumed',
  'run_stopped',
  'run_recovering',
  'worker_acquired',
  'worker_shutdown',
  'target_reached',
  'story_structure_extended',
  'chapter_completed',
  'quality_hold',
  'budget_hold',
  'circuit_open',
  'retry_scheduled',
  'supervisor_failed',
]

const REGISTERED_EVENT_TYPES: KnownSSEEventType[] = [
  'agent_start',
  'agent_complete',
  'chapter_progress',
  'review_needed',
  'error',
  'heartbeat',
  ...CONTINUOUS_EVENT_TYPES,
]

interface UseCockpitStreamResult {
  /** SSE 连接状态 */
  connected: boolean
  /** 实时 Agent 状态（由 SSE 事件驱动更新） */
  agentStatuses: Record<string, AgentStatus>
  /** 流式稿件内容（Drafter 工作时实时拼接） */
  streamingContent: string
  /** 当前流式输出的章节 ID */
  streamingChapterId: string | null
  /** 最近事件日志（用于 UI 提示） */
  lastEvent: SSEEvent | null
  /** 手动重连 */
  reconnect: () => void
}

/**
 * useCockpitStream —— 创作舱 SSE 实时流 Hook
 *
 * 连接 /api/cockpit/{project_id}/stream，监听以下事件：
 * - agent_start:      Agent 开始工作 → 状态置为 working
 * - agent_complete:   Agent 完成 → 状态置为 idle
 * - chapter_progress: 章节内容增量 → 拼接到 streamingContent
 * - review_needed:    需要人工审阅
 * - chapter_completed / quality_hold / budget_hold / retry_scheduled: 持久化生产事件
 * - error:            错误事件
 * - heartbeat:        心跳保活
 */
export function useCockpitStream(
  projectId: string | undefined,
  initialStatuses?: AgentStatus[],
): UseCockpitStreamResult {
  const queryClient = useQueryClient()
  const [connected, setConnected] = useState(false)
  const [agentStatuses, setAgentStatuses] = useState<Record<string, AgentStatus>>({})
  const [streamingContent, setStreamingContent] = useState('')
  const [streamingChapterId, setStreamingChapterId] = useState<string | null>(null)
  const [lastEvent, setLastEvent] = useState<SSEEvent | null>(null)

  const eventSourceRef = useRef<EventSource | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const streamingChapterIdRef = useRef<string | null>(null)

  // 保持 ref 与 state 同步，避免 connect 闭包过期
  useEffect(() => {
    streamingChapterIdRef.current = streamingChapterId
  }, [streamingChapterId])

  // 初始化 agent 状态
  useEffect(() => {
    if (initialStatuses) {
      const map: Record<string, AgentStatus> = {}
      initialStatuses.forEach((s) => {
        map[s.agent_role] = s
      })
      setAgentStatuses(map)
    }
  }, [initialStatuses])

  const syncQueriesForEvent = useCallback(
    (type: SSEEventType, data: Record<string, unknown>) => {
      if (!projectId) return

      if (type === 'heartbeat') {
        // 新版 heartbeat 本身就是完整 ContinuousStatus。直接写入缓存，既能
        // 保持 1 秒级心跳，又不会每秒制造一轮 HTTP 轮询。
        if (data.run_id !== undefined && data.status && data.project_id) {
          queryClient.setQueryData(
            ['continuous-status', projectId],
            data as unknown as ContinuousStatus,
          )
        }
        return
      }

      if (!CONTINUOUS_EVENT_TYPES.includes(type as KnownSSEEventType)) return

      void queryClient.invalidateQueries({ queryKey: ['continuous-status', projectId] })
      void queryClient.invalidateQueries({ queryKey: ['continuous-events', projectId] })
      void queryClient.invalidateQueries({ queryKey: ['cockpit', projectId] })

      if (
        type === 'chapter_completed' ||
        type === 'quality_hold' ||
        type === 'story_structure_extended'
      ) {
        void queryClient.invalidateQueries({ queryKey: ['chapters', projectId] })
        void queryClient.invalidateQueries({ queryKey: ['chapter-version', projectId] })
        void queryClient.invalidateQueries({ queryKey: ['review-chapter', projectId] })
        void queryClient.invalidateQueries({ queryKey: ['review-chapter-version', projectId] })
        void queryClient.invalidateQueries({ queryKey: ['review-queue-pending', projectId] })
        void queryClient.invalidateQueries({ queryKey: ['evolution-overview', projectId] })
        void queryClient.invalidateQueries({ queryKey: ['brain-overview', projectId] })
      }

      if (type === 'story_structure_extended') {
        void queryClient.invalidateQueries({ queryKey: ['preparation-status', projectId] })
        void queryClient.invalidateQueries({ queryKey: ['storyline', projectId] })
      }

      if (type === 'budget_hold' || type === 'chapter_completed') {
        void queryClient.invalidateQueries({ queryKey: ['usage', projectId] })
      }
    },
    [projectId, queryClient],
  )

  const connect = useCallback(() => {
    if (!projectId) return

    // 关闭旧连接
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
      eventSourceRef.current = null
    }

    const url = cockpitApi.stream(projectId)
    const es = new EventSource(url)
    eventSourceRef.current = es

    es.onopen = () => {
      setConnected(true)
    }

    es.onerror = () => {
      setConnected(false)
      // 自动重连（EventSource 本身会重连，这里仅更新状态 + 兜底）
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = setTimeout(() => {
        connect()
      }, 5000)
    }

    const handleEvent = (type: SSEEventType) => (ev: MessageEvent) => {
      let data: Record<string, unknown> = {}
      try {
        data = JSON.parse(ev.data)
      } catch {
        data = { raw: ev.data }
      }

      const sseEvent: SSEEvent = { event: type, data: data as SSEEvent['data'] }
      setLastEvent(sseEvent)
      syncQueriesForEvent(type, data)

      switch (type) {
        case 'agent_start': {
          const role = data.agent_role as AgentRole
          if (role) {
            setAgentStatuses((prev) => ({
              ...prev,
              [role]: {
                agent_role: role,
                status: 'working',
                message: (data.message as string) ?? '工作中',
                current_task: (data.task as string) ?? undefined,
                started_at: new Date().toISOString(),
              },
            }))
          }
          break
        }
        case 'agent_complete': {
          const role = data.agent_role as AgentRole
          if (role) {
            setAgentStatuses((prev) => ({
              ...prev,
              [role]: {
                ...(prev[role] ?? {}),
                agent_role: role,
                status: 'idle',
                message: (data.message as string) ?? '已完成',
              },
            }))
          }
          break
        }
        case 'chapter_progress': {
          // 增量内容拼接
          const delta = (data.delta as string) ?? ''
          const content = (data.content as string) ?? ''
          const chapterId = (data.chapter_id as string) ?? null

          if (chapterId && chapterId !== streamingChapterIdRef.current) {
            // 切换到新章节，重置内容
            setStreamingChapterId(chapterId)
            streamingChapterIdRef.current = chapterId
            setStreamingContent(content || delta)
          } else if (delta) {
            setStreamingContent((prev) => prev + delta)
          } else if (content) {
            setStreamingContent(content)
          }
          if (chapterId) {
            setStreamingChapterId(chapterId)
            streamingChapterIdRef.current = chapterId
          }
          break
        }
        case 'error': {
          const role = data.agent_role as AgentRole
          const errorMsg = (data.error as string) ?? (data.message as string) ?? '未知错误'
          if (role) {
            setAgentStatuses((prev) => ({
              ...prev,
              [role]: {
                ...(prev[role] ?? {}),
                agent_role: role,
                status: 'error',
                message: errorMsg,
              },
            }))
          }
          break
        }
        case 'heartbeat':
          // 状态缓存已在 syncQueriesForEvent 中原子更新。
          break
        case 'review_needed':
          // 由调用方通过 lastEvent 处理
          break
        case 'chapter_completed':
          setStreamingContent('')
          setStreamingChapterId(null)
          streamingChapterIdRef.current = null
          break
      }
    }

    // EventSource 对命名事件没有通配监听，必须显式注册后端可能发送的每类
    // 持久化事件，否则浏览器会静默丢弃 chapter_completed 等关键通知。
    REGISTERED_EVENT_TYPES.forEach(
      (type) => {
        es.addEventListener(type, handleEvent(type) as EventListener)
      },
    )

    // 未命名事件（默认 message）也监听
    es.addEventListener('message', handleEvent('heartbeat') as EventListener)
  }, [projectId, syncQueriesForEvent])

  useEffect(() => {
    connect()

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  const reconnect = useCallback(() => {
    connect()
  }, [connect])

  return {
    connected,
    agentStatuses,
    streamingContent,
    streamingChapterId,
    lastEvent,
    reconnect,
  }
}
