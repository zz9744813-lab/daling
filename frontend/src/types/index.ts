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

/**
 * 章节状态。
 *
 * Cockpit 的兼容路由会把部分状态折叠为 in_progress / finalized，但生产、
 * 审阅和后续直连接口会返回下面这些真实状态。保留 string 扩展位，避免
 * 后端新增状态时整页因为一个未知枚举失去渲染能力。
 */
export type KnownChapterStatus =
  | 'planned'
  | 'draft'
  | 'generating'
  | 'in_progress'
  | 'review'
  | 'approved'
  | 'published'
  | 'finalized'
  | 'failed'
  | 'blocked'

export type ChapterStatus = KnownChapterStatus | (string & {})

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

/** 新建项目工作区使用的结构化创作简报。 */
export type ProjectLengthType = 'short' | 'medium' | 'long' | 'epic' | 'custom'

export interface ProjectBlueprint {
  title: string
  logline: string
  description: string
  genre: string
  protagonist: string
  protagonist_desire: string
  protagonist_flaw: string
  protagonist_fear: string
  core_conflict: string
  story_question: string
  antagonist: string
  ability: string
  ability_cost: string
  world_setting: string
  world_rules: string
  themes: string
  tone: string
  pacing: string
  audience_experience: string
  platform: string
  language: string
  pov: string
  tense: string
  length_type: ProjectLengthType
  target_chapters: number
  chapter_words: number
  volume_count: string
  ending_preference: string
  content_boundaries: string
  autonomy_level: AutonomyLevel
  custom_prompt: string
}

export interface ProjectChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  state?: 'complete' | 'streaming' | 'stopped' | 'error'
}

export interface ChatCreateStatus {
  configured: boolean
  model?: string | null
  source?: string | null
}

export interface ChatBlueprintUpdate {
  config: Partial<ProjectBlueprint> & Record<string, unknown>
  readiness?: number
  missing_fields?: string[]
  suggested_replies?: string[]
  assumptions?: string[]
}

/** 创建项目前对用户大纲做真实解析后的预检结果。 */
export interface OutlineInspection {
  ok: boolean
  filename: string
  extension: string
  size_bytes: number
  char_count: number
  line_count: number
  chapter_heading_count: number
  volume_heading_count: number
  chapter_headings: string[]
  volume_headings: string[]
  preview: string
  text: string
}

export interface CreateProjectPayload extends Partial<Project> {
  title: string
  custom_prompt?: string
  /** 创建时允许提交完整蓝图；后端会校验并保存为公开项目配置。 */
  config?: Record<string, unknown>
  /** 保存对话原文，避免结构化提取遗漏作者的核心创意。 */
  creative_conversation?: Array<Pick<ProjectChatMessage, 'role' | 'content'>>
  creation_blueprint?: Record<string, unknown>
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
  summary?: string | null
  target_chapters?: number | null
  status?: string
  structure_locked?: boolean
  locked_chapters?: number[]
  beats?: StorylineBeat[]
}

export interface StorylineBeat {
  id: string
  volume_id: string | null
  title: string
  beat_index: number
  summary?: string | null
  emotional_arc?: string | null
  chapter_ids?: string[]
  chapter_number?: number | null
  importance?: string
  status?: string
  structure_locked?: boolean
  lock_reason?: string | null
}

export interface Chapter {
  id: string
  project_id: string
  volume_id?: string | null
  beat_id?: string | null
  chapter_number: number
  title: string
  status: ChapterStatus
  summary?: string | null
  word_count?: number
  target_words?: number
  raw_status?: string
  structure_locked?: boolean
  created_at?: string
  updated_at?: string
}

export interface ChapterVersion {
  /** 尚未产生正文版本时兼容路由会返回 null。 */
  id: string | null
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
  session_id?: string | null
  type: string
  item_type?: string
  severity: 'info' | 'warning' | 'critical' | string
  risk_level?: string
  artifact_type?: string | null
  artifact_id?: string | null
  title: string
  description?: string
  status: 'pending' | 'approved' | 'revised' | 'rejected' | 'takeover' | string
  decided_by?: string | null
  decided_at?: string | null
  decision_notes?: string | null
  chapter_no?: number | null
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
  memory_type: string
  key: string
  value: Record<string, unknown>
  source?: string | null
  confidence: number
  status: 'active' | 'rejected' | 'rolled_back' | string
  governance: {
    status: string
    origin?: string
    reviewed_by?: string | null
    reviewed_at?: string | null
    reason?: string | null
    history?: Array<Record<string, unknown>>
  }
  created_at?: string
  updated_at?: string
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
  status?: 'active' | 'untested' | 'inactive' | 'error'
  is_active?: boolean
  default_model?: string
  models?: string[]
  last_health_check_at?: string | null
  latency_ms?: number | null
  tested_model?: string | null
  last_error?: string | null
  created_at?: string
}

