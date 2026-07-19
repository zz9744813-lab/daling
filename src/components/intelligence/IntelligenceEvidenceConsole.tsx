import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  Archive,
  BookOpenCheck,
  BrainCircuit,
  ChevronRight,
  CircleDot,
  Database,
  FileSearch,
  Fingerprint,
  Gauge,
  Layers3,
  Loader2,
  RefreshCw,
  Search,
  ServerCog,
  ShieldCheck,
  X,
} from 'lucide-react'
import { intelligenceApi } from '../../api/client'
import { cn } from '../../lib/cn'
import { useDialogFocus } from '../../lib/useDialogFocus'
import { clampInteger, formatTrackedCost } from '../../lib/workflowGuards'
import type {
  ContextBundleEvidence,
  EvidenceListResponse,
  LlmCallEvidence,
  OutlineEvidenceBinding,
  OutlineEvidenceNode,
  SceneEvidenceArtifact,
} from '../../types'

interface IntelligenceEvidenceConsoleProps {
  projectId: string
  displayedChapter?: number
}

type EvidenceView = 'trace' | 'memory' | 'calls'

const OUTLINE_NODE_PAGE_SIZE = 500
const EVIDENCE_RENDER_PAGE_SIZE = 80
const BINDING_WINDOW_LIMIT = 2000
const SCENE_WINDOW_LIMIT = 1000
const CONTEXT_WINDOW_LIMIT = 500
const CALL_WINDOW_LIMIT = 1000
const MAX_OUTLINE_NODE_PAGES = 10_000

type OutlineNodePageFetcher = (
  projectId: string,
  params: { offset: number; limit: number },
) => Promise<EvidenceListResponse<OutlineEvidenceNode>>

/**
 * The outline endpoint is the only evidence endpoint with a stable offset/total
 * contract. Read every page and fail loudly if the contract stops advancing;
 * returning a partial list here would make the audit console untrustworthy.
 */
export async function fetchAllOutlineNodeEvidence(
  projectId: string,
  fetchPage: OutlineNodePageFetcher = intelligenceApi.outlineNodes,
): Promise<EvidenceListResponse<OutlineEvidenceNode>> {
  let offset = 0
  let firstPage: EvidenceListResponse<OutlineEvidenceNode> | undefined
  let declaredTotal: number | undefined
  const items: OutlineEvidenceNode[] = []
  const seenIds = new Set<string>()

  for (let pageNumber = 0; pageNumber < MAX_OUTLINE_NODE_PAGES; pageNumber += 1) {
    const page = await fetchPage(projectId, { offset, limit: OUTLINE_NODE_PAGE_SIZE })
    firstPage ??= page
    if (Number.isFinite(page.total) && Number(page.total) >= 0) {
      declaredTotal = Number(page.total)
    }

    if (!page.items.length) {
      if (declaredTotal != null && offset < declaredTotal) {
        throw new Error(`来源节点分页在 ${offset}/${declaredTotal} 条处提前结束，未返回完整证据`)
      }
      return {
        ...(firstPage ?? page),
        count: items.length,
        total: Math.max(declaredTotal ?? 0, items.length),
        items,
      }
    }

    for (const item of page.items) {
      if (seenIds.has(item.id)) {
        throw new Error(`来源节点分页未推进：节点 ${item.id} 被重复返回，已拒绝展示不完整证据`)
      }
      seenIds.add(item.id)
      items.push(item)
    }
    offset += page.items.length

    if (declaredTotal != null && offset >= declaredTotal) {
      return {
        ...firstPage,
        count: items.length,
        total: Math.max(declaredTotal, items.length),
        items,
      }
    }
    if (page.items.length < OUTLINE_NODE_PAGE_SIZE) {
      if (declaredTotal != null && offset < declaredTotal) {
        throw new Error(`来源节点分页只返回 ${offset}/${declaredTotal} 条，未返回完整证据`)
      }
      return {
        ...firstPage,
        count: items.length,
        total: items.length,
        items,
      }
    }
  }

  throw new Error(`来源节点超过 ${MAX_OUTLINE_NODE_PAGES.toLocaleString()} 页，已停止以避免静默截断或无限分页`)
}

function appendSearchText(value: unknown, output: string[], depth = 0) {
  if (value == null || depth > 4) return
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    output.push(String(value))
    return
  }
  if (Array.isArray(value)) {
    for (const item of value) appendSearchText(item, output, depth + 1)
    return
  }
  if (typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) {
      output.push(key)
      appendSearchText(item, output, depth + 1)
    }
  }
}

/** Search all supplied primitive and nested evidence fields; whitespace terms use AND semantics. */
export function evidenceMatchesSearch(query: string, ...values: unknown[]) {
  const terms = query.trim().toLocaleLowerCase('zh-CN').split(/\s+/).filter(Boolean)
  if (!terms.length) return true
  const parts: string[] = []
  for (const value of values) appendSearchText(value, parts)
  const haystack = parts.join('\n').toLocaleLowerCase('zh-CN')
  return terms.every((term) => haystack.includes(term))
}

export function nextEvidenceVisibleCount(
  current: number,
  total: number,
  pageSize = EVIDENCE_RENDER_PAGE_SIZE,
) {
  return Math.min(total, Math.max(0, current) + Math.max(1, pageSize))
}

function number(value: number | null | undefined) {
  return Number.isFinite(value) ? Number(value).toLocaleString() : '—'
}

function percent(value: number | null | undefined) {
  return Number.isFinite(value) ? `${(Number(value) * 100).toFixed(Number(value) >= 0.9995 ? 2 : 1)}%` : '—'
}

function shortHash(value: string | null | undefined) {
  if (!value) return '—'
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value
}

