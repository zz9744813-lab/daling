/**
 * Novel Agent OS —— 全局 TypeScript 类型定义
 * 参考 v4.1 / v5.0 方案
 */

/* ============================================================
 * 基础枚举
 * ============================================================ */

/** 智能体角色 */
export enum AgentRole {
  StoryArchitect = 'StoryArchitect',
  ChapterPlanner = 'ChapterPlanner',
  Drafter = 'Drafter',
  Critic = 'Critic',
  ContinuityGuard = 'ContinuityGuard',
  Rewriter = 'Rewriter',
  ChiefEditor = 'ChiefEditor',
  MemoryKeeper = 'MemoryKeeper',
}

/** 自主等级 L1-L4 */
export type AutonomyLevel = 'L1' | 'L2' | 'L3' | 'L4'

/** 章节状态 */
export type ChapterStatus = 'planned' | 'draft' | 'in_progress' | 'finalized'

/** Canon Fact 可变性（v5.0） */
export type FactMutability = 'immutable' | 'evolving' | 'deprecated'

/** Canon Fact 主体类型 */
export type SubjectType = 'character' | 'location' | 'item' | 'faction' | 'event' | 'concept'

/* ============================================================
 * 项目
 * ============================================================ */

export interface ProjectConfig {
  target_chapters?: number
  words_per_chapter?: number
  autonomy_level?: AutonomyLevel
  provider?: string
  model?: string
  language?: string
  genre?: string
  tone?: string
  themes?: string[]
  /** 允许后端返回的额外字段透传 */
  [key: string]: unknown
}

export interface Project {
  id: string
  title: string
  type?: string
  genre?: string
  /** 简介 —— 与 synopsis 互为别名，后端会自动映射 */
  description?: string
  /** synopsis 的别名（后端可能返回该字段） */
  synopsis?: string
  /** 目标总字数 */
  target_words?: number
  /** 目标章数（后端从 extra dict 读取） */
  target_chapters?: number
  /** 当前章号 —— 与 current_chapter_no 互为别名 */
  current_chapter?: number
  /** current_chapter 的别名（后端可能返回该字段） */
  current_chapter_no?: number
  /** 项目状态：draft / active / paused / completed 等 */
  status?: string
  /** 自主等级 L1-L4（创建时发送，后端存入 extra） */
  autonomy_level?: AutonomyLevel
  config?: ProjectConfig
  progress?: number
  created_at?: string
  updated_at?: string
}

/* ============================================================
 * 世界观 & 故事线
 * ============================================================ */

export interface WorldBible {
  id: string
  project_id: string
  setting?: string
  premise?: string
  themes?: string[]
  rules?: string[]
  locations?: string[]
  factions?: string[]
  created_at?: string
  updated_at?: string
}

export interface StorylineVolume {
  id: string
  project_id: string
  title: string
  volume_index: number
  summary?: string
  beats?: StorylineBeat[]
}

export interface StorylineBeat {
  id: string
  volume_id: string
  title: string
  beat_index: number
  summary?: string
  emotional_arc?: string
  chapter_ids?: string[]
}

export interface Chapter {
  id: string
  project_id: string
  volume_id?: string
  beat_id?: string
  chapter_number: number
  title: string
  status: ChapterStatus
  summary?: string
  word_count?: number
  target_words?: number
  created_at?: string
  updated_at?: string
}

export interface ChapterVersion {
  id: string
  chapter_id: string
  version_number: number
  content: string
  word_count?: number
  created_by?: string
  created_at?: string
}

export interface ManuscriptBlock {
  id: string
  chapter_id: string
  block_index: number
  block_type?: 'paragraph' | 'dialogue' | 'scene_break' | 'heading'
  content: string
}

/* ============================================================
 * 角色 / 关系 / 线索 / 当前状态
 * ============================================================ */

export interface Character {
  id: string
  project_id: string
  name: string
  aliases?: string[]
  role?: string
  description?: string
  appearance?: string
  personality?: string
  background?: string
  motivation?: string
  arc?: string
  status?: string
  created_at?: string
}

export interface Relationship {
  id: string
  project_id: string
  from_character_id: string
  to_character_id: string
  relation_type: string
  description?: string
}

export interface PlotThread {
  id: string
  project_id: string
  title: string
  description?: string
  status?: 'open' | 'resolved' | 'abandoned'
  introduced_chapter?: number
  resolved_chapter?: number
}

export interface CurrentStoryState {
  project_id: string
  current_chapter?: number
  current_scene?: string
  time_of_day?: string
  location?: string
  present_characters?: string[]
  active_threads?: string[]
  mood?: string
  last_events?: string[]
}

/* ============================================================
 * 摘要
 * ============================================================ */

export interface ChapterSummary {
  id: string
  chapter_id: string
  summary: string
  key_events?: string[]
  character_changes?: string[]
  created_at?: string
}

export interface NarrativeSummary {
  id: string
  project_id: string
  scope: 'volume' | 'whole' | 'recent'
  content: string
  created_at?: string
}

