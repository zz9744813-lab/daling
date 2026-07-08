import React, { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Plus, Loader2, X, Upload, FileText, CheckCircle, Trash2 } from 'lucide-react'
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
  const [pendingOutline, setPendingOutline] = useState<File | null>(null)

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
    mutationFn: async (data: any) => {
      // 1. 创建项目
      const project = await projectsApi.create(data)
      // 2. 如果有上传的大纲文件，自动上传
      if (pendingOutline && project.id) {
        try {
          const formData = new FormData()
          formData.append('file', pendingOutline)
          await fetch(`/api/projects/${project.id}/upload-outline`, {
            method: 'POST',
            body: formData,
          })
        } catch (e) {
          console.error('大纲上传失败:', e)
        }
      }
      return project
    },
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      setCurrentProject(project)
      setPendingOutline(null)
      navigate('/cockpit')
    },
  })

  const handleOpen = (project: Project) => {
    setCurrentProject(project)
    navigate('/cockpit')
  }

  // 删除项目 mutation
  const deleteMutation = useMutation({
    mutationFn: (projectId: string) => projectsApi.delete(projectId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['projects'] }),
    onError: (err: Error) => alert(`删除失败: ${err.message}`),
  })

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
              <ProjectCard
                key={p.id}
                project={p}
                onOpen={() => handleOpen(p)}
                onDelete={() => {
                  if (
                    window.confirm(
                      '确定删除该项目？所有章节和设定将被永久删除。',
                    )
                  ) {
                    deleteMutation.mutate(p.id)
                  }
                }}
              />
            ))}
          </div>
        )}
      </main>

      {showCreate && (
        <CreateProjectModal
          onClose={() => {
            setShowCreate(false)
            setPendingOutline(null)
          }}
          onSubmit={(data) => createMutation.mutate(data)}
          loading={createMutation.isPending}
          error={createMutation.error?.message}
          outlineFile={pendingOutline}
          onOutlineChange={setPendingOutline}
        />
      )}
    </div>
  )
}

function ProjectCard({
  project,
  onOpen,
  onDelete,
}: {
  project: Project
  onOpen: () => void
  onDelete: () => void
}) {
  const progress = project.progress ?? 0
  const target = project.target_chapters ?? project.config?.target_chapters ?? 0
  const current = project.current_chapter ?? project.current_chapter_no ?? 0

  return (
    <div
      onClick={onOpen}
      className="group relative flex cursor-pointer flex-col rounded-lg border border-ink-700 bg-ink-850 p-5 text-left transition-colors hover:border-gold-500/40 hover:bg-ink-800"
    >
      {/* 删除按钮：右上角，hover 时显示并变红 */}
      <button
        onClick={(e) => {
          e.stopPropagation()
          onDelete()
        }}
        title="删除项目"
        className="absolute right-3 top-3 flex h-7 w-7 items-center justify-center rounded-md text-gray-600 opacity-0 transition-all hover:bg-red-600/20 hover:text-red-400 group-hover:opacity-100"
      >
        <Trash2 size={15} />
      </button>

      <div className="mb-3 flex items-start justify-between pr-8">
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
    </div>
  )
}