function timestamp(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function sourcePath(value: string[] | string | null) {
  if (Array.isArray(value)) return value.filter(Boolean).join(' / ') || '未命名节点'
  return value || '未命名节点'
}

function recordLabel(value: Record<string, unknown>) {
  for (const key of ['title', 'name', 'kind', 'content', 'text', 'id']) {
    const candidate = value[key]
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim()
  }
  return '已选记忆项'
}

function statusTone(status?: string, passed?: boolean) {
  if (passed === true || ['approved', 'passed', 'complete', 'completed', 'success'].includes(status ?? '')) {
    return 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200'
  }
  if (passed === false || ['failed', 'error', 'rejected'].includes(status ?? '')) {
    return 'border-red-400/25 bg-red-400/10 text-red-200'
  }
  return 'border-amber-400/20 bg-amber-400/8 text-amber-200'
}

export function IntelligenceEvidenceConsole({
  projectId,
  displayedChapter,
}: IntelligenceEvidenceConsoleProps) {
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<EvidenceView>('trace')
  const [chapterFilter, setChapterFilter] = useState<number | undefined>(displayedChapter)
  const [searchTerm, setSearchTerm] = useState('')
  const dialogRef = useDialogFocus<HTMLElement>(open, () => setOpen(false))

  useEffect(() => {
    if (!open && displayedChapter != null) setChapterFilter(displayedChapter)
  }, [displayedChapter, open])

  const overviewQuery = useQuery({
    queryKey: ['intelligence-overview', projectId],
    queryFn: () => intelligenceApi.overview(projectId),
    enabled: Boolean(projectId),
    refetchInterval: open ? 5000 : 15000,
  })

  const traceEnabled = open && view === 'trace'
  const nodesQuery = useQuery({
    queryKey: ['intelligence-outline-nodes', projectId],
    queryFn: () => fetchAllOutlineNodeEvidence(projectId),
    enabled: traceEnabled,
    staleTime: 30000,
  })
  const bindingsQuery = useQuery({
    queryKey: ['intelligence-outline-bindings', projectId, chapterFilter],
    queryFn: () =>
      intelligenceApi.outlineBindings(projectId, {
        chapter_no: chapterFilter,
        limit: BINDING_WINDOW_LIMIT,
      }),
    enabled: traceEnabled,
    staleTime: 10000,
  })
  const scenesQuery = useQuery({
    queryKey: ['intelligence-scenes', projectId, chapterFilter],
    queryFn: () =>
      intelligenceApi.scenes(projectId, {
        chapter_no: chapterFilter,
        limit: SCENE_WINDOW_LIMIT,
      }),
    enabled: traceEnabled,
    refetchInterval: traceEnabled ? 5000 : false,
  })
  const contextsQuery = useQuery({
    queryKey: ['intelligence-context-bundles', projectId, chapterFilter],
    queryFn: () =>
      intelligenceApi.contextBundles(projectId, {
        chapter_no: chapterFilter,
        limit: CONTEXT_WINDOW_LIMIT,
      }),
    enabled: open && view === 'memory',
    refetchInterval: open && view === 'memory' ? 7000 : false,
  })
  const callsQuery = useQuery({
    queryKey: ['intelligence-llm-calls', projectId, chapterFilter],
    queryFn: () =>
      intelligenceApi.llmCalls(projectId, {
        chapter_no: chapterFilter,
        limit: CALL_WINDOW_LIMIT,
      }),
    enabled: open && view === 'calls',
    refetchInterval: open && view === 'calls' ? 5000 : false,
  })

  const overview = overviewQuery.data
  const fidelityRate = overview?.production.fidelity_assessment_count
    ? overview.production.fidelity_passed_count / overview.production.fidelity_assessment_count
    : 0
  const approvedScenes = Object.entries(overview?.production.scene_status_counts ?? {})
    .filter(([status]) => ['approved', 'passed', 'complete', 'completed'].includes(status))
    .reduce((total, [, count]) => total + count, 0)

  const selectView = (nextView: EvidenceView) => {
    setView(nextView)
    setSearchTerm('')
  }

  const refresh = () => {
    void overviewQuery.refetch()
    if (view === 'trace') {
      void nodesQuery.refetch()
      void bindingsQuery.refetch()
      void scenesQuery.refetch()
    } else if (view === 'memory') {
      void contextsQuery.refetch()
    } else {
      void callsQuery.refetch()
    }
  }

  const traceLoading = nodesQuery.isLoading || bindingsQuery.isLoading || scenesQuery.isLoading
  const traceError = nodesQuery.error || bindingsQuery.error || scenesQuery.error

  return (
    <>
      <section className="ui-density-readable shrink-0 border-b border-ink-700 bg-ink-950/88 px-3 py-2 sm:px-4" aria-label="生产证据状态">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="group flex w-full items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/72 px-3 py-2 text-left transition-colors hover:border-emerald-400/25 hover:bg-ink-850 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/35"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-blue-400/20 bg-blue-400/8 text-blue-200">
            <Fingerprint size={15} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-xs font-semibold text-gray-100">生产证据台</span>
              {overviewQuery.isLoading ? (
                <span className="inline-flex items-center gap-1 text-[10px] text-gray-500"><Loader2 size={10} className="animate-spin" /> 正在读取</span>
              ) : overviewQuery.isError ? (
                <span className="text-[10px] text-red-300">证据接口暂不可用</span>
              ) : (
                <>
                  <EvidencePill label={`来源 ${overview?.outline.present ? percent(overview.outline.stats?.coverage_ratio) : '未上传'}`} ok={Boolean(overview?.outline.stats?.exact_source_covered)} />
                  <EvidencePill label={`绑定 ${number(overview?.outline.required_binding_count)}`} ok={Boolean(overview?.outline.required_binding_count)} />
                  <EvidencePill label={`场景 ${number(approvedScenes)}`} ok={approvedScenes > 0} />
                  <EvidencePill label={`记忆 ${number(overview?.memory.context_bundle_count)}`} ok={Boolean(overview?.memory.context_bundle_count)} />
                  <EvidencePill label={`调用 ${number(overview?.llm.call_count)}`} ok={Boolean(overview?.llm.call_count)} />
                </>
              )}
            </span>
            <span className="mt-0.5 hidden truncate text-[10px] text-gray-600 sm:block">
              检查大纲节点 → 章节约束 → 场景检查点 → 记忆检索 → 模型调用；空数据不会显示为已通过。
            </span>
          </span>
          <span className="inline-flex shrink-0 items-center gap-1 text-[10px] font-medium text-gray-500 transition-colors group-hover:text-emerald-200">
            查看链路 <ChevronRight size={13} />
          </span>
        </button>
      </section>

      {open && (
        <div
          className="fixed inset-0 z-[75] flex items-end justify-center bg-black/75 backdrop-blur-sm sm:items-center sm:p-4"
          onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false) }}
        >
          <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="evidence-console-title" tabIndex={-1} className="ui-density-readable flex h-[96dvh] w-full max-w-7xl flex-col overflow-hidden rounded-t-3xl border border-ink-700 bg-ink-900 shadow-[0_32px_120px_rgba(0,0,0,0.68)] sm:h-[min(92vh,940px)] sm:rounded-3xl">
            <header className="shrink-0 border-b border-ink-700 bg-ink-900/96 px-4 py-4 sm:px-6">
              <div className="flex items-start gap-3">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-blue-400/20 bg-blue-400/8 text-blue-200"><Fingerprint size={19} /></span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 id="evidence-console-title" className="text-base font-semibold text-gray-50 sm:text-lg">可验证生产证据</h2>
                    {overview?.outline.stats?.revision != null && <span className="rounded-full border border-ink-600 bg-ink-950 px-2 py-0.5 text-[10px] text-gray-400">来源修订 R{overview.outline.stats.revision}</span>}
                    <span className={cn('rounded-full border px-2 py-0.5 text-[10px]', !overview ? 'border-ink-600 bg-ink-950 text-gray-500' : overview.prompt.configured ? 'border-emerald-400/20 bg-emerald-400/8 text-emerald-200' : 'border-amber-400/20 bg-amber-400/8 text-amber-200')}>
                      项目提示词 {!overview ? '状态未知' : overview.prompt.configured ? `${number(overview.prompt.character_count)} 字符` : '未配置'}
                    </span>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-gray-500">这里展示后台已持久化的原始证据与调用审计，不根据流程状态推测成功。</p>
                </div>
                <button type="button" onClick={refresh} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-gray-500 hover:bg-ink-700 hover:text-gray-200" aria-label="刷新证据"><RefreshCw size={16} /></button>
                <button type="button" onClick={() => setOpen(false)} data-dialog-initial-focus className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-gray-500 hover:bg-ink-700 hover:text-gray-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40" aria-label="关闭证据台"><X size={18} /></button>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
                <Metric icon={<FileSearch size={13} />} label="全文索引覆盖" value={!overview ? '—' : overview.outline.present ? percent(overview.outline.stats?.coverage_ratio) : '未上传'} ok={Boolean(overview?.outline.stats?.exact_source_covered)} />
                <Metric icon={<Layers3 size={13} />} label="节点 / 分块" value={`${number(overview?.outline.stats?.node_count)} / ${number(overview?.outline.stats?.chunk_count)}`} ok={Boolean(overview?.outline.stats?.node_count)} />
                <Metric icon={<ShieldCheck size={13} />} label="忠实度通过" value={`${number(overview?.production.fidelity_passed_count)} / ${number(overview?.production.fidelity_assessment_count)}`} ok={fidelityRate === 1 && fidelityRate > 0} />
                <Metric icon={<BrainCircuit size={13} />} label="上下文 / 缓存命中" value={`${number(overview?.memory.context_bundle_count)} / ${number(overview?.memory.retrieval_cache_hits)}`} ok={Boolean(overview?.memory.context_bundle_count)} />
                <Metric icon={<Activity size={13} />} label="真实 LLM 调用" value={number(overview?.llm.call_count)} ok={Boolean(overview?.llm.call_count)} />
                <Metric icon={<Gauge size={13} />} label="缓存输入 / 成本" value={`${number(overview?.llm.cached_input_tokens)} / ${formatTrackedCost(overview?.llm.cost, overview?.llm.call_count)}`} ok={Boolean(overview?.llm.cached_input_tokens)} />
              </div>

              <div className="mt-4 flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
                <nav className="no-scrollbar flex gap-1 overflow-x-auto rounded-xl bg-ink-950/80 p-1" role="tablist" aria-label="证据类型">
                  <EvidenceTab active={view === 'trace'} onClick={() => selectView('trace')}><BookOpenCheck size={13} /> 来源到场景</EvidenceTab>
                  <EvidenceTab active={view === 'memory'} onClick={() => selectView('memory')}><Database size={13} /> 记忆与缓存</EvidenceTab>
                  <EvidenceTab active={view === 'calls'} onClick={() => selectView('calls')}><ServerCog size={13} /> 模型调用账本</EvidenceTab>
                </nav>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <label className="relative flex min-w-0 items-center">
                    <span className="sr-only">检索当前证据</span>
                    <Search size={13} className="pointer-events-none absolute left-2.5 text-gray-600" />
                    <input
                      type="search"
                      value={searchTerm}
                      onChange={(event) => setSearchTerm(event.target.value)}
                      placeholder={view === 'trace' ? '检索原文、路径、绑定或场景' : view === 'memory' ? '检索查询、角色或记忆项' : '检索阶段、模型、哈希或错误'}
                      className="h-8 w-full rounded-lg border border-ink-600 bg-ink-950 py-1 pl-8 pr-8 text-[11px] text-gray-200 outline-none placeholder:text-gray-700 focus:border-emerald-400/50 sm:w-64"
                    />
                    {searchTerm && <button type="button" onClick={() => setSearchTerm('')} className="absolute right-1.5 flex h-6 w-6 items-center justify-center rounded text-gray-600 hover:bg-ink-700 hover:text-gray-200" aria-label="清除证据检索"><X size={12} /></button>}
                  </label>
                  <label className="flex items-center gap-2 whitespace-nowrap text-[11px] text-gray-500">
                    章节筛选
                    <input
                      type="number"
                      min={1}
                      max={5000}
                      value={chapterFilter ?? ''}
                      onChange={(event) => setChapterFilter(event.target.value ? clampInteger(event.target.value, 1, 5000, 1) : undefined)}
                      placeholder="全部"
                      className="h-8 w-24 rounded-lg border border-ink-600 bg-ink-950 px-2 text-xs text-gray-200 outline-none focus:border-emerald-400/50"
                    />
                    {chapterFilter != null && <button type="button" onClick={() => setChapterFilter(undefined)} className="rounded-lg px-2 py-1 text-gray-500 hover:bg-ink-700 hover:text-gray-200">清除</button>}
                  </label>
                </div>
              </div>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto bg-ink-950/35 p-4 sm:p-6">
              {view === 'trace' ? (
                traceLoading ? <Loading label="正在对齐来源节点、章节绑定和场景检查点…" />
                  : traceError ? <ErrorState error={traceError as Error} onRetry={refresh} />
                    : <TraceView
                      nodes={nodesQuery.data?.items ?? []}
                      nodeTotal={nodesQuery.data?.total ?? nodesQuery.data?.items.length ?? 0}
                      bindings={bindingsQuery.data?.items ?? []}
                      scenes={scenesQuery.data?.items ?? []}
                      chapterFilter={chapterFilter}
                      searchTerm={searchTerm}
                      bindingsWindowLimited={(bindingsQuery.data?.items.length ?? 0) >= BINDING_WINDOW_LIMIT}
                      scenesWindowLimited={(scenesQuery.data?.items.length ?? 0) >= SCENE_WINDOW_LIMIT}
                    />
              ) : view === 'memory' ? (
                contextsQuery.isLoading ? <Loading label="正在读取持久化上下文包…" />
                  : contextsQuery.isError ? <ErrorState error={contextsQuery.error as Error} onRetry={refresh} />
                    : <MemoryView bundles={contextsQuery.data?.items ?? []} searchTerm={searchTerm} windowLimited={(contextsQuery.data?.items.length ?? 0) >= CONTEXT_WINDOW_LIMIT} chapterFiltered={chapterFilter != null} />
              ) : (
                callsQuery.isLoading ? <Loading label="正在读取真实模型调用账本…" />
                  : callsQuery.isError ? <ErrorState error={callsQuery.error as Error} onRetry={refresh} />
                    : <CallsView calls={callsQuery.data?.items ?? []} searchTerm={searchTerm} windowLimited={(callsQuery.data?.items.length ?? 0) >= CALL_WINDOW_LIMIT} chapterFiltered={chapterFilter != null} />
              )}
            </div>
          </section>
        </div>
      )}
    </>
  )
}

