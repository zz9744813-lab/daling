import React, { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileText,
  FileUp,
  GitBranch,
  Layers3,
  ListTree,
  Loader2,
  LockKeyhole,
  Pencil,
  RefreshCw,
  Save,
  Sparkles,
  X,
} from 'lucide-react'
import { pipelineApi, projectsApi, storylineApi, governanceApi, continuousApi } from '../api/client'
import { Badge } from '../components/Badge'
import { EmptyState } from '../components/EmptyState'
import { AppShell, AppShellBody } from '../layout/AppShell'
import { BossCommandBar } from '../layout/BossCommandBar'
import { TopBar } from '../layout/TopBar'
import { cn } from '../lib/cn'
import { useProjectStore } from '../store/projectStore'
import {
  CHAPTER_STATUS_MAP,
  type Chapter,
  type ChapterStatus,
  type StorylineBeat,
  type StorylineOverview,
  type StorylineVolume,
} from '../types'

type EditTarget = {
  kind: 'volume' | 'beat'
  id: string
  label: string
  title: string
  summary: string
}

function formatDate(value?: string | null) {
  if (!value) return '尚未记录'
  const time = new Date(value)
  if (Number.isNaN(time.getTime())) return '尚未记录'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(time)
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试。'
}

export default function StorylinePage() {
  const project = useProjectStore((state) => state.currentProject)
  const projectId = project?.id ?? ''
  const queryClient = useQueryClient()
  const uploadRef = useRef<HTMLInputElement>(null)
  const [editTarget, setEditTarget] = useState<EditTarget | null>(null)
  const [volumeCount, setVolumeCount] = useState(1)
  const [chaptersPerVolume, setChaptersPerVolume] = useState(10)
  const [notice, setNotice] = useState<string | null>(null)

  const storylineQuery = useQuery({
    queryKey: ['storyline', projectId],
    queryFn: () => storylineApi.get(projectId),
    enabled: Boolean(projectId),
  })
  const providersQuery = useQuery({
    queryKey: ['providers'],
    queryFn: governanceApi.listProviders,
  })
  const continuousQuery = useQuery({
    queryKey: ['continuous-status', projectId],
    queryFn: () => continuousApi.status(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 5_000,
  })

  const storyline = storylineQuery.data
  const volumes = storyline?.volumes ?? []
  const chapters = storyline?.chapters ?? []
  const productionLocked = continuousQuery.data?.desired_state === 'running'
  const providerStatus = (providersQuery.data?.length ?? 0) > 0 ? 'online' : 'offline'

  const chaptersByBeat = useMemo(() => {
    const result: Record<string, Chapter[]> = {}
    chapters.forEach((chapter) => {
      const key = chapter.beat_id ?? '_unassigned'
      if (!result[key]) result[key] = []
      result[key].push(chapter)
    })
    return result
  }, [chapters])

  const refresh = async (next?: StorylineOverview) => {
    if (next) queryClient.setQueryData(['storyline', projectId], next)
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['storyline', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['preparation-status', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['outline-info', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['chapters', projectId] }),
    ])
  }

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      if (productionLocked) throw new Error('24H 自动写作正在运行，请先暂停再更换大纲。')
      if (!/\.(docx|txt|md|markdown)$/i.test(file.name)) {
        throw new Error('仅支持 DOCX、TXT、MD 或 MARKDOWN 文件。')
      }
      if (file.size > 5 * 1024 * 1024) throw new Error('大纲文件不能超过 5 MB。')
      return projectsApi.uploadOutline(projectId, file)
    },
    onSuccess: async (result) => {
      setNotice(
        storyline?.artifact.exists
          ? '新大纲已保存；现有故事线已标记为待同步，不会被静默覆盖。'
          : `大纲“${result.filename ?? '未命名文件'}”已解析，可生成故事线。`,
      )
      await refresh()
    },
  })

  const generateMutation = useMutation({
    mutationFn: async () => {
      if (productionLocked) throw new Error('24H 自动写作正在运行，请先暂停再生成结构。')
      const artifact = storyline?.artifact
      const replacing = Boolean(artifact?.exists)
      if (replacing && !artifact?.can_replace) {
        throw new Error(
          `已有正文的章节不能整体替换：${artifact?.replace_blocked_by_chapters.join('、') || '未知章节'}`,
        )
      }
      return pipelineApi.generateOutline(projectId, {
        volume_count: Math.max(1, volumeCount),
        chapters_per_volume: Math.max(1, chaptersPerVolume),
        replace_existing: replacing,
        expected_revision: replacing ? artifact?.structure_revision : undefined,
      })
    },
    onSuccess: async () => {
      setNotice(storyline?.artifact.exists ? '故事线已安全替换并生成新修订。' : '故事线已生成。')
      await refresh()
    },
  })

  const editMutation = useMutation({
    mutationFn: async (target: EditTarget) => {
      const revision = storyline?.artifact.structure_revision
      if (!revision) throw new Error('缺少故事线修订号，请刷新后重试。')
      const payload = {
        expected_revision: revision,
        title: target.title.trim(),
        summary: target.summary.trim(),
      }
      return target.kind === 'volume'
        ? storylineApi.updateVolume(projectId, target.id, payload)
        : storylineApi.updateBeat(projectId, target.id, payload)
    },
    onSuccess: async (next) => {
      setEditTarget(null)
      setNotice('结构修改已保存，并生成新的故事线修订。')
      await refresh(next)
    },
  })

  const operationError =
    uploadMutation.error ?? generateMutation.error ?? editMutation.error ?? storylineQuery.error

  return (
    <AppShell>
      <TopBar providerStatus={providerStatus as 'online' | 'offline'} />
      <AppShellBody className="flex-col overflow-hidden">
        <header className="border-b border-ink-700 bg-ink-950/75 px-4 py-4 sm:px-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-gold-400/20 bg-gold-400/8 text-gold-300">
                  <GitBranch size={17} />
                </span>
                <div>
                  <h1 className="text-base font-semibold text-gray-100">故事线工作台</h1>
                  <p className="mt-0.5 text-[11px] text-gray-500">来源可追溯 · 修订可审计 · 已开写结构受保护</p>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <input
                ref={uploadRef}
                type="file"
                accept=".docx,.txt,.md,.markdown"
                className="sr-only"
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  event.target.value = ''
                  if (file) uploadMutation.mutate(file)
                }}
              />
              <ActionButton
                icon={uploadMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <FileUp size={13} />}
                label={storyline?.source.present ? '更换大纲' : '上传大纲'}
                onClick={() => uploadRef.current?.click()}
                disabled={productionLocked || uploadMutation.isPending}
              />
              <ActionButton
                icon={<RefreshCw size={13} className={storylineQuery.isFetching ? 'animate-spin' : ''} />}
                label="刷新"
                onClick={() => storylineQuery.refetch()}
                disabled={storylineQuery.isFetching}
              />
              <ActionButton
                icon={generateMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                label={storyline?.artifact.exists ? '安全重建' : '生成故事线'}
                onClick={() => {
                  const replacing = Boolean(storyline?.artifact.exists)
                  if (
                    replacing &&
                    !window.confirm(
                      '将原子替换尚未开写的卷章结构。旧结构只有在新结构成功生成后才会删除，是否继续？',
                    )
                  ) return
                  generateMutation.mutate()
                }}
                disabled={
                  productionLocked ||
                  generateMutation.isPending ||
                  Boolean(storyline?.artifact.exists && !storyline.artifact.can_replace)
                }
                primary
              />
            </div>
          </div>

          {storyline && (
            <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1.35fr)_repeat(4,minmax(100px,.45fr))]">
              <SourceCard storyline={storyline} />
              <MetricCard icon={<Layers3 size={14} />} label="卷" value={storyline.stats.volume_count} />
              <MetricCard icon={<ListTree size={14} />} label="节拍" value={storyline.stats.beat_count} />
              <MetricCard icon={<BookOpen size={14} />} label="章节" value={storyline.stats.chapter_count} />
              <MetricCard
                icon={<LockKeyhole size={14} />}
                label="已锁定"
                value={storyline.stats.locked_chapter_count}
                warning={storyline.stats.locked_chapter_count > 0}
              />
            </div>
          )}
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
          {productionLocked && (
            <Banner tone="blue" icon={<LockKeyhole size={15} />}>
              24H 自动生产正在运行。故事线保持只读；请先在总控台暂停，才能上传、重建或编辑结构。
            </Banner>
          )}
          {storyline?.artifact.stale && (
            <Banner tone="amber" icon={<AlertTriangle size={15} />}>
              上传大纲已更新到来源修订 {storyline.source.revision}，当前结构仍基于修订{' '}
              {storyline.artifact.based_on_source_revision ?? '未知'}。
              {storyline.artifact.can_replace
                ? ' 可使用“安全重建”同步；只有新结构完整生成后才会原子替换旧结构。'
                : ` 已开写章节 ${storyline.artifact.replace_blocked_by_chapters.join('、')} 受保护，不能整体替换。`}
            </Banner>
          )}
          {notice && (
            <Banner tone="green" icon={<CheckCircle2 size={15} />} onClose={() => setNotice(null)}>
              {notice}
            </Banner>
          )}
          {operationError && (
            <Banner tone="red" icon={<AlertTriangle size={15} />}>
              {errorText(operationError)}
            </Banner>
          )}

          <section className="mb-4 flex flex-wrap items-end gap-3 rounded-2xl border border-ink-700 bg-ink-900/70 p-4">
            <div className="min-w-[190px] flex-1">
              <p className="text-xs font-semibold text-gray-200">结构生成参数</p>
              <p className="mt-1 text-[10px] text-gray-600">上传大纲存在明确卷章时，架构师会优先忠实提取，而非强行套用数量。</p>
            </div>
            <NumberInput label="目标卷数" value={volumeCount} max={50} onChange={setVolumeCount} />
            <NumberInput label="每卷章节" value={chaptersPerVolume} max={200} onChange={setChaptersPerVolume} />
          </section>

          {storylineQuery.isLoading ? (
            <div className="flex min-h-[360px] items-center justify-center gap-2 text-sm text-gray-500">
              <Loader2 size={16} className="animate-spin" /> 正在读取故事线与来源证据…
            </div>
          ) : volumes.length === 0 ? (
            <EmptyState
              icon={<BookOpen size={28} />}
              title={storyline?.source.present ? '大纲已就绪，尚未生成结构' : '从蓝图或已有大纲建立故事线'}
              description={
                storyline?.source.present
                  ? `已读取“${storyline.source.filename ?? '上传大纲'}”。点击“生成故事线”提取卷、节拍和章节。`
                  : '可以直接根据项目蓝图生成，也可以先上传 DOCX / TXT / MD 大纲。'
              }
              className="min-h-[360px] rounded-2xl border border-dashed border-ink-600 bg-ink-900/35"
            />
          ) : (
            <div className="space-y-4 pb-12">
              {volumes.map((volume) => (
                <VolumeRow
                  key={volume.id}
                  volume={volume}
                  chaptersByBeat={chaptersByBeat}
                  productionLocked={productionLocked}
                  onEdit={setEditTarget}
                />
              ))}
              {(chaptersByBeat._unassigned?.length ?? 0) > 0 && (
                <section className="rounded-2xl border border-dashed border-amber-400/20 bg-amber-400/[0.025] p-4">
                  <p className="text-xs font-semibold text-amber-100">未关联节拍的章节</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {chaptersByBeat._unassigned.map((chapter) => (
                      <ChapterStatusChip key={chapter.id} chapter={chapter} />
                    ))}
                  </div>
                </section>
              )}
            </div>
          )}
        </main>
      </AppShellBody>
      <BossCommandBar />

      {editTarget && (
        <EditDialog
          target={editTarget}
          revision={storyline?.artifact.structure_revision ?? 0}
          loading={editMutation.isPending}
          error={editMutation.error ? errorText(editMutation.error) : null}
          onChange={setEditTarget}
          onClose={() => !editMutation.isPending && setEditTarget(null)}
          onSave={() => editMutation.mutate(editTarget)}
        />
      )}
    </AppShell>
  )
}

