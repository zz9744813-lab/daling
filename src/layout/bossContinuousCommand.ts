import type {
  CommandResult,
  ContinuousStartContract,
  ContinuousStatus,
  Project,
} from '../types'

export type ContinuousCommandIntent = 'start' | 'pause' | 'resume' | 'stop' | 'status'

export interface ContinuousCommandApi {
  start: (projectId: string, contract: ContinuousStartContract) => Promise<ContinuousStatus>
  pause: (projectId: string, reason?: string) => Promise<ContinuousStatus>
  resume: (projectId: string) => Promise<ContinuousStatus>
  stop: (projectId: string) => Promise<ContinuousStatus>
  status: (projectId: string) => Promise<ContinuousStatus>
}

export const QUICK_CONTINUOUS_COMMANDS: ReadonlyArray<{
  label: string
  intent: ContinuousCommandIntent
}> = [
  { label: '启动 24H 写作', intent: 'start' },
  { label: '暂停 24H 写作', intent: 'pause' },
  { label: '继续 24H 写作', intent: 'resume' },
  { label: '查看当前状态', intent: 'status' },
]

const ACTIVE_OBSERVED_STATES = new Set([
  'starting',
  'recovering',
  'running',
  'retry_wait',
  'circuit_open',
  'half_open',
])

const OBSERVED_STATE_LABELS: Record<string, string> = {
  starting: '启动中',
  recovering: '恢复中',
  running: '生产中',
  retry_wait: '等待自动重试',
  circuit_open: '熔断冷却',
  half_open: '恢复探测',
  paused: '已暂停',
  quality_hold: '质量接管',
  quality_contract_hold: '纠错契约暂停',
  budget_hold: '预算暂停',
  failed: '监督器异常',
  completed: '目标完成',
  stopped: '已停止',
}

function normalizeCommand(command: string) {
  return command.trim().toLowerCase().replace(/\s+/g, '')
}

/**
 * Only commands that explicitly address 24H/continuous production (plus the
 * exact status shortcut shown in that toolbar) are intercepted. Ordinary Boss
 * commands remain available through the Cockpit command processor.
 */
export function resolveContinuousCommandIntent(
  command: string,
): ContinuousCommandIntent | null {
  const normalized = normalizeCommand(command)
  const exactShortcut = QUICK_CONTINUOUS_COMMANDS.find(
    ({ label }) => normalizeCommand(label) === normalized,
  )
  if (exactShortcut) return exactShortcut.intent

  const addressesContinuousProduction =
    normalized.includes('24h') ||
    normalized.includes('24小时') ||
    normalized.includes('连续写作') ||
    normalized.includes('连续生产') ||
    normalized.includes('自动写作') ||
    normalized.includes('自动生产')
  if (!addressesContinuousProduction) return null

  if (/(停止|终止|关闭|取消)/.test(normalized)) return 'stop'
  if (/(暂停|停下|接管)/.test(normalized)) return 'pause'
  if (/(继续|恢复|重启)/.test(normalized)) return 'resume'
  if (/(状态|查看|进度|运行情况)/.test(normalized)) return 'status'
  if (/(启动|开始|开启|运行)/.test(normalized)) return 'start'
  return null
}

function finiteInteger(value: unknown, fallback: number, min: number, max: number) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return fallback
  return Math.min(max, Math.max(min, Math.round(numeric)))
}