function EvidencePill({ label, ok }: { label: string; ok: boolean }) {
  return <span className={cn('rounded-full border px-1.5 py-0.5 text-[9px]', ok ? 'border-emerald-400/20 bg-emerald-400/8 text-emerald-200' : 'border-ink-600 bg-ink-950 text-gray-500')}>{label}</span>
}

function Metric({ icon, label, value, ok }: { icon: React.ReactNode; label: string; value: string; ok: boolean }) {
  return (
    <div className="rounded-xl border border-ink-700 bg-ink-950/65 px-3 py-2.5">
      <div className={cn('flex items-center gap-1.5 text-[9px]', ok ? 'text-emerald-300' : 'text-gray-600')}>{icon}{label}</div>
      <p className="mt-1 truncate text-sm font-semibold text-gray-100" title={value}>{value}</p>
    </div>
  )
}

function EvidenceTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" role="tab" aria-selected={active} onClick={onClick} className={cn('inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40', active ? 'bg-ink-700 text-gray-100 shadow-sm' : 'text-gray-500 hover:text-gray-200')}>{children}</button>
}

function Loading({ label }: { label: string }) {
  const stages = label.includes('来源')
    ? ['来源节点索引', '章节约束绑定', '场景检查点']
    : label.includes('上下文')
      ? ['检索查询', '上下文装配', '缓存命中记录']
      : ['调用阶段', 'Token 与缓存', '成本与错误审计']

  return (
    <section className="mx-auto w-full max-w-5xl" role="status" aria-live="polite" aria-busy="true">
      <div className="flex items-start gap-3 rounded-2xl border border-blue-400/15 bg-blue-400/[0.045] px-4 py-4 sm:px-5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-blue-400/20 bg-blue-400/10 text-blue-200">
          <Loader2 size={16} className="animate-spin" />
        </span>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-gray-100">正在装配可验证证据</h3>
          <p className="mt-1 text-xs leading-5 text-gray-300">{label}</p>
          <p className="mt-0.5 text-[10px] leading-4 text-gray-400">大数据量会按证据类型分批读取；完成前不会把空结果误报为通过。</p>
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {stages.map((stage, index) => (
          <div key={stage} className="rounded-2xl border border-ink-700 bg-ink-900/65 p-4" aria-hidden="true">
            <div className="flex items-center justify-between gap-3">
              <span className="text-[11px] font-semibold text-gray-300">{stage}</span>
              <span className="font-mono text-[10px] text-gray-400">0{index + 1}</span>
            </div>
            <div className="mt-4 space-y-2.5">
              <div className="h-2.5 w-4/5 animate-pulse rounded-full bg-ink-600" />
              <div className="h-2.5 w-full animate-pulse rounded-full bg-ink-700" />
              <div className="h-2.5 w-3/5 animate-pulse rounded-full bg-ink-700" />
            </div>
            <div className="mt-4 h-7 animate-pulse rounded-lg border border-ink-700 bg-ink-950/55" />
          </div>
        ))}
      </div>
    </section>
  )
}