function VolumeRow({
  volume,
  chaptersByBeat,
  productionLocked,
  onEdit,
}: {
  volume: StorylineVolume
  chaptersByBeat: Record<string, Chapter[]>
  productionLocked: boolean
  onEdit: (target: EditTarget) => void
}) {
  const [open, setOpen] = useState(true)
  const locked = Boolean(volume.structure_locked || productionLocked)
  return (
    <article className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-900 shadow-[0_18px_50px_rgba(0,0,0,.13)]">
      <div className="flex items-center gap-3 px-4 py-4 sm:px-5">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex h-8 w-8 items-center justify-center rounded-lg text-gray-500 hover:bg-ink-700 hover:text-gray-200"
          aria-label={open ? '折叠卷' : '展开卷'}
        >
          {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </button>
        <Badge variant="gold">卷 {volume.volume_index}</Badge>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate font-serif text-base font-semibold text-gray-100">{volume.title}</h2>
            {volume.structure_locked && (
              <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/20 bg-amber-400/8 px-2 py-0.5 text-[9px] text-amber-200">
                <LockKeyhole size={9} /> 含已开写章节
              </span>
            )}
          </div>
          <p className="mt-1 text-[10px] text-gray-600">
            {volume.beats?.length ?? 0} 个节拍 · 目标 {volume.target_chapters ?? volume.beats?.length ?? 0} 章
          </p>
        </div>
        <button
          type="button"
          disabled={locked}
          title={locked ? '自动生产中或本卷已有正文，结构已锁定' : '编辑本卷'}
          onClick={() =>
            onEdit({
              kind: 'volume',
              id: volume.id,
              label: `卷 ${volume.volume_index}`,
              title: volume.title,
              summary: volume.summary ?? '',
            })
          }
          className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-ink-600 px-2.5 text-[10px] text-gray-400 hover:border-ink-500 hover:text-gray-100 disabled:cursor-not-allowed disabled:opacity-30"
        >
          {locked ? <LockKeyhole size={11} /> : <Pencil size={11} />} 编辑
        </button>
      </div>

      {open && (
        <div className="border-t border-ink-700 bg-ink-950/28 px-4 py-4 sm:px-5">
          {volume.summary && <p className="mb-4 max-w-4xl text-xs leading-6 text-gray-500">{volume.summary}</p>}
          <div className="relative space-y-3 before:absolute before:bottom-4 before:left-[15px] before:top-4 before:w-px before:bg-ink-600">
            {(volume.beats ?? []).map((beat) => (
              <BeatRow
                key={beat.id}
                beat={beat}
                chapters={chaptersByBeat[beat.id] ?? []}
                productionLocked={productionLocked}
                onEdit={onEdit}
              />
            ))}
          </div>
        </div>
      )}
    </article>
  )
}

function BeatRow({
  beat,
  chapters,
  productionLocked,
  onEdit,
}: {
  beat: StorylineBeat
  chapters: Chapter[]
  productionLocked: boolean
  onEdit: (target: EditTarget) => void
}) {
  const locked = Boolean(beat.structure_locked || productionLocked)
  return (
    <div className="relative ml-1 rounded-xl border border-ink-700 bg-ink-900/90 p-3 pl-10 sm:p-4 sm:pl-11">
      <span className="absolute left-[9px] top-5 z-10 flex h-3 w-3 items-center justify-center rounded-full border border-gold-400/60 bg-ink-950">
        <span className="h-1 w-1 rounded-full bg-gold-300" />
      </span>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[9px] font-bold uppercase tracking-[.16em] text-gold-400/75">
              Beat {beat.beat_index}
            </span>
            {beat.chapter_number && <span className="text-[10px] text-gray-600">第 {beat.chapter_number} 章</span>}
            {beat.importance && beat.importance !== 'normal' && <Badge variant="outline">{beat.importance}</Badge>}
          </div>
          <h3 className="mt-1 text-sm font-semibold text-gray-200">{beat.title}</h3>
          {beat.summary && <p className="mt-1.5 text-[11px] leading-5 text-gray-500">{beat.summary}</p>}
          {chapters.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {chapters.map((chapter) => (
                <ChapterStatusChip key={chapter.id} chapter={chapter} />
              ))}
            </div>
          )}
        </div>
        <button
          type="button"
          disabled={locked}
          title={locked ? beat.lock_reason ?? '本节拍已锁定' : '编辑节拍'}
          onClick={() =>
            onEdit({
              kind: 'beat',
              id: beat.id,
              label: `节拍 ${beat.beat_index}`,
              title: beat.title,
              summary: beat.summary ?? '',
            })
          }
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-gray-600 hover:bg-ink-700 hover:text-gray-200 disabled:cursor-not-allowed disabled:opacity-25"
        >
          {locked ? <LockKeyhole size={12} /> : <Pencil size={12} />}
        </button>
      </div>
    </div>
  )
}

