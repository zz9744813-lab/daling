import { useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertCircle,
  ArrowRight,
  BookOpen,
  BrainCircuit,
  Clock3,
  FileText,
  FolderOpen,
  Layers3,
  Loader2,
  Paperclip,
  Plus,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  Users,
  Wand2,
  X,
} from 'lucide-react'
import { projectsApi } from '../api/client'
import { useProjectStore } from '../store/projectStore'
import type { Project } from '../types'
import { Button } from '../components/ui'
import { cn } from '../lib/cn'
import { getProjectDisplayTitle } from '../lib/projectDisplayTitle'
import { clampInteger } from '../lib/workflowGuards'

const MAX_ATTACHMENT_SIZE = 5 * 1024 * 1024

const IDEA_STARTERS = [
  {
    title: '从一句灵感开始',
    description: '把零散念头梳理成可持续的长篇核心',
    icon: Sparkles,
    prompt: '我只有一个模糊灵感，请通过一次只问一个问题的方式帮我把它发展成小说。',
  },
  {
    title: '先设计一个主角',
    description: '从欲望、缺陷与改变弧线搭建故事',
    icon: Users,
    prompt: '我想先设计一个令人难忘的主角，再从他的欲望和困境发展故事。',
  },
  {
    title: '构建独特世界',
    description: '先确定世界规则、代价与核心矛盾',
    icon: Wand2,
    prompt: '我想从一个独特世界观开始，请帮我建立规则、代价和会持续制造剧情的矛盾。',
  },
]

const SYSTEM_CAPABILITIES = [
  { icon: Layers3, label: '大纲全文分块', description: '无损索引，不靠一次调用' },
  { icon: BrainCircuit, label: '长期记忆检索', description: '人物、设定、伏笔可回溯' },
  { icon: ShieldCheck, label: '真实纠错闭环', description: '批评、异稿改写与复审' },
  { icon: Activity, label: '24H 持久任务', description: '预算熔断，可暂停恢复' },
]

const STATUS_LABELS: Record<string, string> = {
  draft: '筹备中',
  drafting: '创作中',
  active: '创作中',
  paused: '已暂停',
  reviewing: '待审阅',
  completed: '已完成',
  error: '需处理',
}

function formatRelativeDate(value?: string) {
  if (!value) return '刚刚'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '最近更新'
  const diff = Date.now() - date.getTime()
  const minutes = Math.floor(diff / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
  }).format(date)
}

function projectProgress(project: Project) {
  if (typeof project.progress === 'number' && Number.isFinite(project.progress)) {
    return clampInteger(project.progress, 0, 100, 0)
  }
  const current = project.current_chapter ?? project.current_chapter_no ?? 0
  const target = project.target_chapters ?? project.config?.target_chapters ?? 0
  return target > 0 ? clampInteger((current / target) * 100, 0, 100, 0) : 0
}

