import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Plus, Loader2, X } from 'lucide-react'
import { projectsApi, governanceApi } from '../api/client'
import { useProjectStore } from '../store/projectStore'
import type { Project } from '../types'
import { Button, Input, TextArea, ProgressBar } from '../components/ui'
import { Badge } from '../components/Badge'
import { EmptyState } from '../components/EmptyState'

const GENRE_OPTIONS = [
  '奇幻',
  '玄幻',
  '武侠',
  '都市',
  '悬疑',
  '科幻',
  '历史',
  '言情',
  '恐怖',
  '现实',
  '其他',
]

const TONE_OPTIONS = ['严肃', '轻松', '幽默', '暗黑', '热血', '温馨', '冷峻', '诗意']

export default function ProjectSelectPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const setCurrentProject = useProjectStore((s) => s.setCurrentProject)

  const [showCreate, setShowCreate] = useState(false)

  const { data: projects, isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  })

  // 检查 Provider 是否已配置
  const { data: providers } = useQuery({
    queryKey: ['providers'],
    queryFn: governanceApi.listProviders,
  })

  const createMutation = useMutation({
    mutationFn: projectsApi.create,
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      setCurrentProject(project)
      navigate('/cockpit')
    },
  })

  const handleOpen = (project: Project) => {
    setCurrentProject(project)
    navigate('/cockpit')
  }

  const providerConfigured = (providers?.length ?? 0) > 0

  return (
    <div className="min-h-screen bg-ink-950">
      {/* 顶部品牌 */}
      <header className="flex items-center gap-3 border-b border-ink-800 px-8 py-5">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-ink-800 text-gold-400">
          <span className="font-serif text-lg font-bold">墨</span>
        </div>
        <div>
          <h1 className="text-base font-medium text-gray-100">Novel Agent OS</h1>
          <p className="text-xs text-gray-500">小说智能体操作系统</p>
        </div>
        <div className="ml-auto flex items-center gap-3">
          {!providerConfigured && (
            <Badge variant="amber">未配置 Provider</Badge>
          )}
          <Button variant="primary" size="md" onClick={() => setShowCreate(true)}>
            <Plus size={16} />
            新建项目
          </Button>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-8 py-10">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-lg font-medium text-gray-200">我的项目</h2>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-24 text-gray-500">
            <Loader2 className="animate-spin" size={20} />
          </div>
        ) : !projects || projects.length === 0 ? (
          <EmptyState
            icon={<BookOpen size={28} />}
            title="还没有项目"
            description="创建第一个项目，开启 AI 辅助的长篇小说创作之旅。"
            action={
              <Button variant="primary" onClick={() => setShowCreate(true)}>
                <Plus size={16} />
                创建第一个项目
              </Button>
            }
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <ProjectCard key={p.id} project={p} onOpen={() => handleOpen(p)} />
            ))}
          </div>
        )}
      </main>

      {showCreate && (
        <CreateProjectModal
          onClose={() => setShowCreate(false)}
          onSubmit={(data) => createMutation.mutate(data)}
          loading={createMutation.isPending}
          error={createMutation.error?.message}
        />
      )}
    </div>
  )
}

function ProjectCard({ project, onOpen }: { project: Project; onOpen: () => void }) {
  const progress = project.progress ?? 0
  const target = project.target_chapters ?? project.config?.target_chapters ?? 0
  const current = project.current_chapter ?? project.current_chapter_no ?? 0

  return (
    <button
      onClick={onOpen}
      className="group flex flex-col rounded-lg border border-ink-700 bg-ink-850 p-5 text-left transition-colors hover:border-gold-500/40 hover:bg-ink-800"
    >
      <div className="mb-3 flex items-start justify-between">
        <h3 className="line-clamp-2 font-serif text-base font-medium text-gray-100">
          {project.title}
        </h3>
        {project.genre && <Badge variant="gold">{project.genre}</Badge>}
      </div>

      {(project.description || project.synopsis) && (
        <p className="mb-4 line-clamp-2 flex-1 text-sm text-gray-500">
          {project.description || project.synopsis}
        </p>
      )}

      <div className="mt-auto space-y-2">
        <div className="flex items-center justify-between text-xs text-gray-500">
          <span>
            第 {current} / {target || '—'} 章
          </span>
          <span>{progress}%</span>
        </div>
        <ProgressBar value={progress} />
      </div>
    </button>
  )
}

function CreateProjectModal({
  onClose,
  onSubmit,
  loading,
  error,
}: {
  onClose: () => void
  onSubmit: (data: Partial<Project>) => void
  loading: boolean
  error?: string
}) {
  const [title, setTitle] = useState('')
  const [genre, setGenre] = useState('奇幻')
  const [themes, setThemes] = useState('')
  const [setting, setSetting] = useState('')
  const [tone, setTone] = useState('严肃')
  const [targetChapters, setTargetChapters] = useState('20')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) return
    const themesArr = themes
      .split(/[，,、\n]/)
      .map((t) => t.trim())
      .filter(Boolean)

    onSubmit({
      title: title.trim(),
      genre: genre,
      type: genre,
      // 后端会把 description 映射为 synopsis（别名）
      description: setting.trim() || undefined,
      // 目标章数 —— 后端会存入 extra dict
      target_chapters: Number(targetChapters) || 20,
      // 自主等级 —— 后端会存入 extra
      autonomy_level: 'L2',
      config: {
        target_chapters: Number(targetChapters) || 20,
        genre: genre,
        tone: tone,
        autonomy_level: 'L2',
        ...(themesArr.length > 0 ? { themes: themesArr } : {}),
      },
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border border-ink-700 bg-ink-850 p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-medium text-gray-100">新建项目</h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <X size={18} />
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-red-600/30 bg-red-600/10 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">作品标题 *</label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="为你的小说起个名字"
              autoFocus
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1.5 block text-xs text-gray-400">类型 / 题材</label>
              <select
                value={genre}
                onChange={(e) => setGenre(e.target.value)}
                className="h-9 w-full rounded-md border border-ink-600 bg-ink-900 px-3 text-sm text-gray-200 focus:border-gold-500/60 focus:outline-none focus:ring-1 focus:ring-gold-500/30"
              >
                {GENRE_OPTIONS.map((g) => (
                  <option key={g} value={g}>
                    {g}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1.5 block text-xs text-gray-400">文风</label>
              <select
                value={tone}
                onChange={(e) => setTone(e.target.value)}
                className="h-9 w-full rounded-md border border-ink-600 bg-ink-900 px-3 text-sm text-gray-200 focus:border-gold-500/60 focus:outline-none focus:ring-1 focus:ring-gold-500/30"
              >
                {TONE_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-xs text-gray-400">
              主题（用逗号分隔多个主题）
            </label>
            <Input
              value={themes}
              onChange={(e) => setThemes(e.target.value)}
              placeholder="如：成长、复仇、救赎"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs text-gray-400">设定 / 简介</label>
            <TextArea
              value={setting}
              onChange={(e) => setSetting(e.target.value)}
              rows={3}
              placeholder="描述故事的世界观背景或核心设定…"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs text-gray-400">目标章数</label>
            <Input
              type="number"
              value={targetChapters}
              onChange={(e) => setTargetChapters(e.target.value)}
              placeholder="20"
            />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={onClose} type="button">
              取消
            </Button>
            <Button variant="primary" type="submit" disabled={loading || !title.trim()}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : null}
              创建项目
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