function ChapterStatusChip({ chapter }: { chapter: Chapter }) {
  const statusInfo = CHAPTER_STATUS_MAP[chapter.status as ChapterStatus] ?? {
    label: chapter.status,
    color: 'gray',
  }
  return (
    <span
      className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-ink-600 bg-ink-950 px-2 py-1 text-[10px] text-gray-400"
      title={`第 ${chapter.chapter_number} 章 · ${statusInfo.label}${chapter.structure_locked ? ' · 结构已锁定' : ''}`}
    >
      <span
        className={cn(
          'h-1.5 w-1.5 shrink-0 rounded-full',
          chapter.status === 'finalized' && 'bg-emerald-400',
          chapter.status === 'in_progress' && 'bg-blue-400',
          (chapter.status === 'planned' || chapter.status === 'draft') && 'bg-gray-500',
        )}
      />
      <span className="text-gray-600">{chapter.chapter_number}</span>
      <span className="max-w-[220px] truncate text-gray-300">{chapter.title}</span>
      {chapter.word_count ? <span className="text-gray-700">{chapter.word_count.toLocaleString()}字</span> : null}
      {chapter.structure_locked && <LockKeyhole size={9} className="text-amber-400/70" />}
    </span>
  )
}

function SourceCard({ storyline }: { storyline: StorylineOverview }) {
  const sourceLabel = storyline.source.present
    ? storyline.source.filename ?? '上传大纲'
    : '项目创作蓝图'
  return (
    <div className="rounded-xl border border-ink-700 bg-ink-900/85 p-3">
      <div className="flex items-start gap-2.5">
        <FileText size={15} className="mt-0.5 shrink-0 text-blue-300" />
        <div className="min-w-0">
          <p className="truncate text-xs font-semibold text-gray-200">{sourceLabel}</p>
          <p className="mt-1 text-[9px] text-gray-600">
            来源修订 {storyline.source.revision || '—'} · 结构修订 {storyline.artifact.structure_revision || '—'} ·{' '}
            {formatDate(storyline.source.updated_at ?? storyline.artifact.updated_at)}
          </p>
        </div>
      </div>
    </div>
  )
}

