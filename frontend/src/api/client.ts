import axios, { AxiosInstance } from 'axios'
import type {
  Project,
  Chapter,
  ChapterVersion,
  StorylineOverview,
  BrainOverview,
  CanonFact,
  Provider,
  ModelBinding,
  ReviewQueueItem,
  UsageOverview,
  AgentRun,
  CockpitData,
  CommandResult,
  BibleHints,
  OutlineParams,
  PipelineRunParams,
  ProviderTestParams,
  ProviderTestResult,
  ProviderCreateData,
  ProviderUpdateData,
  ModelBindingCreateData,
  ModelBindingUpdateData,
  ChapterQualityDetail,
  PromptExperimentResult,
  SkillTestResult,
  LearningReport,
  EvolutionOverview,
  PromptVersionView,
  ReflectionData,
  BookMemory,
  BookMemoryData,
  CanonFactAssertData,
  CanonFactSupersedeData,
  CanonCheckResult,
  ReviewReviseData,
  FactMutability,
  CreateProjectPayload,
  ProjectChatMessage,
  ChatCreateStatus,
  ChatBlueprintUpdate,
  ContinuousRunEvent,
  ContinuousStartContract,
  ContinuousStatus,
  OutlineInspection,
} from '../types'

/**
 * Axios 实例 —— baseURL 为空，所有 /api 请求走 vite proxy → http://localhost:8000
 */
const client: AxiosInstance = axios.create({
  baseURL: '',
  headers: { 'Content-Type': 'application/json' },
  timeout: 60000,
})

function readableErrorMessage(value: unknown, fallback = '请求失败'): string {
  if (typeof value === 'string' && value.trim()) return value
  if (Array.isArray(value)) {
    const messages = value
      .map((item) => readableErrorMessage(item, ''))
      .filter(Boolean)
    return messages.length ? messages.join('；') : fallback
  }
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    for (const key of ['message', 'detail', 'error']) {
      const message = readableErrorMessage(record[key], '')
      if (message) return message
    }
  }
  return fallback
}

// 响应拦截：统一抛出后端错误信息
client.interceptors.response.use(
  (res) => res,
  (error) => {
    const msg = readableErrorMessage(
      error?.response?.data?.detail ?? error?.response?.data ?? error?.message,
    )
    return Promise.reject(new Error(msg))
  },
)

/* ============================================================
 * Projects
 * ============================================================ */

export interface ChatCreateStreamHandlers {
  onDelta?: (delta: string) => void
  onBlueprint?: (update: ChatBlueprintUpdate) => void
  onDone?: () => void
}

interface ChatCreateStreamPayload {
  messages: Pick<ProjectChatMessage, 'role' | 'content'>[]
  blueprint?: Record<string, unknown>
}

/** 使用 fetch 读取 POST SSE；EventSource 本身不支持 POST。 */
export async function streamProjectChat(
  payload: ChatCreateStreamPayload,
  handlers: ChatCreateStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch('/api/projects/chat-create/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(payload),
    signal,
  })

  if (!response.ok) {
    let detail = `对话请求失败（${response.status}）`
    try {
      const body = await response.json()
      detail = readableErrorMessage(body?.detail ?? body?.message, detail)
    } catch {
      // 非 JSON 错误响应保留状态码信息。
    }
    throw new Error(detail)
  }
  if (!response.body) throw new Error('模型未返回可读取的流')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finished = false

  const dispatchFrame = (frame: string) => {
    if (!frame.trim()) return
    let eventName = 'message'
    const dataLines: string[] = []

    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith(':')) continue
      if (line.startsWith('event:')) eventName = line.slice(6).trim()
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }

    const raw = dataLines.join('\n')
    if (!raw) return
    if (raw === '[DONE]') {
      if (!finished) handlers.onDone?.()
      finished = true
      return
    }

    let parsed: any = raw
    try {
      parsed = JSON.parse(raw)
    } catch {
      // delta 事件允许直接发送文本。
    }
    const effectiveEvent = eventName === 'message' && parsed?.type ? parsed.type : eventName

    if (effectiveEvent === 'delta') {
      handlers.onDelta?.(
        typeof parsed === 'string' ? parsed : String(parsed?.delta ?? ''),
      )
      return
    }
    if (effectiveEvent === 'blueprint') {
      handlers.onBlueprint?.(parsed as ChatBlueprintUpdate)
      return
    }
    if (effectiveEvent === 'done') {
      if (!finished) handlers.onDone?.()
      finished = true
      return
    }
    if (effectiveEvent === 'error') {
      const message =
        typeof parsed === 'string'
          ? parsed
          : parsed?.message || parsed?.detail || parsed?.error || '模型生成失败'
      throw new Error(message)
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() ?? ''
    for (const frame of frames) dispatchFrame(frame)
    if (done) break
  }
  if (buffer.trim()) dispatchFrame(buffer)
  if (!finished) handlers.onDone?.()
}

