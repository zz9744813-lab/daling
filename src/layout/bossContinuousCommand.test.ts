import { describe, expect, it, vi } from 'vitest'
import type { ContinuousPolicy, ContinuousStatus, Project } from '../types'
import {
  buildQuickStartContract,
  executeContinuousCommand,
  resolveContinuousCommandIntent,
  type ContinuousCommandApi,
} from './bossContinuousCommand'

const POLICY: ContinuousPolicy = {
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

function status(overrides: Partial<ContinuousStatus> = {}): ContinuousStatus {
  return {
    run_id: null,
    project_id: 'project-1',
    running: false,
    desired_state: 'stopped',
    status: 'stopped',
    worker_alive: false,
    heartbeat_stale: false,
    current_chapter: null,
    completed_chapters: 0,
    target_chapters: null,
    autonomy_level: 'L3',
    consecutive_failures: 0,
    total_failures: 0,
    last_error: null,
    errors: [],
    policy: POLICY,
    metrics: {},
    started_at: null,
    stopped_at: null,
    last_heartbeat_at: null,
    next_run_at: null,
    ...overrides,
  }
}

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: 'project-1',
    title: '长篇测试',
    target_chapters: 60,
    current_chapter_no: 12,
    autonomy_level: 'L4',
    ...overrides,
  }
}

function createApi({
  current = status(),
  started = status({
    run_id: 'run-started-12345678',
    running: true,
    desired_state: 'running',
    status: 'starting',
  }),
  paused = status({
    run_id: 'run-paused-12345678',
    desired_state: 'paused',
    status: 'paused',
  }),
  resumed = status({
    run_id: 'run-resumed-12345678',
    running: true,
    desired_state: 'running',
    status: 'recovering',
  }),
  stopped = status({
    run_id: 'run-stopped-12345678',
    desired_state: 'stopped',
    status: 'stopped',
  }),
}: {
  current?: ContinuousStatus
  started?: ContinuousStatus
  paused?: ContinuousStatus
  resumed?: ContinuousStatus
  stopped?: ContinuousStatus
} = {}) {
  const start = vi.fn(async () => started)
  const pause = vi.fn(async () => paused)
  const resume = vi.fn(async () => resumed)
  const stop = vi.fn(async () => stopped)
  const getStatus = vi.fn(async () => current)
  const api: ContinuousCommandApi = {
    start,
    pause,
    resume,
    stop,
    status: getStatus,
  }
  return { api, start, pause, resume, stop, getStatus }
}

describe('24H command routing', () => {
  it.each([
    ['启动 24H 写作', 'start'],
    ['请暂停 24 小时自动写作', 'pause'],
    ['恢复连续生产', 'resume'],
    ['终止自动写作', 'stop'],
    ['查看当前状态', 'status'],
    ['看看 24H 运行情况', 'status'],
  ] as const)('routes %s to the real continuous %s action', (command, intent) => {
    expect(resolveContinuousCommandIntent(command)).toBe(intent)
  })

  it('does not hijack an ordinary WorkSession command', () => {
    expect(resolveContinuousCommandIntent('启动当前普通会话')).toBeNull()
    expect(resolveContinuousCommandIntent('修改第三章对白')).toBeNull()
  })
})

describe('one-click 24H contract', () => {
  it('submits an explicit quality-gated contract for the remaining project chapters', () => {
    const contract = buildQuickStartContract(project())

    expect(contract).toMatchObject({
      target_chapters: 48,
      autonomy_level: 'L4',
      quality_threshold: 85,
      max_rewrite_rounds: 2,
      minimum_rewrite_cycles: 1,
      quality_failure_action: 'retry',
      learning_interval_chapters: 1,
    })
  })

  it('uses safe bounded defaults for malformed project metadata', () => {
    const contract = buildQuickStartContract(project({
      target_chapters: Number.NaN,
      current_chapter_no: 37,
      autonomy_level: 'L1',
    }))

    expect(contract.target_chapters).toBe(10)
    expect(contract.autonomy_level).toBe('L3')
  })
})

describe('truthful ContinuousProduction execution', () => {
  it('starts through the contract endpoint and reports success only after run confirmation', async () => {
    const { api, getStatus, start, resume } = createApi()

    const result = await executeContinuousCommand(api, project(), 'start')

    expect(getStatus).toHaveBeenCalledWith('project-1')
    expect(start).toHaveBeenCalledWith(
      'project-1',
      expect.objectContaining({
        target_chapters: 48,
        minimum_rewrite_cycles: 1,
      }),
    )
    expect(resume).not.toHaveBeenCalled()
    expect(result.ok).toBe(true)
    expect(result.message).toContain('持久化任务已确认启动')
    expect(result.data).toMatchObject({
      transport: 'continuous_production',
      fallback_used: false,
      run_id: 'run-started-12345678',
      desired_state: 'running',
    })
  })

  it('resumes the existing durable run instead of creating or touching a WorkSession', async () => {
    const { api, start, resume } = createApi({
      current: status({
        run_id: 'existing-paused-run',
        desired_state: 'paused',
        status: 'paused',
      }),
    })

    const result = await executeContinuousCommand(api, project(), 'start')

    expect(resume).toHaveBeenCalledWith('project-1')
    expect(start).not.toHaveBeenCalled()
    expect(result.intent).toBe('resume')
    expect(result.message).toContain('已确认恢复')
  })

  it('does not issue a duplicate start when the durable run is already confirmed active', async () => {
    const { api, start, resume } = createApi({
      current: status({
        run_id: 'already-running-run',
        running: true,
        desired_state: 'running',
        status: 'running',
        worker_alive: true,
      }),
    })

    const result = await executeContinuousCommand(api, project(), 'start')

    expect(start).not.toHaveBeenCalled()
    expect(resume).not.toHaveBeenCalled()
    expect(result.message).toContain('已经在运行')
  })

  it('rejects an HTTP-success response that did not create a durable run', async () => {
    const { api } = createApi({ started: status() })

    await expect(executeContinuousCommand(api, project(), 'start')).rejects.toThrow(
      /没有确认持久化运行.*未回退到普通 WorkSession/,
    )
  })

  it('surfaces continuous API unavailability and never attempts another execution path', async () => {
    const { api, getStatus, start, resume } = createApi()
    getStatus.mockRejectedValueOnce(new Error('continuous service unavailable'))

    await expect(executeContinuousCommand(api, project(), 'start')).rejects.toThrow(
      /continuous service unavailable.*未回退到普通 WorkSession/,
    )
    expect(start).not.toHaveBeenCalled()
    expect(resume).not.toHaveBeenCalled()
  })

  it('does not misreport pause when no durable run exists', async () => {
    const { api, pause } = createApi({ paused: status() })

    await expect(executeContinuousCommand(api, project(), 'pause')).rejects.toThrow(
      /没有确认暂停.*未回退到普通 WorkSession/,
    )
    expect(pause).toHaveBeenCalledOnce()
  })

  it('reports an empty continuous status truthfully without treating it as a started task', async () => {
    const { api, start } = createApi()

    const result = await executeContinuousCommand(api, project(), 'status')

    expect(result.ok).toBe(true)
    expect(result.message).toContain('没有已建立的 24H 持久化运行任务')
    expect(result.data).toMatchObject({ run_id: null, fallback_used: false })
    expect(start).not.toHaveBeenCalled()
  })
})
