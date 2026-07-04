import React, { useState, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  PanelLeft,
  PanelRight,
  ChevronRight,
  ScrollText,
  Users,
  GitBranch,
  Sparkles,
  BookOpen,
  ListTree,
  Play,
  Pause,
  RotateCcw,
  Loader2,
  Circle,
  Wifi,
  WifiOff,
  AlertTriangle,
} from 'lucide-react'
import { TopBar } from '../layout/TopBar'
import { BossCommandBar } from '../layout/BossCommandBar'
import { AppShell, AppShellBody } from '../layout/AppShell'
import { cockpitApi, pipelineApi, brainApi, canonFactsApi, reviewQueueApi } from '../api/client'
import { useProjectStore } from '../store/projectStore'
import { useCockpitStream } from '../hooks/useCockpitStream'
import { cn } from '../lib/cn'
import { Badge } from '../components/Badge'
import { EmptyState } from '../components/EmptyState'
import {
  AgentRole,
  AGENT_ROLES,
  CHAPTER_STATUS_MAP,
  type AgentStatus,
  type Chapter,
  type ReviewQueueItem,
} from '../types'

/** 全部 8 个 Agent 角色，按工作流顺序排列 */
const ALL_AGENT_ROLES = Object.values(AgentRole)

/**
 * CockpitPage —— 创作舱（v5.0 设计规范）
 *
 * 布局：TopBar + 左侧 AI 团队 Dock（可收起）+ 中间 Manuscript Desk（稿件主角）
 *       + 右侧 Context Lens（可收起）+ 底部 Boss Command Bar
 * 规范：两侧面板默认可收起，Manuscript Desk 独占屏幕；
 *       正文衬线字体、680px 宽度、行高 2.0；SSE 实时流驱动 Agent 状态与稿件流式输出。
 */