export const projectsApi = {
  list: () => client.get<Project[]>('/api/projects').then((r) => r.data),
  get: (id: string) => client.get<Project>(`/api/projects/${id}`).then((r) => r.data),
  getById: (id: string) => client.get<Project>(`/api/projects/${id}`).then((r) => r.data),
  create: (data: CreateProjectPayload) =>
    client.post<Project>('/api/projects', data).then((r) => r.data),
  update: (id: string, data: Partial<Project>) =>
    client.put<Project>(`/api/projects/${id}`, data).then((r) => r.data),
  remove: (id: string) => client.delete(`/api/projects/${id}`).then((r) => r.data),
  // 删除项目（级联删除所有关联数据）
  delete: (projectId: string) =>
    client.delete(`/api/projects/${projectId}`).then((r) => r.data),
  // 获取上传的大纲信息
  getOutline: (projectId: string) =>
    client.get(`/api/projects/${projectId}/outline`).then((r) => r.data),
  // 获取项目的自定义系统提示词（类似 Gemini Gems）
  getCustomPrompt: (projectId: string) =>
    client.get(`/api/projects/${projectId}/custom-prompt`).then((r) => r.data),
  // 更新项目的自定义系统提示词
  updateCustomPrompt: (projectId: string, text: string) =>
    client.put(`/api/projects/${projectId}/custom-prompt`, { text }).then((r) => r.data),
  uploadOutline: async (projectId: string, file: File) => {
    const body = new FormData()
    body.append('file', file)
    const response = await fetch(`/api/projects/${projectId}/upload-outline`, {
      method: 'POST',
      body,
    })
    if (!response.ok) {
      let detail = `大纲上传失败（${response.status}）`
      try {
        const payload = await response.json()
        detail = readableErrorMessage(payload?.detail ?? payload?.message, detail)
      } catch {
        // 保留状态码错误。
      }
      throw new Error(detail)
    }
    return response.json()
  },
  inspectOutline: async (file: File) => {
    const body = new FormData()
    body.append('file', file)
    const response = await fetch('/api/projects/outline/inspect', { method: 'POST', body })
    if (!response.ok) {
      let detail = `大纲解析失败（${response.status}）`
      try {
        const payload = await response.json()
        detail = readableErrorMessage(payload?.detail ?? payload?.message, detail)
      } catch {
        // 保留状态码错误。
      }
      throw new Error(detail)
    }
    return response.json() as Promise<OutlineInspection>
  },
  chatCreateStatus: () =>
    client
      .get<ChatCreateStatus>('/api/projects/chat-create/status')
      .then((r) => r.data),
  chatCreateStream: (
    payload: ChatCreateStreamPayload,
    handlers: ChatCreateStreamHandlers,
    signal?: AbortSignal,
  ) => streamProjectChat(payload, handlers, signal),
  // 对话式创建项目：多轮对话引导用户描述故事，或从对话中提取项目配置
  chatCreate: (messages: { role: string; content: string }[], extract?: boolean) =>
    client.post('/api/projects/chat-create', { messages, extract: extract || false }).then((r) => r.data),
}

/* ============================================================
 * Cockpit（创作舱）
 * ============================================================ */