function MetricCard({
  icon,
  label,
  value,
  warning,
}: {
  icon: React.ReactNode
  label: string
  value: number
  warning?: boolean
}) {
  return (
    <div className={cn('rounded-xl border bg-ink-900/85 p-3', warning ? 'border-amber-400/20' : 'border-ink-700')}>
      <div className={cn('flex items-center gap-1.5 text-[9px]', warning ? 'text-amber-300' : 'text-gray-600')}>
        {icon} {label}
      </div>
      <p className="mt-1 text-lg font-semibold text-gray-100">{value.toLocaleString()}</p>
    </div>
  )
}

function ActionButton({
  icon,
  label,
  onClick,
  disabled,
  primary,
}: {
  icon: React.ReactNode
  label: string
  onClick: () => void
  disabled?: boolean
  primary?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex h-9 items-center gap-1.5 rounded-xl border px-3 text-[11px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-35',
        primary
          ? 'border-emerald-300/30 bg-emerald-300 text-emerald-950 hover:bg-emerald-200'
          : 'border-ink-600 bg-ink-900 text-gray-300 hover:border-ink-500 hover:bg-ink-800',
      )}
    >
      {icon} {label}
    </button>
  )
}

function NumberInput({
  label,
  value,
  max,
  onChange,
}: {
  label: string
  value: number
  max: number
  onChange: (value: number) => void
}) {
  return (
    <label className="block w-[118px]">
      <span className="text-[9px] text-gray-600">{label}</span>
      <input
        type="number"
        min={1}
        max={max}
        value={value}
        onChange={(event) => onChange(Math.min(max, Math.max(1, Number(event.target.value) || 1)))}
        className="mt-1 h-9 w-full rounded-lg border border-ink-600 bg-ink-950 px-2.5 text-xs text-gray-200 outline-none focus:border-emerald-400/45"
      />
    </label>
  )
}

