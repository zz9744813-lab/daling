import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Beaker,
  BookOpenCheck,
  BrainCircuit,
  CheckCircle2,
  Clock3,
  FileDiff,
  FlaskConical,
  GitBranch,
  Lightbulb,
  Loader2,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Trophy,
  Wand2,
} from 'lucide-react'
import { bookMemoryApi, evolutionApi, governanceApi } from '../api/client'
import { Badge } from '../components/Badge'
import { Button, Card, TextArea } from '../components/ui'
import { AppShell, AppShellBody } from '../layout/AppShell'
import { BossCommandBar } from '../layout/BossCommandBar'
import { TopBar } from '../layout/TopBar'
import { cn } from '../lib/cn'
import { useProjectStore } from '../store/projectStore'
import type {
  EvolutionOverview,
  BookMemory,
  LearningCycleView,
  LearningReport,
  PromptExperimentResult,
  PromptVersionView,
  SkillTestResult,
} from '../types'

type TabKey = 'overview' | 'prompts' | 'skills'

const SKILL_OPTIONS = [
  ['drafting', '正文起草'],
  ['continuity_check', '连续性检查'],
  ['character_extraction', '角色抽取'],
  ['summary_generation', '章节摘要'],
  ['style_analysis', '文风分析'],
  ['plot_planning', '情节规划'],
] as const

export default function EvolutionPage() {
  const project = useProjectStore((state) => state.currentProject)
  const projectId = project?.id ?? ''
  const [tab, setTab] = useState<TabKey>('overview')
  const providers = useQuery({
    queryKey: ['providers'],
    queryFn: governanceApi.listProviders,
  })
  const providerStatus = providers.isLoading || providers.isError
    ? 'unknown'
    : providers.data?.some((provider) => provider.status !== 'inactive')
      ? 'online'
      : 'offline'

  return (
    <AppShell>
      <TopBar providerStatus={providerStatus} />
      <AppShellBody className="flex-col overflow-hidden">
        <header className="shrink-0 border-b border-ink-700 bg-ink-950/80 px-4 py-4 sm:px-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-violet-400/20 bg-violet-400/10 text-violet-300">
                  <BrainCircuit size={17} />
                </span>
                <div>
                  <h1 className="text-base font-semibold text-gray-100">学习与演化中心</h1>
                  <p className="mt-0.5 text-[11px] text-gray-500">
                    从质量证据和人工反馈中学习；候选、评测、晋升与回滚全程留痕
                  </p>
                </div>
              </div>
            </div>
            <span className="rounded-full border border-emerald-400/20 bg-emerald-400/8 px-2.5 py-1 text-[10px] font-medium text-emerald-200">
              {project?.title ?? '未选择项目'}
            </span>
          </div>
          <nav className="mt-4 flex gap-1 rounded-xl bg-ink-900/80 p-1 sm:w-fit" role="tablist" aria-label="学习中心视图">
            <TabButton active={tab === 'overview'} onClick={() => setTab('overview')}>
              <Activity size={13} /> 学习总览
            </TabButton>
            <TabButton active={tab === 'prompts'} onClick={() => setTab('prompts')}>
              <Wand2 size={13} /> Prompt 实验
            </TabButton>
            <TabButton active={tab === 'skills'} onClick={() => setTab('skills')}>
              <FlaskConical size={13} /> 技能评测
            </TabButton>
          </nav>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto bg-ink-950 px-4 py-5 sm:px-6">
          {!projectId ? (
            <div className="mx-auto mt-20 max-w-md text-center text-sm text-gray-500">
              请先选择一个项目，再查看该作品的学习证据。
            </div>
          ) : tab === 'overview' ? (
            <LearningOverview projectId={projectId} />
          ) : tab === 'prompts' ? (
            <PromptExperiment projectId={projectId} />
          ) : (
            <SkillExperiment projectId={projectId} />
          )}
        </main>
      </AppShellBody>
      <BossCommandBar />
    </AppShell>
  )
}