export const cockpitApi = {
  /** 创作舱概览：active_session, recent_runs, review_queue_count, current_chapter, agent_statuses */
  get: (projectId: string) =>
    client.get<CockpitData>(`/api/cockpit/${projectId}`).then((r) => r.data),

  /** SSE 流 URL —— 返回 EventSource 可直接使用的 URL */
  stream: (projectId: string) => `/api/cockpit/${projectId}/stream`,

  /** Boss 自然语言指令 */
  postCommand: (projectId: string, command: string) =>
    client
      .post<CommandResult>(`/api/cockpit/${projectId}/command`, { command })
      .then((r) => r.data),

  /** 接管（暂停自动流程，转为人工） */
  takeover: (projectId: string) =>
    client.post(`/api/cockpit/${projectId}/takeover`).then((r) => r.data),

  // ---- 兼容旧调用的章节方法（部分页面仍在用） ----
  getChapter: (projectId: string, chapterId: string) =>
    client.get<Chapter>(`/api/cockpit/${projectId}/chapters/${chapterId}`).then((r) => r.data),
  getChapterVersion: (projectId: string, chapterId: string) =>
    client
      .get<ChapterVersion>(`/api/cockpit/${projectId}/chapters/${chapterId}/version`)
      .then((r) => r.data),
  listChapters: (projectId: string) =>
    client.get<Chapter[]>(`/api/cockpit/${projectId}/chapters`).then((r) => r.data),
  saveManuscript: (
    projectId: string,
    chapterId: string,
    content: string,
    options?: { base_version_number?: number; submit_for_review?: boolean; notes?: string },
  ) =>
    client
      .post(`/api/cockpit/${projectId}/chapters/${chapterId}/manuscript`, {
        content,
        ...options,
      })
      .then((r) => r.data),
}

/* ============================================================
 * Pipeline（流水线）
 * ============================================================ */
export const pipelineApi = {
  preparationStatus: (projectId: string) =>
    client
      .get<{
        project_id: string
        world_bible_ready: boolean
        outline_ready: boolean
        chapter_count: number
        volume_count: number
      }>(`/api/pipeline/${projectId}/preparation-status`)
      .then((r) => r.data),
  generateBible: (projectId: string, hints: BibleHints) =>
    client.post(`/api/pipeline/${projectId}/generate-bible`, { hints }, { timeout: 300000 }).then((r) => r.data),
  generateOutline: (projectId: string, params: OutlineParams) =>
    client.post(`/api/pipeline/${projectId}/generate-outline`, params, { timeout: 300000 }).then((r) => r.data),
  run: (projectId: string, params: PipelineRunParams) =>
    client.post(`/api/pipeline/${projectId}/run`, params, { timeout: 600000 }).then((r) => r.data),
  chapterQuality: (projectId: string, chapterNo: number) =>
    client
      .get<ChapterQualityDetail>(`/api/pipeline/${projectId}/chapters/${chapterNo}/quality`)
      .then((r) => r.data),
  resumeSession: (projectId: string) =>
    client.post(`/api/pipeline/${projectId}/resume-session`, {}, { timeout: 600000 }).then((r) => r.data),
}

/* ============================================================
 * Storyline（生命线）
 * ============================================================ */
export const storylineApi = {
  get: (projectId: string) =>
    client.get<StorylineOverview>(`/api/storyline/${projectId}`).then((r) => r.data),
  // 兼容旧调用
  listVolumes: (projectId: string) =>
      client
        .get<StorylineOverview>(`/api/storyline/${projectId}`)
        .then((r) => r.data?.volumes ?? []),
  updateVolume: (
    projectId: string,
    volumeId: string,
    data: { expected_revision: number; title?: string; summary?: string },
  ) =>
    client
      .patch<StorylineOverview>(`/api/storyline/${projectId}/volumes/${volumeId}`, data)
      .then((r) => r.data),
  updateBeat: (
    projectId: string,
    beatId: string,
    data: { expected_revision: number; title?: string; summary?: string },
  ) =>
    client
      .patch<StorylineOverview>(`/api/storyline/${projectId}/beats/${beatId}`, data)
      .then((r) => r.data),
}

/* ============================================================
 * Brain（大脑）
 * ============================================================ */
export const brainApi = {
  get: (projectId: string) =>
    client.get<BrainOverview>(`/api/brain/${projectId}`).then((r) => r.data),
  // 兼容旧调用
  listCharacters: (projectId: string) =>
    client.get<BrainOverview>(`/api/brain/${projectId}`).then((r) => r.data?.characters ?? []),
  listCanonFacts: (projectId: string) =>
    client.get<CanonFact[]>(`/api/canon-facts/${projectId}`).then((r) => r.data),
}

/* ============================================================
 * Canon Facts（独立 canon fact 操作）
 * ============================================================ */
export const canonFactsApi = {
  list: (projectId: string, params?: { mutability?: FactMutability; status?: string }) =>
    client
      .get<CanonFact[]>(`/api/canon-facts/${projectId}`, { params })
      .then((r) => r.data),
  assert: (projectId: string, data: CanonFactAssertData) =>
    client.post<CanonFact>(`/api/canon-facts/${projectId}/assert`, data).then((r) => r.data),
  confirm: (projectId: string, factId: string) =>
    client.post<CanonFact>(`/api/canon-facts/${projectId}/confirm`, { fact_id: factId }).then(
      (r) => r.data,
    ),
  supersede: (projectId: string, data: CanonFactSupersedeData) =>
    client.post<CanonFact>(`/api/canon-facts/${projectId}/supersede`, data).then((r) => r.data),
  check: (projectId: string, text: string) =>
    client
      .post<CanonCheckResult>(`/api/canon-facts/${projectId}/check`, { text })
      .then((r) => r.data),
}

