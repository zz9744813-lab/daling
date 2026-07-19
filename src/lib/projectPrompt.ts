export interface ProjectPromptPreset {
  id: 'source' | 'multicall' | 'memory' | 'correction' | 'autopilot'
  label: string
  shortLabel: string
  description: string
  text: string
}

/**
 * 可执行的项目级写作契约。它们写入项目提示词并随项目持久化，
 * 不是只存在于前端的“功能标签”。
 */
export const PROJECT_PROMPT_PRESETS: ProjectPromptPreset[] = [
  {
    id: 'source',
    label: '来源忠实契约',
    shortLabel: '严格遵纲',
    description: '把原始大纲、已确认 Canon 与事件顺序作为硬约束。',
    text: '【来源忠实】原始大纲、已确认 Canon、人物动机与关键事件顺序均为硬约束；任何新增设定不得覆盖或偷换来源事实，无法确认时必须标记缺口并暂停扩写。',
  },
  {
    id: 'multicall',
    label: '长篇多调用契约',
    shortLabel: '多调用成章',
    description: '按卷、章、场景逐层规划，禁止一次调用吞完整长篇。',
    text: '【长篇拆解】不得用一次模型调用完成整部大纲或完整长篇；必须按卷、章、场景逐层拆解，每个场景独立规划、写作和校验，并保留可回溯的来源绑定。',
  },
  {
    id: 'memory',
    label: '记忆检索契约',
    shortLabel: '记忆一致性',
    description: '每个场景写作前读取相关设定、人物状态与伏笔。',
    text: '【记忆一致性】每个场景写作前必须检索相关人物状态、世界规则、时间线、伏笔和最近章节摘要；写后更新可追踪记忆，禁止仅凭当前对话窗口猜测前文。',
  },
  {
    id: 'correction',
    label: '真实纠错契约',
    shortLabel: '纠错闭环',
    description: '批评、连续性审查、异稿改写与复审形成闭环。',
    text: '【质量纠错】章节不得初稿直出；必须经过批评、连续性审查、基于问题清单的非同文改写与独立复审。未达质量阈值时继续返工，达到上限则暂停并保留全部问题与版本证据。',
  },
  {
    id: 'autopilot',
    label: '24H 生产契约',
    shortLabel: '24H 守护',
    description: '后台持续运行，预算、熔断和质量闸门负责安全停机。',
    text: '【24H 自动生产】仅在明确启动的持久化生产契约内连续推进；浏览器关闭后任务可恢复。模型异常、连续失败、预算越界、来源冲突或纠错耗尽时必须自动暂停，禁止跳过质量闸门伪装完成。',
  },
]

function normalizeClause(value: string) {
  return value.trim().replace(/\r\n/g, '\n')
}

/** 保留作者原文，只在缺失时追加完整契约；重复点击不会制造重复提示词。 */
export function mergeProjectPrompt(current: string, clause: string) {
  const existing = normalizeClause(current)
  const normalizedClause = normalizeClause(clause)
  if (!normalizedClause || existing.includes(normalizedClause)) return existing
  return existing ? `${existing}\n\n${normalizedClause}` : normalizedClause
}

export function hasProjectPromptPreset(prompt: string, preset: ProjectPromptPreset) {
  return normalizeClause(prompt).includes(normalizeClause(preset.text))
}