function ErrorState({ error, onRetry }: { error: Error; onRetry: () => void }) {
  return (
    <div className="mx-auto flex min-h-64 max-w-md flex-col items-center justify-center text-center">
      <AlertTriangle size={26} className="text-red-300" />
      <h3 className="mt-3 text-sm font-semibold text-gray-100">证据读取失败</h3>
      <p className="mt-2 text-xs leading-5 text-gray-500">{error.message}</p>
      <button type="button" onClick={onRetry} className="mt-4 inline-flex h-9 items-center gap-2 rounded-xl border border-ink-600 px-3 text-xs text-gray-300 hover:bg-ink-800"><RefreshCw size={13} />重新读取</button>
    </div>
  )
}

function EmptyEvidence({ title, description }: { title: string; description: string }) {
  return (
    <div className="flex min-h-48 flex-col items-center justify-center rounded-2xl border border-dashed border-ink-600 bg-ink-900/35 px-6 text-center">
      <Archive size={22} className="text-gray-700" />
      <h3 className="mt-3 text-sm font-semibold text-gray-300">{title}</h3>
      <p className="mt-1 max-w-md text-xs leading-5 text-gray-600">{description}</p>
    </div>
  )
}

function TraceView({
  nodes,
  nodeTotal,
  bindings,
  scenes,
  chapterFilter,
  searchTerm,
  bindingsWindowLimited,
  scenesWindowLimited,
}: {
  nodes: OutlineEvidenceNode[]
  nodeTotal: number
  bindings: OutlineEvidenceBinding[]
  scenes: SceneEvidenceArtifact[]
  chapterFilter?: number
  searchTerm: string
  bindingsWindowLimited: boolean
  scenesWindowLimited: boolean
}) {
  const nodeIds = useMemo(() => new Set(bindings.map((binding) => binding.node_id)), [bindings])
  const relevantNodes = useMemo(
    () => (chapterFilter ? nodes.filter((node) => nodeIds.has(node.id)) : nodes),
    [chapterFilter, nodeIds, nodes],
  )
  const sceneLatest = useMemo(() => {
    const latest = new Map<string, SceneEvidenceArtifact>()
    for (const scene of scenes) {
      const key = `${scene.chapter_no}:${scene.scene_no}`
      const current = latest.get(key)
      if (!current || scene.attempt_no > current.attempt_no) latest.set(key, scene)
    }
    return [...latest.values()].sort((left, right) => left.chapter_no - right.chapter_no || left.scene_no - right.scene_no)
  }, [scenes])
  const matchingNodes = useMemo(
    () => relevantNodes.filter((node) => evidenceMatchesSearch(searchTerm, node)),
    [relevantNodes, searchTerm],
  )
  const matchingBindings = useMemo(
    () => bindings.filter((binding) => evidenceMatchesSearch(searchTerm, binding)),
    [bindings, searchTerm],
  )
  const matchingScenes = useMemo(
    () => sceneLatest.filter((scene) => evidenceMatchesSearch(searchTerm, scene)),
    [sceneLatest, searchTerm],
  )
  const [visibleNodes, setVisibleNodes] = useState(EVIDENCE_RENDER_PAGE_SIZE)
  const [visibleBindings, setVisibleBindings] = useState(EVIDENCE_RENDER_PAGE_SIZE)
  const [visibleScenes, setVisibleScenes] = useState(EVIDENCE_RENDER_PAGE_SIZE)

  useEffect(() => {
    setVisibleNodes(EVIDENCE_RENDER_PAGE_SIZE)
    setVisibleBindings(EVIDENCE_RENDER_PAGE_SIZE)
    setVisibleScenes(EVIDENCE_RENDER_PAGE_SIZE)
  }, [chapterFilter, searchTerm])

  if (!nodes.length && !bindings.length && !scenes.length) {
    return <EmptyEvidence title="还没有可展示的生产证据" description="上传大纲并完成结构编译后会出现来源节点；章节规划和场景写作完成后会继续补齐绑定、检查点与忠实度判定。" />
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-emerald-400/15 bg-emerald-400/[0.035] px-3 py-2 text-[10px] text-emerald-200/80" aria-live="polite">
        <span className="inline-flex items-center gap-1.5"><ShieldCheck size={12} /> 来源节点已完整分页读取</span>
        <span className="font-mono">{number(nodes.length)} / {number(nodeTotal)}</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-4">
        <Stage title="1. 来源节点" value={matchingNodes.length} icon={<FileSearch size={14} />} ready={matchingNodes.length > 0} />
        <Stage title="2. 章节约束" value={matchingBindings.length} icon={<Layers3 size={14} />} ready={matchingBindings.length > 0} />
        <Stage title="3. 场景检查点" value={matchingScenes.length} icon={<CircleDot size={14} />} ready={matchingScenes.length > 0} />
        <Stage title="4. 忠实度判定" value={matchingScenes.filter((scene) => scene.fidelity?.passed).length} icon={<ShieldCheck size={14} />} ready={matchingScenes.length > 0 && matchingScenes.every((scene) => scene.fidelity?.passed)} />
      </div>
      <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr_1.2fr]">
        <EvidenceColumn title="原始大纲节点" subtitle={chapterFilter ? `与第 ${chapterFilter} 章绑定的来源切片` : '按源文件顺序保留的节点'}>
          {matchingNodes.length ? matchingNodes.slice(0, visibleNodes).map((node) => <NodeCard key={node.id} node={node} bound={nodeIds.has(node.id)} />) : <ColumnEmpty label={searchTerm ? '没有匹配检索词的来源节点' : '当前筛选没有来源节点'} />}
          <EvidenceListProgress shown={Math.min(visibleNodes, matchingNodes.length)} total={matchingNodes.length} onLoadMore={() => setVisibleNodes((current) => nextEvidenceVisibleCount(current, matchingNodes.length))} />
        </EvidenceColumn>
        <EvidenceColumn title="章节来源约束" subtitle="每项要求携带节点 ID、字符范围与覆盖状态">
          {bindingsWindowLimited && <EvidenceWindowWarning limit={BINDING_WINDOW_LIMIT} chapterFiltered={chapterFilter != null} />}
          {matchingBindings.length ? matchingBindings.slice(0, visibleBindings).map((binding) => <BindingCard key={binding.id} binding={binding} />) : <ColumnEmpty label={searchTerm ? '没有匹配检索词的章节绑定' : '当前筛选没有章节绑定'} />}
          <EvidenceListProgress shown={Math.min(visibleBindings, matchingBindings.length)} total={matchingBindings.length} onLoadMore={() => setVisibleBindings((current) => nextEvidenceVisibleCount(current, matchingBindings.length))} />
        </EvidenceColumn>
        <EvidenceColumn title="场景检查点与忠实度" subtitle="按章、场景和尝试次数持久化，失败不会伪装为通过">
          {scenesWindowLimited && <EvidenceWindowWarning limit={SCENE_WINDOW_LIMIT} chapterFiltered={chapterFilter != null} />}
          {matchingScenes.length ? matchingScenes.slice(0, visibleScenes).map((scene) => <SceneCard key={scene.id} scene={scene} />) : <ColumnEmpty label={searchTerm ? '没有匹配检索词的场景检查点' : '当前筛选还没有场景检查点'} />}
          <EvidenceListProgress shown={Math.min(visibleScenes, matchingScenes.length)} total={matchingScenes.length} onLoadMore={() => setVisibleScenes((current) => nextEvidenceVisibleCount(current, matchingScenes.length))} />
        </EvidenceColumn>
      </div>
    </div>
  )
}