export default function ProjectSelectPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const setCurrentProject = useProjectStore((state) => state.setCurrentProject)
  const currentProject = useProjectStore((state) => state.currentProject)
  const clearCurrentProject = useProjectStore((state) => state.clearCurrentProject)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [prompt, setPrompt] = useState('')
  const [attachment, setAttachment] = useState<File | null>(null)
  const [composerError, setComposerError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const promptRef = useRef<HTMLTextAreaElement>(null)

  const projectsQuery = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  })
  const modelStatus = useQuery({
    queryKey: ['chat-create-status'],
    queryFn: projectsApi.chatCreateStatus,
    retry: 1,
    staleTime: 15_000,
  })

  const projects = useMemo(
    () =>
      [...(projectsQuery.data ?? [])].sort((a, b) => {
        const left = new Date(a.updated_at ?? a.created_at ?? 0).getTime()
        const right = new Date(b.updated_at ?? b.created_at ?? 0).getTime()
        return right - left
      }),
    [projectsQuery.data],
  )

  const filteredProjects = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase()
    return projects.filter((project) => {
      const matchesSearch =
        !keyword ||
        getProjectDisplayTitle(project.title, project.id).toLocaleLowerCase().includes(keyword) ||
        project.genre?.toLocaleLowerCase().includes(keyword) ||
        project.description?.toLocaleLowerCase().includes(keyword)
      const status = project.status || 'draft'
      const matchesStatus =
        statusFilter === 'all' ||
        status === statusFilter ||
        (statusFilter === 'active' && status === 'drafting')
      return matchesSearch && matchesStatus
    })
  }, [projects, search, statusFilter])

  const deleteMutation = useMutation({
    mutationFn: (projectId: string) => projectsApi.remove(projectId),
    onSuccess: (_, projectId) => {
      if (currentProject?.id === projectId) clearCurrentProject()
      return queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })

  const openProject = (project: Project) => {
    setCurrentProject(project)
    navigate('/cockpit')
  }

  const startProject = (overridePrompt?: string) => {
    const idea = (overridePrompt ?? prompt).trim()
    if (!idea && !attachment) {
      setComposerError('请输入一个故事想法，或先上传已有大纲。')
      promptRef.current?.focus()
      return
    }
    setComposerError(null)
    navigate('/projects/new', {
      state: {
        initialPrompt: idea,
        attachment,
        autoSend: Boolean(idea),
      },
    })
  }

  const handlePromptKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      startProject()
    }
  }

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (!/\.(docx|txt|md|markdown)$/i.test(file.name)) {
      setComposerError('仅支持 .docx、.txt、.md 或 .markdown 文件。')
      return
    }
    if (file.size > MAX_ATTACHMENT_SIZE) {
      setComposerError('附件不能超过 5 MB。')
      return
    }
    setAttachment(file)
    setComposerError(null)
    promptRef.current?.focus()
  }

  const modelConfigured = Boolean(modelStatus.data?.configured)
  const greeting = projects.length > 0 ? '继续写你的故事' : '把灵感变成长篇故事'

  return (
    <div className="flex min-h-screen bg-ink-950 text-gray-100">
      <aside className="sticky top-0 hidden h-screen w-72 shrink-0 flex-col border-r border-ink-700/80 bg-ink-900/80 px-3 py-4 backdrop-blur-xl md:flex">
        <div className="flex items-center gap-3 px-2 pb-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-gold-500/20 bg-gold-500/10 font-serif text-lg font-semibold text-gold-400">
            墨
          </div>
          <div className="min-w-0">
            <p className="truncate font-serif text-base font-semibold text-gray-100">墨砚</p>
            <p className="text-[11px] text-gray-500">AI 长篇小说工坊</p>
          </div>
        </div>

        <Button
          variant="primary"
          size="md"
          className="w-full justify-start bg-emerald-300 text-emerald-950 hover:bg-emerald-200"
          onClick={() => navigate('/projects/new')}
        >
          <Plus size={16} />
          新建小说
        </Button>

        <label className="relative mt-4 block">
          <Search
            size={14}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-600"
          />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索项目"
            aria-label="搜索项目"
            className="h-9 w-full rounded-lg border border-transparent bg-ink-950 pl-9 pr-3 text-xs text-gray-200 placeholder:text-gray-600 focus:border-emerald-400/35 focus:outline-none"
          />
        </label>

        <div className="mt-5 flex items-center justify-between px-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-gray-600">
            最近项目
          </span>
          <span className="text-[10px] text-gray-600">{projects.length}</span>
        </div>

        <nav className="no-scrollbar mt-2 min-h-0 flex-1 space-y-1 overflow-y-auto" aria-label="项目列表">
          {projectsQuery.isLoading ? (
            <div className="space-y-2 px-1">
              {[0, 1, 2].map((item) => (
                <div key={item} className="h-14 animate-pulse rounded-xl bg-ink-800" />
              ))}
            </div>
          ) : projects.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs leading-5 text-gray-600">
              你的小说会出现在这里
            </p>
          ) : (
            projects.slice(0, 16).map((project) => (
              <button
                key={project.id}
                type="button"
                onClick={() => openProject(project)}
                className="group flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left transition-colors hover:bg-ink-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/35"
              >
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-ink-700 bg-ink-950 text-gray-500 group-hover:text-emerald-300">
                  <BookOpen size={14} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium text-gray-300 group-hover:text-gray-100">
                    {getProjectDisplayTitle(project.title, project.id)}
                  </p>
                  <p className="mt-0.5 truncate text-[10px] text-gray-600">
                    {project.genre || '未分类'} · {formatRelativeDate(project.updated_at || project.created_at)}
                  </p>
                </div>
              </button>
            ))
          )}
        </nav>

        <div className="mt-3 rounded-xl border border-ink-700 bg-ink-950/70 p-3">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                'h-2 w-2 rounded-full',
                modelConfigured
                  ? 'bg-emerald-400'
                  : modelStatus.isLoading
                    ? 'animate-pulse bg-gray-500'
                    : 'bg-amber-400',
              )}
            />
            <span className="text-xs font-medium text-gray-300">
              {modelConfigured
                ? '创作模型已连接'
                : modelStatus.isLoading
                  ? '正在检测创作模型'
                  : modelStatus.isError
                    ? '模型状态未知'
                    : '模型未配置'}
            </span>
          </div>
          <p className="mt-1 truncate pl-4 text-[10px] text-gray-600">
            {modelStatus.data?.model || '发送想法时会再次检测连接'}
          </p>
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <header className="flex h-16 items-center gap-3 border-b border-ink-700/80 bg-ink-950/90 px-4 backdrop-blur-xl md:hidden">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-gold-500/20 bg-gold-500/10 font-serif text-base text-gold-400">
            墨
          </div>
          <div>
            <p className="font-serif text-base font-semibold text-gray-100">墨砚</p>
            <p className="text-[10px] text-gray-600">AI 长篇小说工坊</p>
          </div>
          <button
            type="button"
            onClick={() => navigate('/projects/new')}
            className="ml-auto flex h-10 items-center gap-1.5 rounded-xl bg-emerald-300 px-3 text-sm font-semibold text-emerald-950"
          >
            <Plus size={16} /> 新建
          </button>
        </header>

        <main className="subtle-grid min-h-[calc(100vh-4rem)] px-4 py-10 sm:px-8 md:min-h-screen md:py-14 lg:px-12">
          <div className="mx-auto max-w-6xl">
            <section className="mx-auto max-w-3xl text-center">
              <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-emerald-400/15 bg-emerald-400/6 px-3 py-1.5 text-[11px] text-emerald-200">
                <Sparkles size={13} />
                与 AI 一起完成创意、简报与故事骨架
              </div>
              <h1 className="font-serif text-3xl font-semibold tracking-tight text-gray-50 sm:text-5xl">
                {greeting}
              </h1>
              <p className="mx-auto mt-4 max-w-xl text-sm leading-7 text-gray-500 sm:text-base">
                从一句想法开始，或直接上传已有大纲。系统会保留原文件，并通过自然对话整理成可编辑创作配置。
              </p>

              <div className="mx-auto mt-6 grid max-w-md grid-cols-2 gap-2 rounded-2xl border border-ink-700 bg-ink-900/70 p-1.5">
                <button
                  type="button"
                  onClick={() => promptRef.current?.focus()}
                  className={cn(
                    'flex h-11 items-center justify-center gap-2 rounded-xl text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
                    !attachment
                      ? 'bg-emerald-300 text-emerald-950'
                      : 'text-gray-400 hover:bg-ink-800 hover:text-gray-200',
                  )}
                >
                  <Sparkles size={15} /> 从零开始
                </button>
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className={cn(
                    'flex h-11 items-center justify-center gap-2 rounded-xl text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
                    attachment
                      ? 'bg-emerald-300 text-emerald-950'
                      : 'text-gray-400 hover:bg-ink-800 hover:text-gray-200',
                  )}
                >
                  <FileText size={15} /> 上传已有大纲
                </button>
              </div>

              <div className="mt-4 rounded-2xl border border-ink-600 bg-ink-850/95 p-2 text-left shadow-[0_24px_80px_rgba(0,0,0,0.32)] backdrop-blur-xl focus-within:border-emerald-400/45 focus-within:ring-1 focus-within:ring-emerald-400/15">
                {attachment && (
                  <div className="mb-1.5 flex items-center gap-2 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2">
                    <FileText size={15} className="text-gold-400" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs font-medium text-gray-200">{attachment.name}</p>
                      <p className="text-[10px] text-gray-600">将在创建工作区中继续处理</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setAttachment(null)}
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-gray-500 hover:bg-ink-700 hover:text-gray-200"
                      aria-label="移除附件"
                    >
                      <X size={15} />
                    </button>
                  </div>
                )}
                <textarea
                  ref={promptRef}
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  onKeyDown={handlePromptKeyDown}
                  rows={3}
                  placeholder="例如：一名能听见城市记忆的修复师，必须在七天内找出一场被所有人遗忘的灾难…"
                  className="min-h-[88px] w-full resize-none bg-transparent px-3 py-3 text-sm leading-6 text-gray-100 placeholder:text-gray-600 focus:outline-none sm:text-base"
                  aria-label="描述你想写的故事"
                />
                <div className="flex items-center gap-2 px-1 pb-1">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".docx,.txt,.md,.markdown"
                    className="sr-only"
                    onChange={handleFileChange}
                  />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="flex h-10 items-center gap-2 rounded-xl px-3 text-xs text-gray-400 transition-colors hover:bg-ink-700 hover:text-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40"
                  >
                    <Paperclip size={16} />
                    {attachment ? '更换大纲' : '上传大纲'}
                  </button>
                  <span className="hidden text-[11px] text-gray-600 sm:inline">支持 DOCX / TXT / MD，最大 5 MB</span>
                  <button
                    type="button"
                    onClick={() => startProject()}
                    disabled={!prompt.trim() && !attachment}
                    className="ml-auto flex h-10 items-center gap-2 rounded-xl bg-emerald-300 px-4 text-sm font-semibold text-emerald-950 transition-colors hover:bg-emerald-200 disabled:cursor-not-allowed disabled:bg-ink-700 disabled:text-gray-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300/60"
                  >
                    开始构思
                    <Send size={15} />
                  </button>
                </div>
              </div>
              {composerError && (
                <p className="mt-2 flex items-center justify-center gap-1.5 text-xs text-red-300" role="alert">
                  <AlertCircle size={13} /> {composerError}
                </p>
              )}
              <p className="mt-2 text-[10px] text-gray-600">Enter 开始 · Shift + Enter 换行</p>
            </section>

            <section className="mx-auto mt-7 max-w-5xl overflow-hidden rounded-2xl border border-ink-700 bg-ink-900/52" aria-label="长篇生产能力">
              <div className="flex flex-col gap-1 border-b border-ink-700 px-4 py-3 text-left sm:flex-row sm:items-center sm:justify-between sm:px-5">
                <div>
                  <h2 className="text-xs font-semibold text-gray-200">从灵感到 24H 生产，能力不会在建项时被简化</h2>
                  <p className="mt-1 text-[10px] text-gray-600">项目提示词、来源证据、记忆、模型调用与纠错版本都会进入可审计工作区。</p>
                </div>
                <span className="mt-2 shrink-0 rounded-full border border-emerald-400/20 bg-emerald-400/8 px-2.5 py-1 text-[9px] font-medium text-emerald-200 sm:mt-0">显式启动 · 随时接管</span>
              </div>
              <div className="grid gap-px bg-ink-700 sm:grid-cols-2 xl:grid-cols-4">
                {SYSTEM_CAPABILITIES.map((capability) => {
                  const Icon = capability.icon
                  return (
                    <div key={capability.label} className="flex items-center gap-3 bg-ink-900/95 px-4 py-3 text-left">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-ink-600 bg-ink-950 text-emerald-300"><Icon size={14} /></span>
                      <span className="min-w-0">
                        <span className="block text-[11px] font-semibold text-gray-300">{capability.label}</span>
                        <span className="mt-0.5 block text-[9px] leading-4 text-gray-600">{capability.description}</span>
                      </span>
                    </div>
                  )
                })}
              </div>
            </section>

            <section className="mx-auto mt-6 grid max-w-3xl gap-3 sm:grid-cols-3" aria-label="创作起点">
              {IDEA_STARTERS.map((starter) => {
                const Icon = starter.icon
                return (
                  <button
                    key={starter.title}
                    type="button"
                    onClick={() => startProject(starter.prompt)}
                    className="group rounded-2xl border border-ink-700 bg-ink-900/55 p-4 text-left transition-all hover:-translate-y-0.5 hover:border-emerald-400/30 hover:bg-ink-850 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/35"
                  >
                    <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-ink-800 text-gray-500 transition-colors group-hover:bg-emerald-400/10 group-hover:text-emerald-300">
                      <Icon size={17} />
                    </div>
                    <h2 className="mt-4 text-sm font-medium text-gray-200">{starter.title}</h2>
                    <p className="mt-1 text-xs leading-5 text-gray-600">{starter.description}</p>
                  </button>
                )
              })}
            </section>

            <section className="mt-14 sm:mt-16">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-gray-600">Library</p>
                  <h2 className="mt-1 font-serif text-2xl font-semibold text-gray-100">最近项目</h2>
                  <p className="mt-1 text-xs text-gray-600">从上次停下的地方继续，或处理需要关注的项目。</p>
                </div>
                <div className="flex gap-2 md:hidden">
                  <label className="relative min-w-0 flex-1">
                    <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-600" />
                    <input
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                      placeholder="搜索"
                      aria-label="搜索项目"
                      className="h-10 w-full rounded-xl border border-ink-700 bg-ink-900 pl-9 pr-3 text-xs text-gray-200 focus:border-emerald-400/35 focus:outline-none"
                    />
                  </label>
                </div>
                <label className="flex items-center gap-2 text-xs text-gray-500">
                  <span>状态</span>
                  <select
                    value={statusFilter}
                    onChange={(event) => setStatusFilter(event.target.value)}
                    className="form-select h-9 min-w-28"
                  >
                    <option value="all">全部</option>
                    <option value="draft">筹备中</option>
                    <option value="active">创作中</option>
                    <option value="paused">已暂停</option>
                    <option value="reviewing">待审阅</option>
                    <option value="completed">已完成</option>
                    <option value="error">需处理</option>
                  </select>
                </label>
              </div>

              {deleteMutation.isError && (
                <div className="mt-5 flex items-start gap-2 rounded-xl border border-red-400/20 bg-red-400/8 px-3 py-2.5 text-xs text-red-200" role="alert">
                  <AlertCircle size={14} className="mt-0.5 shrink-0" />
                  <span className="min-w-0 flex-1 break-words">删除失败：{(deleteMutation.error as Error).message}</span>
                  <button type="button" onClick={() => deleteMutation.reset()} className="shrink-0 rounded px-1 text-red-100 hover:bg-red-400/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300/40" aria-label="关闭删除错误">
                    <X size={13} />
                  </button>
                </div>
              )}

              {projectsQuery.isError ? (
                <div className="mt-6 flex flex-col items-center rounded-2xl border border-red-400/15 bg-red-400/5 px-6 py-10 text-center" role="alert">
                  <AlertCircle size={22} className="text-red-300" />
                  <p className="mt-3 text-sm font-medium text-gray-200">项目列表暂时无法加载</p>
                  <p className="mt-1 text-xs text-gray-500">{(projectsQuery.error as Error).message}</p>
                  <Button className="mt-4" size="sm" onClick={() => projectsQuery.refetch()}>
                    重试
                  </Button>
                </div>
              ) : projectsQuery.isLoading ? (
                <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  {[0, 1, 2].map((item) => (
                    <div key={item} className="h-52 animate-pulse rounded-2xl border border-ink-700 bg-ink-900" />
                  ))}
                </div>
              ) : filteredProjects.length > 0 ? (
                <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  {filteredProjects.map((project) => (
                    <ProjectCard
                      key={project.id}
                      project={project}
                      onOpen={() => openProject(project)}
                      deleting={deleteMutation.isPending && deleteMutation.variables === project.id}
                      onDelete={() => {
                        if (window.confirm(`确定将《${getProjectDisplayTitle(project.title, project.id)}》永久删除吗？此操作无法撤销。`)) {
                          deleteMutation.mutate(project.id)
                        }
                      }}
                    />
                  ))}
                </div>
              ) : (
                <div className="mt-6 flex flex-col items-center rounded-2xl border border-dashed border-ink-700 bg-ink-900/35 px-6 py-12 text-center">
                  {projects.length === 0 ? <FolderOpen size={25} className="text-gray-600" /> : <Search size={24} className="text-gray-600" />}
                  <p className="mt-3 text-sm font-medium text-gray-300">
                    {projects.length === 0 ? '还没有项目' : '没有符合条件的项目'}
                  </p>
                  <p className="mt-1 text-xs text-gray-600">
                    {projects.length === 0 ? '从上方输入一个故事想法即可开始。' : '试试其他关键词或状态筛选。'}
                  </p>
                </div>
              )}
            </section>
          </div>
        </main>
      </div>
    </div>
  )
}