export default function CockpitPage() {
  const project = useProjectStore((s) => s.currentProject)
  const projectId = project?.id ?? ''
  const queryClient = useQueryClient()

  const [leftOpen, setLeftOpen] = useState(true)
  const [rightOpen, setRightOpen] = useState(false)

  // ===== 创作舱概览数据 =====
  const { data: cockpit, refetch: refetchCockpit } = useQuery({
    queryKey: ['cockpit', projectId],
    queryFn: () => cockpitApi.get(projectId),
    enabled: !!projectId,
    refetchInterval: 15000,
  })

  // ===== 章节列表（左侧 Dock 底部 + Context Lens） =====
  const { data: chapters } = useQuery({
    queryKey: ['chapters', projectId],
    queryFn: () => cockpitApi.listChapters(projectId),
    enabled: !!projectId,
  })

  const currentChapter =
    cockpit?.current_chapter ??
    chapters?.find((c) => c.status === 'in_progress') ??
    chapters?.[0] ??
    null

  // ===== 当前章节正文版本 =====
  const { data: version } = useQuery({
    queryKey: ['chapter-version', projectId, currentChapter?.id],
    queryFn: () => cockpitApi.getChapterVersion(projectId, currentChapter!.id),
    enabled: !!projectId && !!currentChapter?.id,
  })

  // ===== SSE 实时流 =====
  const stream = useCockpitStream(projectId, cockpit?.agent_statuses)

  // SSE 事件触发数据刷新
  useEffect(() => {
    if (stream.lastEvent?.event === 'agent_complete' || stream.lastEvent?.event === 'review_needed') {
      refetchCockpit()
      queryClient.invalidateQueries({ queryKey: ['chapters', projectId] })
      queryClient.invalidateQueries({ queryKey: ['chapter-version', projectId] })
    }
  }, [stream.lastEvent, refetchCockpit, queryClient, projectId])

  // ===== 审阅队列（当前章节） =====
  const { data: reviewItems } = useQuery({
    queryKey: ['review-queue-pending', projectId],
    queryFn: () => reviewQueueApi.list(projectId, { status: 'pending' }),
    enabled: !!projectId,
  })

  // ===== Pipeline 操作 =====
  const bibleMutation = useMutation({
    mutationFn: () =>
      pipelineApi.generateBible(projectId, {
        title: project?.title,
        genre: project?.genre,
        themes: project?.config?.themes,
        setting: project?.description ?? project?.synopsis,
        tone: project?.config?.tone,
        target_chapters: project?.target_chapters ?? project?.config?.target_chapters,
      }),
    onSuccess: () => {
      refetchCockpit()
      queryClient.invalidateQueries({ queryKey: ['chapters', projectId] })
    },
  })

  const outlineMutation = useMutation({
    mutationFn: () =>
      pipelineApi.generateOutline(projectId, {
        volume_count: 3,
        chapters_per_volume: Math.ceil(
          (project?.target_chapters ?? project?.config?.target_chapters ?? 20) / 3,
        ),
      }),
    onSuccess: () => {
      refetchCockpit()
      queryClient.invalidateQueries({ queryKey: ['chapters', projectId] })
    },
  })

  const runMutation = useMutation({
    mutationFn: () =>
      pipelineApi.run(projectId, {
        target_chapters: project?.target_chapters ?? project?.config?.target_chapters ?? 1,
        mode: 'auto',
      }),
    onSuccess: () => refetchCockpit(),
  })

  const resumeMutation = useMutation({
    mutationFn: () => pipelineApi.resumeSession(projectId),
    onSuccess: () => refetchCockpit(),
  })

  const takeoverMutation = useMutation({
    mutationFn: () => cockpitApi.takeover(projectId),
    onSuccess: () => refetchCockpit(),
  })

  // ===== 合并 Agent 状态（SSE 实时 + 初始数据） =====
  const mergedAgentStatuses = useMemo(() => {
    const map: Record<string, AgentStatus> = {}
    // 先填入初始数据
    cockpit?.agent_statuses?.forEach((s) => {
      map[s.agent_role] = s
    })
    // SSE 实时覆盖
    Object.values(stream.agentStatuses).forEach((s) => {
      map[s.agent_role] = s
    })
    return ALL_AGENT_ROLES.map((role) =>
      map[role] ?? { agent_role: role, status: 'idle' as const },
    )
  }, [cockpit?.agent_statuses, stream.agentStatuses])

  // 是否有 Agent 正在工作
  const anyWorking = mergedAgentStatuses.some((s) => s.status === 'working')
  const drafterWorking =
    stream.agentStatuses[AgentRole.Drafter]?.status === 'working'

  // 稿件显示内容：流式输出 > 已保存版本
  const displayContent = drafterWorking && stream.streamingContent
    ? stream.streamingContent
    : version?.content ?? ''

  // 标记问题段落（当前章节有审阅项时）
  const chapterReviewItems = useMemo(
    () => reviewItems?.filter((r) => r.chapter_id === currentChapter?.id) ?? [],
    [reviewItems, currentChapter?.id],
  )

  const providerStatus =
    cockpit?.agent_statuses?.some((s) => s.status === 'error') ? 'degraded' : 'online'

  return (
    <AppShell>
      <TopBar
        currentChapter={currentChapter?.chapter_number}
        providerStatus={providerStatus as 'online' | 'offline' | 'degraded'}
      />

      <AppShellBody className="relative">
        {/* ============ Left Dock —— AI 团队 ============ */}
        <AgentDock
          open={leftOpen}
          onToggle={() => setLeftOpen((v) => !v)}
          agents={mergedAgentStatuses}
          connected={stream.connected}
          chapters={chapters}
          currentChapterId={currentChapter?.id}
        />

        {/* ============ Manuscript Desk —— 稿件主角 ============ */}
        <main className="manuscript-desk flex min-w-0 flex-1 flex-col">
          {/* 操作工具栏 */}
          <div className="flex shrink-0 items-center gap-1.5 border-b border-ink-700 bg-ink-900/50 px-4 py-1.5">
            <ToolbarButton
              icon={<BookOpen size={13} />}
              label="生成世界观"
              onClick={() => bibleMutation.mutate()}
              loading={bibleMutation.isPending}
            />
            <ToolbarButton
              icon={<ListTree size={13} />}
              label="生成大纲"
              onClick={() => outlineMutation.mutate()}
              loading={outlineMutation.isPending}
            />
            <ToolbarButton
              icon={<Play size={13} />}
              label="开始写作"
              onClick={() => runMutation.mutate()}
              loading={runMutation.isPending}
              variant="primary"
            />
            <ToolbarButton
              icon={<RotateCcw size={13} />}
              label="恢复"
              onClick={() => resumeMutation.mutate()}
              loading={resumeMutation.isPending}
            />
            <ToolbarButton
              icon={<Pause size={13} />}
              label="接管"
              onClick={() => takeoverMutation.mutate()}
              loading={takeoverMutation.isPending}
            />

            <div className="ml-auto flex items-center gap-2 text-xs text-gray-500">
              {anyWorking && (
                <span className="flex items-center gap-1 text-blue-400">
                  <Loader2 size={12} className="animate-spin" />
                  智能体工作中…
                </span>
              )}
              {stream.connected ? (
                <span className="flex items-center gap-1 text-green-400">
                  <Wifi size={12} />
                  实时连接
                </span>
              ) : (
                <span className="flex items-center gap-1 text-gray-600">
                  <WifiOff size={12} />
                  未连接
                </span>
              )}
            </div>
          </div>

          {/* 正文区域 */}
          <div className="min-h-0 flex-1 overflow-y-auto">
            {currentChapter ? (
              <article className="mx-auto px-8 py-12">
                {/* 章节标题 */}
                <header className="mx-auto mb-10 max-w-manuscript text-center">
                  <p className="mb-2 text-xs uppercase tracking-widest text-gray-600">
                    第 {currentChapter.chapter_number} 章
                  </p>
                  <h1 className="font-serif text-2xl font-semibold text-gray-100">
                    {currentChapter.title}
                  </h1>
                  <div className="mt-3 flex items-center justify-center gap-3 text-xs text-gray-500">
                    <Badge
                      variant={
                        CHAPTER_STATUS_MAP[currentChapter.status]?.color as 'gray' | 'blue' | 'green'
                      }
                    >
                      {CHAPTER_STATUS_MAP[currentChapter.status]?.label}
                    </Badge>
                    <span>{displayContent.length} 字</span>
                    <span>·</span>
                    <span>目标 {currentChapter.target_words ?? 3000} 字</span>
                    {chapterReviewItems.length > 0 && (
                      <>
                        <span>·</span>
                        <span className="text-amber-400">
                          {chapterReviewItems.length} 项待审阅
                        </span>
                      </>
                    )}
                  </div>
                  {drafterWorking && (
                    <div className="mt-2 flex items-center justify-center gap-1.5 text-xs text-blue-400">
                      <Loader2 size={11} className="animate-spin" />
                      起草者正在生成正文…
                    </div>
                  )}
                </header>

                {/* 正文（纸质书排版） */}
                <div className="manuscript-text">
                  {displayContent ? (
                    renderManuscript(displayContent, chapterReviewItems)
                  ) : (
                    <p className="text-gray-500">
                      本章尚未开始创作。点击上方「开始写作」或通过下方指令栏让智能体起草。
                    </p>
                  )}
                  {drafterWorking && (
                    <span className="inline-block h-4 w-0.5 animate-pulse bg-gold-500 align-middle" />
                  )}
                </div>
              </article>
            ) : (
              <EmptyState
                icon={<ScrollText size={28} />}
                title="尚无章节"
                description="点击「生成世界观」和「生成大纲」构建故事骨架，或通过指令栏让故事架构师开始。"
                className="h-full"
              />
            )}
          </div>
        </main>

        {/* ============ Context Lens —— 上下文透镜 ============ */}
        <ContextLens
          open={rightOpen}
          onToggle={() => setRightOpen((v) => !v)}
          projectId={projectId}
          currentChapter={currentChapter}
          reviewItems={chapterReviewItems}
        />

        {/* 浮动展开按钮（面板收起时显示） */}
        {!leftOpen && (
          <button
            onClick={() => setLeftOpen(true)}
            className="absolute left-2 top-3 z-20 flex h-8 w-8 items-center justify-center rounded-md border border-ink-700 bg-ink-850/80 text-gray-400 backdrop-blur hover:text-gray-200"
            title="展开 AI 团队"
          >
            <PanelLeft size={16} />
          </button>
        )}
        {!rightOpen && (
          <button
            onClick={() => setRightOpen(true)}
            className="absolute right-2 top-3 z-20 flex h-8 w-8 items-center justify-center rounded-md border border-ink-700 bg-ink-850/80 text-gray-400 backdrop-blur hover:text-gray-200"
            title="展开上下文透镜"
          >
            <PanelRight size={16} />
          </button>
        )}
      </AppShellBody>

      <BossCommandBar />
    </AppShell>
  )
}