function Stage({ title, value, icon, ready }: { title: string; value: number; icon: React.ReactNode; ready: boolean }) {
  return <div className={cn('flex items-center gap-2 rounded-xl border px-3 py-2.5', ready ? 'border-emerald-400/20 bg-emerald-400/[0.045]' : 'border-ink-700 bg-ink-900/55')}><span className={ready ? 'text-emerald-300' : 'text-gray-600'}>{icon}</span><span className="min-w-0 flex-1 text-[10px] text-gray-500">{title}</span><strong className="text-sm text-gray-100">{value}</strong></div>
}

function EvidenceColumn({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return <section className="min-w-0 rounded-2xl border border-ink-700 bg-ink-900/55"><header className="border-b border-ink-700 px-4 py-3"><h3 className="text-xs font-semibold text-gray-100">{title}</h3><p className="mt-1 text-[10px] leading-4 text-gray-600">{subtitle}</p></header><div className="max-h-[50vh] space-y-2 overflow-y-auto p-3">{children}</div></section>
}

function NodeCard({ node, bound }: { node: OutlineEvidenceNode; bound: boolean }) {
  return (
    <article className="rounded-xl border border-ink-700 bg-ink-950/55 p-3">
      <div className="flex items-center gap-2"><span className="text-[9px] font-mono text-gray-600">#{node.ordinal}</span><span className="rounded border border-ink-600 px-1.5 py-0.5 text-[9px] text-gray-500">{node.node_type}</span>{bound && <span className="ml-auto text-[9px] text-emerald-300">已绑定</span>}</div>
      <p className="mt-2 truncate text-[11px] font-medium text-gray-300">{node.title || sourcePath(node.path)}</p>
      <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-[10px] leading-5 text-gray-600">{node.content || '空节点'}</p>
      {node.content && (
        <details className="mt-2 rounded-lg border border-ink-700 px-2 py-1.5">
          <summary className="cursor-pointer text-[9px] text-blue-200/65">查看完整节点原文</summary>
          <p className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap break-words text-[10px] leading-5 text-gray-500">{node.content}</p>
        </details>
      )}
      <JsonEvidenceDetails label="查看节点分析与覆盖元数据" value={{ analysis: node.analysis, coverage: node.coverage }} />
      <p className="mt-2 font-mono text-[9px] text-gray-700">字符 {number(node.char_start)}–{number(node.char_end)} · <span title={node.content_hash}>{shortHash(node.content_hash)}</span></p>
    </article>
  )
}

function BindingCard({ binding }: { binding: OutlineEvidenceBinding }) {
  return (
    <article className="rounded-xl border border-ink-700 bg-ink-950/55 p-3">
      <div className="flex flex-wrap items-center gap-1.5"><span className="rounded bg-blue-400/10 px-1.5 py-0.5 text-[9px] font-semibold text-blue-200">第 {binding.chapter_no} 章</span>{binding.required && <span className="rounded bg-amber-400/10 px-1.5 py-0.5 text-[9px] text-amber-200">必须覆盖</span>}<span className={cn('ml-auto rounded border px-1.5 py-0.5 text-[9px]', statusTone(binding.coverage_status))}>{binding.coverage_status || '待覆盖'}</span></div>
      <p className="mt-2 whitespace-pre-wrap break-words text-[11px] font-medium leading-5 text-gray-300">{binding.requirement_text || binding.source_text || '未记录约束文本'}</p>
      <p className="mt-1 truncate text-[10px] text-gray-600" title={sourcePath(binding.source_path)}>{sourcePath(binding.source_path)}</p>
      {binding.source_text && binding.requirement_text && (
        <details className="mt-2 rounded-lg border border-ink-700 px-2 py-1.5">
          <summary className="cursor-pointer text-[9px] text-blue-200/65">查看完整绑定源文</summary>
          <p className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap break-words text-[10px] leading-5 text-gray-500">{binding.source_text}</p>
        </details>
      )}
      <JsonEvidenceDetails label="查看绑定审计元数据" value={{ evidence: binding.evidence, analysis: binding.analysis }} />
      <p className="mt-2 font-mono text-[9px] text-gray-700">绑定 <span title={binding.id}>{shortHash(binding.id)}</span> · 节点 <span title={binding.node_id}>{shortHash(binding.node_id)}</span></p>
      <p className="mt-0.5 font-mono text-[9px] text-gray-700">字符 {number(binding.source_range[0])}–{number(binding.source_range[1])}</p>
    </article>
  )
}

function SceneCard({ scene }: { scene: SceneEvidenceArtifact }) {
  const fidelity = scene.fidelity
  const sceneText = scene.draft || scene.error || ''
  return (
    <article className="rounded-xl border border-ink-700 bg-ink-950/55 p-3">
      <div className="flex flex-wrap items-center gap-1.5"><span className="text-[11px] font-semibold text-gray-200">第 {scene.chapter_no} 章 · 场景 {scene.scene_no}</span><span className="text-[9px] text-gray-600">尝试 {scene.attempt_no}</span><span className={cn('ml-auto rounded border px-1.5 py-0.5 text-[9px]', statusTone(scene.status))}>{scene.status}</span></div>
      <div className="mt-2 grid grid-cols-3 gap-1.5 text-center"><TinyMetric label="来源约束" value={scene.source_requirement_ids.length} /><TinyMetric label="记忆项" value={scene.memory_ids.length} /><TinyMetric label="实际字数" value={scene.actual_words} /></div>
      {scene.source_requirement_ids.length > 0 && (
        <details className="mt-2 rounded-lg border border-ink-700 px-2 py-1.5">
          <summary className="cursor-pointer text-[9px] text-blue-200/65">查看全部 {number(scene.source_requirement_ids.length)} 个来源绑定</summary>
          <div className="mt-2 flex flex-wrap gap-1">{scene.source_requirement_ids.map((id) => <span key={id} title={id} className="break-all rounded border border-blue-400/15 bg-blue-400/[0.045] px-1.5 py-0.5 font-mono text-[8px] text-blue-200/65">{id}</span>)}</div>
        </details>
      )}
      {scene.memory_ids.length > 0 && (
        <details className="mt-2 rounded-lg border border-ink-700 px-2 py-1.5">
          <summary className="cursor-pointer text-[9px] text-violet-200/65">查看全部 {number(scene.memory_ids.length)} 个记忆引用</summary>
          <div className="mt-2 flex flex-wrap gap-1">{scene.memory_ids.map((id) => <span key={id} title={id} className="break-all rounded border border-violet-400/15 bg-violet-400/[0.045] px-1.5 py-0.5 font-mono text-[8px] text-violet-200/65">{id}</span>)}</div>
        </details>
      )}
      {fidelity ? <div className={cn('mt-2 rounded-lg border px-2.5 py-2', statusTone(undefined, fidelity.passed))}><div className="flex items-center justify-between text-[10px]"><span>忠实度 {number(fidelity.score)}</span><strong>{fidelity.passed ? '通过' : '未通过'}</strong></div>{!fidelity.passed && <p className="mt-1 text-[9px] opacity-80">缺失 {fidelity.missing_ids.length} · 冲突 {fidelity.contradictions.length} · 外部断言 {fidelity.source_external_claims.length}</p>}</div> : <div className="mt-2 rounded-lg border border-dashed border-ink-600 px-2.5 py-2 text-[10px] text-gray-600">尚无忠实度判定</div>}
      {fidelity && <JsonEvidenceDetails label="查看完整忠实度判定" value={fidelity} />}
      <p className="mt-2 line-clamp-2 text-[10px] leading-5 text-gray-600">{sceneText || '检查点未保存正文预览'}</p>
      {sceneText && (
        <details className="mt-2 rounded-lg border border-ink-700 px-2 py-1.5">
          <summary className="cursor-pointer text-[9px] text-gray-500">查看完整检查点正文 / 错误</summary>
          <p className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap break-words text-[10px] leading-5 text-gray-500">{sceneText}</p>
        </details>
      )}
      <p className="mt-2 font-mono text-[9px] text-gray-700">R{scene.source_revision} · <span title={scene.contract_hash}>{shortHash(scene.contract_hash)}</span> · <span title={scene.content_hash}>{shortHash(scene.content_hash)}</span></p>
    </article>
  )
}

function TinyMetric({ label, value }: { label: string; value: number }) {
  return <div className="rounded-lg bg-ink-900 px-1 py-1.5"><p className="text-xs font-semibold text-gray-300">{number(value)}</p><p className="text-[8px] text-gray-700">{label}</p></div>
}

function ColumnEmpty({ label }: { label: string }) {
  return <div className="py-10 text-center text-[11px] text-gray-700">{label}</div>
}

function JsonEvidenceDetails({ label, value }: { label: string; value: unknown }) {
  const rendered = JSON.stringify(value, null, 2)
  if (!rendered || rendered === '{}' || rendered === '[]' || rendered === 'null') return null
  return (
    <details className="mt-2 rounded-lg border border-ink-700 bg-ink-950/35 px-2 py-1.5">
      <summary className="cursor-pointer text-[9px] text-gray-500">{label}</summary>
      <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all font-mono text-[8px] leading-4 text-gray-600">{rendered}</pre>
    </details>
  )
}

function EvidenceListProgress({ shown, total, onLoadMore }: { shown: number; total: number; onLoadMore: () => void }) {
  if (!total) return null
  const complete = shown >= total
  return (
    <div className="sticky bottom-0 rounded-xl border border-ink-700 bg-ink-900/95 px-3 py-2 text-center shadow-[0_-8px_20px_rgba(4,7,12,0.55)]" aria-live="polite">
      <p className="text-[9px] text-gray-500">已显示 {number(shown)} / {number(total)} 条匹配证据{complete ? ' · 已全部显示' : ''}</p>
      {!complete && (
        <button type="button" onClick={onLoadMore} className="mt-1.5 h-7 rounded-lg border border-emerald-400/20 bg-emerald-400/[0.045] px-3 text-[10px] font-medium text-emerald-200 hover:bg-emerald-400/10">
          继续加载 {number(Math.min(EVIDENCE_RENDER_PAGE_SIZE, total - shown))} 条
        </button>
      )}
    </div>
  )
}

function EvidenceWindowWarning({ limit, chapterFiltered }: { limit: number; chapterFiltered: boolean }) {
  return (
    <div className="rounded-xl border border-amber-400/20 bg-amber-400/[0.055] px-3 py-2 text-[9px] leading-4 text-amber-200/80" role="status">
      当前结果已达到接口单次上限 {number(limit)} 条，不能据此断言已读取全部记录。
      {chapterFiltered ? ' 若本章仍达到上限，请缩小检索条件。' : ' 请使用章节筛选逐章查看完整证据。'}
    </div>
  )
}

function MemoryView({ bundles, searchTerm, windowLimited, chapterFiltered }: { bundles: ContextBundleEvidence[]; searchTerm: string; windowLimited: boolean; chapterFiltered: boolean }) {
  const matchingBundles = useMemo(
    () => bundles.filter((bundle) => evidenceMatchesSearch(searchTerm, bundle)),
    [bundles, searchTerm],
  )
  const [visible, setVisible] = useState(EVIDENCE_RENDER_PAGE_SIZE)

  useEffect(() => setVisible(EVIDENCE_RENDER_PAGE_SIZE), [searchTerm])

  if (!bundles.length) return <EmptyEvidence title="还没有上下文包" description="场景规划或写作实际执行检索后，这里会显示选中的长期记忆、硬约束、Token 预算和缓存命中。" />
  return (
    <div className="space-y-3">
      {windowLimited && <EvidenceWindowWarning limit={CONTEXT_WINDOW_LIMIT} chapterFiltered={chapterFiltered} />}
      {matchingBundles.length ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {matchingBundles.slice(0, visible).map((bundle) => <MemoryCard key={bundle.id} bundle={bundle} />)}
        </div>
      ) : <EmptyEvidence title="没有匹配的上下文包" description="请更换检索词，或清除检索查看当前章节的全部已读取记录。" />}
      <EvidenceListProgress shown={Math.min(visible, matchingBundles.length)} total={matchingBundles.length} onLoadMore={() => setVisible((current) => nextEvidenceVisibleCount(current, matchingBundles.length))} />
    </div>
  )
}