/* ============================================================
 * Book Memory（书记忆）
 * ============================================================ */
export const bookMemoryApi = {
  get: (projectId: string) =>
    client.get<BookMemory[]>(`/api/book-memory/${projectId}`).then((r) => r.data),
  add: (projectId: string, data: BookMemoryData) =>
    client.post<BookMemory>(`/api/book-memory/${projectId}`, data).then((r) => r.data),
  extractStyle: (projectId: string) =>
    client.post(`/api/book-memory/${projectId}/extract-style`).then((r) => r.data),
  approve: (projectId: string, memoryId: string, reason?: string) =>
    client
      .post<BookMemory>(`/api/book-memory/${projectId}/memories/${memoryId}/approve`, {
        actor: 'user',
        reason,
      })
      .then((r) => r.data),
  reject: (projectId: string, memoryId: string, reason?: string) =>
    client
      .post<BookMemory>(`/api/book-memory/${projectId}/memories/${memoryId}/reject`, {
        actor: 'user',
        reason,
      })
      .then((r) => r.data),
  rollback: (projectId: string, memoryId: string, reason?: string) =>
    client
      .post<BookMemory>(`/api/book-memory/${projectId}/memories/${memoryId}/rollback`, {
        actor: 'user',
        reason,
      })
      .then((r) => r.data),
}

/* ============================================================
 * Evolution（进化）
 * ============================================================ */
export const evolutionApi = {
  get: (projectId: string) =>
    client.get<EvolutionOverview>(`/api/evolution/${projectId}`).then((r) => r.data),
  promptExperiment: (projectId: string, data: { prompt_a: string; prompt_b: string; test_input: string }) =>
    client
      .post<PromptExperimentResult>(`/api/evolution/${projectId}/prompt-experiment`, data)
      .then((r) => r.data),
  skillTest: (projectId: string, data: { skill_name: string; test_cases: Array<{ input: string; expected?: string }> }) =>
    client
      .post<SkillTestResult>(`/api/evolution/${projectId}/skill-test`, data)
      .then((r) => r.data),
  learningReport: (projectId: string) =>
    client.get<LearningReport>(`/api/evolution/${projectId}/learning-report`).then((r) => r.data),
  createReflection: (projectId: string, data: ReflectionData) =>
    client.post(`/api/evolution/${projectId}/reflection`, data).then((r) => r.data),
      listReflections: (projectId: string) =>
        client.get(`/api/evolution/${projectId}/reflections`).then((r) => r.data),
      evaluatePromptVersion: (projectId: string, versionId: string, force = false) =>
        client
          .post<{
            ok: boolean
            version_id: string
            holdout_status: string
            gate_passed: boolean
            metrics: Record<string, unknown>
          }>(`/api/evolution/${projectId}/prompt-versions/${versionId}/holdout`, { force }, { timeout: 1_800_000 })
          .then((r) => r.data),
      promotePromptVersion: (projectId: string, versionId: string) =>
      client
        .post<{ ok: boolean; version: PromptVersionView }>(
          `/api/evolution/${projectId}/prompt-versions/${versionId}/promote`,
        )
        .then((r) => r.data),
    rollbackPromptVersion: (projectId: string, versionId: string) =>
      client
        .post<{ ok: boolean; version: PromptVersionView }>(
          `/api/evolution/${projectId}/prompt-versions/${versionId}/rollback`,
        )
        .then((r) => r.data),
  }

/* ============================================================
 * Review Queue（审阅队列）
 * ============================================================ */