/* ============================================================
 * Toolbar Button
 * ============================================================ */
function ToolbarButton({
  icon,
  label,
  onClick,
  loading,
  variant = 'ghost',
}: {
  icon: React.ReactNode
  label: string
  onClick: () => void
  loading?: boolean
  variant?: 'ghost' | 'primary'
}) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className={cn(
        'flex h-7 items-center gap-1 rounded-md px-2.5 text-xs font-medium transition-colors',
        variant === 'primary'
          ? 'bg-gold-500 text-ink-950 hover:bg-gold-400'
          : 'text-gray-300 hover:bg-ink-700',
        'disabled:cursor-not-allowed disabled:opacity-40',
      )}
    >
      {loading ? <Loader2 size={12} className="animate-spin" /> : icon}
      <span className="hidden sm:inline">{label}</span>
    </button>
  )
}

/* ============================================================
 * Agent Dock —— 左侧 AI 团队状态面板
 * ============================================================ */
function AgentDock({
  open,
  onToggle,
  agents,
  connected,
  chapters,
  currentChapterId,
}: {
  open: boolean
  onToggle: () => void
  agents: AgentStatus[]
  connected: boolean
  chapters?: Chapter[]
  currentChapterId?: string
}) {
  return (
    <aside
      className={cn(
        'transition-panel h-full shrink-0 overflow-hidden border-r border-ink-700 bg-ink-900',
        open ? 'w-60' : 'w-0',
      )}
    >
      <div className="flex h-full w-60 flex-col">
        {/* 头部 */}
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-ink-700 px-3">
          <span className="flex items-center gap-1.5 text-xs font-medium text-gray-300">
            <Users size={14} />
            AI 团队
          </span>
          <div className="flex items-center gap-2">
            <Circle
              size={7}
              className={cn('fill-current', connected ? 'text-green-400' : 'text-gray-600')}
            />
            <button onClick={onToggle} className="text-gray-500 hover:text-gray-300">
              <PanelLeft size={15} />
            </button>
          </div>
        </div>

        {/* Agent 列表 */}
        <div className="flex-1 overflow-y-auto py-2">
          {agents.map((agent) => (
            <AgentRow key={agent.agent_role} agent={agent} />
          ))}
        </div>

        {/* 章节快速导航 */}
        <div className="shrink-0 border-t border-ink-700">
          <div className="px-3 py-1.5 text-xs font-medium text-gray-500">章节导航</div>
          <div className="max-h-32 overflow-y-auto pb-2">
            {chapters && chapters.length > 0 ? (
              chapters.map((c) => (
                <button
                  key={c.id}
                  className={cn(
                    'flex w-full items-center gap-2 px-3 py-1 text-left text-sm transition-colors',
                    c.id === currentChapterId
                      ? 'bg-ink-800 text-gold-400'
                      : 'text-gray-400 hover:bg-ink-800 hover:text-gray-200',
                  )}
                >
                  <ChevronRight size={13} className="text-gray-600" />
                  <span className="text-xs text-gray-600">{c.chapter_number}</span>
                  <span className="truncate">{c.title}</span>
                </button>
              ))
            ) : (
              <p className="px-3 py-2 text-xs text-gray-600">暂无章节</p>
            )}
          </div>
        </div>

        {/* 工具入口 */}
        <div className="shrink-0 border-t border-ink-700 p-2">
          <DockToolItem icon={<GitBranch size={14} />} label="生命线" to="/storyline" />
          <DockToolItem icon={<Sparkles size={14} />} label="大脑" to="/brain" />
        </div>
      </div>
    </aside>
  )
}