function MemoryCard({ bundle }: { bundle: ContextBundleEvidence }) {
  const primaryItems = bundle.selected_items.slice(0, 8)
  const remainingItems = bundle.selected_items.slice(8)
  return (
    <article className="rounded-2xl border border-ink-700 bg-ink-900/65 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-violet-400/15 bg-violet-400/8 text-violet-200"><BrainCircuit size={14} /></span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-xs font-semibold text-gray-200">{bundle.purpose || '上下文检索'}</h3>
          <p className="mt-0.5 text-[9px] text-gray-600">{bundle.agent_role} · {bundle.chapter_no ? `第 ${bundle.chapter_no} 章` : '项目级'}{bundle.scene_no ? ` / 场景 ${bundle.scene_no}` : ''}</p>
        </div>
        <span className={cn('rounded-full border px-2 py-0.5 text-[9px]', bundle.cache_hit ? 'border-emerald-400/20 bg-emerald-400/8 text-emerald-200' : 'border-ink-600 text-gray-500')}>{bundle.cache_hit ? '检索缓存命中' : '实时检索'}</span>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2"><TinyMetric label="选中项" value={bundle.selected_items.length} /><TinyMetric label="使用 Token" value={bundle.used_tokens} /><TinyMetric label="预算 Token" value={bundle.token_budget} /></div>
      {bundle.query && <p className="mt-3 whitespace-pre-wrap break-words text-[10px] leading-5 text-gray-500"><span className="text-gray-700">查询：</span>{bundle.query}</p>}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {primaryItems.map((item, index) => <span key={`${bundle.id}-${index}`} title={recordLabel(item)} className="max-w-[14rem] truncate rounded-lg border border-ink-700 bg-ink-950 px-2 py-1 text-[9px] text-gray-500">{recordLabel(item)}</span>)}
      </div>
      {remainingItems.length > 0 && (
        <details className="mt-2 rounded-lg border border-ink-700 bg-ink-950/45 px-2.5 py-2">
          <summary className="cursor-pointer text-[9px] text-violet-200/70">展开其余 {number(remainingItems.length)} 个记忆项</summary>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {remainingItems.map((item, index) => <span key={`${bundle.id}-remaining-${index}`} className="max-w-full break-words rounded-lg border border-ink-700 bg-ink-950 px-2 py-1 text-[9px] text-gray-500">{recordLabel(item)}</span>)}
          </div>
        </details>
      )}
      <JsonEvidenceDetails label="查看完整记忆项与来源修订" value={{ selected_items: bundle.selected_items, source_revisions: bundle.source_revisions }} />
      {bundle.rendered_context && (
        <details className="mt-2 rounded-lg border border-ink-700 bg-ink-950/45 px-2.5 py-2">
          <summary className="cursor-pointer text-[9px] text-gray-500">查看实际注入的完整上下文</summary>
          <p className="mt-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-words text-[9px] leading-5 text-gray-600">{bundle.rendered_context}</p>
        </details>
      )}
      <div className="mt-3 flex items-center justify-between gap-3 border-t border-ink-700 pt-2 font-mono text-[9px] text-gray-700"><span>{timestamp(bundle.created_at)}</span><span title={bundle.content_hash}>{shortHash(bundle.content_hash)}</span></div>
    </article>
  )
}

