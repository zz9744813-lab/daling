import { describe, expect, it, vi } from 'vitest'
import type { OutlineCompilationJob, OutlineCompilationStatus } from '../types'
import { waitForOutlineCompilationJob } from './outlineCompilation'

function job(status: OutlineCompilationStatus, error?: string): OutlineCompilationJob {
  return {
    job_id: 'job-1',
    project_id: 'project-1',
    status,
    phase: status,
    progress_percent: status === 'succeeded' ? 100 : 25,
    terminal: ['succeeded', 'failed', 'cancelled'].includes(status),
    request: {},
    request_fingerprint: 'fingerprint',
    source_revision: 1,
    structure_revision: 0,
    replace_existing: false,
    attempt_count: 1,
    result: {},
    error,
  }
}

describe('durable outline job polling', () => {
  it('keeps polling without a wall-clock/attempt cap until persisted success', async () => {
    const states = [
      ...Array.from({ length: 12 }, () => job('running')),
      job('succeeded'),
    ]
    const read = vi.fn(async () => states.shift() ?? job('succeeded'))
    const delay = vi.fn(async () => undefined)

    const result = await waitForOutlineCompilationJob(read, { delay })

    expect(result.status).toBe('succeeded')
    expect(read).toHaveBeenCalledTimes(13)
    expect(delay).toHaveBeenCalledTimes(12)
  })

  it('surfaces the durable backend failure instead of a generic request timeout', async () => {
    const read = vi.fn(async () => job('failed', 'source witness validation failed'))

    await expect(waitForOutlineCompilationJob(read)).rejects.toThrow(
      'source witness validation failed',
    )
  })

  it('publishes every observed progress checkpoint', async () => {
    const states = [job('queued'), job('running'), job('succeeded')]
    const onProgress = vi.fn()
    const result = await waitForOutlineCompilationJob(
      async () => states.shift() ?? job('succeeded'),
      { delay: async () => undefined, onProgress },
    )

    expect(result.status).toBe('succeeded')
    expect(onProgress.mock.calls.map(([value]) => value.status)).toEqual([
      'queued',
      'running',
      'succeeded',
    ])
  })
})