function Banner({
  tone,
  icon,
  children,
  onClose,
}: {
  tone: 'blue' | 'amber' | 'green' | 'red'
  icon: React.ReactNode
  children: React.ReactNode
  onClose?: () => void
}) {
  const styles = {
    blue: 'border-blue-400/20 bg-blue-400/[0.045] text-blue-100',
    amber: 'border-amber-400/20 bg-amber-400/[0.045] text-amber-100',
    green: 'border-emerald-400/20 bg-emerald-400/[0.045] text-emerald-100',
    red: 'border-red-400/20 bg-red-400/[0.045] text-red-100',
  }[tone]
  return (
    <div className={cn('mb-3 flex items-start gap-2 rounded-xl border p-3 text-[11px] leading-5', styles)}>
      <span className="mt-0.5 shrink-0">{icon}</span>
      <p className="min-w-0 flex-1">{children}</p>
      {onClose && (
        <button type="button" onClick={onClose} className="shrink-0 opacity-60 hover:opacity-100" aria-label="关闭提示">
          <X size={12} />
        </button>
      )}
    </div>
  )
}

function EditDialog({
  target,
  revision,
  loading,
  error,
  onChange,
  onClose,
  onSave,
}: {
  target: EditTarget
  revision: number
  loading: boolean
  error: string | null
  onChange: (target: EditTarget) => void
  onClose: () => void
  onSave: () => void
}) {
  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" role="dialog" aria-modal="true">
      <div className="w-full max-w-xl overflow-hidden rounded-2xl border border-ink-600 bg-ink-900 shadow-2xl">
        <div className="flex items-start justify-between border-b border-ink-700 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-gray-100">编辑{target.label}</h2>
            <p className="mt-1 text-[10px] text-gray-600">基于结构修订 {revision} 保存；冲突时不会覆盖他人修改。</p>
          </div>
          <button type="button" onClick={onClose} disabled={loading} className="text-gray-600 hover:text-gray-200">
            <X size={16} />
          </button>
        </div>
        <div className="space-y-4 p-5">
          {error && <Banner tone="red" icon={<AlertTriangle size={14} />}>{error}</Banner>}
          <label className="block">
            <span className="text-[10px] font-medium text-gray-400">标题</span>
            <input
              value={target.title}
              maxLength={255}
              onChange={(event) => onChange({ ...target, title: event.target.value })}
              className="mt-2 h-10 w-full rounded-xl border border-ink-600 bg-ink-950 px-3 text-sm text-gray-100 outline-none focus:border-emerald-400/45"
            />
          </label>
          <label className="block">
            <span className="text-[10px] font-medium text-gray-400">叙事摘要</span>
            <textarea
              value={target.summary}
              rows={7}
              maxLength={30_000}
              onChange={(event) => onChange({ ...target, summary: event.target.value })}
              className="mt-2 w-full resize-y rounded-xl border border-ink-600 bg-ink-950 p-3 text-xs leading-6 text-gray-200 outline-none focus:border-emerald-400/45"
            />
          </label>
        </div>
        <div className="flex justify-end gap-2 border-t border-ink-700 px-5 py-4">
          <button type="button" onClick={onClose} disabled={loading} className="h-9 rounded-lg px-3 text-xs text-gray-500 hover:bg-ink-700 hover:text-gray-200">
            取消
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={loading || !target.title.trim()}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-emerald-300 px-4 text-xs font-semibold text-emerald-950 hover:bg-emerald-200 disabled:opacity-40"
          >
            {loading ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} 保存修订
          </button>
        </div>
      </div>
    </div>
  )
}