function CallsView({ calls, searchTerm, windowLimited, chapterFiltered }: { calls: LlmCallEvidence[]; searchTerm: string; windowLimited: boolean; chapterFiltered: boolean }) {
  const matchingCalls = useMemo(
    () => calls.filter((call) => evidenceMatchesSearch(searchTerm, call)),
    [calls, searchTerm],
  )
  const [visible, setVisible] = useState(EVIDENCE_RENDER_PAGE_SIZE)

  useEffect(() => setVisible(EVIDENCE_RENDER_PAGE_SIZE), [searchTerm])

  if (!calls.length) return <EmptyEvidence title="还没有真实模型调用记录" description="只有经过统一调用内核的真实请求才会进入账本；这里不会用前端流程状态虚构调用次数。" />
  return (
    <div className="space-y-3">
      {windowLimited && <EvidenceWindowWarning limit={CALL_WINDOW_LIMIT} chapterFiltered={chapterFiltered} />}
      {matchingCalls.length ? (
        <div className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-900/55">
          <div className="hidden grid-cols-[minmax(14rem,1.4fr)_minmax(10rem,1fr)_8rem_8rem_8rem] gap-3 border-b border-ink-700 px-4 py-2 text-[9px] uppercase tracking-wider text-gray-700 lg:grid"><span>阶段 / 请求</span><span>模型 / 角色</span><span>Token</span><span>耗时 / 状态</span><span>时间</span></div>
          <div className="divide-y divide-ink-700">{matchingCalls.slice(0, visible).map((call) => <CallRow key={call.id} call={call} />)}</div>
        </div>
      ) : <EmptyEvidence title="没有匹配的模型调用" description="请更换检索词，或清除检索查看当前章节的全部已读取记录。" />}
      <EvidenceListProgress shown={Math.min(visible, matchingCalls.length)} total={matchingCalls.length} onLoadMore={() => setVisible((current) => nextEvidenceVisibleCount(current, matchingCalls.length))} />
    </div>
  )
}

