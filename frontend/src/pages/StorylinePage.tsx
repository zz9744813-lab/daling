import React, { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { GitBranch, ChevronDown, ChevronRight, BookOpen } from 'lucide-react'
import { TopBar } from '../layout/TopBar'
import { BossCommandBar } from '../layout/BossCommandBar'
import { AppShell, AppShellBody } from '../layout/AppShell'
import { storylineApi, governanceApi } from '../api/client'
import { useProjectStore } from '../store/projectStore'
import { Badge } from '../components/Badge'
import { EmptyState } from '../components/EmptyState'
import { cn } from '../lib/cn'
import {
  CHAPTER_STATUS_MAP,
  type StorylineVolume,
  type Chapter,
  type ChapterStatus,
} from '../types'

/**
 * StorylinePage —— 生命线
 * 卷 → beat → 章 三级时间线展示，每章显示状态（planned/draft/in_progress/finalized）
 */
export default function StorylinePage() {
  const project = useProjectStore((s) => s.currentProject)
  const projectId = project?.id ?? ''

  const { data: storyline, isLoading } = useQuery({
    queryKey: ['storyline', projectId],
    queryFn: () => storylineApi.get(projectId),
    enabled: !!projectId,
  })

  const { data: providers } = useQuery({
    queryKey: ['providers'],
    queryFn: governanceApi.listProviders,
  })

  const volumes = storyline?.volumes ?? []
  const chapters = storyline?.chapters ?? []

  // 构建章节映射：beat_id → chapters
  const chaptersByBeat = useMemo(() => {
    const map: Record<string, Chapter[]> = {}
    chapters.forEach((c) => {
      const key = c.beat_id ?? '_unassigned'
      if (!map[key]) map[key] = []
      map[key].push(c)
    })
    return map
  }, [chapters])

  const providerStatus = (providers?.length ?? 0) > 0 ? 'online' : 'offline'

  return (
    <AppShell>
      <TopBar providerStatus={providerStatus as 'online' | 'offline'} />
      <AppShellBody className="flex-col">
        <div className="border-b border-ink-700 px-6 py-3">
          <h1 className="flex items-center gap-2 text-base font-medium text-gray-200">
            <GitBranch size={18} className="text-gold-500" />
            生命线
          </h1>
          <p className="mt-0.5 text-xs text-gray-500">卷 → 节拍 → 章 三级故事结构</p>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          {isLoading ? (
            <p className="py-10 text-center text-sm text-gray-500">加载中…</p>
          ) : volumes.length === 0 ? (
            <EmptyState
              icon={<BookOpen size={28} />}
              title="尚未规划生命线"
              description="在创作舱中点击「生成大纲」，让故事架构师生成卷与节拍结构。"
              className="h-full"
            />
          ) : (
            <div className="space-y-3">
              {volumes.map((v) => (
                <VolumeRow
                  key={v.id}
                  volume={v}
                  chaptersByBeat={chaptersByBeat}
                  unassignedChapters={chaptersByBeat['_unassigned']?.filter(
                    (c) => !c.beat_id && c.volume_id === v.id,
                  )}
                />
              ))}
            </div>
          )}
        </div>
      </AppShellBody>
      <BossCommandBar />
    </AppShell>
  )
}

function VolumeRow({
  volume,
  chaptersByBeat,
  unassignedChapters,
}: {
  volume: StorylineVolume
  chaptersByBeat: Record<string, Chapter[]>
  unassignedChapters?: Chapter[]
}) {
  const [open, setOpen] = useState(true)

  return (
    <div className="overflow-hidden rounded-lg border border-ink-700 bg-ink-850">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left hover:bg-ink-800"
      >
        {open ? (
          <ChevronDown size={16} className="text-gray-500" />
        ) : (
          <ChevronRight size={16} className="text-gray-500" />
        )}
        <Badge variant="gold">卷 {volume.volume_index}</Badge>
        <span className="font-serif text-sm font-medium text-gray-200">{volume.title}</span>
        {volume.beats && (
          <span className="ml-auto text-xs text-gray-500">
            {volume.beats.length} 个节拍
          </span>
        )}
      </button>

      {open && (
        <div className="border-t border-ink-700 px-4 py-3">
          {volume.summary && <p className="mb-3 text-xs text-gray-500">{volume.summary}</p>}

          {volume.beats && volume.beats.length > 0 ? (
            <div className="space-y-3 border-l border-ink-600 pl-4">
              {volume.beats.map((beat) => {
                const beatChapters = chaptersByBeat[beat.id] ?? []
                return (
                  <div key={beat.id} className="relative">
                    <span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full border border-gold-500 bg-ink-900" />
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-300">{beat.title}</span>
                      <Badge variant="outline">节拍 {beat.beat_index}</Badge>
                      {beat.emotional_arc && (
                        <span className="text-xs text-gray-600">· {beat.emotional_arc}</span>
                      )}
                    </div>
                    {beat.summary && (
                      <p className="mt-0.5 text-xs text-gray-500">{beat.summary}</p>
                    )}
                    {beatChapters.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {beatChapters.map((ch) => (
                          <ChapterStatusChip key={ch.id} chapter={ch} />
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="text-xs text-gray-600">本卷暂无节拍</p>
          )}

          {/* 未分配到 beat 的章节 */}
          {unassignedChapters && unassignedChapters.length > 0 && (
            <div className="mt-3 border-t border-ink-700 pt-3">
              <p className="mb-2 text-xs text-gray-600">未分配节拍的章节</p>
              <div className="flex flex-wrap gap-1.5">
                {unassignedChapters.map((ch) => (
                  <ChapterStatusChip key={ch.id} chapter={ch} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
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
      className={cn(
        'inline-flex items-center gap-1.5 rounded border px-2 py-1 text-xs',
        'border-ink-600 bg-ink-900 text-gray-400',
      )}
      title={`第 ${chapter.chapter_number} 章 · ${statusInfo.label}`}
    >
      <span
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          chapter.status === 'finalized' && 'bg-green-400',
          chapter.status === 'in_progress' && 'bg-blue-400',
          (chapter.status === 'planned' || chapter.status === 'draft') && 'bg-gray-500',
        )}
      />
      <span className="text-gray-500">第{chapter.chapter_number}章</span>
      <span className="truncate text-gray-300">{chapter.title}</span>
      <Badge
        variant={statusInfo.color as 'gray' | 'blue' | 'green'}
        className="ml-1"
      >
        {statusInfo.label}
      </Badge>
    </span>
  )
}
