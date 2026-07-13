import React, { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  BrainCircuit,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDollarSign,
  ClipboardCheck,
  Clock3,
  Copy,
  Gauge,
  HeartPulse,
  History,
  Infinity as InfinityIcon,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Square,
  TimerReset,
  X,
  Zap,
} from 'lucide-react'
import { continuousApi, usageApi } from '../../api/client'
import { cn } from '../../lib/cn'
import type {
  ContinuousObservedState,
  ContinuousRunError,
  ContinuousRunEvent,
  ContinuousStartContract,
  ContinuousStatus,
} from '../../types'

interface AutopilotControlCenterProps {
  projectId: string
  /** 当前在稿件桌浏览的章节；不得用于推导生产进度。 */
  displayedChapter?: number
  remainingChapters?: number
  projectAutonomyLevel?: string
  onOpenReview?: () => void
}

type CenterView = 'overview' | 'contract'
type MutationKind = 'pause' | 'resume' | 'stop'

const DEFAULT_CONTRACT: ContinuousStartContract = {
  target_chapters: 10,
  autonomy_level: 'L3',
  quality_threshold: 85,
  max_rewrite_rounds: 2,
  chapter_delay_seconds: 5,
  error_backoff_seconds: 30,
  max_consecutive_failures: 3,
  circuit_cooldown_seconds: 300,
  quality_failure_action: 'retry',
  max_quality_retry_cycles: 2,
  quality_retry_backoff_seconds: 30,
  learning_interval_chapters: 1,
  daily_cost_limit: null,
  daily_token_limit: null,
}

const OBSERVED_STATE: Record<
  string,
  { label: string; tone: 'green' | 'blue' | 'amber' | 'red' | 'gray'; description: string }
> = {
  starting: { label: '启动中', tone: 'blue', description: '监督器正在取得任务租约' },
  recovering: { label: '恢复中', tone: 'amber', description: '服务重启后正在恢复生产任务' },
  running: { label: '生产中', tone: 'green', description: '监督器正在推进下一生产周期' },
  retry_wait: { label: '等待重试', tone: 'amber', description: '已按退避策略安排下一次重试' },
  circuit_open: { label: '熔断冷却', tone: 'amber', description: '保护期结束后会自动进行半开探测' },
  half_open: { label: '恢复探测', tone: 'blue', description: '正在验证模型服务是否恢复' },
  paused: { label: '已暂停', tone: 'gray', description: '操作者已暂停自动生产' },
  quality_hold: { label: '质量接管', tone: 'amber', description: '自动修复周期已用尽，需要人工确认' },
  budget_hold: { label: '预算暂停', tone: 'amber', description: '今日成本或 Token 已达到契约上限' },
  failed: { label: '监督器异常', tone: 'red', description: '监督器遇到不可恢复的内部异常' },
  completed: { label: '目标完成', tone: 'green', description: '本次生产契约已经完成' },
  stopped: { label: '已停止', tone: 'gray', description: '当前没有自动生产任务在执行' },
}

const EVENT_LABELS: Record<string, string> = {
  run_started: '启动任务',
  policy_updated: '策略更新',
  run_paused: '暂停任务',
  run_resumed: '继续任务',
  run_stopped: '停止任务',
  run_recovering: '恢复任务',
  worker_shutdown: '工作器关闭',
  target_reached: '目标完成',
  story_structure_extended: '扩展故事结构',
  chapter_completed: '章节完成',
  quality_bypassed: '质量放行',
  quality_hold: '质量暂停',
  quality_retry_scheduled: '自动质量修复',
  budget_hold: '预算暂停',
  circuit_open: '熔断保护',
  circuit_half_open: '半开探测',
  retry_scheduled: '安排重试',
  supervisor_failed: '监督器异常',
}

function initialContract(
  remainingChapters?: number,
  projectAutonomyLevel?: string,
): ContinuousStartContract {
  const level = ['L2', 'L3', 'L4'].includes(projectAutonomyLevel ?? '')
    ? (projectAutonomyLevel as 'L2' | 'L3' | 'L4')
    : 'L3'
  return {
    ...DEFAULT_CONTRACT,
    autonomy_level: level,
    target_chapters:
      remainingChapters != null
        ? Math.min(5000, Math.max(1, Math.round(remainingChapters)))
        : DEFAULT_CONTRACT.target_chapters,
  }
}

function contractFromStatus(status: ContinuousStatus): ContinuousStartContract {
  // 兼容服务滚动升级期间尚未返回 policy 的旧状态响应；
  // 这些值只作为下一次显式提交的编辑初值，不作为已生效策略展示。
  const policy = status.policy ?? DEFAULT_CONTRACT
  return {
    target_chapters: status.target_chapters,
    autonomy_level: status.autonomy_level ?? DEFAULT_CONTRACT.autonomy_level,
    quality_threshold: policy.quality_threshold,
    max_rewrite_rounds: policy.max_rewrite_rounds,
    chapter_delay_seconds: policy.chapter_delay_seconds,
    error_backoff_seconds: policy.error_backoff_seconds,
    max_consecutive_failures: policy.max_consecutive_failures,
    circuit_cooldown_seconds:
      policy.circuit_cooldown_seconds ?? DEFAULT_CONTRACT.circuit_cooldown_seconds,
    quality_failure_action: policy.quality_failure_action,
    max_quality_retry_cycles:
      policy.max_quality_retry_cycles ?? DEFAULT_CONTRACT.max_quality_retry_cycles,
    quality_retry_backoff_seconds:
      policy.quality_retry_backoff_seconds ?? DEFAULT_CONTRACT.quality_retry_backoff_seconds,
    learning_interval_chapters: policy.learning_interval_chapters,
    daily_cost_limit: policy.daily_cost_limit ?? null,
    daily_token_limit: policy.daily_token_limit ?? null,
  }
}

function parseTime(value?: string | null) {
  if (!value) return null
  const time = new Date(value).getTime()
  return Number.isFinite(time) ? time : null
}

function formatTimestamp(value?: string | null) {
  const time = parseTime(value)
  if (time == null) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(time)
}

function formatRelative(value: string | null | undefined, now: number) {
  const time = parseTime(value)
  if (time == null) return '暂无心跳'
  const seconds = Math.max(0, Math.round((now - time) / 1000))
  if (seconds < 5) return '刚刚'
  if (seconds < 60) return `${seconds} 秒前`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  return `${Math.floor(minutes / 60)} 小时前`
}