function CallRow({ call }: { call: LlmCallEvidence }) {
  return (
    <article className="grid gap-3 px-4 py-3 lg:grid-cols-[minmax(14rem,1.4fr)_minmax(10rem,1fr)_8rem_8rem_8rem] lg:items-center">
      <div className="min-w-0"><div className="flex flex-wrap items-center gap-1.5"><span className="truncate text-[11px] font-semibold text-gray-200">{call.stage || call.purpose || '模型调用'}</span>{call.chapter_no && <span className="rounded bg-blue-400/10 px-1.5 py-0.5 text-[8px] text-blue-200">章 {call.chapter_no}{call.scene_no ? ` · 场景 ${call.scene_no}` : ''}</span>}</div><p className="mt-1 truncate font-mono text-[9px] text-gray-700" title={`request ${call.request_hash}\nprompt ${call.prompt_hash}`}>req {shortHash(call.request_hash)} · prompt {shortHash(call.prompt_hash)}</p>{call.provider_request_id && <p className="mt-0.5 truncate font-mono text-[8px] text-emerald-300/55" title={call.provider_request_id}>provider request {shortHash(call.provider_request_id)}</p>}</div>
      <div className="min-w-0"><p className="truncate text-[10px] text-gray-400" title={call.model_name}>{call.model_name || '未记录模型'}</p><p className="mt-0.5 truncate text-[9px] text-gray-700" title={`${call.provider_name} · ${call.agent_role}`}>{call.provider_name || '—'} · {call.agent_role || '—'}</p></div>
      <div><p className="text-[10px] text-gray-300">{number(call.input_tokens)} → {number(call.output_tokens)}</p><p className={cn('mt-0.5 text-[9px]', call.cached_input_tokens > 0 ? 'text-emerald-300' : 'text-gray-700')}>缓存 {number(call.cached_input_tokens)} · {call.usage_source || 'usage 未知'}</p></div>
      <div><span className={cn('inline-flex rounded border px-1.5 py-0.5 text-[9px]', statusTone(call.status))}>{call.status}</span><p className="mt-1 text-[9px] text-gray-700">{call.latency_ms == null ? '—' : `${number(call.latency_ms)} ms`}</p></div>
      <div><p className="text-[9px] text-gray-500">{timestamp(call.started_at)}</p><p className="mt-1 truncate font-mono text-[8px] text-gray-700" title={call.context_bundle_id ?? undefined}>{call.context_bundle_id ? `ctx ${shortHash(call.context_bundle_id)}` : '无上下文关联'}</p></div>
      {call.error && <p className="rounded-lg border border-red-400/15 bg-red-400/5 px-2 py-1 text-[9px] text-red-300 lg:col-span-5">{call.error}</p>}
      <div className="lg:col-span-5"><JsonEvidenceDetails label="查看完整调用审计记录" value={call} /></div>
    </article>
  )
}
