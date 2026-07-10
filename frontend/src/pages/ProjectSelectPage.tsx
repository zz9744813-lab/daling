import React, { useState, useRef, useEffect } from 'react'
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
      // 提取 AI 创作指令（不从 create 接口传，单独调 API 保存）
      const { custom_prompt, ...projectData } = data
      // 1. 创建项目
      const project = await projectsApi.create(projectData)
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
      // 3. 如果有 AI 创作指令，保存到 project_configs 表
      if (custom_prompt && project.id) {
        try {
          await projectsApi.updateCustomPrompt(project.id, custom_prompt)
        } catch (e) {
          console.error('保存创作指令失败:', e)
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
  onSubmit: (data: Partial<Project> & { custom_prompt?: string }) => void
  loading: boolean
  error?: string
  outlineFile: File | null
  onOutlineChange: (file: File | null) => void
}) {
  // 当前步骤：1=选择来源 2=配置详情 3=确认创建
  const [step, setStep] = useState<1 | 2 | 3>(1)
  // 创建来源：upload=上传大纲 scratch=从零开始
  const [source, setSource] = useState<'upload' | 'scratch' | null>(null)

  // 原有表单状态
  const [title, setTitle] = useState('')
  const [genre, setGenre] = useState('奇幻')
  const [themes, setThemes] = useState('')
  const [setting, setSetting] = useState('')
  const [tone, setTone] = useState('严肃')
  const [lengthType, setLengthType] = useState('long')
  // 从零开始时的创作灵感输入（对话式）
  const [creativePrompt, setCreativePrompt] = useState('')
  // AI 创作指令（自定义系统提示词，类似 Gemini Gems）
  const [customPrompt, setCustomPrompt] = useState('')

  // 新增配置状态
  const [chapterWords, setChapterWords] = useState(3000) // 每章字数目标，默认3000
  const [volumeCount, setVolumeCount] = useState<string>('auto') // 卷数偏好，默认自动
  const [pov, setPov] = useState('第三人称限制') // 写作视角，默认第三人称限制
  const [tense, setTense] = useState('过去时') // 叙事时态，默认过去时

  // 大纲预览相关状态
  const [outlinePreview, setOutlinePreview] = useState('') // 大纲前500字预览
  const [outlineWordCount, setOutlineWordCount] = useState(0) // 大纲总字数

  const fileInputRef = useRef<HTMLInputElement>(null)

  // ============ 对话式创建：聊天状态 ============
  // 聊天消息列表（user / assistant）
  const [chatMessages, setChatMessages] = useState<{ role: 'user' | 'assistant'; content: string }[]>([])
  // 当前输入框内容
  const [chatInput, setChatInput] = useState('')
  // AI 正在回复时的加载状态
  const [chatLoading, setChatLoading] = useState(false)
  // 聊天列表底部锚点，用于自动滚动
  const chatEndRef = useRef<HTMLDivElement>(null)

  // 快捷标签：点击后自动发送对应消息，帮助用户快速开始对话
  const QUICK_TAGS = [
    '末世修仙',
    '都市重生',
    '星际科幻',
    '古代言情',
    '悬疑推理',
    '热血少年',
    '黑暗奇幻',
    '轻松日常',
  ]

  // 聊天消息更新后自动滚动到底部
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatMessages, chatLoading])

  // 发送聊天消息（支持传入 overrideText 用于快捷标签直接发送）
  const sendChatMessage = async (overrideText?: string) => {
    const text = (overrideText ?? chatInput).trim()
    if (!text || chatLoading) return
    const userMsg = { role: 'user' as const, content: text }
    const newMessages = [...chatMessages, userMsg]
    setChatMessages(newMessages)
    setChatInput('')
    setChatLoading(true)
    try {
      const resp = await projectsApi.chatCreate(newMessages)
      setChatMessages([...newMessages, { role: 'assistant', content: resp.reply }])
    } catch (e) {
      setChatMessages([
        ...newMessages,
        { role: 'assistant', content: '抱歉，出了一些问题。请检查是否已配置 LLM 模型。' },
      ])
    } finally {
      setChatLoading(false)
    }
  }

  // 从对话中提取项目配置，自动填充所有配置项后进入第2步
  const generateConfigFromChat = async () => {
    setChatLoading(true)
    try {
      const resp = await projectsApi.chatCreate(chatMessages, true)
      if (resp.config) {
        const c = resp.config
        if (c.title) setTitle(c.title)
        if (c.genre) setGenre(c.genre)
        if (c.tone) setTone(c.tone)
        if (c.themes && Array.isArray(c.themes)) setThemes(c.themes.join('、'))
        if (c.setting) setSetting(c.setting)
        if (c.custom_prompt) setCustomPrompt(c.custom_prompt)
        if (c.length_type) setLengthType(c.length_type)
        if (c.pov) setPov(c.pov)
        if (c.tense) setTense(c.tense)
        if (c.chapter_words) setChapterWords(c.chapter_words)
      }
      setStep(2) // 进入第2步
    } catch (e) {
      alert('配置生成失败，请手动填写')
      setStep(2)
    } finally {
      setChatLoading(false)
    }
  }

  // 篇幅预设（保留）
  const LENGTH_PRESETS: Record<string, { label: string; min: number; max: number; words: string }> = {
    short: { label: '短篇', min: 5, max: 30, words: '3-20万字' },
    medium: { label: '中篇', min: 30, max: 100, words: '20-70万字' },
    long: { label: '长篇', min: 100, max: 500, words: '70-350万字' },
    epic: { label: '大长篇', min: 500, max: 2000, words: '350-1400万字' },
    mega: { label: '超长篇', min: 2000, max: 5000, words: '1400-3500万字' },
  }

  // 估算章节数（取篇幅范围中间值）
  const estimatedChapters = (() => {
    const preset = LENGTH_PRESETS[lengthType] || LENGTH_PRESETS.long
    return Math.round((preset.min + preset.max) / 2)
  })()

  // 文件选择处理：选文件后设置 outlineFile + 提取标题 + 读取前500字预览 + 进入第2步
  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      const validExts = ['.docx', '.txt', '.md', '.markdown']
      const ext = file.name.toLowerCase().match(/\.\w+$/)?.[0]
      if (ext && validExts.includes(ext)) {
        onOutlineChange(file)
        // 自动提取标题：取文件名（去掉扩展名）
        const baseName = file.name.replace(/\.[^.]+$/, '').trim()
        if (baseName) {
          setTitle(baseName)
        }
        // 读取前500字作为预览
        if (ext === '.txt' || ext === '.md' || ext === '.markdown') {
          // 文本文件可直接读取内容
          const reader = new FileReader()
          reader.onload = (event) => {
            const text = event.target?.result as string
            if (text) {
              setOutlinePreview(text.slice(0, 500))
              setOutlineWordCount(text.length)
            }
          }
          reader.readAsText(file)
        } else {
          // .docx 文件无法在前端直接解析为文本，显示文件信息
          setOutlinePreview(
            `[DOCX 文件] ${file.name}\n文件大小：${(file.size / 1024).toFixed(1)} KB\n\n该文件需在后端解析，预览内容将在上传后生成。`,
          )
          setOutlineWordCount(0)
        }
        // 选择文件后自动设置来源为上传，并进入第2步
        setSource('upload')
        setStep(2)
      } else {
        alert('请上传 .docx / .txt / .md 格式的文件')
      }
    }
  }

  // 选择"从零开始"：设置来源，展开填写表单（仍停留在第1步）
  const handleScratchSelect = () => {
    setSource('scratch')
  }

  // 提交处理：在第3步点击"创建项目"时调用
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) return
    const themesArr = themes
      .split(/[，,、\n]/)
      .map((t) => t.trim())
      .filter(Boolean)

    const preset = LENGTH_PRESETS[lengthType] || LENGTH_PRESETS.long

    onSubmit({
      title: title.trim(),
      genre: genre,
      type: genre,
      // 后端会把 description 映射为 synopsis（别名）
      description: setting.trim() || undefined,
      // 目标章数取中间值，Agent 会在 min~max 范围内合理安排
      target_chapters: Math.round((preset.min + preset.max) / 2),
      autonomy_level: 'L2',
      // AI 创作指令（自定义系统提示词），传给 createMutation 保存到 project_configs
      custom_prompt: customPrompt.trim() || undefined,
      config: {
        target_chapters: Math.round((preset.min + preset.max) / 2),
        length_type: lengthType,
        length_label: preset.label,
        chapter_range: { min: preset.min, max: preset.max },
        estimated_words: preset.words,
        genre: genre,
        tone: tone,
        autonomy_level: 'L2',
        // 新增字段
        chapter_words: chapterWords,
        volume_count: volumeCount,
        pov: pov,
        tense: tense,
        // 从零开始时的创作灵感文本，传给后端供 Agent 参考
        ...(creativePrompt.trim() ? { creative_prompt: creativePrompt.trim() } : {}),
        ...(themesArr.length > 0 ? { themes: themesArr } : {}),
      },
    })
  }

  // 步骤进度条配置
  const steps = [
    { num: 1, label: '选择来源' },
    { num: 2, label: '配置详情' },
    { num: 3, label: '确认创建' },
  ] as const

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-ink-700 bg-ink-850 p-6 shadow-xl">
        {/* 顶部标题 + 关闭按钮 */}
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-medium text-gray-100">新建项目</h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <X size={18} />
          </button>
        </div>

        {/* 步骤进度条：1/3 → 2/3 → 3/3 */}
        <div className="mb-6 flex items-center justify-center gap-2">
          {steps.map((s, idx) => (
            <React.Fragment key={s.num}>
              <div className="flex items-center gap-2">
                <div
                  className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-medium transition-colors ${
                    step >= s.num
                      ? 'bg-gold-500 text-ink-950'
                      : 'bg-ink-700 text-gray-500'
                  }`}
                >
                  {/* 已完成的步骤显示勾，当前及未完成显示数字 */}
                  {step > s.num ? <CheckCircle size={14} /> : s.num}
                </div>
                <span
                  className={`text-xs ${step >= s.num ? 'text-gray-200' : 'text-gray-600'}`}
                >
                  {s.label}
                </span>
              </div>
              {/* 步骤之间的连接线 */}
              {idx < steps.length - 1 && (
                <div
                  className={`h-0.5 w-12 ${step > s.num ? 'bg-gold-500' : 'bg-ink-700'}`}
                />
              )}
            </React.Fragment>
          ))}
        </div>

        {/* 错误提示 */}
        {error && (
          <div className="mb-4 rounded-md border border-red-600/30 bg-red-600/10 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* ============ 第1步：选择来源 ============ */}
          {step === 1 && (
            <div className="space-y-4">
              {!source ? (
                // 未选择来源：显示两个大卡片二选一
                <>
                  <p className="text-center text-sm text-gray-400">请选择项目创建方式</p>
                  <div className="grid grid-cols-2 gap-4">
                    {/* 上传大纲文件卡片 */}
                    <div>
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept=".docx,.txt,.md,.markdown"
                        onChange={handleFileSelect}
                        className="hidden"
                      />
                      <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        className="flex h-full w-full flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-ink-600 p-6 text-center transition-colors hover:border-gold-500/40 hover:bg-ink-800/50"
                      >
                        <Upload size={28} className="text-gold-400" />
                        <div>
                          <p className="text-sm font-medium text-gray-200">上传大纲文件</p>
                          <p className="mt-1 text-xs text-gray-500">支持 .docx / .txt / .md</p>
                          <p className="mt-0.5 text-xs text-gray-600">AI 自动解析大纲内容</p>
                        </div>
                      </button>
                    </div>
                    {/* 从零开始卡片 */}
                    <button
                      type="button"
                      onClick={handleScratchSelect}
                      className="flex h-full w-full flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-ink-600 p-6 text-center transition-colors hover:border-gold-500/40 hover:bg-ink-800/50"
                    >
                      <FileText size={28} className="text-gold-400" />
                      <div>
                        <p className="text-sm font-medium text-gray-200">从零开始</p>
                        <p className="mt-1 text-xs text-gray-500">手动填写类型、文风</p>
                        <p className="mt-0.5 text-xs text-gray-600">自定义主题与篇幅</p>
                      </div>
                    </button>
                  </div>
                </>
              ) : (
                // 已选择"从零开始"：进入对话式聊天界面，AI 引导用户描述故事
                <div className="space-y-3">
                  {/* 快捷标签：点击后自动发送对应消息 */}
                  <div className="flex flex-wrap gap-2">
                    {QUICK_TAGS.map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        disabled={chatLoading}
                        onClick={() => sendChatMessage(tag)}
                        className="rounded-full border border-gold-500/30 bg-gold-500/10 px-3 py-1 text-xs text-gold-300 transition-colors hover:bg-gold-500/20 disabled:opacity-50"
                      >
                        {tag}
                      </button>
                    ))}
                  </div>

                  {/* 聊天消息列表（高度 300px，可滚动） */}
                  <div className="h-[300px] space-y-3 overflow-y-auto rounded-lg border border-ink-700 bg-ink-900 p-3">
                    {chatMessages.length === 0 ? (
                      // 空对话时的欢迎提示
                      <div className="flex h-full flex-col items-center justify-center text-center">
                        <p className="text-sm text-gray-400">
                          描述你想写的故事，我会帮你梳理创作思路。
                        </p>
                        <p className="mt-1 text-xs text-gray-600">
                          点击上方标签快速开始，或直接输入你的想法
                        </p>
                      </div>
                    ) : (
                      chatMessages.map((msg, idx) => (
                        <div
                          key={idx}
                          className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                        >
                          {/* 用户消息右对齐金色背景，AI 消息左对齐深色背景 */}
                          <div
                            className={`max-w-[80%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
                              msg.role === 'user'
                                ? 'bg-gold-500/20 text-gold-100'
                                : 'bg-ink-700 text-gray-200'
                            }`}
                          >
                            {msg.content}
                          </div>
                        </div>
                      ))
                    )}
                    {/* AI 正在输入时的"..."动画 */}
                    {chatLoading && (
                      <div className="flex justify-start">
                        <div className="rounded-lg bg-ink-700 px-4 py-3 text-gray-400">
                          <span className="animate-pulse">●●●</span>
                        </div>
                      </div>
                    )}
                    {/* 自动滚动锚点 */}
                    <div ref={chatEndRef} />
                  </div>

                  {/* 底部输入框 + 发送按钮 */}
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={chatInput}
                      onChange={(e) => setChatInput(e.target.value)}
                      onKeyDown={(e) => {
                        // 按 Enter 发送（Shift+Enter 换行）
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault()
                          sendChatMessage()
                        }
                      }}
                      disabled={chatLoading}
                      placeholder="描述你想写的故事，或点击上方标签快速开始..."
                      className="flex-1 rounded-md border border-ink-600 bg-ink-900 px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:border-gold-500/60 focus:outline-none focus:ring-1 focus:ring-gold-500/30 disabled:opacity-50"
                    />
                    <Button
                      variant="primary"
                      type="button"
                      size="sm"
                      disabled={!chatInput.trim() || chatLoading}
                      onClick={() => sendChatMessage()}
                    >
                      发送
                    </Button>
                  </div>

                  {/* 生成配置按钮：对话超过 3 轮后显示（绿色高亮） */}
                  {chatMessages.filter((m) => m.role === 'user').length >= 3 && (
                    <button
                      type="button"
                      disabled={chatLoading}
                      onClick={generateConfigFromChat}
                      className="w-full rounded-md border border-green-500/50 bg-green-600/20 px-4 py-2.5 text-sm font-medium text-green-300 transition-colors hover:bg-green-600/30 disabled:opacity-50"
                    >
                      {chatLoading ? '正在生成配置...' : '生成配置'}
                    </button>
                  )}

                  <p className="text-center text-xs text-gray-600">
                    与 AI 对话描述你的故事，信息足够后点击"生成配置"自动填充项目参数
                  </p>
                </div>
              )}
            </div>
          )}

          {/* ============ 第2步：大纲解析预览 + 更多配置 ============ */}
          {step === 2 && (
            <div className="space-y-5">
              {/* 来源预览区：根据来源显示不同内容 */}
              {source === 'upload' && outlineFile ? (
                // 上传大纲：显示大纲预览 + 自动检测的标题、字数、章节数估算
                <div className="space-y-3">
                  <h4 className="text-xs font-medium text-gray-400">大纲解析预览</h4>
                  {/* 文件信息 */}
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
                      onClick={() => {
                        onOutlineChange(null)
                        setOutlinePreview('')
                        setOutlineWordCount(0)
                      }}
                      className="text-gray-500 hover:text-red-400"
                    >
                      <X size={16} />
                    </button>
                  </div>
                  {/* 大纲内容预览（前500字） */}
                  {outlinePreview && (
                    <div className="rounded-md border border-ink-700 bg-ink-900 p-3">
                      <p className="mb-1 text-xs text-gray-500">大纲内容预览（前500字）</p>
                      <p className="line-clamp-6 whitespace-pre-wrap text-xs text-gray-400">
                        {outlinePreview}
                      </p>
                    </div>
                  )}
                  {/* 自动检测信息：标题、字数、章节数估算 */}
                  <div className="grid grid-cols-3 gap-3">
                    <div className="rounded-md border border-ink-700 bg-ink-900 px-3 py-2">
                      <p className="text-xs text-gray-500">检测标题</p>
                      <p className="truncate text-sm text-gray-200">{title || '未检测到'}</p>
                    </div>
                    <div className="rounded-md border border-ink-700 bg-ink-900 px-3 py-2">
                      <p className="text-xs text-gray-500">大纲字数</p>
                      <p className="text-sm text-gray-200">
                        {outlineWordCount > 0 ? `${outlineWordCount} 字` : '待解析'}
                      </p>
                    </div>
                    <div className="rounded-md border border-ink-700 bg-ink-900 px-3 py-2">
                      <p className="text-xs text-gray-500">章节数估算</p>
                      <p className="text-sm text-gray-200">约 {estimatedChapters} 章</p>
                    </div>
                  </div>
                </div>
              ) : (
                // 从零开始：显示系统提示词配置摘要
                <div className="space-y-3">
                  <h4 className="text-xs font-medium text-gray-400">AI 创作指令</h4>
                  {customPrompt.trim() ? (
                    <div className="rounded-md border border-gold-500/20 bg-gold-500/5 p-3">
                      <p className="whitespace-pre-wrap text-sm text-gray-300">{customPrompt}</p>
                      <p className="mt-2 text-right text-xs text-gray-600">{customPrompt.length} 字</p>
                    </div>
                  ) : (
                    <div className="rounded-md border border-dashed border-ink-600 p-3 text-center text-xs text-gray-600">
                      未配置系统提示词，将使用默认风格
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-3">
                    <div className="rounded-md border border-ink-700 bg-ink-900 px-3 py-2">
                      <p className="text-xs text-gray-500">篇幅</p>
                      <p className="text-sm text-gray-200">
                        {(LENGTH_PRESETS[lengthType] || LENGTH_PRESETS.long).label}
                      </p>
                    </div>
                    <div className="rounded-md border border-ink-700 bg-ink-900 px-3 py-2">
                      <p className="text-xs text-gray-500">预计章数</p>
                      <p className="text-sm text-gray-200">约 {estimatedChapters} 章</p>
                    </div>
                  </div>
                </div>
              )}

              {/* 更多配置选项 */}
              <div className="space-y-4 border-t border-ink-700 pt-4">
                <h4 className="text-xs font-medium text-gray-400">更多配置</h4>

                {/* 作品标题（两种来源都需要） */}
                <div>
                  <label className="mb-1.5 block text-xs text-gray-400">作品标题 *</label>
                  <Input
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="为你的小说起个名字"
                  />
                </div>

                {/* 类型 + 文风：上传大纲时显示（可修改），从零开始时已在摘要中 */}
                {source === 'upload' && (
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
                )}

                {/* 主题：上传大纲时显示 */}
                {source === 'upload' && (
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
                )}

                {/* 设定 / 简介：上传大纲时显示 */}
                {source === 'upload' && (
                  <div>
                    <label className="mb-1.5 block text-xs text-gray-400">设定 / 简介</label>
                    <TextArea
                      value={setting}
                      onChange={(e) => setSetting(e.target.value)}
                      rows={3}
                      placeholder="描述故事的世界观背景或核心设定…"
                    />
                  </div>
                )}

                {/* 篇幅类型：上传大纲时显示 */}
                {source === 'upload' && (
                  <div>
                    <label className="mb-1.5 block text-xs text-gray-400">篇幅类型</label>
                    <div className="grid grid-cols-5 gap-2">
                      {(
                        [
                          { key: 'short', label: '短篇', range: '5-30章' },
                          { key: 'medium', label: '中篇', range: '30-100章' },
                          { key: 'long', label: '长篇', range: '100-500章' },
                          { key: 'epic', label: '大长篇', range: '500-2000章' },
                          { key: 'mega', label: '超长篇', range: '2000-5000章' },
                        ] as const
                      ).map((opt) => (
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
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* 每章字数目标 + 卷数偏好 */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1.5 block text-xs text-gray-400">每章字数目标</label>
                    <div className="grid grid-cols-4 gap-1.5">
                      {[2000, 3000, 4000, 5000].map((w) => (
                        <button
                          key={w}
                          type="button"
                          onClick={() => setChapterWords(w)}
                          className={`rounded-md border px-2 py-1.5 text-xs transition-colors ${
                            chapterWords === w
                              ? 'border-gold-500/60 bg-gold-500/10 text-gold-300'
                              : 'border-ink-600 bg-ink-900 text-gray-400 hover:border-ink-500 hover:text-gray-300'
                          }`}
                        >
                          {w}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <label className="mb-1.5 block text-xs text-gray-400">卷数偏好</label>
                    <div className="grid grid-cols-4 gap-1.5">
                      {(
                        [
                          { value: 'auto', label: '自动' },
                          { value: '1', label: '1卷' },
                          { value: '3', label: '3卷' },
                          { value: '5', label: '5卷' },
                        ] as const
                      ).map((v) => (
                        <button
                          key={v.value}
                          type="button"
                          onClick={() => setVolumeCount(v.value)}
                          className={`rounded-md border px-2 py-1.5 text-xs transition-colors ${
                            volumeCount === v.value
                              ? 'border-gold-500/60 bg-gold-500/10 text-gold-300'
                              : 'border-ink-600 bg-ink-900 text-gray-400 hover:border-ink-500 hover:text-gray-300'
                          }`}
                        >
                          {v.label}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                {/* 写作视角 + 叙事时态 */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1.5 block text-xs text-gray-400">写作视角</label>
                    <select
                      value={pov}
                      onChange={(e) => setPov(e.target.value)}
                      className="h-9 w-full rounded-md border border-ink-600 bg-ink-900 px-3 text-sm text-gray-200 focus:border-gold-500/60 focus:outline-none focus:ring-1 focus:ring-gold-500/30"
                    >
                      <option value="第一人称">第一人称</option>
                      <option value="第三人称限制">第三人称限制</option>
                      <option value="第三人称全知">第三人称全知</option>
                    </select>
                  </div>
                  <div>
                    <label className="mb-1.5 block text-xs text-gray-400">叙事时态</label>
                    <select
                      value={tense}
                      onChange={(e) => setTense(e.target.value)}
                      className="h-9 w-full rounded-md border border-ink-600 bg-ink-900 px-3 text-sm text-gray-200 focus:border-gold-500/60 focus:outline-none focus:ring-1 focus:ring-gold-500/30"
                    >
                      <option value="过去时">过去时</option>
                      <option value="现在时">现在时</option>
                    </select>
                  </div>
                </div>

                {/* AI 创作指令（自定义系统提示词，类似 Gemini Gems） */}
                <div>
                  <label className="mb-1.5 block text-xs text-gray-400">
                    AI 创作指令（系统提示词）
                  </label>
                  <textarea
                    value={customPrompt}
                    onChange={(e) => setCustomPrompt(e.target.value)}
                    rows={5}
                    placeholder="输入你希望 AI 遵循的创作指令..."
                    className="w-full resize-none rounded-md border border-ink-600 bg-ink-900 px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:border-gold-500/60 focus:outline-none focus:ring-1 focus:ring-gold-500/30"
                  />
                  <p className="mt-1 text-xs text-gray-600">
                    类似 Gemini Gems / Custom GPTs，定义 AI 的角色、写作风格、行为准则。例如：'你是一位擅长写热血玄幻的作家，语言风格要简洁有力，多用短句，注重动作描写，少用心理独白'
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* ============ 第3步：确认创建 ============ */}
          {step === 3 && (
            <div className="space-y-4">
              <h4 className="text-xs font-medium text-gray-400">请确认以下配置信息</h4>
              <div className="space-y-3 rounded-lg border border-ink-700 bg-ink-900 p-4">
                {/* 完整配置摘要 */}
                <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                  <div>
                    <p className="text-xs text-gray-500">作品标题</p>
                    <p className="text-sm text-gray-200">{title || '未填写'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">创建来源</p>
                    <p className="text-sm text-gray-200">
                      {source === 'upload' ? '上传大纲文件' : '从零开始'}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">类型 / 题材</p>
                    <p className="text-sm text-gray-200">{genre}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">文风</p>
                    <p className="text-sm text-gray-200">{tone}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">篇幅类型</p>
                    <p className="text-sm text-gray-200">
                      {(LENGTH_PRESETS[lengthType] || LENGTH_PRESETS.long).label} ·{' '}
                      {(LENGTH_PRESETS[lengthType] || LENGTH_PRESETS.long).words}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">目标章数</p>
                    <p className="text-sm text-gray-200">约 {estimatedChapters} 章</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">每章字数</p>
                    <p className="text-sm text-gray-200">{chapterWords} 字</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">卷数偏好</p>
                    <p className="text-sm text-gray-200">
                      {volumeCount === 'auto' ? '自动' : `${volumeCount} 卷`}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">写作视角</p>
                    <p className="text-sm text-gray-200">{pov}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">叙事时态</p>
                    <p className="text-sm text-gray-200">{tense}</p>
                  </div>
                </div>
                {/* 主题 */}
                {themes && (
                  <div className="border-t border-ink-700 pt-3">
                    <p className="text-xs text-gray-500">主题</p>
                    <p className="text-sm text-gray-200">{themes}</p>
                  </div>
                )}
                {/* AI 创作指令 */}
                {customPrompt.trim() && (
                  <div className="border-t border-ink-700 pt-3">
                    <p className="text-xs text-gray-500">AI 创作指令</p>
                    <p className="mt-1 whitespace-pre-wrap text-sm text-gray-400">{customPrompt}</p>
                  </div>
                )}
                {/* 设定 / 简介 */}
                {setting && (
                  <div className="border-t border-ink-700 pt-3">
                    <p className="text-xs text-gray-500">设定 / 简介</p>
                    <p className="mt-1 whitespace-pre-wrap text-sm text-gray-400">{setting}</p>
                  </div>
                )}
                {/* 大纲文件信息（上传大纲时显示） */}
                {source === 'upload' && outlineFile && (
                  <div className="border-t border-ink-700 pt-3">
                    <p className="text-xs text-gray-500">大纲文件</p>
                    <p className="text-sm text-gray-200">{outlineFile.name}</p>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ============ 底部按钮区 ============ */}
          <div className="mt-5 flex justify-between gap-2 border-t border-ink-700 pt-4">
            {/* 左侧：上一步按钮 */}
            <div>
              {/* 第1步从零开始模式：上一步回到来源选择卡片 */}
              {step === 1 && source === 'scratch' && (
                <Button variant="ghost" type="button" onClick={() => setSource(null)}>
                  上一步
                </Button>
              )}
              {/* 第2步、第3步：返回上一步 */}
              {step > 1 && (
                <Button
                  variant="ghost"
                  type="button"
                  onClick={() => {
                    setStep((step - 1) as 1 | 2 | 3)
                    // 从第2步回到第1步时，如果是上传大纲来源，重置回来源选择卡片
                    if (step === 2 && source === 'upload') {
                      setSource(null)
                    }
                  }}
                >
                  上一步
                </Button>
              )}
            </div>
            {/* 右侧：取消 + 下一步/创建项目 */}
            <div className="flex gap-2">
              <Button variant="ghost" type="button" onClick={onClose}>
                取消
              </Button>
              {/* 第1步从零开始模式：下一步进入第2步 */}
              {step === 1 && source === 'scratch' && (
                <Button variant="primary" type="button" onClick={() => setStep(2)}>
                  下一步
                </Button>
              )}
              {/* 第2步：下一步进入第3步（需填写标题） */}
              {step === 2 && (
                <Button
                  variant="primary"
                  type="button"
                  onClick={() => setStep(3)}
                  disabled={!title.trim()}
                >
                  下一步
                </Button>
              )}
              {/* 第3步：创建项目（提交表单） */}
              {step === 3 && (
                <Button
                  variant="primary"
                  type="submit"
                  disabled={loading || !title.trim()}
                >
                  {loading ? <Loader2 size={14} className="animate-spin" /> : null}
                  创建项目
                </Button>
              )}
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