function LearningOverview({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const overview = useQuery({
    queryKey: ['evolution-overview', projectId],
    queryFn: () => evolutionApi.get(projectId),
    refetchInterval: 20_000,
  })
  const rollback = useMutation({
    mutationFn: (versionId: string) => evolutionApi.rollbackPromptVersion(projectId, versionId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['evolution-overview', projectId] }),
  })
  const promote = useMutation({
    mutationFn: (versionId: string) => evolutionApi.promotePromptVersion(projectId, versionId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['evolution-overview', projectId] }),
  })
  const evaluateHoldout = useMutation({
    mutationFn: ({ versionId, force }: { versionId: string; force: boolean }) =>
      evolutionApi.evaluatePromptVersion(projectId, versionId, force),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['evolution-overview', projectId] }),
  })
  const memoryAction = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approve' | 'reject' | 'rollback' }) => {
      if (action === 'approve') return bookMemoryApi.approve(projectId, id, '学习中心人工批准')
      if (action === 'reject') return bookMemoryApi.reject(projectId, id, '学习中心人工驳回')
      return bookMemoryApi.rollback(projectId, id, '学习中心人工回滚')
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evolution-overview', projectId] })
      queryClient.invalidateQueries({ queryKey: ['book-memory', projectId] })
    },
  })

  if (overview.isLoading) return <Loading label="正在读取结构化学习账本…" />
  if (overview.isError) {
    return (
      <ErrorPanel
        message={(overview.error as Error).message}
        onRetry={() => overview.refetch()}
      />
    )
  }
  if (!overview.data) return null

  const data = overview.data
  const report = data.quality_report
  const trend = report?.avg_score_trend ?? []
  const latestScore = trend.length ? trend[trend.length - 1].score : undefined
  const averageScore = trend.length
    ? trend.reduce((sum, point) => sum + point.score, 0) / trend.length
    : null
  const pendingCandidates = data.prompt_versions.filter((version) => version.status === 'candidate')
  const activeMemoryCount = data.memory_status_counts?.active
    ?? data.memory_entries.filter((memory) => memory.status === 'active' || memory.status === 'approved').length

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Metric icon={<BrainCircuit size={15} />} label="学习周期" value={`${data.learning_cycles.length}`} detail="可审计循环" tone="violet" />
        <Metric icon={<BookOpenCheck size={15} />} label="长期记忆" value={`${activeMemoryCount}/${data.memory_count}`} detail="生效 / 全部留痕" tone="green" />
        <Metric icon={<BarChart3 size={15} />} label="近期质量" value={latestScore == null ? '—' : `${latestScore.toFixed(1)}`} detail={averageScore == null ? '暂无章节证据' : `均值 ${averageScore.toFixed(1)}`} tone="blue" />
        <Metric icon={<GitBranch size={15} />} label="Prompt 候选" value={`${pendingCandidates.length}`} detail="未经 holdout 不晋升" tone="amber" />
        <Metric icon={<FileDiff size={15} />} label="待处理反馈" value={`${data.pending_feedback_count}`} detail="下一周期消费" tone={data.pending_feedback_count ? 'amber' : 'gray'} />
      </section>

      {pendingCandidates.length > 0 && (
        <div className="flex items-start gap-3 rounded-2xl border border-amber-400/20 bg-amber-400/[0.045] p-4">
          <ShieldCheck size={16} className="mt-0.5 shrink-0 text-amber-300" />
          <div>
            <p className="text-xs font-semibold text-amber-100">有 {pendingCandidates.length} 个 Prompt 候选等待 holdout</p>
            <p className="mt-1 text-[11px] leading-5 text-gray-500">系统已经从重复问题中形成候选，但不会把“生成了候选”冒充“能力已提升”。只有质量提升 ≥3 分且硬约束不退化才允许晋升。</p>
          </div>
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[1.25fr_0.75fr]">
        <QualityPanel report={report} />
        <IssuePanel report={report} />
      </div>

      <div className="grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
        <CycleTimeline cycles={data.learning_cycles} />
          <PromptRegistry
            versions={data.prompt_versions}
            busy={rollback.isPending || promote.isPending || evaluateHoldout.isPending}
            error={(rollback.error || promote.error || evaluateHoldout.error) as Error | null}
            evaluatingVersionId={evaluateHoldout.variables?.versionId}
            onEvaluate={(id, force) => evaluateHoldout.mutate({ versionId: id, force })}
            onRollback={(id) => rollback.mutate(id)}
          onPromote={(id) => promote.mutate(id)}
        />
      </div>

      <MemoryRegistry
        memories={data.memory_entries ?? []}
        busy={memoryAction.isPending}
        error={memoryAction.error as Error | null}
        activeId={memoryAction.variables?.id}
        onAction={(id, action) => memoryAction.mutate({ id, action })}
      />

      <ReflectionPanel data={data} />
    </div>
  )
}

