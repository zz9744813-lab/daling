import React, { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Brain,
  Users,
  Network,
  Lock,
  RefreshCw,
  Archive,
  CheckCircle2,
  Search,
  Loader2,
  BookOpenCheck,
  RotateCcw,
  AlertTriangle,
} from 'lucide-react'
import { TopBar } from '../layout/TopBar'
import { BossCommandBar } from '../layout/BossCommandBar'
import { AppShell, AppShellBody } from '../layout/AppShell'
import { bookMemoryApi, brainApi, canonFactsApi, governanceApi } from '../api/client'
import { useProjectStore } from '../store/projectStore'
import { Badge } from '../components/Badge'
import { Button, Input, TextArea, Card } from '../components/ui'
import { EmptyState } from '../components/EmptyState'
import { cn } from '../lib/cn'
import type { BookMemory, CanonFact, FactMutability, CanonCheckResult } from '../types'

/**
 * BrainPage —— 大脑
 * 角色卡片 + 关系图占位 + Canon Facts 面板（按 mutability 分组 + 手动确认 + 一致性检查）
 */
export default function BrainPage() {
  const project = useProjectStore((s) => s.currentProject)
  const projectId = project?.id ?? ''
  const queryClient = useQueryClient()

  const brainQuery = useQuery({
    queryKey: ['brain', projectId],
    queryFn: () => brainApi.get(projectId),
    enabled: !!projectId,
  })

  const canonFactsQuery = useQuery({
    queryKey: ['canon-facts', projectId],
    queryFn: () => canonFactsApi.list(projectId),
    enabled: !!projectId,
  })

  const memoriesQuery = useQuery({
    queryKey: ['book-memory', projectId],
    queryFn: () => bookMemoryApi.get(projectId),
    enabled: !!projectId,
  })

  const providersQuery = useQuery({
    queryKey: ['providers'],
    queryFn: governanceApi.listProviders,
  })

  const brain = brainQuery.data
  const canonFacts = canonFactsQuery.data
  const memories = memoriesQuery.data
  const providers = providersQuery.data
  const characters = brain?.characters ?? []
  const relationships = brain?.relationships ?? []

  const factsByMutability = useMemo(() => {
    const groups: Record<FactMutability, CanonFact[]> = {
      immutable: [],
      evolving: [],
      deprecated: [],
    }
    canonFacts?.forEach((f) => {
      const key = (f.mutability ?? 'evolving') as FactMutability
      groups[key]?.push(f)
    })
    return groups
  }, [canonFacts])

  const confirmMutation = useMutation({
    mutationFn: (factId: string) => canonFactsApi.confirm(projectId, factId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['canon-facts', projectId] })
    },
  })

  const memoryAction = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approve' | 'reject' | 'rollback' }) => {
      if (action === 'approve') return bookMemoryApi.approve(projectId, id, '大脑页面人工批准')
      if (action === 'reject') return bookMemoryApi.reject(projectId, id, '大脑页面人工驳回')
      return bookMemoryApi.rollback(projectId, id, '大脑页面人工回滚')
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['book-memory', projectId] })
      queryClient.invalidateQueries({ queryKey: ['evolution-overview', projectId] })
    },
  })

  // 一致性检查
  const [checkText, setCheckText] = useState('')
  const [checkResult, setCheckResult] = useState<CanonCheckResult | null>(null)
  const checkMutation = useMutation({
    mutationFn: (text: string) => canonFactsApi.check(projectId, text),
    onSuccess: (data) => setCheckResult(data),
  })

  const providerStatus = providersQuery.isLoading || providersQuery.isError
    ? 'unknown'
    : providers?.some((provider) => provider.status !== 'inactive')
      ? 'online'
      : 'offline'
  const initialLoading = [brainQuery, canonFactsQuery, memoriesQuery].some(
    (query) => query.isLoading && query.data == null,
  )
  const queryFailures = [
    brainQuery.isError ? '角色与关系' : null,
    canonFactsQuery.isError ? '设定事实' : null,
    memoriesQuery.isError ? '长期记忆' : null,
  ].filter((label): label is string => Boolean(label))

  return (
    <AppShell>
      <TopBar providerStatus={providerStatus} />
      <AppShellBody className="flex-col">
        <div className="border-b border-ink-700 px-4 py-3 sm:px-6">
          <h1 className="flex items-center gap-2 text-base font-medium text-gray-200">
            <Brain size={18} className="text-gold-500" />
            大脑
          </h1>
          <p className="mt-0.5 text-xs text-gray-500">角色 · 关系 · 设定事实库</p>
        </div>

        {initialLoading ? (
          <div className="flex min-h-0 flex-1 items-center justify-center gap-2 px-4 text-sm text-gray-500" role="status" aria-live="polite">
            <Loader2 size={16} className="animate-spin" /> 正在读取角色、设定与长期记忆…
          </div>
        ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
          {queryFailures.length > 0 && (
            <div className="mb-5 flex flex-col gap-3 rounded-xl border border-red-400/20 bg-red-400/8 p-3 text-xs text-red-100 sm:flex-row sm:items-center" role="alert">
              <span className="min-w-0 flex-1 break-words">以下数据读取失败，空白区域不代表没有数据：{queryFailures.join('、')}。</span>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  void brainQuery.refetch()
                  void canonFactsQuery.refetch()
                  void memoriesQuery.refetch()
                }}
              >
                <RefreshCw size={12} /> 重新读取
              </Button>
            </div>
          )}
          {confirmMutation.isError && (
            <p className="mb-5 rounded-xl border border-red-400/20 bg-red-400/8 p-3 text-xs text-red-100" role="alert">
              事实确认失败：{(confirmMutation.error as Error).message}
            </p>
          )}
          {/* 角色卡片 */}
          <section className="mb-8">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="flex items-center gap-1.5 text-sm font-medium text-gray-300">
                <Users size={15} />
                角色
                {characters.length > 0 && <Badge variant="outline">{characters.length}</Badge>}
              </h2>
            </div>
            {!characters || characters.length === 0 ? (
              <EmptyState
                icon={<Users size={24} />}
                title="暂无角色"
                description="让智能体从大纲中提取角色，或通过指令栏让记忆管家生成。"
              />
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {characters.map((c) => (
                  <Card key={c.id} className="hover:border-gold-500/30">
                    <div className="mb-2 flex items-center justify-between">
                        <h3 className="min-w-0 break-words font-serif text-base font-medium text-gray-100">{c.name}</h3>
                      {c.role && <Badge variant="gold">{c.role}</Badge>}
                    </div>
                    {c.description && (
                      <p className="line-clamp-2 break-words text-xs text-gray-500">{c.description}</p>
                    )}
                    {c.aliases && c.aliases.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {c.aliases.map((a) => (
                          <Badge key={a} variant="outline">
                            {a}
                          </Badge>
                        ))}
                      </div>
                    )}
                    {c.motivation && (
                      <p className="mt-2 break-words border-t border-ink-700 pt-2 text-[10px] text-gray-600">
                        动机: {c.motivation}
                      </p>
                    )}
                  </Card>
                ))}
              </div>
            )}
          </section>

          {/* 关系图占位 */}
          <section className="mb-8">
            <h2 className="mb-3 flex items-center gap-1.5 text-sm font-medium text-gray-300">
              <Network size={15} />
              关系图
            </h2>
            <RelationshipGraph characters={characters} relationships={relationships} />
          </section>

          {/* 一致性检查 */}
          <section className="mb-8">
            <h2 className="mb-3 flex items-center gap-1.5 text-sm font-medium text-gray-300">
              <Search size={15} />
              一致性检查
            </h2>
            <Card>
              <TextArea
                value={checkText}
                onChange={(e) => {
                  setCheckText(e.target.value)
                  setCheckResult(null)
                  if (checkMutation.isError) checkMutation.reset()
                }}
                rows={3}
                placeholder="输入待检查的文本段落，连续性守卫将检查是否与已有设定冲突…"
              />
              <div className="mt-2 flex items-center gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => checkText.trim() && checkMutation.mutate(checkText)}
                  disabled={!checkText.trim() || checkMutation.isPending}
                >
                  {checkMutation.isPending ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <Search size={13} />
                  )}
                  检查
                </Button>
                {checkResult && (
                  <div
                    className={cn(
                      'flex items-center gap-1.5 text-xs',
                      checkResult.consistent ? 'text-green-400' : 'text-amber-400',
                    )}
                    role="status"
                    aria-live="polite"
                  >
                    {checkResult.consistent ? (
                      <CheckCircle2 size={13} />
                    ) : (
                      <Archive size={13} />
                    )}
                    {checkResult.consistent
                      ? '未发现冲突'
                      : `发现 ${checkResult.conflicts?.length ?? 0} 处冲突`}
                  </div>
                )}
              </div>
              {checkMutation.isError && (
                <p className="mt-2 rounded-lg border border-red-400/20 bg-red-400/8 p-2 text-xs text-red-200" role="alert">
                  一致性检查失败：{(checkMutation.error as Error).message}
                </p>
              )}
              {checkResult && !checkResult.consistent && checkResult.conflicts && (
                <div className="mt-2 space-y-1">
                  {checkResult.conflicts.map((c, i) => (
                    <div
                      key={i}
                      className="rounded border-l-2 border-amber-500 bg-amber-500/5 px-2 py-1 text-xs text-gray-400"
                    >
                      {c.message}
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </section>

          <MemoryGovernancePanel
            memories={memories ?? []}
            busy={memoryAction.isPending}
            activeId={memoryAction.variables?.id}
            error={memoryAction.error as Error | null}
            onAction={(id, action) => memoryAction.mutate({ id, action })}
          />

          {/* Canon Facts 按可变性分组（v5.0） */}
          <section>
            <h2 className="mb-3 flex items-center gap-1.5 text-sm font-medium text-gray-300">
              <Lock size={15} />
              设定事实库
              {canonFacts && canonFacts.length > 0 && (
                <Badge variant="outline">{canonFacts.length}</Badge>
              )}
            </h2>
            {!canonFacts || canonFacts.length === 0 ? (
              <EmptyState
                icon={<Lock size={24} />}
                title="暂无设定事实"
                description="连续性守卫将自动从已定稿章节中提取并维护事实。"
              />
            ) : (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <FactGroup
                  title="不可变 (Immutable)"
                  icon={<Lock size={14} />}
                  variant="green"
                  facts={factsByMutability.immutable}
                  projectId={projectId}
                  onConfirm={(factId) => confirmMutation.mutate(factId)}
                  confirming={confirmMutation.isPending}
                />
                <FactGroup
                  title="可演变 (Evolving)"
                  icon={<RefreshCw size={14} />}
                  variant="blue"
                  facts={factsByMutability.evolving}
                  projectId={projectId}
                  onConfirm={(factId) => confirmMutation.mutate(factId)}
                  confirming={confirmMutation.isPending}
                />
                <FactGroup
                  title="已废弃 (Deprecated)"
                  icon={<Archive size={14} />}
                  variant="gray"
                  facts={factsByMutability.deprecated}
                  projectId={projectId}
                  onConfirm={(factId) => confirmMutation.mutate(factId)}
                  confirming={confirmMutation.isPending}
                />
              </div>
            )}
          </section>
        </div>
        )}
      </AppShellBody>
      <BossCommandBar />
    </AppShell>
  )
}

function MemoryGovernancePanel({
  memories,
  busy,
  activeId,
  error,
  onAction,
}: {
  memories: BookMemory[]
  busy: boolean
  activeId?: string
  error: Error | null
  onAction: (id: string, action: 'approve' | 'reject' | 'rollback') => void
}) {
  return (
    <section className="mb-8">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="flex items-center gap-1.5 text-sm font-medium text-gray-300">
          <BookOpenCheck size={15} />
          长期记忆
        </h2>
        <Badge variant="outline">{memories.length}</Badge>
        <span className="text-[10px] text-gray-600">停用条目不会进入下一章上下文</span>
      </div>
      {error && (
        <p className="mb-3 rounded-lg border border-red-400/20 bg-red-400/8 p-2 text-xs text-red-200">
          {error.message}
        </p>
      )}
      {memories.length === 0 ? (
        <EmptyState
          icon={<BookOpenCheck size={24} />}
          title="暂无长期记忆"
          description="审稿证据与人工修改形成的学习规则会出现在这里。"
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {memories.map((memory) => {
            const isActive = memory.status === 'active' || memory.status === 'approved'
            const working = busy && activeId === memory.id
            return (
              <Card key={memory.id} className="bg-ink-850">
                <div className="flex items-center gap-2">
                  <Badge variant="outline">{memory.memory_type}</Badge>
                  <span className="min-w-0 flex-1 truncate text-xs font-medium text-gray-300">
                    {memory.key}
                  </span>
                  <Badge variant={isActive ? 'green' : memory.status === 'rejected' ? 'red' : 'gray'}>
                    {isActive ? '生效' : memory.status === 'rejected' ? '已驳回' : '已回滚'}
                  </Badge>
                </div>
                <p className="mt-2 line-clamp-4 break-words text-[11px] leading-5 text-gray-500">
                  {memoryValueText(memory.value)}
                </p>
                <div className="mt-2 text-[10px] text-gray-600">
                  <p className="break-words">来源：{memory.source || memory.governance?.origin || '未记录'}</p>
                  <p>置信度：{Math.round((memory.confidence ?? 0) * 100)}%</p>
                  {memory.governance?.reviewed_at && (
                    <p className="break-words">最近治理：{memory.governance.reviewed_by || 'system'} · {formatMemoryTime(memory.governance.reviewed_at)}</p>
                  )}
                  {memory.governance?.reason && <p className="line-clamp-2">原因：{memory.governance.reason}</p>}
                </div>
                <div className="mt-3 flex flex-wrap gap-2 border-t border-ink-700 pt-3">
                  {!isActive ? (
                    <Button size="sm" variant="secondary" disabled={busy} onClick={() => onAction(memory.id, 'approve')}>
                      {working ? <Loader2 size={11} className="animate-spin" /> : <CheckCircle2 size={11} />}
                      批准恢复
                    </Button>
                  ) : (
                    <>
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAction(memory.id, 'rollback')}>
                        {working ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />}
                        回滚
                      </Button>
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAction(memory.id, 'reject')}>
                        <AlertTriangle size={11} />
                        驳回
                      </Button>
                    </>
                  )}
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </section>
  )
}

function memoryValueText(value: Record<string, unknown>) {
  const primary = value.instruction ?? value.text ?? value.overall_summary
  if (primary) return String(primary)
  return Object.entries(value)
    .slice(0, 6)
    .map(([key, item]) => `${key}: ${typeof item === 'string' ? item : JSON.stringify(item)}`)
    .join('；')
}

function formatMemoryTime(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(date)
}

/* ============================================================
 * 关系图占位（SVG）
 * ============================================================ */
function RelationshipGraph({
  characters,
  relationships,
}: {
  characters: { id: string; name: string }[]
  relationships: { from_character_id: string; to_character_id: string; relation_type: string }[]
}) {
  if (characters.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center rounded-lg border border-dashed border-ink-600 bg-ink-850 text-sm text-gray-600">
        <Network size={28} className="mr-2 text-gray-700" />
        暂无角色数据，无法绘制关系图
      </div>
    )
  }

  // 简单圆形布局
  const radius = 120
  const centerX = 200
  const centerY = 120
  const positions = characters.slice(0, 8).map((c, i) => {
    const angle = (i / Math.min(characters.length, 8)) * Math.PI * 2 - Math.PI / 2
    return {
      ...c,
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
    }
  })

  return (
    <div className="rounded-lg border border-ink-700 bg-ink-850 p-4">
      <svg viewBox="0 0 400 240" className="w-full">
        {/* 关系连线 */}
        {relationships.map((rel, i) => {
          const from = positions.find((p) => p.id === rel.from_character_id)
          const to = positions.find((p) => p.id === rel.to_character_id)
          if (!from || !to) return null
          return (
            <g key={i}>
              <line
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                stroke="#2d3242"
                strokeWidth="1"
                strokeDasharray="3,3"
              />
              <text
                x={(from.x + to.x) / 2}
                y={(from.y + to.y) / 2}
                fill="#6b7280"
                fontSize="8"
                textAnchor="middle"
              >
                {rel.relation_type}
              </text>
            </g>
          )
        })}
        {/* 角色节点 */}
        {positions.map((c) => (
          <g key={c.id}>
            <circle cx={c.x} cy={c.y} r="18" fill="#1a1d29" stroke="#c9a227" strokeWidth="1.5" />
            <text
              x={c.x}
              y={c.y + 3}
              fill="#e7e5e0"
              fontSize="9"
              textAnchor="middle"
            >
              {c.name.slice(0, 2)}
            </text>
          </g>
        ))}
      </svg>
      {relationships.length === 0 && (
        <p className="text-center text-xs text-gray-600">暂无关系数据</p>
      )}
    </div>
  )
}

/* ============================================================
 * Fact Group —— 按可变性分组的设定事实
 * ============================================================ */
function FactGroup({
  title,
  icon,
  variant,
  facts,
  projectId,
  onConfirm,
  confirming,
}: {
  title: string
  icon: React.ReactNode
  variant: 'green' | 'blue' | 'gray'
  facts: CanonFact[]
  projectId: string
  onConfirm: (factId: string) => void
  confirming: boolean
}) {
  return (
    <div>
      <div className="mb-2 flex items-center gap-1.5">
        <span className="text-gray-400">{icon}</span>
        <span className="text-xs font-medium text-gray-400">{title}</span>
        <Badge variant={variant}>{facts.length}</Badge>
      </div>
      <div className="space-y-2">
        {facts.length === 0 ? (
          <p className="rounded-md border border-ink-700 bg-ink-850 px-3 py-3 text-xs text-gray-600">
            无
          </p>
        ) : (
          facts.map((f) => (
              <div
                key={f.id}
                className="rounded-md border border-ink-700 bg-ink-850 px-3 py-2"
              >
              {/* subject → predicate → object */}
              <div className="flex min-w-0 flex-wrap items-center gap-1 text-xs">
                <Badge variant="outline" className="max-w-full break-all">{f.subject_type}</Badge>
                <span className="min-w-0 break-words font-medium text-gray-300">
                  {f.subject_name || f.subject_id}
                </span>
                <span className="text-gray-600">→</span>
                <span className="break-words text-gray-400">{f.predicate}</span>
                <span className="text-gray-600">→</span>
                <span className="min-w-0 break-words text-gray-200">{f.object_value}</span>
              </div>

              {/* 来源 & 确认章节 */}
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-gray-600">
                {f.source_chapter != null && (
                  <span>来源: 第 {f.source_chapter} 章</span>
                )}
                {f.confirmed_chapter != null && (
                  <span className="text-green-600">确认: 第 {f.confirmed_chapter} 章</span>
                )}
                {f.confidence != null && (
                  <span>置信度: {Math.round(f.confidence * 100)}%</span>
                )}
              </div>

              {/* 确认按钮 */}
              {!f.confirmed && f.mutability !== 'deprecated' && (
                <button
                  onClick={() => onConfirm(f.id)}
                  disabled={confirming}
                  className="mt-1.5 flex items-center gap-1 text-[10px] text-gold-500 hover:text-gold-400 disabled:opacity-40"
                >
                  {confirming ? (
                    <Loader2 size={10} className="animate-spin" />
                  ) : (
                    <CheckCircle2 size={10} />
                  )}
                  确认事实
                </button>
              )}
              {f.confirmed && (
                <span className="mt-1.5 flex items-center gap-1 text-[10px] text-green-500">
                  <CheckCircle2 size={10} />
                  已确认
                </span>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