/* ============================================================
 * 工作会话 / 审阅队列 / Agent 运行
 * ============================================================ */

export interface WorkSession {
  id: string
  project_id: string
  started_at?: string
  ended_at?: string
  chapters_worked?: string[]
  words_written?: number
  agent_runs?: string[]
}

export interface ReviewQueueItem {
  id: string
  project_id: string
  chapter_id?: string
  type: 'continuity' | 'quality' | 'style' | 'canon'
  severity: 'info' | 'warning' | 'critical'
  title: string
  description?: string
  status: 'pending' | 'resolved' | 'dismissed'
  created_at?: string
}

export interface AgentRun {
  id: string
  project_id?: string
  /** 智能体角色（旧字段，部分接口仍返回） */
  agent_role?: AgentRole
  /** 智能体名称 —— recent_runs 中使用，与 agent_role 同义 */
  agent_name?: string
  chapter_id?: string
  /** 运行状态：兼容 success / completed 两种命名 */
  status: 'pending' | 'running' | 'completed' | 'success' | 'failed'
  autonomy_level?: AutonomyLevel
  input?: string
  output?: string
  /** 旧字段：总 token 数 */
  tokens_used?: number
  /** 新字段：输入 token 数（recent_runs 返回） */
  input_tokens?: number
  /** 新字段：输出 token 数（recent_runs 返回） */
  output_tokens?: number
  /** 运行耗时（毫秒） */
  duration_ms?: number
  started_at?: string
  finished_at?: string
  created_at?: string
  error?: string | null
}

/* ============================================================
 * Canon Fact（v5.0）
 * ============================================================ */

export interface CanonFact {
  id: string
  project_id: string
  fact_type: string
  subject_type: SubjectType
  subject_id: string
  subject_name?: string
  predicate: string
  object_value: string
  mutability: FactMutability
  source_chapter?: number
  confirmed_chapter?: number
  confidence?: number
  confirmed?: boolean
  superseded_by?: string
  created_at?: string
  updated_at?: string
}

/* ============================================================
 * 书记忆 / 规划反思
 * ============================================================ */

export interface BookMemory {
  id: string
  project_id: string
  memory_type?: string
  content: string
  importance?: number
  related_chapters?: number[]
  created_at?: string
}

export interface PlanningReflection {
  id: string
  project_id: string
  chapter_id?: string
  reflection: string
  lessons?: string[]
  adjustments?: string[]
  created_at?: string
}

/* ============================================================
 * Provider / Usage（治理）
 * ============================================================ */

export interface Provider {
  id: string
  name: string
  /** Provider 类型 —— 与 provider_type 互为别名 */
  type?: string
  /** provider_type 的别名（后端可能返回该字段） */
  provider_type?: string
  base_url?: string
  status?: 'active' | 'inactive' | 'error'
  models?: string[]
  created_at?: string
}

export interface ModelBinding {
  id: string
  project_id?: string
  agent_role: AgentRole
  provider_id: string
  provider_name?: string
  model: string
  is_default?: boolean
}

export interface UsageRecord {
  id: string
  project_id: string
  agent_role?: AgentRole
  provider?: string
  model?: string
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  cost?: number
  created_at?: string
}

/* ============================================================
 * 常量映射
 * ============================================================ */

export const AUTONOMY_LEVELS: Record<AutonomyLevel, string> = {
  L1: 'L1 · 手动确认',
  L2: 'L2 · 建议审批',
  L3: 'L3 · 自动执行',
  L4: 'L4 · 全自主',
}

export const AGENT_ROLES: Record<AgentRole, string> = {
  [AgentRole.StoryArchitect]: '故事架构师',
  [AgentRole.ChapterPlanner]: '章节规划师',
  [AgentRole.Drafter]: '起草者',
  [AgentRole.Critic]: '评论家',
  [AgentRole.ContinuityGuard]: '连续性守卫',
  [AgentRole.Rewriter]: '改写者',
  [AgentRole.ChiefEditor]: '主编',
  [AgentRole.MemoryKeeper]: '记忆管家',
}

export const CHAPTER_STATUS_MAP: Record<ChapterStatus, { label: string; color: string }> = {
  planned: { label: '已规划', color: 'gray' },
  draft: { label: '草稿', color: 'gray' },
  in_progress: { label: '进行中', color: 'blue' },
  finalized: { label: '已定稿', color: 'green' },
}

export const FACT_MUTABILITY_MAP: Record<FactMutability, { label: string; color: string }> = {
  immutable: { label: '不可变', color: 'green' },
  evolving: { label: '可演变', color: 'blue' },
  deprecated: { label: '已废弃', color: 'gray' },
}

export const REVIEW_SEVERITY_MAP: Record<ReviewQueueItem['severity'], { label: string; color: string }> = {
  info: { label: '提示', color: 'gray' },
  warning: { label: '警告', color: 'amber' },
  critical: { label: '严重', color: 'red' },
}

/* ============================================================
 * v5.0 Phase 6 —— Cockpit / Pipeline / SSE / Evolution 扩展类型
 * ============================================================ */