function MemoryRegistry({
  memories,
  busy,
  error,
  activeId,
  onAction,
}: {
  memories: BookMemory[]
  busy: boolean
  error: Error | null
  activeId?: string
  onAction: (id: string, action: 'approve' | 'reject' | 'rollback') => void
}) {
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all')
  const statusCounts = useMemo(() => {
    const active = memories.filter((memory) => memory.status === 'active' || memory.status === 'approved').length
    return { active, inactive: memories.length - active }
  }, [memories])
  const filteredMemories = useMemo(
    () => memories.filter((memory) => {
      const active = memory.status === 'active' || memory.status === 'approved'
      return statusFilter === 'all' || (statusFilter === 'active' ? active : !active)
    }),
    [memories, statusFilter],
  )

  return (
    <Card className="overflow-hidden bg-ink-900/75 p-0">
      <PanelHeader
        icon={<BookOpenCheck size={15} />}
        title="长期记忆治理"
        subtitle="自动学习规则可审阅、批准、驳回或回滚；停用后立即退出生产上下文"
      />
      {error && (
        <p className="mx-4 mt-4 rounded-lg border border-red-400/20 bg-red-400/8 p-2 text-[11px] text-red-200">
          {error.message}
        </p>
      )}
      {memories.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-ink-700 px-4 py-3 sm:px-5">
          {([
            ['all', `全部 ${memories.length}`],
            ['active', `生效 ${statusCounts.active}`],
            ['inactive', `停用 ${statusCounts.inactive}`],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setStatusFilter(value)}
              className={cn(
                'rounded-lg border px-2.5 py-1.5 text-[10px] font-medium transition-colors',
                statusFilter === value
                  ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200'
                  : 'border-ink-700 bg-ink-950/45 text-gray-500 hover:text-gray-300',
              )}
            >
              {label}
            </button>
          ))}
          <span className="ml-auto text-[9px] text-gray-600">展示最近 {memories.length} 条审计记录</span>
        </div>
      )}
      <div className="grid max-h-[520px] gap-3 overflow-y-auto p-4 sm:grid-cols-2 sm:p-5 xl:grid-cols-3">
        {memories.length === 0 ? (
          <div className="sm:col-span-2 xl:col-span-3">
            <EmptyInline title="尚无长期记忆" description="质量证据或人工反馈形成规则后会在此留痕。" />
          </div>
        ) : filteredMemories.length === 0 ? (
          <div className="sm:col-span-2 xl:col-span-3">
            <EmptyInline title="当前筛选没有记忆" description="切换到全部或另一状态继续查看。" />
          </div>
        ) : (
          filteredMemories.map((memory) => {
            const isActive = memory.status === 'active' || memory.status === 'approved'
            const working = busy && activeId === memory.id
            return (
              <article key={memory.id} className="rounded-xl border border-ink-700 bg-ink-950/45 p-3">
                <div className="flex items-center gap-2">
                  <Badge variant="outline" className="text-[9px]">{memory.memory_type}</Badge>
                  <span className="min-w-0 flex-1 truncate text-xs font-medium text-gray-300">{memory.key}</span>
                  <StatusBadge status={memory.status} />
                </div>
                <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-[10px] leading-5 text-gray-500">
                  {memoryText(memory.value)}
                </p>
                <div className="mt-2 space-y-1 text-[9px] text-gray-600">
                  <p>来源：{memory.source || memory.governance?.origin || '未记录'}</p>
                  <p>置信度：{Math.round((memory.confidence ?? 0) * 100)}%</p>
                  {memory.governance?.reviewed_at && (
                    <p>最近治理：{memory.governance.reviewed_by || 'system'} · {formatTime(memory.governance.reviewed_at)}</p>
                  )}
                  {memory.governance?.reason && <p className="line-clamp-2">原因：{memory.governance.reason}</p>}
                  {(memory.governance?.history?.length ?? 0) > 0 && (
                    <p>审计动作：{memory.governance.history?.length} 次</p>
                  )}
                </div>
                <div className="mt-3 flex flex-wrap gap-2 border-t border-ink-700/70 pt-3">
                  {!isActive && (
                    <Button size="sm" variant="secondary" disabled={busy} onClick={() => onAction(memory.id, 'approve')}>
                      {working ? <Loader2 size={11} className="animate-spin" /> : <CheckCircle2 size={11} />} 批准恢复
                    </Button>
                  )}
                  {isActive && (
                    <>
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAction(memory.id, 'rollback')}>
                        {working ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />} 回滚停用
                      </Button>
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAction(memory.id, 'reject')}>
                        <AlertTriangle size={11} /> 驳回
                      </Button>
                    </>
                  )}
                </div>
              </article>
            )
          })
        )}
      </div>
    </Card>
  )
}

function memoryText(value: Record<string, unknown>) {
  const instruction = value.instruction ?? value.text ?? value.overall_summary
  if (instruction) return String(instruction)
  return Object.entries(value)
    .slice(0, 8)
    .map(([key, item]) => `${key}: ${typeof item === 'string' ? item : JSON.stringify(item)}`)
    .join('；')
}