function AgentRow({ agent }: { agent: AgentStatus }) {
  const statusConfig = {
    idle: { color: 'text-gray-500', bg: 'bg-gray-600/20', label: '空闲' },
    working: { color: 'text-blue-400', bg: 'bg-blue-600/20', label: '工作中' },
    error: { color: 'text-red-400', bg: 'bg-red-600/20', label: '异常' },
  }
  const cfg = statusConfig[agent.status]

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 hover:bg-ink-800/50">
      <span
        className={cn(
          'flex h-6 w-6 shrink-0 items-center justify-center rounded text-[10px] font-medium',
          cfg.bg,
          cfg.color,
        )}
      >
        {agent.status === 'working' ? (
          <Loader2 size={11} className="animate-spin" />
        ) : agent.status === 'error' ? (
          <AlertTriangle size={11} />
        ) : (
          <Circle size={6} className="fill-current" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between">
          <span className="truncate text-xs font-medium text-gray-300">
            {AGENT_ROLES[agent.agent_role]}
          </span>
          <span className={cn('text-[10px]', cfg.color)}>{cfg.label}</span>
        </div>
        {agent.message && (
          <p className="truncate text-[10px] text-gray-600">{agent.message}</p>
        )}
      </div>
    </div>
  )
}

function DockToolItem({ icon, label, to }: { icon: React.ReactNode; label: string; to: string }) {
  return (
    <a
      href={to}
      className="flex items-center gap-2 rounded px-2 py-1.5 text-xs text-gray-400 hover:bg-ink-800 hover:text-gray-200"
    >
      {icon}
      {label}
    </a>
  )
}

/* ============================================================
 * Context Lens —— 右侧上下文透镜
 * ============================================================ */
function ContextLens({
  open,
  onToggle,
  projectId,
  currentChapter,
  reviewItems,
}: {
  open: boolean
  onToggle: () => void
  projectId: string
  currentChapter: Chapter | null
  reviewItems: ReviewQueueItem[]
}) {
  const { data: brain } = useQuery({
    queryKey: ['brain-overview', projectId],
    queryFn: () => brainApi.get(projectId),
    enabled: !!projectId && open,
  })

  const { data: canonFacts } = useQuery({
    queryKey: ['canon-facts-lens', projectId],
    queryFn: () => canonFactsApi.list(projectId),
    enabled: !!projectId && open,
  })

  const characters = brain?.characters ?? []
  const summaries = brain?.summaries ?? []
  const currentState = brain?.current_state

  return (
    <aside
      className={cn(
        'transition-panel h-full shrink-0 overflow-hidden border-l border-ink-700 bg-ink-900',
        open ? 'w-72' : 'w-0',
      )}
    >
      <div className="flex h-full w-72 flex-col">
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-ink-700 px-3">
          <span className="flex items-center gap-1.5 text-xs font-medium text-gray-300">
            <Sparkles size={14} />
            上下文透镜
          </span>
          <button onClick={onToggle} className="text-gray-500 hover:text-gray-300">
            <PanelRight size={15} />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
          {/* 当前状态 */}
          {currentState && (
            <LensSection title="当前状态">
              <div className="space-y-1 text-xs text-gray-500">
                {currentState.location && (
                  <div>
                    <span className="text-gray-600">地点:</span> {currentState.location}
                  </div>
                )}
                {currentState.time_of_day && (
                  <div>
                    <span className="text-gray-600">时间:</span> {currentState.time_of_day}
                  </div>
                )}
                {currentState.mood && (
                  <div>
                    <span className="text-gray-600">氛围:</span> {currentState.mood}
                  </div>
                )}
                {currentState.last_events && currentState.last_events.length > 0 && (
                  <div>
                    <span className="text-gray-600">最近事件:</span>
                    <ul className="mt-0.5 list-inside list-disc">
                      {currentState.last_events.slice(0, 3).map((e, i) => (
                        <li key={i}>{e}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </LensSection>
          )}

          {/* 在场角色 */}
          <LensSection title="角色">
            {characters.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {characters.slice(0, 8).map((c) => (
                  <Badge key={c.id} variant={c.role === '主角' ? 'gold' : 'outline'}>
                    {c.name}
                  </Badge>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-600">暂无角色数据</p>
            )}
          </LensSection>

          {/* 前文摘要 */}
          <LensSection title="前文摘要">
            {summaries.length > 0 ? (
              <p className="text-xs leading-relaxed text-gray-500">
                {summaries[summaries.length - 1]?.summary ?? '暂无摘要'}
              </p>
            ) : (
              <p className="text-xs text-gray-600">暂无前文摘要</p>
            )}
          </LensSection>

          {/* 设定事实 */}
          <LensSection title="设定事实">
            {canonFacts && canonFacts.length > 0 ? (
              <div className="space-y-1.5">
                {canonFacts.slice(0, 5).map((f) => (
                  <div key={f.id} className="text-xs">
                    <span className="text-gray-300">{f.subject_name || f.subject_id}</span>
                    <span className="text-gray-600"> → </span>
                    <span className="text-gray-400">{f.object_value}</span>
                  </div>
                ))}
                {canonFacts.length > 5 && (
                  <p className="text-[10px] text-gray-600">还有 {canonFacts.length - 5} 条…</p>
                )}
              </div>
            ) : (
              <p className="text-xs text-gray-600">暂无设定事实</p>
            )}
          </LensSection>

          {/* 待审阅项 */}
          {reviewItems.length > 0 && (
            <LensSection title="待审阅">
              <div className="space-y-1.5">
                {reviewItems.map((r) => (
                  <div
                    key={r.id}
                    className="rounded border-l-2 border-amber-500 bg-amber-500/5 px-2 py-1 text-xs"
                  >
                    <div className="flex items-center gap-1.5">
                      <Badge
                        variant={
                          r.severity === 'critical' ? 'red' : r.severity === 'warning' ? 'amber' : 'gray'
                        }
                      >
                        {r.type}
                      </Badge>
                      <span className="text-gray-400">{r.title}</span>
                    </div>
                    {r.description && (
                      <p className="mt-0.5 text-[10px] text-gray-600">{r.description}</p>
                    )}
                  </div>
                ))}
              </div>
            </LensSection>
          )}
        </div>
      </div>
    </aside>
  )
}

function LensSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="mb-2 text-xs font-medium text-gray-400">{title}</h4>
      <div className="rounded-md border border-ink-700 bg-ink-850 p-2.5">{children}</div>
    </div>
  )
}

/* ============================================================
 * 稿件渲染 —— 按段落渲染，问题段落用琥珀色竖线标记
 * ============================================================ */
function renderManuscript(content: string, reviewItems: ReviewQueueItem[] = []) {
  const paragraphs = content.split(/\n+/).filter(Boolean)

  // 简单策略：如果有审阅项，标记前几段为问题段落（实际应根据后端段落级标注）
  const flaggedIndices = new Set<number>()
  if (reviewItems.length > 0) {
    // 将审阅项映射到段落（简化：均匀分布）
    reviewItems.forEach((_, i) => {
      const idx = Math.floor((i / reviewItems.length) * paragraphs.length)
      flaggedIndices.add(idx)
    })
  }

  return paragraphs.map((p, i) => {
    const isFlagged = flaggedIndices.has(i)
    return (
      <p
        key={i}
        className={isFlagged ? 'border-l-2 border-amber-500 pl-3' : undefined}
        style={isFlagged ? { textIndent: '1.5em' } : undefined}
      >
        {p}
      </p>
    )
  })
}