/** 智能体运行状态（创作舱 Dock） */
export interface AgentStatus {
  agent_role: AgentRole
  status: 'idle' | 'working' | 'error'
  message?: string
  current_task?: string
  progress?: number
  started_at?: string
}

/** 创作舱概览数据 */
export interface CockpitData {
  project_id?: string
  active_session?: WorkSession | null
  recent_runs?: AgentRun[]
  review_queue_count?: number
  current_chapter?: Chapter | null
  agent_statuses?: AgentStatus[]
}

/** Boss 指令执行结果 */
export interface CommandResult {
  ok: boolean
  intent?: string
  message?: string
}

/** 生成世界观提示 */
export interface BibleHints {
  title?: string
  genre?: string
  themes?: string[]
  setting?: string
  tone?: string
  target_chapters?: number
}

/** 生成大纲参数 */
export interface OutlineParams {
  volume_count: number
  chapters_per_volume: number
}

/** Pipeline 运行参数 */
export interface PipelineRunParams {
  target_chapters?: number
  mode?: string
}

/** Provider 测试参数 */
export interface ProviderTestParams {
  provider_type: string
  base_url: string
  api_key: string
  model: string
}

/** Provider 测试结果 */
export interface ProviderTestResult {
  ok: boolean
  message?: string
  latency_ms?: number
}

/** Provider 创建数据 */
export interface ProviderCreateData {
  name: string
  provider_type: string
  base_url: string
  api_key?: string
  model?: string
  models?: string[]
}

/** Model Binding 创建数据 */
export interface ModelBindingCreateData {
  project_id?: string
  agent_role: AgentRole
  provider_id: string
  model: string
  is_default?: boolean
}

/** Prompt 实验 A/B 结果 */
export interface PromptExperimentResult {
  id?: string
  prompt_a?: string
  prompt_b?: string
  test_input?: string
  winner?: 'A' | 'B' | 'tie' | string
  scores?: { a?: number; b?: number }
  output_a?: string
  output_b?: string
  created_at?: string
  [key: string]: unknown
}

/** 技能测试结果 */
export interface SkillTestResult {
  id?: string
  skill_name?: string
  test_cases?: Array<{ input: string; expected?: string }>
  pass_rate?: number
  results?: Array<{ case: string; passed: boolean; output?: string }>
  created_at?: string
  [key: string]: unknown
}

/** 学习报告 */
export interface LearningReport {
  id?: string
  title?: string
  summary?: string
  content?: string
  quality_trend?: Array<{ chapter: number; score: number }>
  common_issues?: string[]
  suggestions?: string[]
  created_at?: string
  [key: string]: unknown
}

/** 规划反思 */
export interface ReflectionData {
  chapter_id?: string
  reflection: string
  lessons?: string[]
  adjustments?: string[]
}

/** Book Memory 操作数据 */
export interface BookMemoryData {
  memory_type?: string
  content: string
  importance?: number
  related_chapters?: number[]
}

/** Canon Fact 断言数据 */
export interface CanonFactAssertData {
  subject_type: SubjectType
  subject_id: string
  subject_name?: string
  predicate: string
  object_value: string
  mutability?: FactMutability
  source_chapter?: number
}

/** Canon Fact 取代数据 */
export interface CanonFactSupersedeData {
  old_fact_id: string
  new_object_value: string
  reason?: string
  source_chapter?: number
}

/** Canon Fact 检查结果 */
export interface CanonCheckResult {
  consistent: boolean
  conflicts?: Array<{ fact_id: string; message: string }>
  [key: string]: unknown
}

/** 审阅操作参数（revise 需要反馈） */
export interface ReviewReviseData {
  feedback?: string
  instruction?: string
}

/** SSE 事件类型 */
export type SSEEventType =
  | 'agent_start'
  | 'agent_complete'
  | 'chapter_progress'
  | 'review_needed'
  | 'error'
  | 'heartbeat'

/** SSE 事件载荷 */
export interface SSEEvent {
  event: SSEEventType
  data: {
    agent_role?: AgentRole
    message?: string
    chapter_id?: string
    chapter_number?: number
    content?: string
    delta?: string
    word_count?: number
    item?: ReviewQueueItem
    error?: string
    [key: string]: unknown
  }
}

/** Storyline 概览（v5.0 统一接口） */
export interface StorylineOverview {
  volumes?: StorylineVolume[]
  chapters?: Chapter[]
  [key: string]: unknown
}

/** Brain 概览（v5.0 统一接口） */
export interface BrainOverview {
  characters?: Character[]
  relationships?: Relationship[]
  plot_threads?: PlotThread[]
  current_state?: CurrentStoryState
  summaries?: ChapterSummary[]
  [key: string]: unknown
}

/** Usage 概览 */
export interface UsageOverview {
  total_tokens?: number
  total_cost?: number
  records?: UsageRecord[]
  by_agent?: Record<string, { tokens: number; cost: number }>
  [key: string]: unknown
}
