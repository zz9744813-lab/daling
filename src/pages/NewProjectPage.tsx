import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
  type ReactNode,
} from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  BookOpen,
  BrainCircuit,
  CheckCircle2,
  FileText,
  Layers3,
  Loader2,
  Paperclip,
  PanelRight,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  X,
} from 'lucide-react'
import { projectsApi } from '../api/client'
import { useProjectStore } from '../store/projectStore'
import type {
  AutonomyLevel,
  ChatBlueprintUpdate,
  OutlineInspection,
  Project,
  ProjectBlueprint,
  ProjectChatMessage,
  ProjectLengthType,
} from '../types'
import { Button, Input, TextArea } from '../components/ui'
import { cn } from '../lib/cn'
import {
  clampInteger,
  getOutlineInspectionIssue,
  isOutlineInspectionAccepted,
} from '../lib/workflowGuards'
import {
  PROJECT_PROMPT_PRESETS,
  hasProjectPromptPreset,
  mergeProjectPrompt,
} from '../lib/projectPrompt'

const DRAFT_KEY = 'naos-new-project-draft-v2'
const MAX_ATTACHMENT_SIZE = 5 * 1024 * 1024

const DEFAULT_BLUEPRINT: ProjectBlueprint = {
  title: '',
  logline: '',
  description: '',
  genre: '',
  protagonist: '',
  protagonist_desire: '',
  protagonist_flaw: '',
  protagonist_fear: '',
  core_conflict: '',
  story_question: '',
  antagonist: '',
  ability: '',
  ability_cost: '',
  world_setting: '',
  world_rules: '',
  themes: '',
  tone: '',
  pacing: '',
  audience_experience: '',
  platform: '',
  language: '中文',
  pov: '第三人称限制',
  tense: '过去时',
  length_type: 'long',
  target_chapters: 120,
  chapter_words: 3000,
  volume_count: '自动',
  ending_preference: '',
  content_boundaries: '',
  autonomy_level: 'L3',
  custom_prompt: '',
}

const STARTER_PROMPTS = [
  '我有一个世界观，但还没有主角',
  '帮我把一句灵感发展成长篇故事',
  '我想写强冲突、快节奏的升级流',
  '从一个复杂主角开始设计故事',
]

const OUTLINE_PROMPTS = [
  '严格保留大纲中的全部硬设定、人物动机和关键事件，只优化表达与节奏',
  '先梳理大纲的卷章结构、伏笔和人物弧线，列出缺口，但不要自行改动事实',
  '按长篇连载标准完善写作契约，重点保证跨章一致性与伏笔回收',
  '识别大纲中不可改动的原文证据，并把它们作为后续章节的硬约束',
]

const LENGTH_OPTIONS: Array<{
  value: ProjectLengthType
  label: string
  chapters?: number
}> = [
  { value: 'short', label: '短篇', chapters: 20 },
  { value: 'medium', label: '中篇', chapters: 60 },
  { value: 'long', label: '长篇', chapters: 120 },
  { value: 'epic', label: '大长篇', chapters: 300 },
  { value: 'custom', label: '自定义' },
]

const AUTONOMY_OPTIONS: Array<{
  value: AutonomyLevel
  label: string
  description: string
  badge?: string
}> = [
  { value: 'L1', label: '人工主导', description: '每个关键步骤都等待作者确认' },
  { value: 'L2', label: '审批协作', description: 'AI 推进场景，在章节闸门等待确认' },
  { value: 'L3', label: '自动纠错', description: '自动完成批评、改写与复审，异常才暂停', badge: '推荐' },
  { value: 'L4', label: '24H 自主生产', description: '持久化连续推进，由预算、熔断和质量闸门守护', badge: '连续' },
]

const PRODUCTION_STAGES = [
  {
    title: '无损来源编译',
    description: '全文切分、多次调用、逐节点可回溯',
    icon: Layers3,
  },
  {
    title: '场景级生产',
    description: '规划、起草、批评、连续性检查',
    icon: Activity,
  },
  {
    title: '记忆与学习',
    description: '写前检索，写后沉淀反馈与 Canon',
    icon: BrainCircuit,
  },
  {
    title: '纠错与守护',
    description: '异稿改写、复审、预算熔断与暂停恢复',
    icon: ShieldCheck,
  },
]

const REQUIRED_FIELDS: Array<{ key: keyof ProjectBlueprint; label: string }> = [
  { key: 'title', label: '作品标题' },
  { key: 'logline', label: '一句话故事' },
  { key: 'genre', label: '类型 / 题材' },
  { key: 'protagonist', label: '主角' },
  { key: 'protagonist_desire', label: '主角欲望' },
  { key: 'core_conflict', label: '核心冲突' },
  { key: 'world_setting', label: '世界设定' },
  { key: 'tone', label: '叙事语气' },
]

interface AttachmentMeta {
  name: string
  size: number
  type: string
  lastModified: number
  needsReselect: boolean
}

type CreationMode = 'idea' | 'outline'

interface StoredDraft {
  version: 2
  creationMode?: CreationMode
  blueprint: ProjectBlueprint
  messages: ProjectChatMessage[]
  attachment: AttachmentMeta | null
  suggestedReplies: string[]
  assumptions: string[]
  aiReadiness: number
  savedAt: string
}

interface NewProjectLocationState {
  initialPrompt?: string
  attachment?: File
  autoSend?: boolean
}

