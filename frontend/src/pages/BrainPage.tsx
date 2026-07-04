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
} from 'lucide-react'
import { TopBar } from '../layout/TopBar'
import { BossCommandBar } from '../layout/BossCommandBar'
import { AppShell, AppShellBody } from '../layout/AppShell'
import { brainApi, canonFactsApi, governanceApi } from '../api/client'
import { useProjectStore } from '../store/projectStore'
import { Badge } from '../components/Badge'
import { Button, Input, TextArea, Card } from '../components/ui'
import { EmptyState } from '../components/EmptyState'
import { cn } from '../lib/cn'
import type { CanonFact, FactMutability, CanonCheckResult } from '../types'

/**
 * BrainPage —— 大脑
 * 角色卡片 + 关系图占位 + Canon Facts 面板（按 mutability 分组 + 手动确认 + 一致性检查）
 */
export default function BrainPage() {
  const project = useProjectStore((s) => s.currentProject)
  const projectId = project?.id ?? ''
  const queryClient = useQueryClient()

  const { data: brain } = useQuery({
    queryKey: ['brain', projectId],
    queryFn: () => brainApi.get(projectId),
    enabled: !!projectId,
  })

  const { data: canonFacts } = useQuery({
    queryKey: ['canon-facts', projectId],
    queryFn: () => canonFactsApi.list(projectId),
    enabled: !!projectId,
  })

  const { data: providers } = useQuery({
    queryKey: ['providers'],
    queryFn: governanceApi.listProviders,
  })

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

  // 一致性检查
  const [checkText, setCheckText] = useState('')
  const [checkResult, setCheckResult] = useState<CanonCheckResult | null>(null)
  const checkMutation = useMutation({
    mutationFn: (text: string) => canonFactsApi.check(projectId, text),
    onSuccess: (data) => setCheckResult(data),
  })

  const providerStatus = (providers?.length ?? 0) > 0 ? 'online' : 'offline'

  return (
    <AppShell>
      <TopBar providerStatus={providerStatus as 'online' | 'offline'} />
      <AppShellBody className="flex-col">
        <div className="border-b border-ink-700 px-6 py-3">
          <h1 className="flex items-center gap-2 text-base font-medium text-gray-200">
            <Brain size={18} className="text-gold-500" />
            大脑
          </h1>
          <p className="mt-0.5 text-xs text-gray-500">角色 · 关系 · 设定事实库</p>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
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
                      <h3 className="font-serif text-base font-medium text-gray-100">{c.name}</h3>
                      {c.role && <Badge variant="gold">{c.role}</Badge>}
                    </div>
                    {c.description && (
                      <p className="line-clamp-2 text-xs text-gray-500">{c.description}</p>
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
                      <p className="mt-2 border-t border-ink-700 pt-2 text-[10px] text-gray-600">
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
                onChange={(e) => setCheckText(e.target.value)}
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
      </AppShellBody>
      <BossCommandBar />
    </AppShell>
  )
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
              <div className="flex items-center gap-1 text-xs">
                <Badge variant="outline">{f.subject_type}</Badge>
                <span className="font-medium text-gray-300">
                  {f.subject_name || f.subject_id}
                </span>
                <span className="text-gray-600">→</span>
                <span className="text-gray-400">{f.predicate}</span>
                <span className="text-gray-600">→</span>
                <span className="text-gray-200">{f.object_value}</span>
              </div>

              {/* 来源 & 确认章节 */}
              <div className="mt-1.5 flex items-center gap-3 text-[10px] text-gray-600">
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