export interface ModelBinding {
  id: string
  project_id?: string | null
  agent_role?: AgentRole | null
  provider_id: string
  provider_name?: string
  model: string
  model_name?: string
  display_name?: string | null
  is_default: boolean
  context_window?: number
  max_output_tokens?: number
  capabilities?: {
    is_reasoning?: boolean
    timeout_seconds?: number
    max_retries?: number
    [key: string]: unknown
  }
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

export const CHAPTER_STATUS_MAP: Record<string, { label: string; color: string }> = {
  planned: { label: '已规划', color: 'gray' },
  draft: { label: '草稿', color: 'gray' },
  generating: { label: '生成中', color: 'blue' },
  in_progress: { label: '进行中', color: 'blue' },
  review: { label: '待质检', color: 'amber' },
  approved: { label: '已批准', color: 'green' },
  published: { label: '已发布', color: 'green' },
  finalized: { label: '已定稿', color: 'green' },
  failed: { label: '生成失败', color: 'red' },
  blocked: { label: '已阻断', color: 'red' },
}

export function getChapterStatusMeta(status?: ChapterStatus) {
  if (status && CHAPTER_STATUS_MAP[status]) return CHAPTER_STATUS_MAP[status]
  return {
    label: status ? `未知状态 · ${status}` : '状态未知',
    color: 'gray',
  }
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
  data?: Record<string, unknown>
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
  hints?: Record<string, unknown>
  replace_existing?: boolean
  expected_revision?: number
}

/** Pipeline 运行参数 */
export interface PipelineRunParams {
  target_chapters?: number
  mode?: string
}

/** 24 小时自动生产：操作者希望系统保持的状态。 */
export type ContinuousDesiredState = 'running' | 'paused' | 'stopped'

/** 24 小时自动生产：后台监督器当前实际观察到的状态。 */
export type ContinuousObservedState =
  | 'starting'
  | 'recovering'
  | 'running'
  | 'retry_wait'
  | 'paused'
  | 'quality_hold'
  | 'budget_hold'
  | 'failed'
  | 'completed'
  | 'stopped'
  | string

export interface ContinuousPolicy {
  quality_threshold: number
  max_rewrite_rounds: number
  chapter_delay_seconds: number
  error_backoff_seconds: number
  max_consecutive_failures: number
  circuit_cooldown_seconds: number
  quality_failure_action: 'retry' | 'pause'
  max_quality_retry_cycles: number
  quality_retry_backoff_seconds: number
  learning_interval_chapters: number
  daily_cost_limit: number | null
  daily_token_limit: number | null
}

/** 启动或更新自动生产时提交并由后端持久化的运行契约。 */
export interface ContinuousStartContract extends ContinuousPolicy {
  target_chapters: number | null
  autonomy_level: 'L2' | 'L3' | 'L4'
}

export interface ContinuousRunError {
  at?: string
  chapter_no?: number | null
  message: string
}

export interface ContinuousMetrics {
  last_chapter?: number
  last_score?: number
  last_word_count?: number
  scored_chapters?: number
  average_score?: number
  today_input_tokens?: number
  today_output_tokens?: number
  today_total_tokens?: number
  today_cost?: number
  today_requests?: number
  usage_updated_at?: string
  [key: string]: unknown
}

export interface ContinuousStatus {
  run_id: string | null
  project_id: string
  running: boolean
  desired_state: ContinuousDesiredState
  status: ContinuousObservedState
  worker_alive: boolean
  heartbeat_stale: boolean
  current_chapter: number | null
  completed_chapters: number
  target_chapters: number | null
  /** 新版状态接口返回的项目级剩余章数；旧版缺失时由前端按已批准章数推导。 */
  remaining_chapters?: number | null
  autonomy_level: 'L2' | 'L3' | 'L4'
  consecutive_failures: number
  total_failures: number
  last_error: string | null
  errors: ContinuousRunError[]
  policy: ContinuousPolicy
  metrics: ContinuousMetrics
  started_at: string | null
  stopped_at: string | null
  last_heartbeat_at: string | null
  next_run_at: string | null
}

export interface ContinuousRunEvent {
  id: string
  run_id: string
  event_type: string
  severity: 'info' | 'warning' | 'error' | string
  chapter_no: number | null
  message: string
  data: Record<string, unknown>
  created_at: string | null
}

/** Provider 测试参数 */
  export interface ProviderTestParams {
    provider_id?: string
    provider_type?: string
    base_url?: string
    api_key?: string
    model?: string
}

/** Provider 测试结果 */
  export interface ProviderTestResult {
    ok: boolean
    message?: string
    latency_ms?: number
    model?: string
    last_health_check_at?: string
    error?: string
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

export interface ProviderUpdateData {
  name?: string
  provider_type?: string
  base_url?: string
  /** 留空并省略可保留服务端现有密钥；显式空串表示清除。 */
  api_key?: string
  default_model?: string
  models?: string[]
  is_active?: boolean
}

/** Model Binding 创建数据 */
export interface ModelBindingCreateData {
  project_id?: string | null
  agent_role: AgentRole
  provider_id: string
  model: string
  display_name?: string | null
  context_window?: number
  max_output_tokens?: number
  capabilities?: {
    is_reasoning?: boolean
    timeout_seconds?: number
    max_retries?: number
    [key: string]: unknown
  }
  is_default?: boolean
}

export interface ModelBindingUpdateData {
  project_id?: string | null
  agent_role?: AgentRole | null
  provider_id?: string
  model?: string
  display_name?: string | null
  context_window?: number
  max_output_tokens?: number
  capabilities?: {
    is_reasoning?: boolean
    timeout_seconds?: number
    max_retries?: number
    [key: string]: unknown
  }
  is_default?: boolean
}

export interface ChapterQualityVersionRef {
  id: string
  version_no: number
  status: string
  word_count: number
  created_by_agent: string | null
  created_at: string | null
  is_current: boolean
}

export interface ChapterQualityAssessment {
  id: string
  session_id: string | null
  version_id: string | null
  agent_run_id: string | null
  assessor: string
  assessment_type: string
  round_no: number
  rubric_version: string
  model_name: string | null
  dimension_scores: Record<string, unknown>
  overall_score: number | null
  verdict: string
  passed: boolean
  issue_ids: string[]
  raw_result: Record<string, unknown>
  created_at: string | null
}

export interface ChapterQualityIssue {
  id: string
  assessment_id: string
  version_id: string | null
  block_id: string | null
  issue_fingerprint: string
  source: string
  category: string
  severity: string
  block_no: number | null
  location: string | null
  quoted_text: string | null
  description: string
  expected: string | null
  actual: string | null
  suggestion: string | null
  status: string
  resolved_by_revision_id: string | null
  extra: Record<string, unknown>
  created_at: string | null
}

export interface ChapterRevisionAttempt {
  id: string
  session_id: string | null
  input_version_id: string | null
  output_version_id: string | null
  round_no: number
  status: string
  instruction_source: string
  instruction: string | null
  trigger_issue_ids: string[]
  score_before: number | null
  score_after: number | null
  diff_summary: string | null
  error: string | null
  extra: Record<string, unknown>
  created_at: string | null
}

export interface ChapterQualityDetail {
  project_id: string
  chapter: {
    id: string
    chapter_no: number
    title: string
    status: string
    word_count: number
    current_version_id: string | null
  }
  summary: {
    assessment_count: number
    issue_count: number
    open_issue_count: number
    revision_attempt_count: number
    latest_score: number | null
    latest_verdict: string | null
    quality_passed: boolean | null
  }
  version_refs: ChapterQualityVersionRef[]
  assessments: ChapterQualityAssessment[]
  issues: ChapterQualityIssue[]
  revision_attempts: ChapterRevisionAttempt[]
}

/** Prompt 实验 A/B 结果 */
export interface PromptExperimentResult {
  experiment_id?: string
  prompt_a?: string
  prompt_b?: string
  test_input?: string
  winner?: 'A' | 'B' | 'tie' | string
  scores?: { a?: number; b?: number }
  score_scale?: number
  response_a?: string
  response_b?: string
  output_a?: string
  output_b?: string
  judge_reasoning?: string
  input_tokens?: number
  output_tokens?: number
  cost?: number
  duration_ms?: number
  created_at?: string
  [key: string]: unknown
}

/** 技能测试结果 */
export interface SkillTestResult {
  test_id?: string
  skill_name?: string
  test_count?: number
  avg_score?: number
  previous_avg_score?: number | null
  improvement?: number
  pass_rate?: number
  results?: Array<{
    case_index?: number
    input?: string
    response?: string
    score?: number
    reasoning?: string
    error?: string
    case?: string
    passed?: boolean
    output?: string
  }>
  input_tokens?: number
  output_tokens?: number
  cost?: number
  duration_ms?: number
  created_at?: string
  [key: string]: unknown
}

/** 学习报告 */
export interface LearningReport {
  period: { start?: number; end?: number }
  avg_score_trend: Array<{
    chapter_no: number
    score: number
    passed?: boolean
    verdict?: string
    assessment_id?: string
  }>
  common_issues: Array<{
    issue_type: string
    source?: string
    severity?: string
    count: number
    open_count?: number
  }>
  lessons_learned: Array<Record<string, unknown>>
  suggestions: string[]
}

export interface LearningCycleView {
  id: string
  status: string
  source_from?: string | null
  source_to?: string | null
  feedback_count: number
  assessment_count: number
  memory_count: number
  prompt_candidate_count: number
  candidate_memory_ids?: string[]
  candidate_prompt_version_ids?: string[]
  promotion_decision?: string | null
  holdout_metrics?: Record<string, unknown>
  rollback_reason?: string | null
  error?: string | null
  started_at?: string | null
  completed_at?: string | null
  created_at?: string | null
}

export interface PromptVersionView {
  id: string
  agent_role: string
  version_no: number
  status: string
  template: string
  parent_version_id?: string | null
  learning_cycle_id?: string | null
  evaluation_metrics: Record<string, unknown>
  source?: {
    type: 'autonomous_learning' | 'manual' | string
    learning_cycle_id?: string | null
    evidence_count?: number | null
    baseline_champion_id?: string | null
  }
  activated_at?: string | null
  retired_at?: string | null
  created_at?: string | null
}

export interface LearningReflectionView {
  id: string
  reflection_type: string
  chapter_no?: number | null
  content: string
  decisions: Array<Record<string, unknown>>
  lessons_learned: Array<Record<string, unknown>>
  created_at?: string | null
}

export interface EvolutionOverview {
  project_id: string
  prompt_experiments: Array<Record<string, unknown>>
  skill_tests: Array<Record<string, unknown>>
  reflections_count: number
  latest_suggestions: string[]
  quality_report: LearningReport
  learning_cycles: LearningCycleView[]
  prompt_versions: PromptVersionView[]
  recent_reflections: LearningReflectionView[]
  memory_entries: BookMemory[]
  memory_count: number
  memory_status_counts: Record<string, number>
  pending_feedback_count: number
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
  memory_type: string
  key: string
  value: Record<string, unknown> | string
  source?: string
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
  decision_notes?: string
  decided_by?: string
  revised_content?: string
  revision_instruction?: string
}

/** SSE 事件类型 */
export type KnownSSEEventType =
  | 'agent_start'
  | 'agent_complete'
  | 'chapter_progress'
  | 'review_needed'
  | 'error'
  | 'heartbeat'
  | 'run_started'
  | 'policy_updated'
  | 'run_paused'
  | 'run_resumed'
  | 'run_stopped'
  | 'run_recovering'
  | 'worker_acquired'
  | 'worker_shutdown'
  | 'target_reached'
  | 'story_structure_extended'
  | 'chapter_completed'
  | 'quality_hold'
  | 'budget_hold'
  | 'circuit_open'
  | 'retry_scheduled'
  | 'supervisor_failed'

export type SSEEventType = KnownSSEEventType | (string & {})

/** SSE 事件载荷 */
export interface SSEEvent {
  event: SSEEventType
  data: {
    agent_role?: AgentRole
    message?: string
    chapter_id?: string
    chapter_number?: number
    chapter_no?: number | null
    run_id?: string | null
    project_id?: string
    severity?: string
    created_at?: string
    status?: ContinuousObservedState
    desired_state?: ContinuousDesiredState
    worker_alive?: boolean
    heartbeat_stale?: boolean
    completed_chapters?: number
    target_chapters?: number | null
    remaining_chapters?: number | null
    data?: Record<string, unknown>
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
  project_id: string
  volumes: StorylineVolume[]
  chapters: Chapter[]
  source: {
    type: 'uploaded_outline' | 'project_blueprint' | string
    present: boolean
    filename?: string | null
    revision: number
    sha256?: string | null
    updated_at?: string | null
  }
  artifact: {
    exists: boolean
    ready: boolean
    stale: boolean
    stale_reason?: string | null
    structure_revision: number
    based_on_source_revision?: number | null
    generated_at?: string | null
    updated_at?: string | null
    last_change?: string | null
    can_replace: boolean
    replace_blocked_by_chapters: number[]
  }
  stats: {
    volume_count: number
    beat_count: number
    chapter_count: number
    locked_chapter_count: number
    written_word_count: number
  }
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

export interface UsageDailyStat {
  stat_date: string
  total_requests: number
  input_tokens: number
  output_tokens: number
  cost: number
}

export interface UsageAgentStat {
  agent_name: string
  total_runs: number
  success_count: number
  failed_count: number
  input_tokens: number
  output_tokens: number
  cost: number
  avg_duration_ms?: number | null
}

/** 与 GET /api/usage/{project_id} 的真实响应保持一致。 */
export interface UsageOverview {
  project_id: string
  today_input_tokens: number
  today_output_tokens: number
  today_cost: number
  today_requests: number
  total_input_tokens: number
  total_output_tokens: number
  total_cost: number
  total_requests: number
  by_agent: UsageAgentStat[]
  daily: UsageDailyStat[]
}