export const reviewQueueApi = {
  list: (projectId: string, params?: { status?: string }) =>
    client
      .get<ReviewQueueItem[]>(`/api/review-queue/${projectId}`, { params })
      .then((r) => r.data),
    approve: (projectId: string, itemId: string, data?: Pick<ReviewReviseData, 'decision_notes' | 'decided_by'>) =>
      client
        .post(`/api/review-queue/${projectId}/items/${itemId}/approve`, data ?? {}, { timeout: 600000 })
        .then((r) => r.data),
  revise: (projectId: string, itemId: string, data?: ReviewReviseData) =>
    client
        .post(`/api/review-queue/${projectId}/items/${itemId}/revise`, data ?? {}, { timeout: 600000 })
        .then((r) => r.data),
    reject: (projectId: string, itemId: string, data?: Pick<ReviewReviseData, 'decision_notes' | 'decided_by'>) =>
      client
        .post(`/api/review-queue/${projectId}/items/${itemId}/reject`, data ?? {})
        .then((r) => r.data),
    takeover: (projectId: string, itemId: string, data?: Pick<ReviewReviseData, 'decision_notes' | 'decided_by'>) =>
      client
        .post(`/api/review-queue/${projectId}/items/${itemId}/takeover`, data ?? {})
        .then((r) => r.data),
}

/* ============================================================
 * Governance（治理）—— Provider / Model Bindings
 * ============================================================ */
export const governanceApi = {
  listProviders: () => client.get<Provider[]>('/api/providers').then((r) => r.data),
  createProvider: (data: ProviderCreateData) =>
    client.post<Provider>('/api/providers', data).then((r) => r.data),
  updateProvider: (providerId: string, data: ProviderUpdateData) =>
    client.patch<Provider>(`/api/providers/${providerId}`, data).then((r) => r.data),
  deleteProvider: (providerId: string, force = false) =>
    client
      .delete<{ ok: boolean; provider_id: string; deleted_bindings: number }>(
        `/api/providers/${providerId}`,
        { params: force ? { force: true } : undefined },
      )
      .then((r) => r.data),
  testProvider: (data: ProviderTestParams) =>
    client.post<ProviderTestResult>('/api/providers/test', data).then((r) => r.data),
  listBindings: (projectId?: string) =>
    client
      .get<ModelBinding[]>('/api/model-bindings', {
        params: projectId ? { project_id: projectId } : {},
      })
      .then((r) => r.data),
  createBinding: (data: ModelBindingCreateData) =>
    client.post<ModelBinding>('/api/model-bindings', data).then((r) => r.data),
  updateBinding: (bindingId: string, data: ModelBindingUpdateData) =>
    client
      .patch<ModelBinding>(`/api/model-bindings/${bindingId}`, {
        ...data,
        project_id: data.project_id === null ? '' : data.project_id,
      })
      .then((r) => r.data),
  deleteBinding: (bindingId: string) =>
    client
      .delete<{ ok: boolean; binding_id: string }>(`/api/model-bindings/${bindingId}`)
      .then((r) => r.data),
}

/* ============================================================
 * Usage（用量）
 * ============================================================ */
export const usageApi = {
  get: (projectId: string) =>
    client.get<UsageOverview>(`/api/usage/${projectId}`).then((r) => r.data),
}

/* ============================================================
 * Provider（独立 provider 操作 —— 兼容旧引用）
 * ============================================================ */
export const providerApi = governanceApi

/* ============================================================
 * Agent Runs（兼容旧引用）
 * ============================================================ */
export const agentRunApi = {
  list: (projectId: string) =>
    client.get<AgentRun[]>(`/api/cockpit/${projectId}/runs`).then((r) => r.data),
}

/* ============================================================
 * Continuous（24 小时连续写作）
 * ============================================================ */
export const continuousApi = {
  /** 创建新运行，或在运行中原子更新后端持久化的生产契约。 */
  start: (projectId: string, contract: ContinuousStartContract) =>
    client
      .post<ContinuousStatus>(`/api/pipeline/${projectId}/continuous/start`, contract)
      .then((r) => r.data),
  pause: (projectId: string, reason = '用户在 24H 总控台暂停') =>
    client
      .post<ContinuousStatus>(`/api/pipeline/${projectId}/continuous/pause`, { reason })
      .then((r) => r.data),
  resume: (projectId: string) =>
    client
      .post<ContinuousStatus>(`/api/pipeline/${projectId}/continuous/resume`, {})
      .then((r) => r.data),
  stop: (projectId: string) =>
    client
      .post<ContinuousStatus>(`/api/pipeline/${projectId}/continuous/stop`, {})
      .then((r) => r.data),
  status: (projectId: string) =>
    client
      .get<ContinuousStatus>(`/api/pipeline/${projectId}/continuous/status`)
      .then((r) => r.data),
  events: (projectId: string, limit = 100) =>
    client
      .get<ContinuousRunEvent[]>(`/api/pipeline/${projectId}/continuous/events`, {
        params: { limit },
      })
      .then((r) => r.data),
}

export default client