function ProjectCard({
  project,
  onOpen,
  onDelete,
  deleting,
}: {
  project: Project
  onOpen: () => void
  onDelete: () => void
  deleting: boolean
}) {
  const current = project.current_chapter ?? project.current_chapter_no ?? 0
  const target = project.target_chapters ?? project.config?.target_chapters ?? 0
  const progress = projectProgress(project)
  const status = project.status || 'draft'
  const statusLabel = STATUS_LABELS[status] || status
  const displayTitle = getProjectDisplayTitle(project.title, project.id)

  return (
    <article className="group relative flex min-h-52 flex-col overflow-hidden rounded-2xl border border-ink-700 bg-ink-900/75 p-5 shadow-sm transition-all hover:-translate-y-0.5 hover:border-emerald-400/25 hover:bg-ink-850 hover:shadow-[0_18px_55px_rgba(0,0,0,0.22)]">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/8 to-transparent" />
      <div className="flex items-start gap-3">
        <button type="button" onClick={onOpen} className="min-w-0 flex-1 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/35">
          <div className="mb-3 flex min-w-0 items-center gap-2 text-[10px] text-gray-600">
            <span
              className={cn(
                'rounded-full border px-2 py-0.5',
                status === 'error'
                  ? 'border-red-400/20 bg-red-400/8 text-red-300'
                  : status === 'active' || status === 'drafting'
                    ? 'border-emerald-400/20 bg-emerald-400/8 text-emerald-300'
                    : 'border-ink-600 bg-ink-950 text-gray-500',
              )}
            >
              {statusLabel}
            </span>
            <span className="truncate" title={project.genre || '未分类'}>{project.genre || '未分类'}</span>
          </div>
          <h3 className="line-clamp-2 font-serif text-xl font-semibold leading-7 text-gray-100 transition-colors group-hover:text-emerald-50">
            {displayTitle}
          </h3>
        </button>
        <div className="relative flex gap-1">
          <button
            type="button"
            onClick={onDelete}
            disabled={deleting}
            className="flex h-9 w-9 items-center justify-center rounded-lg text-gray-600 transition-colors hover:bg-red-400/10 hover:text-red-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/35 disabled:opacity-50"
            aria-label={`删除《${displayTitle}》`}
          >
            {deleting ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
          </button>
        </div>
      </div>

      <button type="button" onClick={onOpen} className="mt-3 flex flex-1 flex-col rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/35">
        <p className="line-clamp-2 text-xs leading-5 text-gray-500">
          {project.description || project.synopsis || '故事简报正在等待你继续完善。'}
        </p>
        <div className="mt-auto w-full pt-5">
          <div className="flex items-center justify-between text-[11px] text-gray-600">
            <span className="inline-flex items-center gap-1.5">
              <BookOpen size={12} />
              {current > 0 ? `第 ${current} 章` : '尚未开始正文'}
              {target ? ` / ${target}` : ''}
            </span>
            <span>{progress}%</span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-ink-700">
            <div className="h-full rounded-full bg-gradient-to-r from-gold-500 to-emerald-400" style={{ width: `${progress}%` }} />
          </div>
          <div className="mt-3 flex items-center justify-between text-[10px] text-gray-600">
            <span className="inline-flex items-center gap-1">
              <Clock3 size={11} /> {formatRelativeDate(project.updated_at || project.created_at)}
            </span>
            <span className="inline-flex items-center gap-1 text-gray-400 transition-colors group-hover:text-emerald-300">
              {current > 0 ? '继续创作' : '完善故事骨架'} <ArrowRight size={12} />
            </span>
          </div>
        </div>
      </button>
    </article>
  )
}