function formatCountdown(value: string | null | undefined, now: number) {
  const time = parseTime(value)
  if (time == null) return '—'
  const seconds = Math.max(0, Math.ceil((time - now) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `${minutes} 分 ${remainder} 秒`
}

function formatDuration(seconds: number) {
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`
  return `${(seconds / 3600).toFixed(seconds % 3600 === 0 ? 0 : 1)} 小时`
}

function formatCost(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '—'
  return `$${value.toFixed(value >= 1 ? 2 : 4)}`
}

function normalizeRunError(error: ContinuousRunError | string) {
  if (typeof error === 'string') return { message: error, at: undefined, chapter_no: null }
  return error
}

function observedMeta(state?: ContinuousObservedState) {
  return OBSERVED_STATE[state ?? 'stopped'] ?? {
    label: state || '未知状态',
    tone: 'gray' as const,
    description: '监督器返回了尚未识别的状态',
  }
}

function toneClasses(tone: 'green' | 'blue' | 'amber' | 'red' | 'gray') {
  return {
    green: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200',
    blue: 'border-blue-400/25 bg-blue-400/10 text-blue-200',
    amber: 'border-amber-400/25 bg-amber-400/10 text-amber-200',
    red: 'border-red-400/25 bg-red-400/10 text-red-200',
    gray: 'border-ink-600 bg-ink-850 text-gray-300',
  }[tone]
}

export function AutopilotControlCenter({
  projectId,
  displayedChapter,
  remainingChapters,
  projectAutonomyLevel,
  onOpenReview,
}: AutopilotControlCenterProps) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<CenterView>('overview')
  const [contract, setContract] = useState<ContinuousStartContract>(() =>
    initialContract(remainingChapters, projectAutonomyLevel),
  )
  const [unlimited, setUnlimited] = useState(false)
  const [stopConfirmation, setStopConfirmation] = useState(false)
  const [copied, setCopied] = useState(false)
  const [now, setNow] = useState(Date.now())
  const hydratedRunRef = useRef<string | null>(null)
  const defaultHydratedProjectRef = useRef<string | null>(null)

  const statusQuery = useQuery({
    queryKey: ['continuous-status', projectId],
    queryFn: () => continuousApi.status(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 4_000,
  })
  const status = statusQuery.data
  const active = status?.desired_state === 'running'

  const eventsQuery = useQuery({
    queryKey: ['continuous-events', projectId],
    queryFn: () => continuousApi.events(projectId, 100),
    enabled: Boolean(projectId && status?.run_id),
    refetchInterval: open || active ? 5_000 : 20_000,
  })

  const usageQuery = useQuery({
    queryKey: ['usage', projectId],
    queryFn: () => usageApi.get(projectId),
    enabled: Boolean(projectId),
    refetchInterval: active ? 15_000 : 60_000,
  })

  useEffect(() => {
    if (status?.run_id) {
      if (hydratedRunRef.current === status.run_id) return
      hydratedRunRef.current = status.run_id
      defaultHydratedProjectRef.current = projectId
      const saved = contractFromStatus(status)
      setContract(saved)
      setUnlimited(saved.target_chapters == null)
      return
    }

    const backendRemaining =
      typeof status?.remaining_chapters === 'number' ? status.remaining_chapters : undefined
    const defaultRemaining = backendRemaining ?? remainingChapters
    if (defaultRemaining == null || defaultHydratedProjectRef.current === projectId) return
    defaultHydratedProjectRef.current = projectId
    hydratedRunRef.current = null
    setContract(initialContract(defaultRemaining, projectAutonomyLevel))
    setUnlimited(false)
  }, [projectId, projectAutonomyLevel, remainingChapters, status])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!open) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const refreshAll = async (nextStatus?: ContinuousStatus) => {
    if (nextStatus) {
      queryClient.setQueryData(['continuous-status', projectId], nextStatus)
    }
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['continuous-status', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['continuous-events', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['usage', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['cockpit', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['chapters', projectId] }),
    ])
  }

  const startMutation = useMutation({
    mutationFn: () =>
      continuousApi.start(projectId, {
        ...contract,
        target_chapters: unlimited ? null : contract.target_chapters,
      }),
    onSuccess: async (nextStatus) => {
      hydratedRunRef.current = nextStatus.run_id
      setView('overview')
      await refreshAll(nextStatus)
    },
  })

  const actionMutation = useMutation({
    mutationFn: async (kind: MutationKind) => {
      if (kind === 'pause') return continuousApi.pause(projectId)
      if (kind === 'resume') return continuousApi.resume(projectId)
      return continuousApi.stop(projectId)
    },
    onSuccess: async (nextStatus) => {
      setStopConfirmation(false)
      await refreshAll(nextStatus)
    },
  })

  const isBusy = startMutation.isPending || actionMutation.isPending
  const meta = observedMeta(status?.status)
  const completed = status?.completed_chapters ?? 0
  const target = status?.target_chapters
  const events = eventsQuery.data ?? []
  const latestEvent = events[0]
  const currentRunError = startMutation.error ?? actionMutation.error
  const workerConcern = Boolean(
    status?.desired_state === 'running' && !status.worker_alive,
  )
  const heartbeatDelayed = Boolean(
    status?.desired_state === 'running' && status.worker_alive && status.heartbeat_stale,
  )
  const canResumePausedRun = Boolean(
    status?.run_id &&
      status.desired_state !== 'running' &&
      status.status === 'paused',
  )
  const isQualityHold = status?.status === 'quality_hold'
  const isBudgetHold = status?.status === 'budget_hold'
  const isFailedHold = status?.status === 'failed'

  const openCenter = (nextView?: CenterView) => {
    const resolvedView = nextView ?? (status?.run_id ? 'overview' : 'contract')
    if (resolvedView === 'contract' && status) {
      const saved = contractFromStatus(status)
      setContract(saved)
      setUnlimited(saved.target_chapters == null)
    }
    setView(resolvedView)
    setOpen(true)
  }

  const copyRunId = async () => {
    if (!status?.run_id) return
    try {
      await navigator.clipboard.writeText(status.run_id)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1_500)
    } catch {
      setCopied(false)
    }
  }

  const setNumber = (
    key:
      | 'target_chapters'
      | 'quality_threshold'
      | 'max_rewrite_rounds'
      | 'chapter_delay_seconds'
      | 'error_backoff_seconds'
      | 'max_consecutive_failures'
      | 'circuit_cooldown_seconds'
      | 'max_quality_retry_cycles'
      | 'quality_retry_backoff_seconds'
      | 'learning_interval_chapters',
    value: number,
  ) => setContract((current) => ({ ...current, [key]: value }))

  const targetIsValid = unlimited || Boolean(contract.target_chapters && contract.target_chapters > 0)

  return (
    <>
      <section
        className={cn(
          'shrink-0 border-b px-3 py-2 sm:px-4',
          statusQuery.isError
            ? 'border-red-400/20 bg-red-400/[0.035]'
            : workerConcern || heartbeatDelayed
              ? 'border-amber-400/20 bg-amber-400/[0.035]'
              : 'border-ink-700 bg-ink-950/72',
        )}
        aria-label="24 小时自动生产状态"
      >
        <div className="flex min-w-0 flex-wrap items-center gap-2 sm:flex-nowrap sm:gap-3">
          <button
            type="button"
            onClick={() => openCenter()}
            className="group flex min-w-0 items-center gap-2 rounded-xl px-1.5 py-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40"
          >
            <span
              className={cn(
                'relative flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border',
                toneClasses(statusQuery.isError ? 'red' : meta.tone),
              )}
            >
              {active ? <Activity size={15} className="animate-pulse" /> : <Zap size={15} />}
              {active && !workerConcern && (
                <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-emerald-300 ring-2 ring-ink-950" />
              )}
            </span>
            <span className="min-w-0">
              <span className="flex items-center gap-1.5">
                <span className="whitespace-nowrap text-xs font-semibold text-gray-100">24H 总控</span>
                <span
                  className={cn(
                    'rounded-full border px-1.5 py-0.5 text-[9px] font-semibold',
                    toneClasses(statusQuery.isError ? 'red' : meta.tone),
                  )}
                >
                  {statusQuery.isLoading
                    ? '同步中'
                    : statusQuery.isError
                      ? '状态不可用'
                      : meta.label}
                </span>
              </span>
              <span className="block max-w-48 truncate text-[10px] text-gray-600 sm:max-w-72">
                {statusQuery.isError
                  ? (statusQuery.error as Error).message
                  : status?.run_id
                    ? latestEvent?.message || meta.description
                    : '配置可持久恢复的自动生产契约'}
              </span>
            </span>
            <ChevronRight size={13} className="shrink-0 text-gray-600 transition-transform group-hover:translate-x-0.5 group-hover:text-emerald-300" />
          </button>

          {status?.run_id && !statusQuery.isError && (
            <div className="order-3 grid w-full grid-cols-2 gap-x-4 gap-y-1 border-t border-ink-700/70 pt-2 text-[10px] sm:order-none sm:ml-1 sm:flex sm:w-auto sm:flex-1 sm:border-l sm:border-t-0 sm:pl-4 sm:pt-0">
              <CompactMetric
                label="意图 / 实际"
                value={`${status.desired_state === 'running' ? '运行' : status.desired_state === 'paused' ? '暂停' : '停止'} / ${meta.label}`}
              />
              <CompactMetric
                label="生产进度"
                value={target ? `${completed} / ${target} 章` : `已完成 ${completed} 章`}
              />
              <CompactMetric
                label="生产位置"
                value={status.current_chapter ? `第 ${status.current_chapter} 章` : '等待下一周期'}
                className="hidden md:block"
              />
              <CompactMetric
                label="正在浏览"
                value={displayedChapter ? `第 ${displayedChapter} 章` : '—'}
                className="hidden xl:block"
              />
              <CompactMetric
                label="监督心跳"
                value={status.heartbeat_stale ? '已超时' : formatRelative(status.last_heartbeat_at, now)}
                danger={status.heartbeat_stale}
                className="hidden lg:block"
              />
              {status.status === 'retry_wait' && (
                <CompactMetric
                  label="下次重试"
                  value={formatCountdown(status.next_run_at, now)}
                  danger
                />
              )}
            </div>
          )}

          <div className="ml-auto flex shrink-0 items-center gap-1.5">
            {statusQuery.isError ? (
              <StripButton
                label="重试"
                icon={<RefreshCw size={12} />}
                onClick={() => statusQuery.refetch()}
              />
            ) : !status?.run_id || ['stopped', 'completed'].includes(status.status) ? (
              <StripButton
                label="配置并启动"
                icon={<Play size={12} />}
                onClick={() => openCenter('contract')}
                primary
              />
            ) : isQualityHold ? (
              <StripButton
                label="处理质检"
                icon={<ClipboardCheck size={12} />}
                onClick={() => (onOpenReview ? onOpenReview() : openCenter('overview'))}
                disabled={isBusy}
                attention
              />
            ) : isBudgetHold ? (
              <StripButton
                label="调整预算"
                icon={<CircleDollarSign size={12} />}
                onClick={() => openCenter('contract')}
                disabled={isBusy}
                attention
              />
            ) : isFailedHold ? (
              <StripButton
                label="检查熔断"
                icon={<ShieldAlert size={12} />}
                onClick={() => openCenter('overview')}
                disabled={isBusy}
                danger
              />
            ) : workerConcern ? (
              <StripButton
                label="恢复 Worker"
                icon={actionMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                onClick={() => actionMutation.mutate('resume')}
                disabled={isBusy}
                attention
              />
            ) : active ? (
              <StripButton
                label="暂停"
                icon={actionMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Pause size={12} />}
                onClick={() => actionMutation.mutate('pause')}
                disabled={isBusy}
              />
            ) : canResumePausedRun ? (
              <StripButton
                label="恢复生产"
                icon={actionMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                onClick={() => actionMutation.mutate('resume')}
                disabled={isBusy}
                primary
              />
            ) : status?.run_id ? (
              <StripButton
                label="查看原因"
                icon={<AlertTriangle size={12} />}
                onClick={() => openCenter('overview')}
              />
            ) : null}
            <StripButton
              label="总控详情"
              icon={<Settings2 size={12} />}
              onClick={() => openCenter()}
              className="hidden sm:inline-flex"
            />
          </div>
        </div>
      </section>

      {open && (
        <div
          className="fixed inset-0 z-[70] flex items-end justify-center bg-black/70 backdrop-blur-sm sm:items-center sm:p-4"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) setOpen(false)
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="autopilot-title"
            className="flex h-[96dvh] w-full flex-col overflow-hidden rounded-t-3xl border border-ink-700 bg-ink-900 shadow-[0_30px_120px_rgba(0,0,0,0.62)] sm:h-[min(92vh,880px)] sm:max-w-6xl sm:rounded-3xl"
          >
            <header className="shrink-0 border-b border-ink-700 bg-ink-900/95 px-4 py-4 sm:px-6">
              <div className="flex items-start gap-3">
                <div className={cn('flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border', toneClasses(meta.tone))}>
                  <Zap size={19} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 id="autopilot-title" className="text-base font-semibold text-gray-50 sm:text-lg">
                      24 小时自动生产总控
                    </h2>
                    {status?.run_id && (
                      <span className={cn('rounded-full border px-2 py-0.5 text-[10px] font-semibold', toneClasses(meta.tone))}>
                        {meta.label}
                      </span>
                    )}
                    {workerConcern && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/25 bg-amber-400/10 px-2 py-0.5 text-[10px] text-amber-200">
                        <ShieldAlert size={10} /> 监督器需关注
                      </span>
                    )}
                    {heartbeatDelayed && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-blue-400/25 bg-blue-400/10 px-2 py-0.5 text-[10px] text-blue-200">
                        <Activity size={10} className="animate-pulse" /> 长模型调用中 · 心跳稍有延迟
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-xs leading-5 text-gray-500">
                    持久化运行意图、质量门、失败熔断、学习节奏与预算约束；关闭浏览器不会停止任务。
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-gray-500 hover:bg-ink-700 hover:text-gray-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40"
                  aria-label="关闭 24 小时总控"
                >
                  <X size={18} />
                </button>
              </div>

              <div className="mt-4 flex items-center gap-1 rounded-xl bg-ink-950/80 p-1 sm:w-fit">
                <ViewTab active={view === 'overview'} onClick={() => setView('overview')} disabled={!status?.run_id}>
                  <Activity size={13} /> 运行总览
                </ViewTab>
                <ViewTab active={view === 'contract'} onClick={() => openCenter('contract')}>
                  <ClipboardCheck size={13} /> 生产契约
                </ViewTab>
              </div>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto">
              {statusQuery.isLoading ? (
                <div className="flex h-full min-h-80 items-center justify-center gap-2 text-sm text-gray-500">
                  <Loader2 size={18} className="animate-spin" /> 正在读取持久化运行状态…
                </div>
              ) : statusQuery.isError ? (
                <div className="mx-auto flex min-h-80 max-w-lg flex-col items-center justify-center px-6 text-center">
                  <AlertTriangle size={28} className="text-red-300" />
                  <h3 className="mt-4 text-sm font-semibold text-gray-100">无法读取自动生产状态</h3>
                  <p className="mt-2 text-xs leading-6 text-gray-500">{(statusQuery.error as Error).message}</p>
                  <button
                    type="button"
                    onClick={() => statusQuery.refetch()}
                    className="mt-5 inline-flex h-10 items-center gap-2 rounded-xl bg-emerald-300 px-4 text-sm font-semibold text-emerald-950"
                  >
                    <RefreshCw size={14} /> 重新连接
                  </button>
                </div>
              ) : view === 'contract' ? (
                <ContractEditor
                  contract={contract}
                  unlimited={unlimited}
                  setUnlimited={setUnlimited}
                  setContract={setContract}
                  setNumber={setNumber}
                  status={status}
                  usageToday={usageQuery.data?.today_cost}
                  usageTokens={
                    usageQuery.data
                      ? usageQuery.data.today_input_tokens + usageQuery.data.today_output_tokens
                      : undefined
                  }
                  error={startMutation.error as Error | null}
                />
              ) : status ? (
                <RunOverview
                  status={status}
                  events={events}
                  eventsLoading={eventsQuery.isLoading}
                  eventsError={eventsQuery.error as Error | null}
                  usageToday={usageQuery.data?.today_cost}
                  usageTokens={
                    usageQuery.data
                      ? usageQuery.data.today_input_tokens + usageQuery.data.today_output_tokens
                      : status.metrics.today_total_tokens
                  }
                  usageError={usageQuery.error as Error | null}
                  now={now}
                  copied={copied}
                  onCopyRunId={copyRunId}
                  onRefresh={() => {
                    void statusQuery.refetch()
                    void eventsQuery.refetch()
                    void usageQuery.refetch()
                  }}
                  onEditContract={() => openCenter('contract')}
                />
              ) : null}
            </div>

            <footer className="shrink-0 border-t border-ink-700 bg-ink-900/97 px-4 py-3 sm:px-6">
              {currentRunError && (
                <div className="mb-3 flex items-start gap-2 rounded-xl border border-red-400/20 bg-red-400/8 px-3 py-2 text-xs text-red-200" role="alert">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>{(currentRunError as Error).message}</span>
                </div>
              )}
              {view === 'contract' ? (
                <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-[10px] leading-5 text-gray-600 sm:max-w-xl">
                    保存会把完整契约提交给后台监督器；运行中保存将原子更新后续生产周期的策略。
                  </p>
                  <div className="flex gap-2">
                    {status?.run_id && (
                      <button
                        type="button"
                        onClick={() => setView('overview')}
                        className="h-10 flex-1 rounded-xl border border-ink-600 px-4 text-sm text-gray-300 hover:bg-ink-800 sm:flex-none"
                      >
                        取消
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => startMutation.mutate()}
                      disabled={isBusy || !targetIsValid}
                      className="inline-flex h-10 flex-1 items-center justify-center gap-2 rounded-xl bg-emerald-300 px-5 text-sm font-semibold text-emerald-950 hover:bg-emerald-200 disabled:cursor-not-allowed disabled:opacity-50 sm:flex-none"
                    >
                      {startMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : active ? <Check size={14} /> : <Play size={14} />}
                      {active ? '保存并更新运行策略' : status?.run_id ? '按此契约重新启动' : '确认并启动自动生产'}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-wrap items-center justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => openCenter('contract')}
                    disabled={isBusy}
                    className="inline-flex h-10 items-center gap-2 rounded-xl border border-ink-600 px-3 text-sm text-gray-300 hover:bg-ink-800 disabled:opacity-50"
                  >
                    <Settings2 size={14} /> 编辑策略
                  </button>
                  {workerConcern ? (
                    <button
                      type="button"
                      onClick={() => actionMutation.mutate('resume')}
                      disabled={isBusy}
                      className="inline-flex h-10 items-center gap-2 rounded-xl border border-amber-300/30 bg-amber-300/10 px-4 text-sm font-semibold text-amber-100 hover:bg-amber-300/15 disabled:opacity-50"
                    >
                      {actionMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                      从安全点恢复 Worker
                    </button>
                  ) : active ? (
                    <button
                      type="button"
                      onClick={() => actionMutation.mutate('pause')}
                      disabled={isBusy}
                      className="inline-flex h-10 items-center gap-2 rounded-xl border border-amber-400/25 bg-amber-400/10 px-4 text-sm font-semibold text-amber-100 hover:bg-amber-400/15 disabled:opacity-50"
                    >
                      {actionMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <Pause size={14} />}
                      暂停
                    </button>
                  ) : isQualityHold ? (
                    <button
                      type="button"
                      onClick={() => {
                        setOpen(false)
                        onOpenReview?.()
                      }}
                      disabled={isBusy}
                      className="inline-flex h-10 items-center gap-2 rounded-xl border border-amber-300/30 bg-amber-300/10 px-4 text-sm font-semibold text-amber-100 hover:bg-amber-300/15 disabled:opacity-50"
                    >
                      <ClipboardCheck size={14} /> 先处理质检项
                    </button>
                  ) : isBudgetHold ? (
                    <button
                      type="button"
                      onClick={() => setView('contract')}
                      disabled={isBusy}
                      className="inline-flex h-10 items-center gap-2 rounded-xl border border-amber-300/30 bg-amber-300/10 px-4 text-sm font-semibold text-amber-100 hover:bg-amber-300/15 disabled:opacity-50"
                    >
                      <CircleDollarSign size={14} /> 调整预算后再运行
                    </button>
                  ) : isFailedHold ? (
                    <button
                      type="button"
                      onClick={() => setView('contract')}
                      disabled={isBusy}
                      className="inline-flex h-10 items-center gap-2 rounded-xl border border-red-300/25 bg-red-300/8 px-4 text-sm font-semibold text-red-100 hover:bg-red-300/12 disabled:opacity-50"
                    >
                      <ShieldAlert size={14} /> 检查失败并重建契约
                    </button>
                  ) : canResumePausedRun ? (
                    <button
                      type="button"
                      onClick={() => actionMutation.mutate('resume')}
                      disabled={isBusy}
                      className="inline-flex h-10 items-center gap-2 rounded-xl bg-emerald-300 px-4 text-sm font-semibold text-emerald-950 hover:bg-emerald-200 disabled:opacity-50"
                    >
                      {actionMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                      继续生产
                    </button>
                  ) : null}
                  {status?.run_id && !['stopped', 'completed'].includes(status.status) && (
                    stopConfirmation ? (
                      <div className="flex items-center gap-1 rounded-xl border border-red-400/25 bg-red-400/8 p-1">
                        <span className="px-2 text-[11px] text-red-200">确定永久停止本次运行？</span>
                        <button
                          type="button"
                          onClick={() => actionMutation.mutate('stop')}
                          disabled={isBusy}
                          className="inline-flex h-8 items-center gap-1 rounded-lg bg-red-400 px-2.5 text-xs font-semibold text-red-950"
                        >
                          <Square size={11} fill="currentColor" /> 确认停止
                        </button>
                        <button
                          type="button"
                          onClick={() => setStopConfirmation(false)}
                          className="h-8 rounded-lg px-2 text-xs text-gray-400 hover:text-gray-100"
                        >
                          取消
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setStopConfirmation(true)}
                        disabled={isBusy}
                        className="inline-flex h-10 items-center gap-2 rounded-xl border border-red-400/20 px-3 text-sm text-red-300 hover:bg-red-400/8 disabled:opacity-50"
                      >
                        <Square size={13} /> 停止
                      </button>
                    )
                  )}
                </div>
              )}
            </footer>
          </section>
        </div>
      )}
    </>
  )
}

function CompactMetric({
  label,
  value,
  danger = false,
  className,
}: {
  label: string
  value: string
  danger?: boolean
  className?: string
}) {
  return (
    <span className={cn('min-w-0 sm:min-w-24', className)}>
      <span className="block text-[9px] uppercase tracking-wider text-gray-700">{label}</span>
      <span className={cn('block truncate font-medium', danger ? 'text-amber-200' : 'text-gray-400')}>
        {value}
      </span>
    </span>
  )
}

function StripButton({
  label,
  icon,
  onClick,
  disabled = false,
  primary = false,
  attention = false,
  danger = false,
  className,
}: {
  label: string
  icon: React.ReactNode
  onClick: () => void
    disabled?: boolean
    primary?: boolean
    attention?: boolean
    danger?: boolean
    className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'h-8 items-center gap-1.5 rounded-lg border px-2.5 text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40 disabled:cursor-not-allowed disabled:opacity-50',
          primary
            ? 'border-emerald-300 bg-emerald-300 text-emerald-950 hover:bg-emerald-200'
            : danger
              ? 'border-red-400/30 bg-red-400/10 text-red-200 hover:bg-red-400/15'
              : attention
                ? 'border-amber-300/30 bg-amber-300/10 text-amber-100 hover:bg-amber-300/15'
                : 'border-ink-600 bg-ink-850 text-gray-300 hover:bg-ink-700 hover:text-gray-100',
        className ?? 'inline-flex',
      )}
    >
      {icon}
      {label}
    </button>
  )
}

function ViewTab({
  active,
  onClick,
  disabled = false,
  children,
}: {
  active: boolean
  onClick: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-35 sm:flex-none',
        active ? 'bg-ink-700 text-gray-100 shadow-sm' : 'text-gray-500 hover:text-gray-200',
      )}
    >
      {children}
    </button>
  )
}

function RunOverview({
  status,
  events,
  eventsLoading,
  eventsError,
  usageToday,
  usageTokens,
  usageError,
  now,
  copied,
  onCopyRunId,
  onRefresh,
  onEditContract,
}: {
  status: ContinuousStatus
  events: ContinuousRunEvent[]
  eventsLoading: boolean
  eventsError: Error | null
  usageToday?: number
  usageTokens?: number
  usageError: Error | null
  now: number
  copied: boolean
  onCopyRunId: () => void
  onRefresh: () => void
  onEditContract: () => void
}) {
  const meta = observedMeta(status.status)
  const target = status.target_chapters
  const completed = status.completed_chapters
  const progress = target ? Math.min(100, Math.round((completed / target) * 100)) : 0
  const budget = status.policy.daily_cost_limit
  const tokenBudget = status.policy.daily_token_limit
  const budgetProgress = budget && usageToday != null ? Math.min(100, (usageToday / budget) * 100) : null
  const tokenBudgetProgress =
    tokenBudget && usageTokens != null ? Math.min(100, (usageTokens / tokenBudget) * 100) : null
  const errors = (status.errors ?? []).map(normalizeRunError).slice().reverse()

  return (
    <div className="grid min-h-full lg:grid-cols-[minmax(0,1fr)_330px]">
      <div className="min-w-0 space-y-5 border-ink-700 p-4 sm:p-6 lg:border-r">
        {(status.last_error || status.heartbeat_stale || (status.desired_state === 'running' && !status.worker_alive)) && (
          <div
            className={cn(
              'flex items-start gap-3 rounded-2xl border p-4',
              status.status === 'failed'
                ? 'border-red-400/25 bg-red-400/8'
                : 'border-amber-400/25 bg-amber-400/8',
            )}
            role="alert"
          >
            <ShieldAlert size={17} className={status.status === 'failed' ? 'mt-0.5 shrink-0 text-red-300' : 'mt-0.5 shrink-0 text-amber-300'} />
            <div className="min-w-0">
              <p className={cn('text-xs font-semibold', status.status === 'failed' ? 'text-red-100' : 'text-amber-100')}>
                {status.heartbeat_stale
                  ? '监督心跳已超时'
                  : status.desired_state === 'running' && !status.worker_alive
                    ? '运行意图仍为运行，但当前工作器不在线'
                    : meta.label}
              </p>
              <p className="mt-1 break-words text-xs leading-5 text-gray-400">
                {status.last_error || meta.description}
              </p>
            </div>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            icon={<Activity size={15} />}
            label="意图 / 实际"
            value={`${status.desired_state === 'running' ? '运行' : status.desired_state === 'paused' ? '暂停' : '停止'} / ${meta.label}`}
            subtext={meta.description}
            tone={meta.tone}
          />
          <MetricCard
            icon={<BarChart3 size={15} />}
            label="本次生产"
            value={target ? `${completed} / ${target} 章` : `${completed} 章`}
            subtext={target ? `已完成 ${progress}%` : '持续运行，直到手动停止'}
            tone="green"
          />
          <MetricCard
            icon={<HeartPulse size={15} />}
            label="监督心跳"
            value={status.heartbeat_stale ? '已超时' : formatRelative(status.last_heartbeat_at, now)}
            subtext={status.worker_alive ? '当前工作器在线' : '当前工作器未在线'}
            tone={status.heartbeat_stale || !status.worker_alive ? 'amber' : 'green'}
          />
          <MetricCard
            icon={<CircleDollarSign size={15} />}
            label="今日估算成本"
            value={usageError ? '读取失败' : formatCost(usageToday)}
            subtext={
              budget != null
                ? `成本预算 ${formatCost(budget)}`
                : tokenBudget != null
                  ? `Token 预算 ${tokenBudget.toLocaleString()}`
                  : '未设置日预算上限'
            }
            tone={
              (budgetProgress != null && budgetProgress >= 80) ||
              (tokenBudgetProgress != null && tokenBudgetProgress >= 80)
                ? 'amber'
                : 'gray'
            }
          />
        </div>

        {target && (
          <section className="rounded-2xl border border-ink-700 bg-ink-950/45 p-4">
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium text-gray-300">生产契约进度</span>
              <span className="font-mono text-gray-500">{progress}%</span>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-ink-700">
              <div
                className="h-full rounded-full bg-gradient-to-r from-emerald-500 via-emerald-300 to-gold-400 transition-[width] duration-500"
                style={{ width: `${progress}%` }}
              />
            </div>
            <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-[11px] text-gray-500">
              <span>当前：{status.current_chapter ? `第 ${status.current_chapter} 章` : '等待下一周期'}</span>
              <span>剩余：{Math.max(0, target - completed)} 章</span>
              {status.status === 'retry_wait' && (
                <span className="text-amber-200">下次重试：{formatCountdown(status.next_run_at, now)}</span>
              )}
            </div>
          </section>
        )}

        <section>
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-100">
                <History size={15} className="text-emerald-300" /> 真实运行时间线
              </h3>
              <p className="mt-1 text-[11px] text-gray-600">由后端持久化事件生成，不包含本地推测。</p>
            </div>
            <button
              type="button"
              onClick={onRefresh}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-ink-600 px-2.5 text-[11px] text-gray-400 hover:bg-ink-800 hover:text-gray-100"
            >
              <RefreshCw size={12} /> 刷新
            </button>
          </div>
          <div className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-950/40">
            {eventsLoading ? (
              <div className="flex items-center justify-center gap-2 py-12 text-xs text-gray-600">
                <Loader2 size={14} className="animate-spin" /> 读取事件…
              </div>
            ) : eventsError ? (
              <div className="px-4 py-8 text-center text-xs text-red-300">{eventsError.message}</div>
            ) : events.length === 0 ? (
              <div className="px-4 py-10 text-center text-xs text-gray-600">尚无持久化运行事件</div>
            ) : (
              <div className="max-h-80 overflow-y-auto">
                {events.map((event, index) => (
                  <TimelineEvent key={event.id} event={event} last={index === events.length - 1} />
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      <aside className="space-y-4 bg-ink-950/25 p-4 sm:p-5">
        <section className="rounded-2xl border border-ink-700 bg-ink-900 p-4">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-gray-200">运行身份</h3>
            <button
              type="button"
              onClick={onCopyRunId}
              className="inline-flex h-7 items-center gap-1 rounded-lg px-2 text-[10px] text-gray-500 hover:bg-ink-700 hover:text-gray-200"
            >
              {copied ? <Check size={11} /> : <Copy size={11} />}
              {copied ? '已复制' : '复制 ID'}
            </button>
          </div>
          <p className="mt-2 break-all font-mono text-[10px] leading-5 text-gray-500">{status.run_id || '—'}</p>
          <dl className="mt-3 space-y-2 border-t border-ink-700 pt-3 text-[11px]">
            <DetailRow label="启动时间" value={formatTimestamp(status.started_at)} />
            <DetailRow label="最近心跳" value={formatTimestamp(status.last_heartbeat_at)} />
            <DetailRow
              label="连续失败"
              value={`${status.consecutive_failures} / ${status.policy.max_consecutive_failures}`}
              danger={status.consecutive_failures > 0}
            />
            <DetailRow label="累计失败" value={String(status.total_failures)} danger={status.total_failures > 0} />
          </dl>
        </section>

        <section className="rounded-2xl border border-ink-700 bg-ink-900 p-4">
          <div className="flex items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-xs font-semibold text-gray-200">
              <Gauge size={14} className="text-gold-400" /> 生效策略
            </h3>
            <button type="button" onClick={onEditContract} className="text-[10px] text-emerald-300 hover:text-emerald-200">
              修改
            </button>
          </div>
          <dl className="mt-3 space-y-2 text-[11px]">
            <DetailRow label="自治等级" value={status.autonomy_level} />
            <DetailRow label="质量门" value={`${status.policy.quality_threshold} 分`} />
            <DetailRow label="返工上限" value={`${status.policy.max_rewrite_rounds} 轮`} />
            <DetailRow label="章节间隔" value={formatDuration(status.policy.chapter_delay_seconds)} />
            <DetailRow label="错误退避" value={formatDuration(status.policy.error_backoff_seconds)} />
            <DetailRow label="熔断冷却" value={formatDuration(status.policy.circuit_cooldown_seconds)} />
            <DetailRow
              label="质量失败"
              value={
                status.policy.quality_failure_action === 'retry'
                  ? `同章自动重做 ${status.policy.max_quality_retry_cycles} 次`
                  : '立即暂停等待确认'
              }
            />
            {status.policy.quality_failure_action === 'retry' && (
              <DetailRow
                label="质量重试间隔"
                value={formatDuration(status.policy.quality_retry_backoff_seconds)}
              />
            )}
            <DetailRow label="深度演化" value={`每 ${status.policy.learning_interval_chapters} 章`} />
            <DetailRow
              label="日 Token 上限"
              value={tokenBudget == null ? '未设置' : tokenBudget.toLocaleString()}
            />
          </dl>
        </section>

        <section className="rounded-2xl border border-ink-700 bg-ink-900 p-4">
          <h3 className="flex items-center gap-2 text-xs font-semibold text-gray-200">
            <BarChart3 size={14} className="text-blue-300" /> 最近质量
          </h3>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <MiniMetric label="最近评分" value={status.metrics.last_score != null ? `${status.metrics.last_score}` : '—'} />
            <MiniMetric label="平均评分" value={status.metrics.average_score != null ? `${status.metrics.average_score}` : '—'} />
            <MiniMetric label="最近字数" value={status.metrics.last_word_count != null ? status.metrics.last_word_count.toLocaleString() : '—'} />
            <MiniMetric label="计分章节" value={status.metrics.scored_chapters != null ? `${status.metrics.scored_chapters}` : '—'} />
          </div>
        </section>

        {(budget != null || tokenBudget != null) && (
          <section className="rounded-2xl border border-ink-700 bg-ink-900 p-4">
            <h3 className="text-xs font-semibold text-gray-200">今日预算</h3>
            {budget != null && (
              <BudgetMeter
                label="成本"
                value={`${formatCost(usageToday)} / ${formatCost(budget)}`}
                progress={budgetProgress}
              />
            )}
            {tokenBudget != null && (
              <BudgetMeter
                label="Token"
                value={`${(usageTokens ?? 0).toLocaleString()} / ${tokenBudget.toLocaleString()}`}
                progress={tokenBudgetProgress}
              />
            )}
          </section>
        )}

        {errors.length > 0 && (
          <section className="rounded-2xl border border-red-400/15 bg-red-400/[0.035] p-4">
            <h3 className="flex items-center gap-2 text-xs font-semibold text-red-100">
              <AlertTriangle size={14} /> 最近错误
            </h3>
            <div className="mt-3 space-y-2">
              {errors.slice(0, 4).map((error, index) => (
                <div key={`${error.at ?? index}-${error.message}`} className="rounded-xl border border-red-400/10 bg-ink-950/35 p-2.5">
                  <p className="break-words text-[11px] leading-5 text-red-100/80">{error.message}</p>
                  <p className="mt-1 text-[9px] text-gray-700">
                    {error.chapter_no ? `第 ${error.chapter_no} 章 · ` : ''}{formatTimestamp(error.at)}
                  </p>
                </div>
              ))}
            </div>
          </section>
        )}
      </aside>
    </div>
  )
}

function ContractEditor({
  contract,
  unlimited,
  setUnlimited,
  setContract,
  setNumber,
  status,
  usageToday,
  usageTokens,
  error,
}: {
  contract: ContinuousStartContract
  unlimited: boolean
  setUnlimited: (value: boolean) => void
  setContract: React.Dispatch<React.SetStateAction<ContinuousStartContract>>
  setNumber: (
    key:
      | 'target_chapters'
      | 'quality_threshold'
      | 'max_rewrite_rounds'
      | 'chapter_delay_seconds'
      | 'error_backoff_seconds'
      | 'max_consecutive_failures'
      | 'circuit_cooldown_seconds'
      | 'max_quality_retry_cycles'
      | 'quality_retry_backoff_seconds'
      | 'learning_interval_chapters',
    value: number,
  ) => void
  status?: ContinuousStatus
  usageToday?: number
  usageTokens?: number
  error: Error | null
}) {
  return (
    <div className="mx-auto max-w-5xl space-y-5 p-4 sm:p-6">
      {status?.run_id && status.desired_state === 'running' && (
        <div className="flex items-start gap-3 rounded-2xl border border-blue-400/20 bg-blue-400/8 p-4 text-xs text-blue-100">
          <Activity size={15} className="mt-0.5 shrink-0" />
          <p className="leading-5">任务正在运行。保存后，监督器会持久化新策略，并从后续生产周期开始使用。</p>
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-2xl border border-red-400/20 bg-red-400/8 p-4 text-xs text-red-100" role="alert">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {error.message}
        </div>
      )}

      <ContractSection
        icon={<Zap size={16} />}
        title="生产目标与自治等级"
        description="定义本次监督任务要完成多少章，以及 AI 可以自主推进到什么程度。"
      >
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_1.35fr]">
          <div>
            <FieldLabel label="本次生产目标" hint="按完成章数计数" />
            <div className="mt-2 flex gap-2">
              <input
                type="number"
                min={1}
                max={5000}
                value={contract.target_chapters ?? ''}
                disabled={unlimited}
                onChange={(event) => setNumber('target_chapters', Math.max(1, Number(event.target.value) || 1))}
                className="h-10 min-w-0 flex-1 rounded-xl border border-ink-600 bg-ink-950 px-3 text-sm text-gray-100 focus:border-emerald-400/50 focus:outline-none disabled:opacity-35"
                aria-label="本次自动生产目标章数"
              />
              <span className="flex h-10 items-center rounded-xl border border-ink-700 bg-ink-850 px-3 text-xs text-gray-500">章</span>
            </div>
            <label className="mt-3 flex cursor-pointer items-center gap-2 text-xs text-gray-400">
              <input
                type="checkbox"
                checked={unlimited}
                onChange={(event) => setUnlimited(event.target.checked)}
                className="h-4 w-4 rounded border-ink-600 bg-ink-950 accent-emerald-400"
              />
              <InfinityIcon size={13} /> 持续运行，直到手动停止
            </label>
          </div>

          <div>
            <FieldLabel label="自治等级" hint="后端仅允许 L2-L4 自动生产" />
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              {([
                ['L2', '守门协作', '关键质量异常暂停'],
                ['L3', '主动生产', '自动推进并按策略熔断'],
                ['L4', '连续经营', '适合规则稳定的成熟项目'],
              ] as const).map(([level, label, description]) => (
                <button
                  type="button"
                  key={level}
                  onClick={() => setContract((current) => ({ ...current, autonomy_level: level }))}
                  className={cn(
                    'rounded-xl border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
                    contract.autonomy_level === level
                      ? 'border-emerald-400/40 bg-emerald-400/10'
                      : 'border-ink-600 bg-ink-950/55 hover:border-ink-500',
                  )}
                >
                  <span className="text-[10px] font-bold text-emerald-300">{level}</span>
                  <span className="mt-1 block text-xs font-semibold text-gray-200">{label}</span>
                  <span className="mt-1 block text-[10px] leading-4 text-gray-600">{description}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </ContractSection>

      <div className="grid gap-5 lg:grid-cols-2">
        <ContractSection
          icon={<ShieldCheck size={16} />}
          title="质量门与自动返工"
          description="未通过质量门的章节先返工，达到上限后执行指定动作。"
        >
          <div className="space-y-4">
            <div>
              <FieldLabel label="终审质量阈值" hint={`${contract.quality_threshold} / 100`} />
              <input
                type="range"
                min={50}
                max={100}
                value={contract.quality_threshold}
                onChange={(event) => setNumber('quality_threshold', Number(event.target.value))}
                className="mt-3 w-full accent-emerald-400"
              />
              <div className="mt-1 flex justify-between text-[9px] text-gray-700"><span>50</span><span>严格 100</span></div>
            </div>
            <NumberField
              label="最大返工轮次"
              value={contract.max_rewrite_rounds}
              min={0}
              max={5}
              suffix="轮"
              onChange={(value) => setNumber('max_rewrite_rounds', value)}
            />
            <div>
              <FieldLabel label="单轮返工仍未达标" hint="始终停留在同一章" />
              <div className="mt-2 grid grid-cols-2 gap-2">
                {([
                  ['retry', '自动重新规划', '重新起草同一章'],
                  ['pause', '立即人工接管', '停止 API 消耗'],
                ] as const).map(([action, label, description]) => (
                  <button
                    key={action}
                    type="button"
                    onClick={() =>
                      setContract((current) => ({
                        ...current,
                        quality_failure_action: action,
                      }))
                    }
                    className={cn(
                      'rounded-xl border p-3 text-left transition-colors',
                      contract.quality_failure_action === action
                        ? 'border-emerald-400/35 bg-emerald-400/10'
                        : 'border-ink-600 bg-ink-950/55 hover:border-ink-500',
                    )}
                  >
                    <span className="block text-[11px] font-semibold text-gray-200">{label}</span>
                    <span className="mt-1 block text-[9px] text-gray-600">{description}</span>
                  </button>
                ))}
              </div>
            </div>
            {contract.quality_failure_action === 'retry' && (
              <div className="grid gap-3 sm:grid-cols-2">
                <NumberField
                  label="完整修复周期"
                  value={contract.max_quality_retry_cycles}
                  min={0}
                  max={10}
                  suffix="次"
                  onChange={(value) => setNumber('max_quality_retry_cycles', value)}
                />
                <NumberField
                  label="修复周期间隔"
                  value={contract.quality_retry_backoff_seconds}
                  min={0}
                  max={3600}
                  suffix="秒"
                  hint={formatDuration(contract.quality_retry_backoff_seconds)}
                  onChange={(value) => setNumber('quality_retry_backoff_seconds', value)}
                />
              </div>
            )}
            <div className="rounded-xl border border-amber-400/18 bg-amber-400/[0.045] p-3">
              <p className="flex items-center gap-2 text-[11px] font-medium text-amber-100">
                <ShieldAlert size={13} /> 不越章安全线
              </p>
              <p className="mt-1 text-[10px] leading-5 text-gray-600">
                未批准章节不会写入正式记忆。自动修复只会重新规划并重写当前章；
                {contract.quality_failure_action === 'retry'
                  ? `连续 ${contract.max_quality_retry_cycles} 个完整修复周期仍失败才暂停。`
                  : '首次未达标便暂停等待人工处理。'}
              </p>
            </div>
          </div>
        </ContractSection>

        <ContractSection
          icon={<TimerReset size={16} />}
          title="节奏与学习"
          description="控制章节间的冷却时间，以及系统生成学习反思的频率。"
        >
          <div className="space-y-4">
            <NumberField
              label="章节间隔"
              value={contract.chapter_delay_seconds}
              min={0}
              max={86400}
              suffix="秒"
              hint={formatDuration(contract.chapter_delay_seconds)}
              onChange={(value) => setNumber('chapter_delay_seconds', value)}
            />
            <NumberField
              label="深度演化评估周期"
              value={contract.learning_interval_chapters}
              min={1}
              max={50}
              suffix="章"
              hint={`每 ${contract.learning_interval_chapters} 章评估 Prompt 候选`}
              onChange={(value) => setNumber('learning_interval_chapters', value)}
            />
            <div className="rounded-xl border border-blue-400/15 bg-blue-400/[0.035] p-3">
              <p className="flex items-center gap-2 text-[11px] font-medium text-blue-200"><BrainCircuit size={13} /> 自主学习边界</p>
              <p className="mt-1 text-[10px] leading-5 text-gray-600">每章都会沉淀质量反思与可追溯记忆；此周期只控制高风险 Prompt 候选的深度评估频率，候选未经 holdout 不会自动晋升。</p>
            </div>
          </div>
        </ContractSection>

        <ContractSection
          icon={<RotateCcw size={16} />}
          title="失败退避与熔断"
          description="错误会按指数退避；达到阈值后进入冷却，并自动执行半开恢复探测。"
        >
          <div className="space-y-4">
            <NumberField
              label="基础退避时间"
              value={contract.error_backoff_seconds}
              min={1}
              max={3600}
              suffix="秒"
              hint={formatDuration(contract.error_backoff_seconds)}
              onChange={(value) => setNumber('error_backoff_seconds', value)}
            />
            <NumberField
              label="连续失败熔断"
              value={contract.max_consecutive_failures}
              min={1}
              max={20}
              suffix="次"
              onChange={(value) => setNumber('max_consecutive_failures', value)}
            />
            <NumberField
              label="熔断冷却时间"
              value={contract.circuit_cooldown_seconds}
              min={1}
              max={3600}
              suffix="秒"
              hint="冷却后自动半开探测；连续未恢复会指数延长，最长 1 小时"
              onChange={(value) => setNumber('circuit_cooldown_seconds', value)}
            />
            <div className="rounded-xl border border-amber-400/15 bg-amber-400/[0.035] p-3 text-[10px] leading-5 text-gray-600">
              普通错误重试：{formatDuration(contract.error_backoff_seconds)}、{formatDuration(Math.min(3600, contract.error_backoff_seconds * 2))}、{formatDuration(Math.min(3600, contract.error_backoff_seconds * 4))}…；达到阈值后不会永久暂停，而会在熔断冷却后自动探测。
            </div>
          </div>
        </ContractSection>

        <ContractSection
          icon={<CircleDollarSign size={16} />}
          title="成本与 Token 预算"
          description="任一预算达到上限，监督器都会进入预算暂停并写入真实事件。"
        >
          <div className="space-y-4">
            <div>
              <FieldLabel label="每日成本上限" hint="USD；可留空" />
              <div className="mt-2 flex gap-2">
                <span className="flex h-10 items-center rounded-xl border border-ink-700 bg-ink-850 px-3 text-sm text-gray-500">$</span>
                <input
                  type="number"
                  min={0}
                  step="0.01"
                  value={contract.daily_cost_limit ?? ''}
                  onChange={(event) =>
                    setContract((current) => ({
                      ...current,
                      daily_cost_limit: event.target.value === '' ? null : Math.max(0, Number(event.target.value)),
                    }))
                  }
                  placeholder="不设上限"
                  className="h-10 min-w-0 flex-1 rounded-xl border border-ink-600 bg-ink-950 px-3 text-sm text-gray-100 placeholder:text-gray-700 focus:border-emerald-400/50 focus:outline-none"
                />
              </div>
            </div>
            <div>
              <FieldLabel label="每日 Token 上限" hint="至少 1,000；可留空" />
              <div className="mt-2 flex gap-2">
                <input
                  type="number"
                  min={1000}
                  step={1000}
                  value={contract.daily_token_limit ?? ''}
                  onChange={(event) =>
                    setContract((current) => ({
                      ...current,
                      daily_token_limit:
                        event.target.value === '' ? null : Math.max(1000, Number(event.target.value)),
                    }))
                  }
                  placeholder="不设上限"
                  className="h-10 min-w-0 flex-1 rounded-xl border border-ink-600 bg-ink-950 px-3 text-sm text-gray-100 placeholder:text-gray-700 focus:border-emerald-400/50 focus:outline-none"
                />
                <span className="flex h-10 items-center rounded-xl border border-ink-700 bg-ink-850 px-3 text-xs text-gray-500">Token</span>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <MiniMetric label="今日成本" value={formatCost(usageToday)} />
              <MiniMetric label="成本上限" value={formatCost(contract.daily_cost_limit)} />
              <MiniMetric label="今日 Token" value={usageTokens == null ? '—' : usageTokens.toLocaleString()} />
              <MiniMetric label="Token 上限" value={contract.daily_token_limit == null ? '—' : contract.daily_token_limit.toLocaleString()} />
            </div>
            <p className="text-[10px] leading-5 text-gray-600">用量来自真实 AgentRun 聚合；没有统计时显示“—”，不会用本地估算冒充实际消耗。</p>
          </div>
        </ContractSection>
      </div>

      <section className="rounded-2xl border border-emerald-400/18 bg-emerald-400/[0.035] p-4 sm:p-5">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-emerald-100"><ClipboardCheck size={15} /> 启动前契约摘要</h3>
        <div className="mt-4 grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <SummaryItem label="生产目标" value={unlimited ? '持续到手动停止' : `${contract.target_chapters ?? '—'} 章`} />
          <SummaryItem label="自治与质量" value={`${contract.autonomy_level} · ≥ ${contract.quality_threshold} 分`} />
          <SummaryItem
            label="返工与熔断"
            value={
              contract.quality_failure_action === 'retry'
                ? `${contract.max_rewrite_rounds} 轮/周期 · ${contract.max_quality_retry_cycles} 个自动周期`
                : `${contract.max_rewrite_rounds} 轮 · 未达标即暂停`
            }
          />
          <SummaryItem
            label="预算"
            value={
              contract.daily_cost_limit == null && contract.daily_token_limit == null
                ? '未设日预算'
                : [
                    contract.daily_cost_limit == null ? null : `${formatCost(contract.daily_cost_limit)} / 日`,
                    contract.daily_token_limit == null ? null : `${contract.daily_token_limit.toLocaleString()} Token`,
                  ].filter(Boolean).join(' · ')
            }
          />
        </div>
      </section>
    </div>
  )
}

function ContractSection({
  icon,
  title,
  description,
  children,
}: {
  icon: React.ReactNode
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-2xl border border-ink-700 bg-ink-900/75 p-4 sm:p-5">
      <div className="flex items-start gap-3 border-b border-ink-700/75 pb-4">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-emerald-400/18 bg-emerald-400/8 text-emerald-300">{icon}</span>
        <div>
          <h3 className="text-sm font-semibold text-gray-100">{title}</h3>
          <p className="mt-1 text-[10px] leading-5 text-gray-600">{description}</p>
        </div>
      </div>
      <div className="mt-4">{children}</div>
    </section>
  )
}

function MetricCard({
  icon,
  label,
  value,
  subtext,
  tone,
}: {
  icon: React.ReactNode
  label: string
  value: string
  subtext: string
  tone: 'green' | 'blue' | 'amber' | 'red' | 'gray'
}) {
  return (
    <article className="min-w-0 rounded-2xl border border-ink-700 bg-ink-900/80 p-4">
      <div className={cn('flex h-8 w-8 items-center justify-center rounded-xl border', toneClasses(tone))}>{icon}</div>
      <p className="mt-4 text-[10px] uppercase tracking-[0.12em] text-gray-700">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-gray-100" title={value}>{value}</p>
      <p className="mt-1 truncate text-[10px] text-gray-600" title={subtext}>{subtext}</p>
    </article>
  )
}

function TimelineEvent({ event, last }: { event: ContinuousRunEvent; last: boolean }) {
  const tone = event.severity === 'error' ? 'red' : event.severity === 'warning' ? 'amber' : 'green'
  return (
    <article className="relative flex gap-3 px-4 py-3.5 sm:px-5">
      {!last && <span className="absolute bottom-0 left-[27px] top-8 w-px bg-ink-700 sm:left-[31px]" />}
      <span className={cn('relative z-10 mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border bg-ink-950', toneClasses(tone))}>
        {event.severity === 'error' ? <AlertTriangle size={10} /> : event.severity === 'warning' ? <Clock3 size={10} /> : <CheckCircle2 size={10} />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-[10px] font-semibold text-gray-300">{EVENT_LABELS[event.event_type] || event.event_type}</span>
          {event.chapter_no && <span className="rounded-full bg-ink-800 px-1.5 py-0.5 text-[9px] text-gray-500">第 {event.chapter_no} 章</span>}
          <time className="ml-auto text-[9px] text-gray-700" dateTime={event.created_at ?? undefined}>{formatTimestamp(event.created_at)}</time>
        </div>
        <p className="mt-1 break-words text-xs leading-5 text-gray-500">{event.message}</p>
      </div>
    </article>
  )
}

function FieldLabel({ label, hint }: { label: string; hint?: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <label className="text-xs font-medium text-gray-300">{label}</label>
      {hint && <span className="text-[10px] text-gray-700">{hint}</span>}
    </div>
  )
}

function NumberField({
  label,
  value,
  min,
  max,
  suffix,
  hint,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  suffix: string
  hint?: string
  onChange: (value: number) => void
}) {
  return (
    <div>
      <FieldLabel label={label} hint={hint} />
      <div className="mt-2 flex gap-2">
        <input
          type="number"
          min={min}
          max={max}
          value={value}
          onChange={(event) => onChange(Math.min(max, Math.max(min, Number(event.target.value) || min)))}
          className="h-10 min-w-0 flex-1 rounded-xl border border-ink-600 bg-ink-950 px-3 text-sm text-gray-100 focus:border-emerald-400/50 focus:outline-none"
        />
        <span className="flex h-10 min-w-12 items-center justify-center rounded-xl border border-ink-700 bg-ink-850 px-3 text-xs text-gray-500">{suffix}</span>
      </div>
    </div>
  )
}

function SegmentButton({
  active,
  onClick,
  title,
  description,
}: {
  active: boolean
  onClick: () => void
  title: string
  description: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-xl border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
        active ? 'border-emerald-400/35 bg-emerald-400/8' : 'border-ink-600 bg-ink-950/55 hover:border-ink-500',
      )}
    >
      <span className={cn('block text-xs font-semibold', active ? 'text-emerald-200' : 'text-gray-300')}>{title}</span>
      <span className="mt-1 block text-[10px] leading-4 text-gray-600">{description}</span>
    </button>
  )
}

function DetailRow({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <dt className="text-gray-600">{label}</dt>
      <dd className={cn('max-w-[65%] text-right font-medium', danger ? 'text-amber-200' : 'text-gray-400')}>{value}</dd>
    </div>
  )
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-ink-700 bg-ink-950/55 p-2.5">
      <p className="text-[9px] text-gray-700">{label}</p>
      <p className="mt-1 truncate text-xs font-semibold text-gray-300" title={value}>{value}</p>
    </div>
  )
}

function BudgetMeter({
  label,
  value,
  progress,
}: {
  label: string
  value: string
  progress: number | null
}) {
  const warning = progress != null && progress >= 80
  return (
    <div className="mt-3">
      <div className="flex items-center justify-between gap-3 text-[10px]">
        <span className="text-gray-600">{label}</span>
        <span className={warning ? 'text-amber-200' : 'text-gray-400'}>{value}</span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-ink-700">
        <div
          className={cn('h-full rounded-full transition-[width]', warning ? 'bg-amber-400' : 'bg-emerald-400')}
          style={{ width: `${progress ?? 0}%` }}
        />
      </div>
    </div>
  )
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-emerald-400/10 bg-ink-950/35 px-3 py-2.5">
      <p className="text-[9px] uppercase tracking-wider text-gray-700">{label}</p>
      <p className="mt-1 font-medium text-gray-300">{value}</p>
    </div>
  )
}
