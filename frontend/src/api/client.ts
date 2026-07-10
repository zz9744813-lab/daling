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
  ModelBindingCreateData,
  PromptExperimentResult,
  SkillTestResult,
  LearningReport,
  ReflectionData,
  BookMemory,
  BookMemoryData,
  CanonFactAssertData,
  CanonFactSupersedeData,
  CanonCheckResult,
  ReviewReviseData,
  FactMutability,
} from '../types'

/**
 * Axios 实例 —— baseURL 为空，所有 /api 请求走 vite proxy → http://localhost:8000
 */
const client: AxiosInstance = axios.create({
  baseURL: '',
  headers: { 'Content-Type': 'application/json' },
  timeout: 60000,
})

// 响应拦截：统一抛出后端错误信息
client.interceptors.response.use(
  (res) => res,
  (error) => {
    const msg = error?.response?.data?.detail || error?.message || '请求失败'
    return Promise.reject(new Error(msg))
  },
)

/* ============================================================
 * Projects
 * ============================================================ */
export const projectsApi = {
  list: () => client.get<Project[]>('/api/projects').then((r) => r.data),
  get: (id: string) => client.get<Project>(`/api/projects/${id}`).then((r) => r.data),
  getById: (id: string) => client.get<Project>(`/api/projects/${id}`).then((r) => r.data),
  create: (data: Partial<Project>) => client.post<Project>('/api/projects', data).then((r) => r.data),
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
  saveManuscript: (projectId: string, chapterId: string, content: string) =>
    client
      .post(`/api/cockpit/${projectId}/chapters/${chapterId}/manuscript`, { content })
      .then((r) => r.data),
}

/* ============================================================
 * Pipeline（流水线）
 * ============================================================ */
export const pipelineApi = {
  generateBible: (projectId: string, hints: BibleHints) =>
    client.post(`/api/pipeline/${projectId}/generate-bible`, { hints }, { timeout: 300000 }).then((r) => r.data),
  generateOutline: (projectId: string, params: OutlineParams) =>
    client.post(`/api/pipeline/${projectId}/generate-outline`, params, { timeout: 300000 }).then((r) => r.data),
  run: (projectId: string, params: PipelineRunParams) =>
    client.post(`/api/pipeline/${projectId}/run`, params, { timeout: 600000 }).then((r) => r.data),
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
}

/* ============================================================
 * Evolution（进化）
 * ============================================================ */
export const evolutionApi = {
  get: (projectId: string) =>
    client.get(`/api/evolution/${projectId}`).then((r) => r.data),
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
}

/* ============================================================
 * Review Queue（审阅队列）
 * ============================================================ */
export const reviewQueueApi = {
  list: (projectId: string, params?: { status?: string }) =>
    client
      .get<ReviewQueueItem[]>(`/api/review-queue/${projectId}`, { params })
      .then((r) => r.data),
  approve: (projectId: string, itemId: string) =>
    client
      .post(`/api/review-queue/${projectId}/items/${itemId}/approve`)
      .then((r) => r.data),
  revise: (projectId: string, itemId: string, data?: ReviewReviseData) =>
    client
      .post(`/api/review-queue/${projectId}/items/${itemId}/revise`, data ?? {})
      .then((r) => r.data),
  reject: (projectId: string, itemId: string) =>
    client
      .post(`/api/review-queue/${projectId}/items/${itemId}/reject`)
      .then((r) => r.data),
  takeover: (projectId: string, itemId: string) =>
    client
      .post(`/api/review-queue/${projectId}/items/${itemId}/takeover`)
      .then((r) => r.data),
}

/* ============================================================
 * Governance（治理）—— Provider / Model Bindings
 * ============================================================ */
export const governanceApi = {
  listProviders: () => client.get<Provider[]>('/api/providers').then((r) => r.data),
  createProvider: (data: ProviderCreateData) =>
    client.post<Provider>('/api/providers', data).then((r) => r.data),
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
  // 启动连续写作
  start: (projectId: string) =>
    client.post(`/api/pipeline/${projectId}/continuous/start`, {}).then((r) => r.data),
  // 停止连续写作
  stop: (projectId: string) =>
    client.post(`/api/pipeline/${projectId}/continuous/stop`, {}).then((r) => r.data),
  // 查询连续写作状态
  status: (projectId: string) =>
    client.get(`/api/pipeline/${projectId}/continuous/status`).then((r) => r.data),
}

export default client