function CreateProjectModal({
  onClose,
  onSubmit,
  loading,
  error,
  outlineFile,
  onOutlineChange,
}: {
  onClose: () => void
  onSubmit: (data: Partial<Project>) => void
  loading: boolean
  error?: string
  outlineFile: File | null
  onOutlineChange: (file: File | null) => void
}) {
  const [title, setTitle] = useState('')
  const [genre, setGenre] = useState('奇幻')
  const [themes, setThemes] = useState('')
  const [setting, setSetting] = useState('')
  const [tone, setTone] = useState('严肃')
  const [lengthType, setLengthType] = useState('long')
  const [uploadingOutline, setUploadingOutline] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      const validExts = ['.docx', '.txt', '.md', '.markdown']
      const ext = file.name.toLowerCase().match(/\.\w+$/)?.[0]
      if (ext && validExts.includes(ext)) {
        onOutlineChange(file)
      } else {
        alert('请上传 .docx / .txt / .md 格式的文件')
      }
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) return
    const themesArr = themes
      .split(/[，,、\n]/)
      .map((t) => t.trim())
      .filter(Boolean)

    // 篇幅类型 → 章数范围（Agent 在生成大纲时会在此范围内合理安排）
    const LENGTH_PRESETS: Record<string, { label: string; min: number; max: number; words: string }> = {
      short: { label: '短篇', min: 5, max: 30, words: '3-20万字' },
      medium: { label: '中篇', min: 30, max: 100, words: '20-70万字' },
      long: { label: '长篇', min: 100, max: 500, words: '70-350万字' },
      epic: { label: '大长篇', min: 500, max: 2000, words: '350-1400万字' },
      mega: { label: '超长篇', min: 2000, max: 5000, words: '1400-3500万字' },
    }
    const preset = LENGTH_PRESETS[lengthType] || LENGTH_PRESETS.long

    onSubmit({
      title: title.trim(),
      genre: genre,
      type: genre,
      // 后端会把 description 映射为 synopsis（别名）
      description: setting.trim() || undefined,
      // 目标章数取中间值，Agent 会在 min~max 范围内合理安排
      target_chapters: Math.round((preset.min + preset.max) / 2),
      // 篇幅类型与范围传给后端，Agent 生成大纲时参考
      autonomy_level: 'L2',
      config: {
        target_chapters: Math.round((preset.min + preset.max) / 2),
        length_type: lengthType,
        length_label: preset.label,
        chapter_range: { min: preset.min, max: preset.max },
        estimated_words: preset.words,
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

          {/* 上传详细大纲 */}
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">
              上传详细大纲（可选）
            </label>
            <input
              ref={fileInputRef}
              type="file"
              accept=".docx,.txt,.md,.markdown"
              onChange={handleFileSelect}
              className="hidden"
            />
            {outlineFile ? (
              <div className="flex items-center gap-3 rounded-md border border-green-600/30 bg-green-600/10 px-3 py-2.5">
                <CheckCircle size={18} className="text-green-400" />
                <div className="flex-1 min-w-0">
                  <p className="truncate text-sm text-gray-200">{outlineFile.name}</p>
                  <p className="text-xs text-gray-500">
                    {(outlineFile.size / 1024).toFixed(1)} KB · 已就绪
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => onOutlineChange(null)}
                  className="text-gray-500 hover:text-red-400"
                >
                  <X size={16} />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="flex w-full items-center gap-3 rounded-md border border-dashed border-ink-600 px-3 py-3 text-left transition-colors hover:border-gold-500/40 hover:bg-ink-800/50"
              >
                <Upload size={18} className="text-gray-500" />
                <div>
                  <p className="text-sm text-gray-300">点击上传大纲文件</p>
                  <p className="text-xs text-gray-600">
                    支持 .docx / .txt / .md — AI 将按照你的大纲生成世界观
                  </p>
                </div>
              </button>
            )}
            <p className="mt-1.5 text-xs text-gray-600">
              上传后，系统会解析大纲内容，在生成世界观时严格参考你的设定
            </p>
          </div>

          <div>
            <label className="mb-1.5 block text-xs text-gray-400">篇幅类型</label>
            <div className="grid grid-cols-5 gap-2">
              {([
                { key: 'short', label: '短篇', range: '5-30章', desc: '3-20万字' },
                { key: 'medium', label: '中篇', range: '30-100章', desc: '20-70万字' },
                { key: 'long', label: '长篇', range: '100-500章', desc: '70-350万字' },
                { key: 'epic', label: '大长篇', range: '500-2000章', desc: '350-1400万字' },
                { key: 'mega', label: '超长篇', range: '2000-5000章', desc: '1400-3500万字' },
              ] as const).map((opt) => (
                <button
                  key={opt.key}
                  type="button"
                  onClick={() => setLengthType(opt.key)}
                  className={`rounded-md border px-2 py-2 text-center transition-colors ${
                    lengthType === opt.key
                      ? 'border-gold-500/60 bg-gold-500/10 text-gold-300'
                      : 'border-ink-600 bg-ink-900 text-gray-400 hover:border-ink-500 hover:text-gray-300'
                  }`}
                >
                  <p className="text-sm font-medium">{opt.label}</p>
                  <p className="text-xs text-gray-600">{opt.range}</p>
                  <p className="text-xs text-gray-700">{opt.desc}</p>
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-xs text-gray-600">
              Agent 会根据篇幅类型自动安排合理的卷数与章数
            </p>
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
