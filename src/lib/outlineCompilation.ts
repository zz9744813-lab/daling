import type { OutlineCompilationJob } from '../types'

type ReadOutlineJob = (signal?: AbortSignal) => Promise<OutlineCompilationJob>
type Delay = (milliseconds: number, signal?: AbortSignal) => Promise<void>

export function abortableDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const abort = () => {
      window.clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const finish = () => {
      signal?.removeEventListener('abort', abort)
      resolve()
    }
    const timer = window.setTimeout(finish, Math.max(0, milliseconds))
    if (signal?.aborted) return abort()
    signal?.addEventListener('abort', abort, { once: true })
  })
}

/** Poll a durable job until its persisted terminal state; intentionally no wall-clock timeout. */
export async function waitForOutlineCompilationJob(
  read: ReadOutlineJob,
  options?: {
    signal?: AbortSignal
    pollIntervalMs?: number
    delay?: Delay
    onProgress?: (job: OutlineCompilationJob) => void
  },
): Promise<OutlineCompilationJob> {
  const interval = Math.max(250, options?.pollIntervalMs ?? 2_000)
  const delay = options?.delay ?? abortableDelay
  while (true) {
    const job = await read(options?.signal)
    options?.onProgress?.(job)
    if (job.status === 'succeeded') return job
    if (job.status === 'failed' || job.status === 'cancelled') {
      throw new Error(job.error || `大纲编译任务已${job.status === 'failed' ? '失败' : '取消'}`)
    }
    await delay(interval, options?.signal)
  }
}