function QualityPanel({ report }: { report?: LearningReport }) {
  const trend = report?.avg_score_trend ?? []
  return (
    <Card className="overflow-hidden bg-ink-900/75 p-0">
      <PanelHeader icon={<BarChart3 size={15} />} title="章节质量趋势" subtitle="来自 ChiefEditor / Critic 结构化评估，不解析日志猜分" />
      <div className="p-4 sm:p-5">
        {trend.length === 0 ? (
          <EmptyInline title="还没有质量评估" description="完成第一章完整流水线后会显示真实得分。" />
        ) : (
          <div className="flex h-48 items-end gap-2 overflow-x-auto pb-1">
            {trend.slice(-24).map((point) => (
              <div key={`${point.chapter_no}-${point.assessment_id ?? point.verdict}`} className="flex h-full min-w-8 flex-1 flex-col items-center justify-end" title={`第 ${point.chapter_no} 章 · ${point.score.toFixed(1)} 分`}>
                <span className="mb-1 text-[9px] font-medium text-gray-500">{point.score.toFixed(0)}</span>
                <div className="flex h-36 w-full items-end rounded-lg bg-ink-950/60 p-1">
                  <div
                    className={cn(
                      'w-full rounded-md transition-[height]',
                      point.score >= 85 ? 'bg-emerald-400/70' : point.score >= 70 ? 'bg-amber-400/70' : 'bg-red-400/70',
                    )}
                    style={{ height: `${Math.max(4, point.score)}%` }}
                  />
                </div>
                <span className="mt-1 text-[9px] text-gray-600">{point.chapter_no}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  )
}

function IssuePanel({ report }: { report?: LearningReport }) {
  const issues = report?.common_issues ?? []
  return (
    <Card className="overflow-hidden bg-ink-900/75 p-0">
      <PanelHeader icon={<AlertTriangle size={15} />} title="重复问题模式" subtitle="按来源、类别和处理状态聚合" />
      <div className="space-y-2 p-4 sm:p-5">
        {issues.length === 0 ? (
          <EmptyInline title="没有重复问题" description="质量账本尚未发现可学习模式。" />
        ) : (
          issues.slice(0, 8).map((issue) => (
            <div key={`${issue.source}-${issue.issue_type}`} className="rounded-xl border border-ink-700 bg-ink-950/45 p-3">
              <div className="flex items-center gap-2">
                <span className="truncate text-xs font-medium text-gray-300">{issue.issue_type}</span>
                <Badge variant={issue.severity === 'critical' || issue.severity === 'high' ? 'red' : 'amber'} className="ml-auto text-[9px]">
                  {issue.count} 次
                </Badge>
              </div>
              <div className="mt-2 flex justify-between text-[10px] text-gray-600">
                <span>{issue.source || 'quality'}</span>
                <span>未解决 {issue.open_count ?? 0}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </Card>
  )
}

function CycleTimeline({ cycles }: { cycles: LearningCycleView[] }) {
  return (
    <Card className="overflow-hidden bg-ink-900/75 p-0">
      <PanelHeader icon={<Activity size={15} />} title="学习周期" subtitle="观察 → 记忆 → 候选 → holdout / 回滚" />
      <div className="max-h-[430px] overflow-y-auto p-4 sm:p-5">
        {cycles.length === 0 ? (
          <EmptyInline title="尚无学习周期" description="批准章节后会自动创建 post-chapter 周期。" />
        ) : (
          <div className="space-y-2">
            {cycles.map((cycle) => (
              <article key={cycle.id} className="rounded-xl border border-ink-700 bg-ink-950/45 p-3">
                <div className="flex items-center gap-2">
                  {cycle.status === 'completed' ? <CheckCircle2 size={13} className="text-emerald-300" /> : <Clock3 size={13} className="text-amber-300" />}
                  <span className="text-xs font-medium text-gray-300">{cycle.status === 'completed' ? '学习完成' : cycle.status}</span>
                  <time className="ml-auto text-[9px] text-gray-600">{formatTime(cycle.completed_at || cycle.created_at)}</time>
                </div>
                <div className="mt-3 grid grid-cols-4 gap-2 text-center">
                  <TinyStat label="评估" value={cycle.assessment_count} />
                  <TinyStat label="反馈" value={cycle.feedback_count} />
                  <TinyStat label="记忆" value={cycle.memory_count} />
                  <TinyStat label="候选" value={cycle.prompt_candidate_count} />
                </div>
                {(cycle.source_from || cycle.source_to) && (
                  <p className="mt-2 text-[9px] text-gray-600">证据窗口：{formatTime(cycle.source_from)} — {formatTime(cycle.source_to)}</p>
                )}
                <p className="mt-2 text-[10px] text-gray-600">决策：{decisionLabel(cycle.promotion_decision)}</p>
                {cycle.rollback_reason && <p className="mt-1 text-[10px] text-amber-300">回滚原因：{cycle.rollback_reason}</p>}
                {cycle.error && <p className="mt-1 line-clamp-2 text-[10px] text-red-300">异常：{cycle.error}</p>}
              </article>
            ))}
          </div>
        )}
      </div>
    </Card>
  )
}

function PromptRegistry({
  versions,
  busy,
  error,
  evaluatingVersionId,
  onEvaluate,
  onRollback,
  onPromote,
}: {
  versions: PromptVersionView[]
  busy: boolean
    error: Error | null
    evaluatingVersionId?: string
    onEvaluate: (id: string, force: boolean) => void
  onRollback: (id: string) => void
  onPromote: (id: string) => void
}) {
  return (
    <Card className="overflow-hidden bg-ink-900/75 p-0">
      <PanelHeader icon={<GitBranch size={15} />} title="Prompt 版本注册表" subtitle="候选、champion、评测指标与回滚入口" />
      {error && <p className="mx-4 mt-4 rounded-lg border border-red-400/20 bg-red-400/8 p-2 text-[11px] text-red-200">{error.message}</p>}
      <div className="max-h-[430px] space-y-2 overflow-y-auto p-4 sm:p-5">
        {versions.length === 0 ? (
          <EmptyInline title="尚无 Prompt 候选" description="重复质量问题达到证据门槛后会自动生成候选版本。" />
        ) : (
          versions.map((version) => {
              const metrics = version.evaluation_metrics ?? {}
              const holdoutStatus = String(metrics.holdout_status ?? 'pending')
              const holdoutPassed = holdoutStatus === 'passed' && metrics.gate_passed === true
              const hasEvaluation = ['passed', 'failed', 'error', 'stale'].includes(holdoutStatus)
            return (
              <article key={version.id} className="rounded-xl border border-ink-700 bg-ink-950/45 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[10px] font-bold text-violet-300">v{version.version_no}</span>
                  <span className="text-xs font-medium text-gray-300">{version.agent_role}</span>
                  <StatusBadge status={version.status} />
                  <time className="ml-auto text-[9px] text-gray-600">{formatTime(version.created_at)}</time>
                </div>
                <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-[10px] leading-5 text-gray-500">{version.template}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px] text-gray-600">
                    <span>
                      来源：{version.source?.type === 'autonomous_learning'
                        ? `自动学习周期 ${shortId(version.source.learning_cycle_id)}`
                        : '人工/导入'}
                    </span>
                    {typeof version.source?.evidence_count === 'number' && <span>证据 {version.source.evidence_count} 条</span>}
                    {version.parent_version_id && <span>父版本 {shortId(version.parent_version_id)}</span>}
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-ink-700/70 pt-3">
                    <span className="text-[9px] text-gray-600">holdout: {holdoutStatus}</span>
                    {typeof metrics.quality_gain === 'number' && <span className="text-[9px] text-gray-600">提升 {metrics.quality_gain.toFixed(1)} 分</span>}
                    {metrics.hard_constraint_regression === true && <span className="text-[9px] text-red-300">硬约束退化</span>}
                    {version.status === 'candidate' && (
                      <>
                        <Button size="sm" variant="secondary" disabled={busy} onClick={() => onEvaluate(version.id, hasEvaluation)} title="显式调用真实模型运行固定评测集">
                          {evaluatingVersionId === version.id ? <Loader2 size={11} className="animate-spin" /> : <Beaker size={11} />} {hasEvaluation ? '重新评测' : '运行 holdout'}
                        </Button>
                        <Button size="sm" variant="secondary" disabled={busy || !holdoutPassed} onClick={() => onPromote(version.id)} title={holdoutPassed ? '晋升为当前 champion' : '尚未通过 holdout'}>
                        <Trophy size={11} /> 晋升
                      </Button>
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => onRollback(version.id)}>
                        <RotateCcw size={11} /> 回滚
                      </Button>
                      </>
                    )}
                    {version.status === 'champion' && (
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => onRollback(version.id)} title="回滚并恢复上一合格 champion">
                        <RotateCcw size={11} /> 回滚 champion
                      </Button>
                    )}
                </div>
              </article>
            )
          })
        )}
      </div>
    </Card>
  )
}

function ReflectionPanel({ data }: { data: EvolutionOverview }) {
  return (
    <Card className="overflow-hidden bg-ink-900/75 p-0">
      <PanelHeader icon={<BookOpenCheck size={15} />} title="反思与下一步建议" subtitle={`${data.reflections_count} 条持久化反思`} />
      <div className="grid gap-4 p-4 sm:p-5 lg:grid-cols-2">
        <div className="space-y-2">
          <h3 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-600">最近反思</h3>
          {data.recent_reflections.length === 0 ? (
            <p className="text-xs text-gray-600">尚无反思记录。</p>
          ) : (
            data.recent_reflections.slice(0, 6).map((reflection) => (
              <article key={reflection.id} className="rounded-xl border border-ink-700 bg-ink-950/45 p-3">
                <div className="flex items-center gap-2 text-[10px] text-gray-600">
                  <span>{reflection.chapter_no ? `第 ${reflection.chapter_no} 章` : reflection.reflection_type}</span>
                  <time className="ml-auto">{formatTime(reflection.created_at)}</time>
                </div>
                <p className="mt-1 text-[11px] leading-5 text-gray-400">{reflection.content}</p>
              </article>
            ))
          )}
        </div>
        <div className="space-y-2">
          <h3 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-600">证据驱动建议</h3>
          {data.latest_suggestions.length === 0 ? (
            <p className="text-xs text-gray-600">暂无足够证据形成建议。</p>
          ) : (
            data.latest_suggestions.map((suggestion, index) => (
              <div key={`${suggestion}-${index}`} className="flex items-start gap-2 rounded-xl border border-gold-400/12 bg-gold-400/[0.035] p-3 text-[11px] leading-5 text-gray-400">
                <Lightbulb size={12} className="mt-1 shrink-0 text-gold-400" />
                {suggestion}
              </div>
            ))
          )}
        </div>
      </div>
    </Card>
  )
}

function PromptExperiment({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const [promptA, setPromptA] = useState('')
  const [promptB, setPromptB] = useState('')
  const [testInput, setTestInput] = useState('')
  const [result, setResult] = useState<PromptExperimentResult | null>(null)
  const mutation = useMutation({
    mutationFn: () => evolutionApi.promptExperiment(projectId, { prompt_a: promptA, prompt_b: promptB, test_input: testInput }),
    onSuccess: (next) => {
      setResult(next)
      void queryClient.invalidateQueries({ queryKey: ['evolution-overview', projectId] })
    },
  })
  const canRun = Boolean(promptA.trim() && promptB.trim() && testInput.trim())
  const scale = result?.score_scale || 40

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="rounded-2xl border border-blue-400/15 bg-blue-400/[0.035] p-4 text-[11px] leading-5 text-gray-400">
        <p className="flex items-center gap-2 font-medium text-blue-200"><Beaker size={13} /> 同输入、双 Prompt、独立 Judge</p>
        <p className="mt-1">实验结果会持久化，但不会直接覆盖生产 Prompt。正式晋升仍受 holdout 与硬约束回归门控制。</p>
      </div>
      <Card className="bg-ink-900/75 p-5">
        <div className="grid gap-4 lg:grid-cols-2">
          <PromptField label="Prompt A · 基线" value={promptA} onChange={setPromptA} />
          <PromptField label="Prompt B · 候选" value={promptB} onChange={setPromptB} />
        </div>
        <label className="mt-4 block text-xs font-medium text-gray-300">固定测试输入</label>
        <TextArea className="mt-2" rows={4} value={testInput} onChange={(event) => setTestInput(event.target.value)} placeholder="输入具有代表性的章节任务；A/B 会收到完全相同的输入。" />
        <div className="mt-4 flex items-center gap-3">
          <Button variant="primary" disabled={!canRun || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} 运行真实实验
          </Button>
          {mutation.isError && <span className="text-xs text-red-300">{(mutation.error as Error).message}</span>}
        </div>
      </Card>

      {result && (
        <Card className="overflow-hidden bg-ink-900/75 p-0">
          <PanelHeader icon={<Trophy size={15} />} title="实验结果" subtitle={`实验 ID ${result.experiment_id || '—'}`} />
          <div className="space-y-5 p-4 sm:p-5">
            <div className="grid gap-3 sm:grid-cols-2">
              <ExperimentScore label="Prompt A" score={result.scores?.a} scale={scale} winner={result.winner === 'A'} />
              <ExperimentScore label="Prompt B" score={result.scores?.b} scale={scale} winner={result.winner === 'B'} />
            </div>
            {result.judge_reasoning && <div className="rounded-xl border border-gold-400/12 bg-gold-400/[0.035] p-3 text-[11px] leading-5 text-gray-400"><span className="font-medium text-gold-300">Judge：</span>{result.judge_reasoning}</div>}
            <div className="grid gap-3 lg:grid-cols-2">
              <OutputPanel label="A 输出" content={result.output_a || result.response_a || ''} />
              <OutputPanel label="B 输出" content={result.output_b || result.response_b || ''} />
            </div>
          </div>
        </Card>
      )}
    </div>
  )
}

function SkillExperiment({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const [skill, setSkill] = useState<string>(SKILL_OPTIONS[0][0])
  const [cases, setCases] = useState('')
  const [result, setResult] = useState<SkillTestResult | null>(null)
  const parsedCases = useMemo(
    () => cases.split(/\n+/).map((line) => line.trim()).filter(Boolean).map((line) => {
      const [input, expected] = line.split('||').map((value) => value.trim())
      return { input, expected: expected || undefined }
    }),
    [cases],
  )
  const mutation = useMutation({
    mutationFn: () => evolutionApi.skillTest(projectId, { skill_name: skill, test_cases: parsedCases }),
    onSuccess: (next) => {
      setResult(next)
      void queryClient.invalidateQueries({ queryKey: ['evolution-overview', projectId] })
    },
  })

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <Card className="bg-ink-900/75 p-5">
        <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
          <div>
            <label className="text-xs font-medium text-gray-300">评测技能</label>
            <select value={skill} onChange={(event) => setSkill(event.target.value)} className="mt-2 h-10 w-full rounded-xl border border-ink-600 bg-ink-950 px-3 text-sm text-gray-200 focus:border-emerald-400/50 focus:outline-none">
              {SKILL_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label} · {value}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs font-medium text-gray-300">测试用例</label>
            <TextArea className="mt-2" rows={6} value={cases} onChange={(event) => setCases(event.target.value)} placeholder={'每行一个用例，可用 || 分隔期望结果\n例如：写一段雨夜追逐 || 环境必须参与冲突'} />
          </div>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Button variant="primary" disabled={parsedCases.length === 0 || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} 运行 {parsedCases.length || 0} 个用例
          </Button>
          {mutation.isError && <span className="text-xs text-red-300">{(mutation.error as Error).message}</span>}
        </div>
      </Card>

      {result && (
        <Card className="overflow-hidden bg-ink-900/75 p-0">
          <PanelHeader icon={<FlaskConical size={15} />} title={`${result.skill_name || skill} 评测结果`} subtitle={`${result.test_count ?? result.results?.length ?? 0} 个真实用例`} />
          <div className="space-y-4 p-4 sm:p-5">
            <div className="grid gap-3 sm:grid-cols-3">
              <MiniResult label="平均分" value={result.avg_score == null ? '—' : `${result.avg_score.toFixed(2)} / 10`} />
              <MiniResult label="通过率" value={result.pass_rate == null ? '—' : `${Math.round(result.pass_rate * 100)}%`} />
              <MiniResult label="相对上次" value={result.improvement == null ? '—' : `${result.improvement >= 0 ? '+' : ''}${result.improvement.toFixed(2)}`} />
            </div>
            <div className="space-y-2">
              {(result.results ?? []).map((item, index) => {
                const passed = item.passed ?? (item.score ?? 0) >= 7
                return (
                  <article key={item.case_index ?? index} className={cn('rounded-xl border p-3', passed ? 'border-emerald-400/15 bg-emerald-400/[0.025]' : 'border-red-400/15 bg-red-400/[0.025]')}>
                    <div className="flex items-center gap-2">
                      {passed ? <CheckCircle2 size={13} className="text-emerald-300" /> : <AlertTriangle size={13} className="text-red-300" />}
                      <span className="truncate text-xs font-medium text-gray-300">{item.case || item.input || `用例 ${index + 1}`}</span>
                      <span className="ml-auto text-[10px] text-gray-500">{item.score?.toFixed(1) ?? '0'} / 10</span>
                    </div>
                    {(item.output || item.response) && <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-[10px] leading-5 text-gray-500">{item.output || item.response}</p>}
                    {item.reasoning && <p className="mt-2 text-[10px] leading-5 text-gray-600">Judge：{item.reasoning}</p>}
                    {item.error && <p className="mt-2 text-[10px] text-red-300">{item.error}</p>}
                  </article>
                )
              })}
            </div>
          </div>
        </Card>
      )}
    </div>
  )
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" role="tab" aria-selected={active} tabIndex={active ? 0 : -1} onClick={onClick} onKeyDown={(event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const tabs = Array.from(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? [])
    const index = tabs.indexOf(event.currentTarget)
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
    tabs[nextIndex]?.click()
    tabs[nextIndex]?.focus()
  }} className={cn('flex h-9 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40', active ? 'bg-ink-700 text-emerald-200' : 'text-gray-500 hover:bg-ink-800 hover:text-gray-200')}>{children}</button>
}

function PanelHeader({ icon, title, subtitle }: { icon: React.ReactNode; title: string; subtitle: string }) {
  return <header className="flex items-start gap-3 border-b border-ink-700 bg-ink-900/80 px-4 py-4 sm:px-5"><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-violet-400/15 bg-violet-400/8 text-violet-300">{icon}</span><div><h2 className="text-sm font-semibold text-gray-100">{title}</h2><p className="mt-1 text-[10px] leading-4 text-gray-600">{subtitle}</p></div></header>
}

function Metric({ icon, label, value, detail, tone }: { icon: React.ReactNode; label: string; value: string; detail: string; tone: 'violet' | 'green' | 'blue' | 'amber' | 'gray' }) {
  const classes = { violet: 'border-violet-400/18 bg-violet-400/8 text-violet-300', green: 'border-emerald-400/18 bg-emerald-400/8 text-emerald-300', blue: 'border-blue-400/18 bg-blue-400/8 text-blue-300', amber: 'border-amber-400/18 bg-amber-400/8 text-amber-300', gray: 'border-ink-600 bg-ink-800 text-gray-400' }[tone]
  return <article className="rounded-2xl border border-ink-700 bg-ink-900/75 p-4"><span className={cn('flex h-8 w-8 items-center justify-center rounded-xl border', classes)}>{icon}</span><p className="mt-3 text-[9px] uppercase tracking-[0.13em] text-gray-600">{label}</p><p className="mt-1 text-xl font-semibold text-gray-100">{value}</p><p className="mt-1 truncate text-[10px] text-gray-600">{detail}</p></article>
}

function EmptyInline({ title, description }: { title: string; description: string }) {
  return <div className="py-10 text-center"><Sparkles size={18} className="mx-auto text-gray-700" /><p className="mt-3 text-xs font-medium text-gray-400">{title}</p><p className="mt-1 text-[10px] text-gray-600">{description}</p></div>
}

function TinyStat({ label, value }: { label: string; value: number }) {
  return <div className="rounded-lg bg-ink-800/70 px-2 py-1.5"><p className="text-[9px] text-gray-600">{label}</p><p className="mt-0.5 text-xs font-semibold text-gray-300">{value}</p></div>
}

function StatusBadge({ status }: { status: string }) {
  const variant = ['champion', 'active', 'approved'].includes(status)
    ? 'green'
    : ['candidate', 'pending'].includes(status)
      ? 'amber'
      : ['rolled_back', 'rejected', 'error', 'failed'].includes(status)
        ? 'red'
        : 'gray'
  return <Badge variant={variant} className="text-[9px]">{status}</Badge>
}

function PromptField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <div><label className="text-xs font-medium text-gray-300">{label}</label><TextArea className="mt-2" rows={9} value={value} onChange={(event) => onChange(event.target.value)} placeholder="输入完整 system prompt…" /></div>
}

function ExperimentScore({ label, score, scale, winner }: { label: string; score?: number; scale: number; winner: boolean }) {
  const pct = Math.min(100, Math.max(0, ((score ?? 0) / scale) * 100))
  return <div className={cn('rounded-xl border p-3', winner ? 'border-gold-400/25 bg-gold-400/[0.045]' : 'border-ink-700 bg-ink-950/45')}><div className="flex items-center justify-between"><span className="text-xs font-medium text-gray-300">{label}</span>{winner && <Badge variant="gold">胜者</Badge>}<span className="ml-auto text-xs text-gray-400">{score?.toFixed(1) ?? '—'} / {scale}</span></div><div className="mt-3 h-2 overflow-hidden rounded-full bg-ink-700"><div className="h-full rounded-full bg-gradient-to-r from-violet-500 to-gold-400" style={{ width: `${pct}%` }} /></div></div>
}

function OutputPanel({ label, content }: { label: string; content: string }) {
  return <div><p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-gray-600">{label}</p><div className="max-h-80 overflow-y-auto whitespace-pre-wrap rounded-xl border border-ink-700 bg-ink-950/55 p-3 text-[11px] leading-6 text-gray-400">{content || '无输出'}</div></div>
}

function MiniResult({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border border-ink-700 bg-ink-950/45 p-3"><p className="text-[9px] text-gray-600">{label}</p><p className="mt-1 text-sm font-semibold text-gray-200">{value}</p></div>
}

function Loading({ label }: { label: string }) {
  return <div className="flex min-h-80 items-center justify-center gap-2 text-sm text-gray-500"><Loader2 size={17} className="animate-spin" /> {label}</div>
}

function ErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="mx-auto mt-20 max-w-md rounded-2xl border border-red-400/20 bg-red-400/[0.035] p-6 text-center"><AlertTriangle size={22} className="mx-auto text-red-300" /><p className="mt-3 text-sm font-medium text-red-100">学习账本读取失败</p><p className="mt-2 text-xs leading-5 text-gray-500">{message}</p><Button className="mt-4" onClick={onRetry}><RefreshCw size={12} /> 重试</Button></div>
}

function formatTime(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}

function shortId(value?: string | null) {
  if (!value) return '—'
  return value.length > 10 ? value.slice(0, 8) : value
}

function decisionLabel(value?: string | null) {
  return { memory_applied: '学习记忆已应用', candidate_requires_holdout: '候选等待 holdout', rolled_back: '已回滚' }[value ?? ''] ?? value ?? '尚无决策'
}