/** Build an explicit, quality-gated contract for the one-click 24H entry. */
export function buildQuickStartContract(project: Project): ContinuousStartContract {
  const rawConfiguredTarget = project.target_chapters ?? project.config?.target_chapters
  const hasConfiguredTarget =
    Number.isFinite(Number(rawConfiguredTarget)) && Number(rawConfiguredTarget) > 0
  const configuredTarget = finiteInteger(rawConfiguredTarget, 10, 1, 5000)
  const currentChapter = finiteInteger(
    project.current_chapter_no ?? project.current_chapter,
    0,
    0,
    5000,
  )
  const configuredLevel = project.autonomy_level ?? project.config?.autonomy_level
  const autonomyLevel = ['L2', 'L3', 'L4'].includes(configuredLevel ?? '')
    ? (configuredLevel as 'L2' | 'L3' | 'L4')
    : 'L3'

  return {
    // A configured project target is an absolute manuscript target, so submit
    // only the remaining chapters. Without one, the safe quick-start default is
    // ten *new* chapters rather than "chapter 10 minus the current chapter".
    target_chapters: hasConfiguredTarget
      ? Math.max(1, configuredTarget - currentChapter)
      : 10,
    autonomy_level: autonomyLevel,
    quality_threshold: 85,
    max_rewrite_rounds: 2,
    minimum_rewrite_cycles: 1,
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
}

function stateLabel(status: ContinuousStatus) {
  return OBSERVED_STATE_LABELS[status.status] ?? status.status ?? '未知状态'
}

function runLabel(status: ContinuousStatus) {
  return status.run_id ? status.run_id.slice(0, 8) : '无运行 ID'
}

function resultData(status: ContinuousStatus): Record<string, unknown> {
  return {
    transport: 'continuous_production',
    fallback_used: false,
    run_id: status.run_id,
    desired_state: status.desired_state,
    observed_state: status.status,
    worker_alive: status.worker_alive,
    continuous_status: status,
  }
}

function requireActiveRun(status: ContinuousStatus, action: string) {
  if (
    !status.run_id ||
    status.desired_state !== 'running' ||
    status.running !== true ||
    !ACTIVE_OBSERVED_STATES.has(status.status)
  ) {
    throw new Error(
      `后端没有确认持久化运行（run_id=${status.run_id ?? 'null'}，desired_state=${status.desired_state}，status=${status.status}）`,
    )
  }
  return {
    ok: true,
    intent: action,
    message: `24H 持久化任务已确认${action === 'resume' ? '恢复' : '启动'}：运行 ${runLabel(status)}，当前${stateLabel(status)}${status.worker_alive ? '，监督器在线' : '，监督器正在取得执行权'}`,
    data: resultData(status),
  } satisfies CommandResult
}

function requirePausedRun(status: ContinuousStatus) {
  if (!status.run_id || status.desired_state !== 'paused' || status.status !== 'paused') {
    throw new Error(
      `后端没有确认暂停（run_id=${status.run_id ?? 'null'}，desired_state=${status.desired_state}，status=${status.status}）`,
    )
  }
  return {
    ok: true,
    intent: 'pause',
    message: `24H 持久化任务已确认暂停：运行 ${runLabel(status)}，后台不会继续领取新章节`,
    data: resultData(status),
  } satisfies CommandResult
}

function requireStoppedRun(status: ContinuousStatus) {
  if (!status.run_id || status.desired_state !== 'stopped' || status.status !== 'stopped') {
    throw new Error(
      `后端没有确认停止（run_id=${status.run_id ?? 'null'}，desired_state=${status.desired_state}，status=${status.status}）`,
    )
  }
  return {
    ok: true,
    intent: 'stop',
    message: `24H 持久化任务已确认停止：运行 ${runLabel(status)} 不会自行恢复`,
    data: resultData(status),
  } satisfies CommandResult
}

function statusResult(status: ContinuousStatus): CommandResult {
  if (!status.run_id) {
    return {
      ok: true,
      intent: 'status',
      message: '当前没有已建立的 24H 持久化运行任务',
      data: resultData(status),
    }
  }
  return {
    ok: true,
    intent: 'status',
    message: `24H 运行 ${runLabel(status)}：期望${status.desired_state}，实际${stateLabel(status)}${status.last_error ? `；最近错误：${status.last_error}` : ''}`,
    data: resultData(status),
  }
}

function errorMessage(error: unknown) {
  return error instanceof Error && error.message.trim() ? error.message : '连续生产接口未返回可确认结果'
}

/**
 * Execute only against the ContinuousProduction contract API. There is
 * deliberately no WorkSession/cockpit-command dependency in this function.
 */
export async function executeContinuousCommand(
  api: ContinuousCommandApi,
  project: Project,
  intent: ContinuousCommandIntent,
): Promise<CommandResult> {
  try {
    if (intent === 'status') return statusResult(await api.status(project.id))
    if (intent === 'pause') {
      return requirePausedRun(await api.pause(project.id, '用户从 24H 快捷控制暂停'))
    }
    if (intent === 'resume') return requireActiveRun(await api.resume(project.id), 'resume')
    if (intent === 'stop') return requireStoppedRun(await api.stop(project.id))

    const current = await api.status(project.id)
    if (
      current.run_id &&
      current.desired_state === 'running' &&
      current.running === true &&
      ACTIVE_OBSERVED_STATES.has(current.status)
    ) {
      return {
        ...requireActiveRun(current, 'start'),
        message: `24H 持久化任务已经在运行：运行 ${runLabel(current)}，当前${stateLabel(current)}`,
      }
    }
    if (current.run_id && current.desired_state === 'paused') {
      return requireActiveRun(await api.resume(project.id), 'resume')
    }
    return requireActiveRun(
      await api.start(project.id, buildQuickStartContract(project)),
      'start',
    )
  } catch (error) {
    const action = {
      start: '启动',
      pause: '暂停',
      resume: '恢复',
      stop: '停止',
      status: '查询',
    }[intent]
    throw new Error(
      `24H ${action}失败：${errorMessage(error)}。本次操作未回退到普通 WorkSession。`,
    )
  }
}