function makeId(prefix: string) {
  const suffix =
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${suffix}`
}

function loadDraft(): StoredDraft | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as StoredDraft
    if (parsed?.version !== 2 || !parsed.blueprint) return null
    return {
      ...parsed,
      blueprint: sanitizeBlueprint(parsed.blueprint),
      messages: Array.isArray(parsed.messages) ? parsed.messages : [],
      attachment: parsed.attachment
        ? { ...parsed.attachment, needsReselect: true }
        : null,
      suggestedReplies: Array.isArray(parsed.suggestedReplies)
        ? parsed.suggestedReplies
        : [],
      assumptions: Array.isArray(parsed.assumptions) ? parsed.assumptions : [],
      aiReadiness: Number(parsed.aiReadiness) || 0,
    }
  } catch {
    return null
  }
}

function fileMeta(file: File): AttachmentMeta {
  return {
    name: file.name,
    size: file.size,
    type: file.type,
    lastModified: file.lastModified,
    needsReselect: false,
  }
}

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function normalizeReadiness(value: unknown) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 0
  const percent = numeric <= 1 ? numeric * 100 : numeric
  return Math.round(Math.max(0, Math.min(100, percent)))
}

function sanitizeBlueprint(value: Partial<ProjectBlueprint>): ProjectBlueprint {
  const blueprint = { ...DEFAULT_BLUEPRINT, ...value }
  return {
    ...blueprint,
    target_chapters: clampInteger(blueprint.target_chapters, 1, 5000, 120),
    chapter_words: clampInteger(blueprint.chapter_words, 500, 20_000, 3000),
  }
}

function toText(value: unknown) {
  if (Array.isArray(value)) return value.filter(Boolean).join('、')
  return typeof value === 'string' ? value : ''
}

function mergeBlueprint(
  current: ProjectBlueprint,
  incoming: Record<string, unknown>,
): ProjectBlueprint {
  const next = { ...current }
  const text = (target: keyof ProjectBlueprint, ...keys: string[]) => {
    for (const key of keys) {
      const value = toText(incoming[key])
      if (value) {
        ;(next as any)[target] = value
        return
      }
    }
  }

  text('title', 'title')
  text('logline', 'logline', 'premise', 'hook')
  text('description', 'description', 'synopsis')
  text('genre', 'genre', 'type')
  text('protagonist', 'protagonist', 'main_character')
  text('protagonist_desire', 'protagonist_desire', 'protagonist_goal', 'desire')
  text('protagonist_flaw', 'protagonist_flaw', 'flaw')
  text('protagonist_fear', 'protagonist_fear', 'fear')
  text('core_conflict', 'core_conflict', 'conflict', 'main_conflict')
  text('story_question', 'story_question')
  text('antagonist', 'antagonist')
  text('ability', 'ability', 'power')
  text('ability_cost', 'ability_cost', 'power_cost')
  text('world_setting', 'world_setting', 'setting', 'worldview')
  text('world_rules', 'world_rules', 'rules')
  text('themes', 'themes')
  text('tone', 'tone', 'style')
  text('pacing', 'pacing')
  text('audience_experience', 'audience_experience', 'audience', 'reader_experience')
  text('platform', 'platform')
  text('language', 'language')
  text('pov', 'pov', 'point_of_view')
  text('tense', 'tense')
  text('volume_count', 'volume_count', 'volumes')
  text('ending_preference', 'ending_preference', 'ending')
  text('content_boundaries', 'content_boundaries', 'boundaries', 'avoid')
  text('custom_prompt', 'custom_prompt', 'system_prompt')

  const length = incoming.length_type
  if (['short', 'medium', 'long', 'epic', 'custom'].includes(String(length))) {
    next.length_type = length as ProjectLengthType
  }
  const chapters = Number(incoming.target_chapters ?? incoming.chapter_count)
  if (Number.isFinite(chapters) && chapters > 0) {
    next.target_chapters = clampInteger(chapters, 1, 5000, next.target_chapters)
  }
  const chapterWords = Number(
    incoming.chapter_words ?? incoming.words_per_chapter,
  )
  if (Number.isFinite(chapterWords) && chapterWords > 0) {
    next.chapter_words = clampInteger(chapterWords, 500, 20_000, next.chapter_words)
  }
  const autonomy = incoming.autonomy_level
  if (['L1', 'L2', 'L3', 'L4'].includes(String(autonomy))) {
    next.autonomy_level = autonomy as AutonomyLevel
  }
  return next
}

export default function NewProjectPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const setCurrentProject = useProjectStore((state) => state.setCurrentProject)
  const routeState = (location.state ?? {}) as NewProjectLocationState
  const restored = useMemo(loadDraft, [])

  const initialFile = routeState.attachment instanceof File ? routeState.attachment : null
  const [blueprint, setBlueprint] = useState<ProjectBlueprint>(
    sanitizeBlueprint(restored?.blueprint ?? {}),
  )
  const [messages, setMessages] = useState<ProjectChatMessage[]>(
    restored?.messages ?? [],
  )
  const [composer, setComposer] = useState(routeState.initialPrompt ?? '')
  const [creationMode, setCreationMode] = useState<CreationMode>(
    initialFile || restored?.attachment || restored?.creationMode === 'outline'
      ? 'outline'
      : 'idea',
  )
  const [attachment, setAttachment] = useState<File | null>(initialFile)
  const [attachmentMeta, setAttachmentMeta] = useState<AttachmentMeta | null>(
    initialFile ? fileMeta(initialFile) : restored?.attachment ?? null,
  )
  const [suggestedReplies, setSuggestedReplies] = useState<string[]>(
    restored?.suggestedReplies ?? [],
  )
  const [assumptions, setAssumptions] = useState<string[]>(
    restored?.assumptions ?? [],
  )
  const [aiReadiness, setAiReadiness] = useState(restored?.aiReadiness ?? 0)
  const [chatLoading, setChatLoading] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [pageError, setPageError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState(restored?.savedAt ?? '')
  const [mobileBlueprintOpen, setMobileBlueprintOpen] = useState(false)
  const [creationStage, setCreationStage] = useState('')
  const [outlineInspection, setOutlineInspection] = useState<OutlineInspection | null>(null)
  const [outlineInspecting, setOutlineInspecting] = useState(false)
  const [outlineContextSent, setOutlineContextSent] = useState(false)

  const controllerRef = useRef<AbortController | null>(null)
  const messagesRef = useRef(messages)
  const blueprintRef = useRef(blueprint)
  const chatEndRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const panelRef = useRef<HTMLElement>(null)
  const panelCloseRef = useRef<HTMLButtonElement>(null)
  const initialHandledRef = useRef(false)
  const initialOutlineInspectedRef = useRef(false)
  const outlineInspectionRequestRef = useRef(0)

  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  useEffect(() => {
    blueprintRef.current = blueprint
  }, [blueprint])

  const modelStatus = useQuery({
    queryKey: ['chat-create-status'],
    queryFn: projectsApi.chatCreateStatus,
    retry: 1,
    staleTime: 15_000,
  })

  const inspectOutlineFile = useCallback(async (file: File) => {
    const requestId = outlineInspectionRequestRef.current + 1
    outlineInspectionRequestRef.current = requestId
    setOutlineInspecting(true)
    setOutlineInspection(null)
    setOutlineContextSent(false)
    setPageError(null)
    try {
      const inspection = await projectsApi.inspectOutline(file)
      if (outlineInspectionRequestRef.current !== requestId) return null
      setOutlineInspection(inspection)
      const issue = getOutlineInspectionIssue(inspection)
      if (issue) setPageError(issue)
      return inspection
    } catch (error) {
      if (outlineInspectionRequestRef.current === requestId) {
        setPageError((error as Error).message || '大纲解析失败')
      }
      return null
    } finally {
      if (outlineInspectionRequestRef.current === requestId) {
        setOutlineInspecting(false)
      }
    }
  }, [])

  useEffect(() => {
    if (!initialFile || initialOutlineInspectedRef.current) return
    initialOutlineInspectedRef.current = true
    void inspectOutlineFile(initialFile)
  }, [initialFile, inspectOutlineFile])

  const calculatedReadiness = useMemo(() => {
    const filled = REQUIRED_FIELDS.filter(({ key }) => {
      const value = blueprint[key]
      return typeof value === 'number' ? value > 0 : String(value ?? '').trim().length > 0
    }).length
    return Math.round((filled / REQUIRED_FIELDS.length) * 100)
  }, [blueprint])
  const readiness = Math.max(calculatedReadiness, aiReadiness)
  const outlineInspectionAccepted = isOutlineInspectionAccepted(outlineInspection)
  const outlineNeedsReselect = Boolean(attachmentMeta?.needsReselect && !attachment)
  const outlineInspectionIssue = attachment
    ? getOutlineInspectionIssue(outlineInspection)
    : null
  const outlineConversationBlocked = Boolean(
    creationMode === 'outline' &&
      attachmentMeta &&
      (attachmentMeta.needsReselect ||
        !attachment ||
        outlineInspecting ||
        !outlineInspectionAccepted),
  )
  const outlineCreationBlocked = Boolean(
    outlineNeedsReselect ||
      (attachment && (outlineInspecting || !outlineInspectionAccepted)),
  )
  const localMissing = REQUIRED_FIELDS.filter(({ key }) => {
    const value = blueprint[key]
    return typeof value === 'number' ? value <= 0 : !String(value ?? '').trim()
  }).map(({ label }) => label)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [messages, chatLoading])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const draft: StoredDraft = {
        version: 2,
        creationMode,
        blueprint,
        messages: messages.map((message) => ({
          ...message,
          state: message.state === 'streaming' ? 'stopped' : message.state,
        })),
        attachment: attachmentMeta
          ? { ...attachmentMeta, needsReselect: !attachment }
          : null,
        suggestedReplies,
        assumptions,
        aiReadiness,
        savedAt: new Date().toISOString(),
      }
      try {
        localStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
        setSavedAt(draft.savedAt)
      } catch {
        // localStorage 不可用时不影响当前编辑。
      }
    }, 350)
    return () => window.clearTimeout(timer)
  }, [
    blueprint,
    messages,
    creationMode,
    attachmentMeta,
    attachment,
    suggestedReplies,
    assumptions,
    aiReadiness,
  ])

  useEffect(() => {
    if (!mobileBlueprintOpen) return
    const previous = document.activeElement as HTMLElement | null
    document.body.style.overflow = 'hidden'
    panelCloseRef.current?.focus()

    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMobileBlueprintOpen(false)
        return
      }
      if (event.key !== 'Tab' || !panelRef.current) return
      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
        ),
      )
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = ''
      document.removeEventListener('keydown', handleKeyDown)
      previous?.focus()
    }
  }, [mobileBlueprintOpen])

  const applyBlueprintUpdate = useCallback((update: ChatBlueprintUpdate) => {
    if (update.config) {
      setBlueprint((current) => mergeBlueprint(current, update.config))
    }
    if (update.readiness != null) setAiReadiness(normalizeReadiness(update.readiness))
    if (Array.isArray(update.suggested_replies)) {
      setSuggestedReplies(update.suggested_replies.filter(Boolean).slice(0, 4))
    }
    if (Array.isArray(update.assumptions)) {
      setAssumptions(update.assumptions.filter(Boolean))
    }
  }, [])

  const runConversation = useCallback(
    async (conversation: ProjectChatMessage[]) => {
      if (chatLoading) return
      const assistantId = makeId('assistant')
      const assistantMessage: ProjectChatMessage = {
        id: assistantId,
        role: 'assistant',
        content: '',
        state: 'streaming',
      }
      const nextMessages = [...conversation, assistantMessage]
      setMessages(nextMessages)
      messagesRef.current = nextMessages
      setChatLoading(true)
      setChatError(null)
      setPageError(null)
      setSuggestedReplies([])

      const controller = new AbortController()
      controllerRef.current = controller
      let receivedDelta = false

      const updateAssistant = (updater: (message: ProjectChatMessage) => ProjectChatMessage) => {
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantId ? updater(message) : message,
          ),
        )
      }

      try {
        await projectsApi.chatCreateStream(
          {
            messages: conversation.map(({ role, content }) => ({ role, content })),
            blueprint: blueprintRef.current as unknown as Record<string, unknown>,
          },
          {
            onDelta: (delta) => {
              if (!delta) return
              receivedDelta = true
              updateAssistant((message) => ({
                ...message,
                content: message.content + delta,
              }))
            },
            onBlueprint: applyBlueprintUpdate,
          },
          controller.signal,
        )
        updateAssistant((message) => ({
          ...message,
          content:
            message.content ||
            (receivedDelta
              ? message.content
              : '创作简报已更新。你可以继续补充，也可以直接编辑右侧简报。'),
          state: 'complete',
        }))
      } catch (error) {
        if (controller.signal.aborted) {
          updateAssistant((message) => ({
            ...message,
            content: message.content || '已停止这次生成。',
            state: 'stopped',
          }))
          return
        }

        const message = (error as Error).message || '对话生成失败'
        // 流式接口尚未部署时，自动兼容旧 POST 接口。
        if (/404|not found|method not allowed/i.test(message)) {
          try {
            const legacy = await projectsApi.chatCreate(
              conversation.map(({ role, content }) => ({ role, content })),
            )
            if (legacy?.config) {
              applyBlueprintUpdate({ config: legacy.config })
            }
            updateAssistant((current) => ({
              ...current,
              content: legacy?.reply || '创作简报已更新。',
              state: 'complete',
            }))
            return
          } catch (legacyError) {
            const legacyMessage = (legacyError as Error).message || message
            setChatError(legacyMessage)
          }
        } else {
          setChatError(message)
        }
        updateAssistant((current) => ({
          ...current,
          content: current.content || '这次回复没有完成。你可以重试，已填写的简报不会丢失。',
          state: 'error',
        }))
      } finally {
        if (controllerRef.current === controller) controllerRef.current = null
        setChatLoading(false)
        window.setTimeout(() => composerRef.current?.focus(), 0)
      }
    },
    [applyBlueprintUpdate, chatLoading],
  )

  const handleSend = useCallback(
    async (override?: string) => {
      const text = (override ?? composer).trim()
      if ((!text && !attachmentMeta) || chatLoading) return
      if (outlineInspecting) {
        setPageError('正在解析大纲，请稍候再发送。')
        return
      }
      if (creationMode === 'outline' && attachmentMeta?.needsReselect && !attachment) {
        setPageError('草稿中的大纲需要重新选择，当前尚未读取或预检该文件。')
        return
      }
      if (creationMode === 'outline' && attachment && !outlineInspectionAccepted) {
        setPageError(outlineInspectionIssue || '大纲预检未通过，请重新选择文件。')
        return
      }
      const includeOutlineContext = Boolean(
        creationMode === 'outline' &&
          attachmentMeta &&
          attachment &&
          outlineInspectionAccepted &&
          !outlineContextSent,
      )
      const attachmentNote = creationMode === 'outline' && attachmentMeta && attachment
        ? includeOutlineContext
          ? `\n\n【大纲结构预览】\n文件：${attachmentMeta.name}（${formatBytes(attachmentMeta.size)}）\n预检：${outlineInspection?.volume_heading_count ?? 0} 个卷标题、${outlineInspection?.chapter_heading_count ?? 0} 个章标题、${outlineInspection?.node_count ?? 0} 个来源节点、${outlineInspection?.chunk_count ?? 0} 个分块、${outlineInspection?.char_count ?? 0} 字符。\n以下内容仅供创建顾问理解方向，不代表全文编译：\n---\n${outlineInspection?.preview ?? ''}\n---\n重要：创建项目后，后台会保存原文件并按段落、表格原顺序无损索引全文，再通过分块多调用链编译；不得依据这段预览宣称已处理全文。`
          : outlineInspectionAccepted
            ? `\n\n[已附加并预检：${attachmentMeta.name}，${formatBytes(attachmentMeta.size)}。当前对话只使用结构预览；创建后后台将保存并无损索引全文。]`
            : `\n\n[已选择大纲：${attachmentMeta.name}，但尚无可验证预检结果；当前消息未向顾问提供大纲内容。]`
        : ''
      const content =
        text ||
        `我上传了一份名为《${attachmentMeta?.name}》的大纲。请先询问最关键的创作偏好。`
      const userMessage: ProjectChatMessage = {
        id: makeId('user'),
        role: 'user',
        content: `${content}${attachmentNote}`,
        state: 'complete',
      }
      const stableMessages = messagesRef.current.filter(
        (message) => message.state !== 'streaming',
      )
      const conversation = [...stableMessages, userMessage]
      setComposer('')
      setMessages(conversation)
      messagesRef.current = conversation
      if (includeOutlineContext) setOutlineContextSent(true)
      await runConversation(conversation)
    },
    [
      attachmentMeta,
      attachment,
      chatLoading,
      composer,
      creationMode,
      outlineContextSent,
      outlineInspectionAccepted,
      outlineInspectionIssue,
      outlineInspecting,
      outlineInspection,
      runConversation,
    ],
  )

  useEffect(() => {
    if (!routeState.autoSend || !routeState.initialPrompt?.trim()) return
    if (initialFile && (outlineInspecting || !outlineInspection)) return
    const timer = window.setTimeout(() => {
      if (initialHandledRef.current) return
      initialHandledRef.current = true
      void handleSend(routeState.initialPrompt)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [
    handleSend,
    initialFile,
    outlineInspecting,
    outlineInspection,
    routeState.autoSend,
    routeState.initialPrompt,
  ])

  const handleRetry = useCallback(() => {
    if (chatLoading) return
    const current = messagesRef.current
    let lastUser = -1
    for (let index = current.length - 1; index >= 0; index -= 1) {
      if (current[index].role === 'user') {
        lastUser = index
        break
      }
    }
    if (lastUser < 0) return
    const conversation = current.slice(0, lastUser + 1)
    setMessages(conversation)
    messagesRef.current = conversation
    void runConversation(conversation)
  }, [chatLoading, runConversation])

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void handleSend()
    }
  }

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    const allowed = /\.(docx|txt|md|markdown)$/i.test(file.name)
    if (!allowed) {
      setPageError('仅支持 .docx、.txt、.md 或 .markdown 文件。')
      return
    }
    if (file.size > MAX_ATTACHMENT_SIZE) {
      setPageError('附件不能超过 5 MB。')
      return
    }
    setAttachment(file)
    setAttachmentMeta(fileMeta(file))
    setCreationMode('outline')
    setPageError(null)
    void inspectOutlineFile(file)
  }

  const setField = useCallback(
    <K extends keyof ProjectBlueprint>(key: K, value: ProjectBlueprint[K]) => {
      setBlueprint((current) => ({ ...current, [key]: value }))
      setAiReadiness(0)
    },
    [],
  )

  const applyPromptPreset = useCallback((text: string) => {
    setBlueprint((current) => ({
      ...current,
      custom_prompt: mergeProjectPrompt(current.custom_prompt, text),
    }))
    setAiReadiness(0)
  }, [])

  const handleLengthChange = (value: ProjectLengthType) => {
    const preset = LENGTH_OPTIONS.find((option) => option.value === value)
    setBlueprint((current) => ({
      ...current,
      length_type: value,
      target_chapters: preset?.chapters ?? current.target_chapters,
    }))
    setAiReadiness(0)
  }

  const clearDraft = () => {
    const blueprintChanged = (Object.keys(DEFAULT_BLUEPRINT) as Array<keyof ProjectBlueprint>)
      .some((key) => blueprint[key] !== DEFAULT_BLUEPRINT[key])
    if (
      (messages.length > 0 || blueprintChanged || Boolean(attachmentMeta) || Boolean(composer.trim())) &&
      !window.confirm('清空当前创作草稿？此操作无法撤销。')
    ) {
      return
    }
    controllerRef.current?.abort()
    localStorage.removeItem(DRAFT_KEY)
    setBlueprint(DEFAULT_BLUEPRINT)
    setMessages([])
    messagesRef.current = []
    setAttachment(null)
    setAttachmentMeta(null)
    setOutlineInspection(null)
    setOutlineContextSent(false)
    setCreationMode('idea')
    setSuggestedReplies([])
    setAssumptions([])
    setAiReadiness(0)
    setComposer('')
    setPageError(null)
    setChatError(null)
    setSavedAt('')
  }

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!blueprint.title.trim()) throw new Error('请先填写作品标题。')
      if (attachmentMeta?.needsReselect && !attachment) {
        throw new Error('草稿中的附件需要重新选择，或先移除附件。')
      }
      let verifiedInspection = outlineInspection
      if (attachment && !verifiedInspection) {
        verifiedInspection = await inspectOutlineFile(attachment)
      }
      if (attachment) {
        const inspectionIssue = getOutlineInspectionIssue(verifiedInspection)
        if (inspectionIssue) throw new Error(inspectionIssue)
      }

      const themes = blueprint.themes
        .split(/[，,、\n]/)
        .map((item) => item.trim())
        .filter(Boolean)
      const worldRules = blueprint.world_rules
        .split(/\n+/)
        .map((item) => item.trim())
        .filter(Boolean)
      const creativePrompt = messages
        .filter((message) => message.role === 'user' && message.content.trim())
        .map((message) => message.content.trim())
        .join('\n\n')
        .slice(0, 12_000)
      const targetChapters = clampInteger(blueprint.target_chapters, 1, 5000, 120)
      const chapterWords = clampInteger(blueprint.chapter_words, 500, 20_000, 3000)
      let created: Project | null = null

      try {
        setCreationStage('正在保存创作简报…')
        created = await projectsApi.create({
          title: blueprint.title.trim(),
          genre: blueprint.genre.trim() || undefined,
          type: blueprint.genre.trim() || undefined,
          description:
            blueprint.description.trim() ||
            blueprint.logline.trim() ||
            blueprint.world_setting.trim() ||
            undefined,
          target_chapters: targetChapters,
          target_words: targetChapters * chapterWords,
          autonomy_level: blueprint.autonomy_level,
          custom_prompt: blueprint.custom_prompt.trim() || undefined,
          creative_conversation: messages
            .filter((message) => message.content.trim())
            .map(({ role, content }) => ({ role, content: content.trim() })),
          creation_blueprint: {
            ...blueprint,
            themes,
            world_rules: worldRules,
            protagonist_goal: blueprint.protagonist_desire,
            flaw: blueprint.protagonist_flaw,
            fear: blueprint.protagonist_fear,
            setting: blueprint.world_setting,
            audience: blueprint.audience_experience,
            target_chapters: targetChapters,
            words_per_chapter: chapterWords,
            chapter_words: chapterWords,
          },
          config: {
            ...blueprint,
            title: blueprint.title.trim(),
            themes,
            world_rules: worldRules,
            protagonist_goal: blueprint.protagonist_desire,
            flaw: blueprint.protagonist_flaw,
            fear: blueprint.protagonist_fear,
            setting: blueprint.world_setting,
            audience: blueprint.audience_experience,
            creative_prompt: creativePrompt || blueprint.logline.trim(),
            target_chapters: targetChapters,
            words_per_chapter: chapterWords,
            chapter_words: chapterWords,
            autonomy_level: blueprint.autonomy_level,
            creative_blueprint_version: 2,
          },
        })

        if (attachment) {
          setCreationStage('正在上传并校验大纲…')
          await projectsApi.uploadOutline(created.id, attachment)
        }
        setCreationStage('项目已就绪')
        return created
      } catch (error) {
        if (created?.id) {
          setCreationStage('正在回滚未完成的项目…')
          try {
            await projectsApi.remove(created.id)
          } catch (rollbackError) {
            throw new Error(
              `${(error as Error).message}；自动回滚失败：${(rollbackError as Error).message}`,
            )
          }
        }
        throw error
      }
    },
    onSuccess: async (project) => {
      localStorage.removeItem(DRAFT_KEY)
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
      setCurrentProject(project)
      navigate('/cockpit', {
        replace: true,
        state: { created: true, blueprint },
      })
    },
    onError: (error: Error) => {
      setPageError(error.message)
      setCreationStage('')
    },
  })

  const modelLabel = modelStatus.data?.configured
    ? modelStatus.data.model || '模型已连接'
    : modelStatus.isError
      ? '模型状态未知'
      : modelStatus.isLoading
        ? '正在检测模型'
        : '模型未配置'

  const draftTime = savedAt
    ? new Intl.DateTimeFormat('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
      }).format(new Date(savedAt))
    : ''

  return (
    <div className="flex h-screen h-[100dvh] min-h-[640px] flex-col overflow-hidden bg-ink-950 text-gray-100">
      <header className="relative z-30 flex h-16 shrink-0 items-center gap-3 border-b border-ink-700/80 bg-ink-950/90 px-4 backdrop-blur-xl sm:px-6">
        <button
          type="button"
          onClick={() => navigate('/')}
          className="flex h-10 w-10 items-center justify-center rounded-xl text-gray-400 transition-colors hover:bg-ink-800 hover:text-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/50"
          aria-label="返回项目首页"
        >
          <ArrowLeft size={19} />
        </button>
        <div className="flex min-w-0 items-center gap-3">
          <div className="hidden h-9 w-9 items-center justify-center rounded-xl border border-gold-500/20 bg-gold-500/10 text-gold-400 sm:flex">
            <BookOpen size={17} />
          </div>
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold text-gray-100 sm:text-base">
              创建新小说
            </h1>
            <div className="flex items-center gap-2 text-[11px] text-gray-500">
              <span className="inline-flex items-center gap-1">
                <span
                  className={cn(
                    'h-1.5 w-1.5 rounded-full',
                    modelStatus.data?.configured ? 'bg-emerald-400' : 'bg-amber-400',
                  )}
                />
                {modelLabel}
              </span>
              {draftTime && (
                <span className="hidden items-center gap-1 sm:inline-flex">
                  · 草稿已保存 {draftTime}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={clearDraft}
            className="hidden h-9 items-center gap-1.5 rounded-lg px-3 text-xs text-gray-500 transition-colors hover:bg-ink-800 hover:text-gray-200 sm:flex"
          >
            <Trash2 size={14} />
            清空草稿
          </button>
          <button
            type="button"
            onClick={() => setMobileBlueprintOpen(true)}
            className="flex h-10 items-center gap-2 rounded-xl border border-ink-600 bg-ink-850 px-3 text-sm text-gray-200 shadow-sm lg:hidden"
            aria-label="打开创作配置"
          >
            <PanelRight size={17} />
            配置与创建
            <span className="hidden rounded-full bg-emerald-400/15 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-300 sm:inline">
              {readiness}%
            </span>
          </button>
        </div>
      </header>

      <div className="relative flex min-h-0 flex-1">
        <main className="relative flex min-w-0 flex-1 flex-col bg-ink-950">
          <div className="subtle-grid pointer-events-none absolute inset-0 opacity-40" />
          <div
            className="relative mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col overflow-y-auto px-4 pb-5 sm:px-8"
            role={messages.length > 0 ? 'log' : undefined}
            aria-live={messages.length > 0 ? 'polite' : undefined}
            aria-relevant={messages.length > 0 ? 'additions text' : undefined}
            aria-label={messages.length > 0 ? '创作顾问对话' : undefined}
          >
            {messages.length === 0 ? (
              <section className="mx-auto flex w-full max-w-6xl flex-col items-center py-8 text-center sm:py-12">
                <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-emerald-400/20 bg-emerald-400/10 text-emerald-300 shadow-[0_18px_60px_rgba(28,78,57,0.18)]">
                  <Sparkles size={21} />
                </div>
                <p className="mb-2 text-xs font-medium uppercase tracking-[0.2em] text-emerald-300/80">
                  Novel Agent OS · Project Studio
                </p>
                <h2 className="max-w-3xl font-serif text-2xl font-semibold leading-tight text-gray-50 sm:text-4xl">
                  先建立可长期执行的写作契约
                </h2>
                <p className="mt-3 max-w-2xl text-sm leading-7 text-gray-400 sm:text-base">
                  大纲是事实来源，项目总提示词是所有 Agent 的长期规则。两项都在这里明确配置，后续仍可修改，不会因自动写作而丢失。
                </p>

                <label className="mt-6 flex w-full max-w-5xl flex-col gap-2 text-left sm:flex-row sm:items-center">
                  <span className="shrink-0 text-xs font-medium text-gray-400">作品标题</span>
                  <Input
                    value={blueprint.title}
                    onChange={(event) => setField('title', event.target.value)}
                    maxLength={200}
                    placeholder="输入标题；暂未确定也可以先填写工作名"
                    className="h-11 flex-1 bg-ink-900/80 text-[15px]"
                  />
                  <span className="shrink-0 text-[10px] text-gray-600">用于保存项目，不影响后续改名</span>
                </label>

                <div className="mt-7 grid w-full max-w-5xl gap-4 text-left xl:grid-cols-[1.05fr_0.95fr]">
                  <section className="rounded-2xl border border-ink-700 bg-ink-900/78 p-4 shadow-[0_18px_60px_rgba(0,0,0,0.18)] sm:p-5">
                    <div className="flex items-start gap-3">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-gold-400/20 bg-gold-400/8 text-gold-300">
                        <FileText size={18} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-sm font-semibold text-gray-100">1. 故事来源 / 详细大纲</h3>
                          <span className="rounded-full border border-blue-400/20 bg-blue-400/8 px-2 py-0.5 text-[9px] font-semibold text-blue-200">无损全文索引</span>
                        </div>
                        <p className="mt-1 text-[11px] leading-5 text-gray-500">
                          支持 DOCX、TXT、MD。DOCX 的正文与表格会按原顺序保存和切分，不会塞进一次模型调用。
                        </p>
                      </div>
                    </div>

                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      className={cn(
                        'mt-4 flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
                        attachmentMeta && outlineNeedsReselect
                          ? 'border-amber-400/25 bg-amber-400/[0.045] hover:bg-amber-400/[0.07]'
                          : attachmentMeta
                          ? 'border-emerald-400/25 bg-emerald-400/[0.045] hover:bg-emerald-400/[0.07]'
                          : 'border-dashed border-ink-600 bg-ink-950/55 hover:border-emerald-400/30 hover:bg-ink-950',
                      )}
                    >
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-ink-800 text-gray-400"><Paperclip size={16} /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium text-gray-200">
                          {attachmentMeta ? attachmentMeta.name : '选择故事大纲'}
                        </span>
                        <span className="mt-0.5 block text-[10px] text-gray-600">
                          {attachmentMeta
                            ? outlineNeedsReselect
                              ? '刷新后需重新选择文件'
                              : outlineInspecting
                              ? '正在按全文结构预检…'
                              : outlineInspection
                                ? `${formatBytes(attachmentMeta.size)} · 预检完成，可随时更换`
                                : `${formatBytes(attachmentMeta.size)} · 等待预检`
                            : '最大 5 MB · 大纲不是必填，但长篇项目强烈建议上传'}
                        </span>
                      </span>
                      <span className="shrink-0 rounded-lg border border-ink-600 px-2.5 py-1.5 text-[10px] font-medium text-gray-400">
                        {attachmentMeta ? (outlineNeedsReselect ? '重新选择' : '更换') : '上传'}
                      </span>
                    </button>

                    {outlineInspecting ? (
                      <div className="mt-3 rounded-xl border border-ink-700 bg-ink-950/50 p-3" aria-live="polite">
                        <div className="flex items-center gap-2 text-[11px] text-gray-400"><Loader2 size={13} className="animate-spin text-emerald-300" /> 正在提取段落、表格、标题层级与连续字符区间…</div>
                        <div className="mt-2 h-1 overflow-hidden rounded-full bg-ink-700"><div className="h-full w-2/3 animate-pulse rounded-full bg-emerald-400/65" /></div>
                      </div>
                    ) : outlineInspection ? (
                      <div className="mt-3">
                        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 2xl:grid-cols-5">
                          <InspectionMetric label="可见字符" value={outlineInspection.char_count.toLocaleString()} />
                          <InspectionMetric label="来源节点" value={(outlineInspection.node_count ?? 0).toLocaleString()} />
                          <InspectionMetric label="分块调用单元" value={(outlineInspection.chunk_count ?? 0).toLocaleString()} />
                          <InspectionMetric
                            label="索引覆盖"
                            value={outlineInspection.coverage_ratio == null ? '—' : `${(outlineInspection.coverage_ratio * 100).toFixed(2)}%`}
                            ok={outlineInspection.exact_source_covered}
                          />
                          <InspectionMetric label="项目修订" value="创建后 R1" />
                        </div>
                        <div className={cn(
                          'mt-2 flex items-start gap-2 rounded-lg border px-2.5 py-2 text-[10px] leading-5',
                          outlineInspection.exact_source_covered
                            ? 'border-emerald-400/15 bg-emerald-400/[0.035] text-emerald-100/70'
                            : 'border-amber-400/15 bg-amber-400/[0.035] text-amber-100/70',
                        )}>
                          {outlineInspectionAccepted
                            ? <CheckCircle2 size={12} className="mt-1 shrink-0" />
                            : <AlertCircle size={12} className="mt-1 shrink-0" />}
                          <span>
                            当前创作顾问只读取结构预览；点击创建后，后台保存原文件并无损索引全文，再按分块多调用编译。{outlineInspectionAccepted ? '本次预检已通过可验证性检查。' : outlineInspectionIssue}
                          </span>
                        </div>
                      </div>
                    ) : null}
                  </section>

                  <section className="rounded-2xl border border-emerald-400/20 bg-emerald-400/[0.035] p-4 shadow-[0_18px_60px_rgba(0,0,0,0.18)] sm:p-5">
                    <div className="flex items-start gap-3">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-emerald-400/20 bg-emerald-400/10 text-emerald-300"><Sparkles size={17} /></span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-sm font-semibold text-gray-100">2. 项目总提示词</h3>
                          <span className="rounded-full border border-emerald-400/20 bg-emerald-400/10 px-2 py-0.5 text-[9px] font-semibold text-emerald-200">注入所有 Agent</span>
                        </div>
                        <p className="mt-1 text-[11px] leading-5 text-gray-500">定义文风、视角、世界规则、不可改动项与内容禁区；不是一次性的聊天消息。</p>
                      </div>
                    </div>
                    <TextArea
                      value={blueprint.custom_prompt}
                      onChange={(event) => setField('custom_prompt', event.target.value)}
                      aria-label="项目总提示词"
                      rows={7}
                      maxLength={20_000}
                      placeholder="例如：严格遵守来源大纲与已确认世界规则；每章按多个场景独立规划、写作和校验；保持第三人称限制视角；人物行为必须符合既有动机；不得引入来源之外的关键设定……"
                      className="mt-4 min-h-[150px] bg-ink-950/75"
                    />
                    <PromptPresetPicker
                      prompt={blueprint.custom_prompt}
                      onApply={applyPromptPreset}
                      compact
                    />
                    <div className="mt-2 flex items-center justify-between gap-3">
                      <span className="text-[10px] text-gray-600">{blueprint.custom_prompt.length.toLocaleString()} / 20,000 字符</span>
                      <button
                        type="button"
                        disabled={chatLoading}
                        onClick={() => void handleSend('请结合当前项目蓝图和已上传大纲的结构预览，生成一份可长期执行的“项目总提示词”，明确来源忠实度、叙事视角、人物一致性、文风、节奏、改错规则、内容边界和禁止事项。请同步写入项目总提示词字段；不要宣称已经处理大纲全文。')}
                        className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-emerald-400/20 bg-emerald-400/10 px-2.5 text-[10px] font-medium text-emerald-200 hover:bg-emerald-400/15 disabled:opacity-50"
                      >
                        <Sparkles size={11} /> {blueprint.custom_prompt.trim() ? '让顾问优化' : '让顾问起草'}
                      </button>
                    </div>
                  </section>
                </div>

                <section className="mt-4 w-full max-w-5xl overflow-hidden rounded-2xl border border-ink-700 bg-ink-900/58 text-left" aria-labelledby="production-path-title">
                  <div className="flex flex-col gap-2 border-b border-ink-700 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                    <div>
                      <h3 id="production-path-title" className="text-xs font-semibold text-gray-200">创建后不是“一次生成”，而是一条可监督的生产线</h3>
                      <p className="mt-1 text-[10px] leading-4 text-gray-600">创建项目只保存来源与契约；进入准备台确认结构后，再由你显式启动 24H 任务。</p>
                    </div>
                    <span className="shrink-0 rounded-full border border-blue-400/20 bg-blue-400/8 px-2.5 py-1 text-[9px] font-semibold text-blue-200">关闭浏览器可恢复</span>
                  </div>
                  <div className="grid gap-px bg-ink-700 sm:grid-cols-2 xl:grid-cols-4">
                    {PRODUCTION_STAGES.map((stage, index) => {
                      const Icon = stage.icon
                      return (
                        <div key={stage.title} className="flex gap-3 bg-ink-900/95 px-4 py-3.5">
                          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-ink-600 bg-ink-950 text-emerald-300"><Icon size={14} /></span>
                          <div className="min-w-0">
                            <p className="text-[10px] font-mono text-gray-700">0{index + 1}</p>
                            <p className="mt-0.5 text-[11px] font-semibold text-gray-200">{stage.title}</p>
                            <p className="mt-1 text-[10px] leading-4 text-gray-600">{stage.description}</p>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </section>

                <button
                  type="button"
                  onClick={() => setMobileBlueprintOpen(true)}
                  className="mt-4 flex min-h-11 w-full max-w-5xl items-center justify-center gap-2 rounded-xl border border-emerald-400/25 bg-emerald-400/[0.07] px-4 text-sm font-semibold text-emerald-200 transition-colors hover:bg-emerald-400/12 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40 lg:hidden"
                >
                  <PanelRight size={15} />
                  继续完善配置并创建
                  <span className="rounded-full border border-emerald-400/20 px-1.5 py-0.5 text-[9px]">{readiness}%</span>
                </button>

                <div className="mt-8 flex w-full max-w-5xl items-center gap-3 text-left">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-ink-600 bg-ink-900 text-[11px] font-semibold text-gray-400">3</span>
                  <div><h3 className="text-sm font-semibold text-gray-200">用创作顾问完善蓝图</h3><p className="mt-0.5 text-[10px] text-gray-600">选择从零构思，或围绕已上传大纲补充改编要求；完整配置始终保留在右侧。</p></div>
                </div>
                <div
                  className="mt-3 grid w-full max-w-5xl grid-cols-2 gap-1.5 rounded-2xl border border-ink-700 bg-ink-900/75 p-1.5"
                  role="tablist"
                  aria-label="选择项目创建方式"
                >
                  <button
                    type="button"
                    role="tab"
                    aria-selected={creationMode === 'idea'}
                    onClick={() => setCreationMode('idea')}
                    className={cn(
                      'flex min-h-12 items-center justify-center gap-2 rounded-xl px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
                      creationMode === 'idea'
                        ? 'bg-emerald-300 text-emerald-950 shadow-sm'
                        : 'text-gray-400 hover:bg-ink-800 hover:text-gray-200',
                    )}
                  >
                    <Sparkles size={16} />
                    从零开始
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={creationMode === 'outline'}
                    onClick={() => setCreationMode('outline')}
                    className={cn(
                      'flex min-h-12 items-center justify-center gap-2 rounded-xl px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
                      creationMode === 'outline'
                        ? 'bg-emerald-300 text-emerald-950 shadow-sm'
                        : 'text-gray-400 hover:bg-ink-800 hover:text-gray-200',
                    )}
                  >
                    <FileText size={16} />
                    基于已有大纲
                  </button>
                </div>

                {creationMode === 'idea' ? (
                  <div className="mt-5 grid w-full max-w-2xl gap-2 sm:grid-cols-2">
                    {STARTER_PROMPTS.map((prompt) => (
                      <button
                        key={prompt}
                        type="button"
                        onClick={() => void handleSend(prompt)}
                        className="group flex min-h-12 items-center justify-between rounded-xl border border-ink-700 bg-ink-900/70 px-4 py-3 text-left text-sm text-gray-300 transition-all hover:-translate-y-0.5 hover:border-emerald-400/35 hover:bg-ink-850 hover:text-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40"
                      >
                        <span>{prompt}</span>
                        <ArrowLeft
                          size={14}
                          className="rotate-180 text-gray-600 transition-transform group-hover:translate-x-0.5 group-hover:text-emerald-300"
                        />
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="mt-5 grid w-full max-w-5xl gap-2 sm:grid-cols-2">
                    {OUTLINE_PROMPTS.map((prompt) => (
                      <button
                        key={prompt}
                        type="button"
                        onClick={() => void handleSend(prompt)}
                        disabled={!outlineInspectionAccepted || outlineInspecting}
                        className="group flex min-h-12 items-center justify-between rounded-xl border border-ink-700 bg-ink-900/70 px-4 py-3 text-left text-sm text-gray-300 transition-all hover:-translate-y-0.5 hover:border-emerald-400/35 hover:bg-ink-850 hover:text-gray-100 disabled:cursor-not-allowed disabled:opacity-45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40"
                      >
                        <span>{prompt}</span>
                        <ArrowLeft size={14} className="ml-3 shrink-0 rotate-180 text-gray-600 transition-transform group-hover:translate-x-0.5 group-hover:text-emerald-300" />
                      </button>
                    ))}
                    {!outlineInspection && (
                      <p className="col-span-full mt-1 text-center text-[10px] text-amber-200/70">请先在上方上传并完成大纲预检，再让顾问基于结构预览完善蓝图。</p>
                    )}
                  </div>
                )}
              </section>
            ) : (
              <div className="space-y-7 py-8 sm:py-10">
                {messages.map((message) => (
                  <article
                    key={message.id}
                    className={cn(
                      'flex gap-3 sm:gap-4',
                      message.role === 'user' ? 'justify-end' : 'justify-start',
                    )}
                  >
                    {message.role === 'assistant' && (
                      <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-emerald-400/20 bg-emerald-400/10 text-emerald-300">
                        <Sparkles size={15} />
                      </div>
                    )}
                    <div
                      className={cn(
                        'max-w-[88%] break-words whitespace-pre-wrap text-sm leading-7 sm:max-w-[78%] sm:text-[15px]',
                        message.role === 'user'
                          ? 'rounded-2xl rounded-tr-md border border-gold-500/15 bg-gold-500/10 px-4 py-3 text-gray-100'
                          : 'pt-1 text-gray-200',
                      )}
                    >
                      {renderMessageText(message.content)}
                      {message.state === 'streaming' && (
                        <span className="ml-1 inline-block h-4 w-0.5 animate-pulse bg-emerald-300 align-middle" />
                      )}
                      {message.state === 'stopped' && (
                        <span className="mt-2 block text-xs text-amber-300">已停止</span>
                      )}
                      {message.state === 'error' && (
                        <span className="mt-2 block text-xs text-red-300">回复未完成</span>
                      )}
                    </div>
                  </article>
                ))}
                <div ref={chatEndRef} />
              </div>
            )}
          </div>

          <div className="relative z-10 shrink-0 bg-gradient-to-t from-ink-950 via-ink-950 to-transparent px-4 pb-4 pt-3 sm:px-8 sm:pb-6">
            <div className="mx-auto max-w-3xl">
              {(chatError || pageError) && (
                <div
                  className="mb-2 flex items-start gap-2 rounded-xl border border-red-400/20 bg-red-400/8 px-3 py-2.5 text-xs text-red-200"
                  role="alert"
                >
                  <AlertCircle size={15} className="mt-0.5 shrink-0" />
                  <span className="flex-1">{pageError || chatError}</span>
                  {chatError && !chatLoading && (
                    <button
                      type="button"
                      onClick={handleRetry}
                      className="inline-flex shrink-0 items-center gap-1 font-medium text-red-100 underline decoration-red-300/40 underline-offset-2"
                    >
                      <RotateCcw size={12} /> 重试
                    </button>
                  )}
                </div>
              )}

              {suggestedReplies.length > 0 && !chatLoading && (
                <div className="no-scrollbar mb-2 flex gap-2 overflow-x-auto pb-1" aria-label="建议回复">
                  {suggestedReplies.map((reply) => (
                    <button
                      key={reply}
                      type="button"
                      onClick={() => void handleSend(reply)}
                      className="shrink-0 rounded-full border border-emerald-400/25 bg-emerald-400/8 px-3 py-1.5 text-xs text-emerald-100 transition-colors hover:bg-emerald-400/15"
                    >
                      {reply}
                    </button>
                  ))}
                </div>
              )}

              <div className="rounded-2xl border border-ink-600 bg-ink-850/95 p-2 shadow-[0_20px_70px_rgba(0,0,0,0.34)] backdrop-blur-xl focus-within:border-emerald-400/45 focus-within:ring-1 focus-within:ring-emerald-400/15">
                {attachmentMeta && (
                  <div className="mb-1.5 flex items-center gap-2 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-xs">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gold-500/10 text-gold-400">
                      <FileText size={15} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium text-gray-200">{attachmentMeta.name}</p>
                      <p className={attachmentMeta.needsReselect ? 'text-amber-300' : 'text-gray-500'}>
                        {attachmentMeta.needsReselect
                          ? '刷新后需重新选择此文件'
                          : outlineInspecting
                            ? '正在预检大纲全文结构…'
                            : outlineInspection
                              ? `${outlineInspection.char_count.toLocaleString()} 字符 · 顾问仅使用结构预览 · 创建后无损索引全文`
                              : `${formatBytes(attachmentMeta.size)} · 解析未完成`}
                      </p>
                    </div>
                    {attachmentMeta.needsReselect && (
                      <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        className="rounded-lg px-2 py-1 text-amber-200 hover:bg-amber-400/10"
                      >
                        重新选择
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => {
                        setAttachment(null)
                        setAttachmentMeta(null)
                        outlineInspectionRequestRef.current += 1
                        setOutlineInspection(null)
                        setOutlineInspecting(false)
                        setOutlineContextSent(false)
                        setPageError(null)
                      }}
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-gray-500 hover:bg-ink-700 hover:text-gray-200"
                      aria-label="移除附件"
                    >
                      <X size={15} />
                    </button>
                  </div>
                )}
                <textarea
                  ref={composerRef}
                  value={composer}
                  onChange={(event) => setComposer(event.target.value)}
                  onKeyDown={handleComposerKeyDown}
                  rows={3}
                  disabled={chatLoading}
                  placeholder={
                    creationMode === 'outline'
                      ? '补充必须保留的情节、改编方向或文风要求（可选）…'
                      : '描述故事灵感、人物或冲突…'
                  }
                  className="max-h-40 min-h-[72px] w-full resize-none bg-transparent px-2.5 py-2 text-sm leading-6 text-gray-100 placeholder:text-gray-600 focus:outline-none disabled:opacity-70 sm:text-[15px]"
                  aria-label="输入故事想法"
                />
                <div className="flex items-center gap-2 px-1">
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
                    className="flex h-9 items-center gap-1.5 rounded-lg px-2.5 text-xs text-gray-400 transition-colors hover:bg-ink-700 hover:text-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40"
                    aria-label="附加大纲文件"
                  >
                    <Paperclip size={16} />
                    <span>上传大纲</span>
                  </button>
                  <span className="hidden text-[11px] text-gray-600 sm:inline">
                    Enter 发送 · Shift + Enter 换行
                  </span>
                  <div className="ml-auto">
                    {chatLoading ? (
                      <button
                        type="button"
                        onClick={() => controllerRef.current?.abort()}
                        className="flex h-9 w-9 items-center justify-center rounded-xl bg-gray-100 text-ink-950 transition-colors hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300"
                        aria-label="停止生成"
                      >
                        <Square size={14} fill="currentColor" />
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => void handleSend()}
                        disabled={outlineConversationBlocked || (!composer.trim() && !attachmentMeta)}
                        className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-300 text-emerald-950 transition-all hover:bg-emerald-200 disabled:cursor-not-allowed disabled:bg-ink-700 disabled:text-gray-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300/70"
                        aria-label="发送消息"
                      >
                        <Send size={16} />
                      </button>
                    )}
                  </div>
                </div>
              </div>
              <p className="mt-2 text-center text-[10px] leading-4 text-gray-600 sm:text-[11px]">
                AI 会提出建议，但故事方向与最终内容始终由你决定。
              </p>
            </div>
          </div>
        </main>

        {mobileBlueprintOpen && (
          <button
            type="button"
            className="fixed inset-0 z-40 bg-black/65 backdrop-blur-sm lg:hidden"
            onClick={() => setMobileBlueprintOpen(false)}
            aria-label="关闭创作配置"
          />
        )}
        <aside
          ref={panelRef}
          role={mobileBlueprintOpen ? 'dialog' : undefined}
          aria-modal={mobileBlueprintOpen ? true : undefined}
          aria-labelledby="blueprint-title"
          className={cn(
            'fixed inset-y-0 right-0 z-50 w-[min(92vw,420px)] flex-col border-l border-ink-700 bg-ink-900 shadow-2xl lg:static lg:z-20 lg:flex lg:w-[420px] lg:shadow-none',
            mobileBlueprintOpen ? 'flex' : 'hidden',
          )}
        >
          <div className="flex shrink-0 items-start gap-3 border-b border-ink-700 px-5 py-4">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-emerald-400/10 text-emerald-300">
              <FileText size={17} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <h2 id="blueprint-title" className="text-sm font-semibold text-gray-100">
                  创作配置
                </h2>
                <span className="text-xs font-semibold text-emerald-300">{readiness}%</span>
              </div>
              <p className="mt-0.5 text-[11px] text-gray-500">项目简报、提示词与写作边界</p>
              <div className="mt-2 h-1 overflow-hidden rounded-full bg-ink-700">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-300 transition-[width] duration-500"
                  style={{ width: `${readiness}%` }}
                />
              </div>
            </div>
            <button
              ref={panelCloseRef}
              type="button"
              onClick={() => setMobileBlueprintOpen(false)}
              className="flex h-9 w-9 items-center justify-center rounded-lg text-gray-500 hover:bg-ink-700 hover:text-gray-200 lg:hidden"
              aria-label="关闭创作配置"
            >
              <X size={17} />
            </button>
          </div>

          <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-5 py-5">
            {localMissing.length > 0 && (
              <section className="rounded-xl border border-amber-400/15 bg-amber-400/5 p-3">
                <p className="text-xs font-medium text-amber-200">还可补充</p>
                <p className="mt-1 text-[11px] leading-5 text-amber-100/60">
                  {localMissing.slice(0, 5).join('、')}
                  {localMissing.length > 5 ? ` 等 ${localMissing.length} 项` : ''}
                </p>
              </section>
            )}

            <section className="rounded-2xl border border-emerald-400/20 bg-emerald-400/[0.045] p-4 shadow-[0_16px_45px_rgba(0,0,0,0.12)]">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-gray-100">项目提示词</h3>
                    <span className="rounded-full border border-emerald-400/20 bg-emerald-400/10 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-emerald-200">
                      所有 Agent
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] leading-5 text-gray-500">
                    定义 AI 的写作角色、文风、叙事方法和长期禁区；创建后仍可随时修改。
                  </p>
                </div>
              </div>
              <TextArea
                value={blueprint.custom_prompt}
                onChange={(event) => setField('custom_prompt', event.target.value)}
                aria-label="项目提示词"
                rows={7}
                maxLength={20_000}
                placeholder="例如：你是一位擅长长篇东方幻想的小说家。保持第三人称限制视角；人物行动必须符合既有动机；严格遵守已确认的大纲与世界规则；避免无成本升级和机械总结……"
                className="mt-3 bg-ink-950/75"
              />
              <PromptPresetPicker
                prompt={blueprint.custom_prompt}
                onApply={applyPromptPreset}
                compact
              />
              <div className="mt-2 flex items-center justify-between gap-3">
                <span className="text-[10px] text-gray-600">
                  {blueprint.custom_prompt.length.toLocaleString()} / 20,000 字符
                </span>
                <button
                  type="button"
                  disabled={chatLoading}
                  onClick={() =>
                    void handleSend(
                      '请根据当前已经确认的创作简报，为整个项目生成一份专业、可执行的项目提示词。它将注入所有写作 Agent，需要明确叙事视角、文风、人物一致性、大纲约束、世界规则、节奏原则和内容边界。请同步更新右侧“项目提示词”。',
                    )
                  }
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-emerald-400/20 bg-emerald-400/10 px-2.5 text-[11px] font-medium text-emerald-200 transition-colors hover:bg-emerald-400/15 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Sparkles size={12} />
                  {blueprint.custom_prompt.trim() ? '让 AI 优化' : '让 AI 生成'}
                </button>
              </div>
            </section>

            <BlueprintSection title="故事核心" description="先把故事最重要的承诺说清楚" defaultOpen>
              <Field label="作品标题" required>
                <Input
                  value={blueprint.title}
                  onChange={(event) => setField('title', event.target.value)}
                  placeholder="例如：长夜航标"
                />
              </Field>
              <Field label="一句话故事" hint="主角 + 目标 + 阻力">
                <TextArea
                  value={blueprint.logline}
                  onChange={(event) => setField('logline', event.target.value)}
                  rows={2}
                  placeholder="用一句话说明故事最吸引人的部分"
                />
              </Field>
              <Field label="简介 / 故事承诺">
                <TextArea
                  value={blueprint.description}
                  onChange={(event) => setField('description', event.target.value)}
                  rows={3}
                  placeholder="读者将跟随怎样的一段旅程？"
                />
              </Field>
              <Field label="类型 / 题材">
                <Input
                  value={blueprint.genre}
                  onChange={(event) => setField('genre', event.target.value)}
                  placeholder="如：近未来悬疑、东方奇幻"
                />
              </Field>
            </BlueprintSection>

            <BlueprintSection title="人物与冲突" description="决定故事为什么必须发生">
              <Field label="主角">
                <TextArea
                  value={blueprint.protagonist}
                  onChange={(event) => setField('protagonist', event.target.value)}
                  rows={2}
                  placeholder="身份、性格、缺陷与处境"
                />
              </Field>
              <Field label="主角真正想要什么">
                <TextArea
                  value={blueprint.protagonist_desire}
                  onChange={(event) => setField('protagonist_desire', event.target.value)}
                  rows={2}
                  placeholder="外在目标与内在需求"
                />
              </Field>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="核心缺陷">
                  <TextArea
                    value={blueprint.protagonist_flaw}
                    onChange={(event) => setField('protagonist_flaw', event.target.value)}
                    rows={2}
                    placeholder="会反复导致错误选择的性格盲点"
                  />
                </Field>
                <Field label="深层恐惧">
                  <TextArea
                    value={blueprint.protagonist_fear}
                    onChange={(event) => setField('protagonist_fear', event.target.value)}
                    rows={2}
                    placeholder="主角最不愿面对的失去或真相"
                  />
                </Field>
              </div>
              <Field label="核心冲突">
                <TextArea
                  value={blueprint.core_conflict}
                  onChange={(event) => setField('core_conflict', event.target.value)}
                  rows={3}
                  placeholder="谁或什么持续阻止主角？失败代价是什么？"
                />
              </Field>
              <Field label="对手 / 反方力量">
                <TextArea
                  value={blueprint.antagonist}
                  onChange={(event) => setField('antagonist', event.target.value)}
                  rows={2}
                  placeholder="对方自己的目标、正当性与资源"
                />
              </Field>
              <Field label="贯穿全书的故事问题">
                <Input
                  value={blueprint.story_question}
                  onChange={(event) => setField('story_question', event.target.value)}
                  placeholder="例如：改变制度是否一定会制造新的牺牲者？"
                />
              </Field>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="能力 / 金手指">
                  <TextArea
                    value={blueprint.ability}
                    onChange={(event) => setField('ability', event.target.value)}
                    rows={2}
                    placeholder="它能解决什么问题"
                  />
                </Field>
                <Field label="代价与上限">
                  <TextArea
                    value={blueprint.ability_cost}
                    onChange={(event) => setField('ability_cost', event.target.value)}
                    rows={2}
                    placeholder="使用条件、代价、暴露风险"
                  />
                </Field>
              </div>
            </BlueprintSection>

            <BlueprintSection title="世界与主题" description="约束越清晰，长篇越稳定">
              <Field label="世界设定">
                <TextArea
                  value={blueprint.world_setting}
                  onChange={(event) => setField('world_setting', event.target.value)}
                  rows={3}
                  placeholder="时代、地点、社会结构与特殊机制"
                />
              </Field>
              <Field label="不可违背的世界规则" hint="每行一条">
                <TextArea
                  value={blueprint.world_rules}
                  onChange={(event) => setField('world_rules', event.target.value)}
                  rows={3}
                  placeholder={'力量必有代价\n时间旅行不能改变既成事实'}
                />
              </Field>
              <Field label="主题" hint="可用逗号分隔">
                <Input
                  value={blueprint.themes}
                  onChange={(event) => setField('themes', event.target.value)}
                  placeholder="成长、记忆、选择"
                />
              </Field>
            </BlueprintSection>

            <BlueprintSection title="阅读体验" description="定义读者翻页时应有的感受">
              <Field label="语气 / 文风">
                <Input
                  value={blueprint.tone}
                  onChange={(event) => setField('tone', event.target.value)}
                  placeholder="冷峻克制、轻快幽默、热血高燃…"
                />
              </Field>
              <Field label="节奏与信息密度">
                <Input
                  value={blueprint.pacing}
                  onChange={(event) => setField('pacing', event.target.value)}
                  placeholder="如：前快后稳；高信息密度；每章有明确回报"
                />
              </Field>
              <Field label="目标读者体验">
                <TextArea
                  value={blueprint.audience_experience}
                  onChange={(event) => setField('audience_experience', event.target.value)}
                  rows={2}
                  placeholder="希望读者紧张、治愈、震撼，还是持续获得爽感？"
                />
              </Field>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="发布平台 / 受众">
                  <Input
                    value={blueprint.platform}
                    onChange={(event) => setField('platform', event.target.value)}
                    placeholder="如：男频长篇、出版向、全年龄"
                  />
                </Field>
                <Field label="创作语言">
                  <Input
                    value={blueprint.language}
                    onChange={(event) => setField('language', event.target.value)}
                    placeholder="中文"
                  />
                </Field>
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="叙事视角">
                  <select
                    value={blueprint.pov}
                    onChange={(event) => setField('pov', event.target.value)}
                    className="form-select"
                  >
                    <option>第一人称</option>
                    <option>第三人称限制</option>
                    <option>第三人称多视角</option>
                    <option>第三人称全知</option>
                  </select>
                </Field>
                <Field label="叙事时态">
                  <select
                    value={blueprint.tense}
                    onChange={(event) => setField('tense', event.target.value)}
                    className="form-select"
                  >
                    <option>过去时</option>
                    <option>现在时</option>
                    <option>混合（需明确规则）</option>
                  </select>
                </Field>
              </div>
              <Field label="结局偏好">
                <Input
                  value={blueprint.ending_preference}
                  onChange={(event) => setField('ending_preference', event.target.value)}
                  placeholder="圆满、开放、苦涩但有希望…"
                />
              </Field>
              <Field label="内容边界" hint="不希望出现的内容">
                <TextArea
                  value={blueprint.content_boundaries}
                  onChange={(event) => setField('content_boundaries', event.target.value)}
                  rows={2}
                  placeholder="例如：不描写虐待动物；避免无意义的后宫线"
                />
              </Field>
            </BlueprintSection>

            <BlueprintSection title="规模与节奏" description="所有数字之后都可以调整">
              <Field label="篇幅">
                <div className="grid grid-cols-5 gap-1.5">
                  {LENGTH_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => handleLengthChange(option.value)}
                      className={cn(
                        'min-h-9 rounded-lg border px-1 text-[11px] transition-colors',
                        blueprint.length_type === option.value
                          ? 'border-emerald-400/45 bg-emerald-400/10 text-emerald-200'
                          : 'border-ink-600 bg-ink-950 text-gray-500 hover:text-gray-200',
                      )}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="目标章数">
                  <Input
                    type="number"
                    min={1}
                    max={5000}
                    value={blueprint.target_chapters}
                    onChange={(event) =>
                      setField('target_chapters', clampInteger(event.target.value, 1, 5000, 1))
                    }
                  />
                </Field>
                <Field label="每章字数">
                  <Input
                    type="number"
                    min={500}
                    max={20000}
                    step={500}
                    value={blueprint.chapter_words}
                    onChange={(event) =>
                      setField('chapter_words', clampInteger(event.target.value, 500, 20_000, 500))
                    }
                  />
                </Field>
              </div>
              <Field label="卷数">
                <Input
                  value={blueprint.volume_count}
                  onChange={(event) => setField('volume_count', event.target.value)}
                  placeholder="自动，或填写 3"
                />
              </Field>
            </BlueprintSection>

            <BlueprintSection title="自动化与接管" description="明确项目默认边界；24H 任务仍需在总控台显式启动" defaultOpen>
              <div className="space-y-2" role="radiogroup" aria-label="AI 自治等级">
                {AUTONOMY_OPTIONS.map((option) => {
                  const selected = blueprint.autonomy_level === option.value
                  return (
                    <button
                      key={option.value}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      onClick={() => setField('autonomy_level', option.value)}
                      className={cn(
                        'flex w-full items-start gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
                        selected
                          ? 'border-emerald-400/35 bg-emerald-400/[0.07]'
                          : 'border-ink-700 bg-ink-950/55 hover:border-ink-600 hover:bg-ink-950',
                      )}
                    >
                      <span className={cn('mt-0.5 flex h-6 w-8 shrink-0 items-center justify-center rounded-lg border font-mono text-[10px] font-semibold', selected ? 'border-emerald-400/30 bg-emerald-400/12 text-emerald-200' : 'border-ink-600 text-gray-600')}>{option.value}</span>
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-2 text-xs font-semibold text-gray-200">
                          {option.label}
                          {option.badge && <span className={cn('rounded-full border px-1.5 py-0.5 text-[8px]', option.value === 'L4' ? 'border-blue-400/20 bg-blue-400/8 text-blue-200' : 'border-emerald-400/20 bg-emerald-400/8 text-emerald-200')}>{option.badge}</span>}
                        </span>
                        <span className="mt-1 block text-[10px] leading-4 text-gray-600">{option.description}</span>
                      </span>
                      <span className={cn('mt-1 h-3.5 w-3.5 shrink-0 rounded-full border-2', selected ? 'border-emerald-300 bg-emerald-300 shadow-[inset_0_0_0_3px_#151713]' : 'border-ink-600')} />
                    </button>
                  )
                })}
              </div>
              <div className="rounded-xl border border-blue-400/15 bg-blue-400/[0.035] p-3 text-[10px] leading-5 text-blue-100/65">
                <p className="font-semibold text-blue-100/85">24H 总控不会在创建时偷偷启动</p>
                <p className="mt-1">进入准备台确认世界观与卷章骨架后，可配置章数、质量阈值、真实纠错轮次、学习频率、Token / 成本上限以及暂停、恢复和停止策略。</p>
              </div>
            </BlueprintSection>

            {(assumptions.length > 0 || aiReadiness > 0) && (
              <section className="rounded-xl border border-ink-700 bg-ink-950/60 p-3">
                <p className="text-xs font-medium text-gray-300">AI 当前假设</p>
                {assumptions.length > 0 ? (
                  <ul className="mt-2 space-y-1.5 text-[11px] leading-5 text-gray-500">
                    {assumptions.map((assumption) => (
                      <li key={assumption} className="flex gap-2">
                        <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-gold-400" />
                        <span>{assumption}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-[11px] text-gray-600">暂无需要确认的假设。</p>
                )}
              </section>
            )}

          </div>

          <div className="shrink-0 border-t border-ink-700 bg-ink-900/95 p-4 backdrop-blur-xl">
            {(pageError || createMutation.isError) && (
              <div className="mb-3 flex gap-2 rounded-xl border border-red-400/20 bg-red-400/8 p-2.5 text-xs text-red-200" role="alert">
                <AlertCircle size={14} className="mt-0.5 shrink-0" />
                <span>{pageError || createMutation.error?.message}</span>
              </div>
            )}
            {createMutation.isPending && (
              <div className="mb-3 flex items-center gap-2 text-xs text-emerald-200" role="status" aria-live="polite">
                <Loader2 size={14} className="animate-spin" />
                {creationStage}
              </div>
            )}
            <div className="mb-3 grid grid-cols-3 gap-1.5" aria-label="项目创建检查">
              <CreationGate
                label="故事来源"
                value={attachmentMeta ? (outlineInspectionAccepted ? '已验证' : outlineInspecting ? '预检中' : '需处理') : '从零构思'}
                ready={!attachmentMeta || outlineInspectionAccepted}
              />
              <CreationGate
                label="项目提示词"
                value={blueprint.custom_prompt.trim() ? `${blueprint.custom_prompt.length.toLocaleString()} 字符` : '可稍后配置'}
                ready={Boolean(blueprint.custom_prompt.trim())}
              />
              <CreationGate
                label="自动化边界"
                value={`${blueprint.autonomy_level} · ${AUTONOMY_OPTIONS.find((option) => option.value === blueprint.autonomy_level)?.label ?? '已设置'}`}
                ready
              />
            </div>
            <Button
              variant="primary"
              size="lg"
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !blueprint.title.trim() || outlineCreationBlocked}
              className="w-full bg-emerald-300 text-emerald-950 hover:bg-emerald-200"
            >
              {createMutation.isPending ? (
                <Loader2 size={16} className="animate-spin" />
              ) : outlineCreationBlocked ? (
                <AlertCircle size={16} />
              ) : readiness >= 75 ? (
                <CheckCircle2 size={16} />
              ) : (
                <Sparkles size={16} />
              )}
              {!blueprint.title.trim()
                ? '填写标题后创建'
                : attachmentMeta?.needsReselect && !attachment
                  ? '重新选择大纲后创建'
                  : attachment && outlineInspecting
                    ? '等待大纲预检'
                    : attachment && !outlineInspectionAccepted
                      ? '大纲预检未通过'
                      : readiness >= 75
                        ? '创建项目并进入准备台'
                        : '按当前信息创建'}
            </Button>
            <p className="mt-2 text-center text-[10px] leading-4 text-gray-600">
              创建后进入准备台审阅简报、世界观与卷章骨架；24H 自动生产必须在总控台显式启动。
            </p>
          </div>
        </aside>
      </div>
    </div>
  )
}

function BlueprintSection({
  title,
  description,
  children,
  defaultOpen = false,
}: {
  title: string
  description: string
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className="group overflow-hidden rounded-2xl border border-ink-700 bg-ink-950/28"
    >
      <summary className="flex cursor-pointer list-none items-center gap-3 px-3.5 py-3 transition-colors hover:bg-ink-950/65 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-400/35 [&::-webkit-details-marker]:hidden">
        <span className="min-w-0 flex-1">
          <span className="block text-xs font-semibold text-gray-200">{title}</span>
          <span className="mt-0.5 block text-[10px] leading-4 text-gray-600">{description}</span>
        </span>
        <span className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border border-ink-600 text-gray-600 transition-transform', open && 'rotate-90 border-emerald-400/20 text-emerald-300')}>
          <ArrowLeft size={11} className="rotate-180" />
        </span>
      </summary>
      <div className="space-y-3 border-t border-ink-700 px-3.5 py-4">{children}</div>
    </details>
  )
}

function PromptPresetPicker({
  prompt,
  onApply,
  compact = false,
}: {
  prompt: string
  onApply: (text: string) => void
  compact?: boolean
}) {
  const activeCount = PROJECT_PROMPT_PRESETS.filter((preset) =>
    hasProjectPromptPreset(prompt, preset),
  ).length

  return (
    <div className={cn('mt-3 rounded-xl border border-ink-700 bg-ink-950/55', compact ? 'p-2.5' : 'p-3')}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-[10px] font-medium text-gray-400">一键写入可执行契约</p>
        <span className={cn('rounded-full border px-2 py-0.5 text-[8px] font-semibold', activeCount === PROJECT_PROMPT_PRESETS.length ? 'border-emerald-400/20 bg-emerald-400/8 text-emerald-200' : 'border-ink-600 text-gray-600')}>
          {activeCount} / {PROJECT_PROMPT_PRESETS.length} 已写入
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5" aria-label="项目提示词契约模板">
        {PROJECT_PROMPT_PRESETS.map((preset) => {
          const active = hasProjectPromptPreset(prompt, preset)
          return (
            <button
              key={preset.id}
              type="button"
              aria-pressed={active}
              disabled={active}
              onClick={() => onApply(preset.text)}
              title={`${preset.label}：${preset.description}`}
              className={cn(
                'inline-flex min-h-7 items-center gap-1 rounded-lg border px-2 text-[9px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/35',
                active
                  ? 'cursor-default border-emerald-400/20 bg-emerald-400/8 text-emerald-200'
                  : 'border-ink-600 bg-ink-900 text-gray-500 hover:border-emerald-400/25 hover:text-gray-200',
              )}
            >
              {active ? <CheckCircle2 size={10} /> : <Sparkles size={10} />}
              {preset.shortLabel}
            </button>
          )
        })}
      </div>
      {!compact && <p className="mt-2 text-[9px] leading-4 text-gray-700">模板会追加到当前提示词，不会覆盖你的原文；创建后仍可逐字修改。</p>}
    </div>
  )
}

function InspectionMetric({
  label,
  value,
  ok,
}: {
  label: string
  value: string
  ok?: boolean
}) {
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-950/65 px-2.5 py-2">
      <p className={cn('truncate text-xs font-semibold', ok ? 'text-emerald-200' : 'text-gray-200')}>
        {value}
      </p>
      <p className="mt-0.5 truncate text-[9px] text-gray-700">{label}</p>
    </div>
  )
}

function CreationGate({
  label,
  value,
  ready,
}: {
  label: string
  value: string
  ready: boolean
}) {
  return (
    <div className={cn('min-w-0 rounded-lg border px-2 py-1.5', ready ? 'border-emerald-400/15 bg-emerald-400/[0.035]' : 'border-ink-700 bg-ink-950/50')}>
      <p className="truncate text-[8px] uppercase tracking-wide text-gray-700">{label}</p>
      <p className={cn('mt-0.5 truncate text-[9px] font-medium', ready ? 'text-emerald-200/80' : 'text-gray-500')} title={value}>{value}</p>
    </div>
  )
}

/** Render the small Markdown subset models commonly use without introducing
 * a heavyweight renderer. Text is still escaped by React. */
function renderMessageText(content: string): ReactNode {
  return content.split(/(\*\*[^*\n]+\*\*)/g).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={`${index}-${part.slice(2, 12)}`} className="font-semibold text-gray-50">{part.slice(2, -2)}</strong>
    }
    return part
  })
}

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string
  hint?: string
  required?: boolean
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-center gap-1 text-[11px] font-medium text-gray-400">
        {label}
        {required && <span className="text-gold-400">*</span>}
        {hint && <span className="ml-auto font-normal text-gray-600">{hint}</span>}
      </span>
      {children}
    </label>
  )
}
