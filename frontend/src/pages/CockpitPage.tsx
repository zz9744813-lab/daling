import React, { useState, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'react-router-dom'
import {
  PanelLeft,
  PanelRight,
  ChevronRight,
  ScrollText,
  Users,
  GitBranch,
  Sparkles,
  BookOpen,
  ListTree,
  Play,
  Pause,
  RotateCcw,
  Loader2,
  Circle,
  Wifi,
  WifiOff,
  AlertTriangle,
  Settings,
  X,
  CheckCircle2,
  ArrowRight,
  Paperclip,
  ShieldAlert,
  Pencil,
  ThumbsUp,
  Ban,
  Hand,
} from 'lucide-react'
import { TopBar } from '../layout/TopBar'
import { BossCommandBar } from '../layout/BossCommandBar'
import { AppShell, AppShellBody } from '../layout/AppShell'
import { cockpitApi, pipelineApi, brainApi, canonFactsApi, reviewQueueApi, projectsApi, continuousApi } from '../api/client'
import { useProjectStore } from '../store/projectStore'
import { useCockpitStream } from '../hooks/useCockpitStream'
import { cn } from '../lib/cn'
import { Badge } from '../components/Badge'
import { EmptyState } from '../components/EmptyState'
import { AutopilotControlCenter } from '../components/autopilot/AutopilotControlCenter'
import {
  AgentRole,
  AGENT_ROLES,
  CHAPTER_STATUS_MAP,
  getChapterStatusMeta,
  type AgentStatus,
  type Chapter,
  type ChapterQualityDetail,
  type ChapterVersion,
  type Project,
  type ReviewQueueItem,
} from '../types'

/** 全部 8 个 Agent 角色，按工作流顺序排列 */
const ALL_AGENT_ROLES = Object.values(AgentRole)
type ReviewActionKind = 'approve' | 'revise' | 'reject' | 'takeover'
type RevisionMode = 'instruction' | 'content'

/**
 * CockpitPage —— 创作舱（v5.0 设计规范）
 *
 * 布局：TopBar + 左侧 AI 团队 Dock（可收起）+ 中间 Manuscript Desk（稿件主角）
 *       + 右侧 Context Lens（可收起）+ 底部 Boss Command Bar
 * 规范：两侧面板默认可收起，Manuscript Desk 独占屏幕；
 *       正文衬线字体、680px 宽度、行高 2.0；SSE 实时流驱动 Agent 状态与稿件流式输出。
 */
export default function CockpitPage() {
  const location = useLocation()
  const project = useProjectStore((s) => s.currentProject)
  const projectId = project?.id ?? ''
  const queryClient = useQueryClient()

  const [leftOpen, setLeftOpen] = useState(
    () => typeof window !== 'undefined' && window.innerWidth >= 768,
  )
  const [rightOpen, setRightOpen] = useState(false)
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null)
  // 创作指令模态框状态
  const [showPromptModal, setShowPromptModal] = useState(false)
  const [promptEditing, setPromptEditing] = useState('')
  const [preparationDismissed, setPreparationDismissed] = useState(false)
  const [bibleReady, setBibleReady] = useState(false)
  const [outlineReady, setOutlineReady] = useState(false)
  const [reviewAction, setReviewAction] = useState<{
    item: ReviewQueueItem
    kind: ReviewActionKind
  } | null>(null)
  const [revisionMode, setRevisionMode] = useState<RevisionMode>('instruction')
  const [reviewInstruction, setReviewInstruction] = useState('')
  const [reviewContent, setReviewContent] = useState('')
  const [reviewContentSeed, setReviewContentSeed] = useState<string | null>(null)
  const [reviewNotes, setReviewNotes] = useState('')
  const [manualEditing, setManualEditing] = useState(false)
  const [manualText, setManualText] = useState('')
  const [manualBaseVersion, setManualBaseVersion] = useState(0)

  // ===== 创作舱概览数据 =====
  const { data: cockpit, refetch: refetchCockpit } = useQuery({
    queryKey: ['cockpit', projectId],
    queryFn: () => cockpitApi.get(projectId),
    enabled: !!projectId,
    refetchInterval: 15000,
  })

  const { data: continuousStatus } = useQuery({
    queryKey: ['continuous-status', projectId],
    queryFn: () => continuousApi.status(projectId),
    enabled: !!projectId,
    refetchInterval: 5000,
  })
  const productionLocked = continuousStatus?.desired_state === 'running'
  const qualityHoldActive = continuousStatus?.status === 'quality_hold'

  // ===== 检查是否上传了大纲 =====
  const { data: outlineInfo } = useQuery({
    queryKey: ['outline-info', projectId],
    queryFn: () => projectsApi.getOutline(projectId),
    enabled: !!projectId,
  })
  const hasUploadedOutline = (outlineInfo?.char_count ?? 0) > 0

  const uploadOutlineMutation = useMutation({
    mutationFn: (file: File) => {
      if (productionLocked) {
        throw new Error('24H 自动写作正在运行。请先暂停，再更换大纲或结构。')
      }
      if (!/\.(docx|txt|md|markdown)$/i.test(file.name)) {
        throw new Error('仅支持 .docx、.txt、.md 或 .markdown 文件。')
      }
      if (file.size > 5 * 1024 * 1024) {
        throw new Error('大纲文件不能超过 5 MB。')
      }
      return projectsApi.uploadOutline(projectId, file)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['outline-info', projectId] })
      queryClient.invalidateQueries({ queryKey: ['preparation-status', projectId] })
    },
  })

  const { data: preparationStatus } = useQuery({
    queryKey: ['preparation-status', projectId],
    queryFn: () => pipelineApi.preparationStatus(projectId),
    enabled: !!projectId,
  })

  // ===== 章节列表（左侧 Dock 底部 + Context Lens） =====
  const { data: chapters } = useQuery({
    queryKey: ['chapters', projectId],
    queryFn: () => cockpitApi.listChapters(projectId),
    enabled: !!projectId,
  })

  const activeChapter = chapters?.find((chapter) =>
    ['generating', 'in_progress', 'review'].includes(chapter.status),
  )
  const latestWrittenChapter = [...(chapters ?? [])]
    .filter((chapter) => (chapter.word_count ?? 0) > 0)
    .sort((left, right) => right.chapter_number - left.chapter_number)[0]
  const currentChapter =
    chapters?.find((c) => c.id === selectedChapterId) ??
    activeChapter ??
    latestWrittenChapter ??
    cockpit?.current_chapter ??
    chapters?.[0] ??
    null

  // ===== 当前章节正文版本 =====
  const { data: version } = useQuery({
    queryKey: ['chapter-version', projectId, currentChapter?.id],
    queryFn: () => cockpitApi.getChapterVersion(projectId, currentChapter!.id),
    enabled: !!projectId && !!currentChapter?.id,
  })

  const beginManualEdit = () => {
    if (!currentChapter || !version) return
    setManualText(version.content ?? '')
    setManualBaseVersion(version.version_number ?? 0)
    setManualEditing(true)
  }

  const manualSaveMutation = useMutation({
    mutationFn: () => {
      if (!currentChapter) throw new Error('当前没有可编辑章节。')
      if (!manualText.trim()) throw new Error('正文不能为空。')
      return cockpitApi.saveManuscript(projectId, currentChapter.id, manualText, {
        base_version_number: manualBaseVersion,
        submit_for_review: true,
        notes: '人工接管后编辑并提交重新质检',
      })
    },
    onSuccess: async () => {
      setManualEditing(false)
      setRightOpen(true)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['chapter-version', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['chapters', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['review-queue-pending', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['evolution-overview', projectId] }),
      ])
    },
  })

  const cancelManualEdit = () => {
    if (manualText !== (version?.content ?? '') && !window.confirm('人工编辑尚未保存，确定放弃吗？')) return
    setManualEditing(false)
  }

  // ===== SSE 实时流 =====
  const stream = useCockpitStream(projectId, cockpit?.agent_statuses)

  // SSE 事件触发数据刷新
  useEffect(() => {
    if (stream.lastEvent?.event === 'agent_complete' || stream.lastEvent?.event === 'review_needed') {
      refetchCockpit()
      queryClient.invalidateQueries({ queryKey: ['chapters', projectId] })
      queryClient.invalidateQueries({ queryKey: ['chapter-version', projectId] })
    }
  }, [stream.lastEvent, refetchCockpit, queryClient, projectId])

  // ===== 审阅队列（当前章节） =====
  const { data: reviewItems } = useQuery({
    queryKey: ['review-queue-pending', projectId],
    queryFn: () => reviewQueueApi.list(projectId, { status: 'pending' }),
    enabled: !!projectId,
  })

  const reviewChapterId = reviewAction?.item.chapter_id
  const { data: reviewChapter, isFetching: reviewChapterLoading, error: reviewChapterError } = useQuery({
    queryKey: ['review-chapter', projectId, reviewChapterId],
    queryFn: () => cockpitApi.getChapter(projectId, reviewChapterId!),
    enabled: !!projectId && !!reviewChapterId && !!reviewAction,
  })
  const { data: reviewVersion, isFetching: reviewVersionLoading, error: reviewVersionError } = useQuery<ChapterVersion>({
    queryKey: ['review-chapter-version', projectId, reviewChapterId],
    queryFn: () => cockpitApi.getChapterVersion(projectId, reviewChapterId!),
    enabled: !!projectId && !!reviewChapterId && !!reviewAction,
  })

  useEffect(() => {
    if (reviewAction?.kind !== 'revise' || !reviewVersion || !reviewChapterId) return
    if (reviewVersion.chapter_id !== reviewChapterId) return
    const seed = `${reviewAction.item.id}:${reviewVersion.id ?? reviewVersion.version_number}`
    if (reviewContentSeed === seed) return
    setReviewContent(reviewVersion.content ?? '')
    setReviewContentSeed(seed)
  }, [reviewAction, reviewChapterId, reviewContentSeed, reviewVersion])

  const reviewDecisionMutation = useMutation({
    mutationFn: async ({
      item,
      kind,
      mode,
      text,
      notes,
    }: {
      item: ReviewQueueItem
      kind: ReviewActionKind
      mode: RevisionMode
      text: string
      notes: string
    }) => {
      if (kind === 'approve') {
        return reviewQueueApi.approve(projectId, item.id, { decision_notes: notes })
      }
      if (kind === 'reject') {
        return reviewQueueApi.reject(projectId, item.id, { decision_notes: notes })
      }
      if (kind === 'takeover') {
        return reviewQueueApi.takeover(projectId, item.id, { decision_notes: notes })
      }
      if (!item.chapter_id || reviewVersion?.chapter_id !== item.chapter_id || reviewChapter?.id !== item.chapter_id) {
        throw new Error('尚未读取到该审阅项对应章节与版本，不能提交修订。')
      }
      if (!text.trim()) throw new Error(mode === 'content' ? '请输入修订后的完整正文。' : '请输入明确的重写要求。')
      return reviewQueueApi.revise(projectId, item.id, {
        decision_notes: notes,
        revision_instruction: mode === 'instruction' ? text.trim() : undefined,
        revised_content: mode === 'content' ? text : undefined,
      })
    },
    onSuccess: async () => {
      setReviewAction(null)
      setReviewInstruction('')
      setReviewContent('')
      setReviewContentSeed(null)
      setReviewNotes('')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['review-queue-pending', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['chapters', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['chapter-version', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['cockpit', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['continuous-status', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['continuous-events', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['evolution-overview', projectId] }),
      ])
    },
  })

  const openReviewAction = (item: ReviewQueueItem, kind: ReviewActionKind) => {
    setReviewAction({ item, kind })
    setRevisionMode('instruction')
    setReviewInstruction('')
    setReviewContent('')
    setReviewContentSeed(null)
    setReviewNotes('')
  }

  // ===== Pipeline 操作 =====
  const targetChapterCount = Math.max(
    1,
    Number(project?.target_chapters ?? project?.config?.target_chapters ?? 30) || 30,
  )
  const configuredVolumes = Number(project?.config?.volume_count)
  const plannedVolumeCount = Number.isFinite(configuredVolumes) && configuredVolumes > 0
    ? Math.min(20, Math.round(configuredVolumes))
    : Math.max(1, Math.min(8, Math.ceil(targetChapterCount / 40)))
  const plannedChaptersPerVolume = Math.max(
    1,
    Math.min(50, Math.ceil(targetChapterCount / plannedVolumeCount)),
  )

  const bibleMutation = useMutation({
    mutationFn: () => {
      if (productionLocked) throw new Error('请先暂停 24H 自动写作，再重新生成世界观。')
      return pipelineApi.generateBible(projectId, {
        title: project?.title,
        genre: project?.genre,
        themes: project?.config?.themes,
        setting: project?.description ?? project?.synopsis,
        tone: project?.config?.tone,
        target_chapters: project?.target_chapters ?? project?.config?.target_chapters,
      })
    },
    onSuccess: () => {
      setBibleReady(true)
      refetchCockpit()
      queryClient.invalidateQueries({ queryKey: ['preparation-status', projectId] })
      queryClient.invalidateQueries({ queryKey: ['chapters', projectId] })
    },
    onError: (err: Error) => {
      console.error('Bible failed:', err)
      alert(`生成世界观失败: ${err.message}`)
    },
  })

  const outlineMutation = useMutation({
    mutationFn: () => {
      if (productionLocked) throw new Error('请先暂停 24H 自动写作，再重新生成大纲。')
      return pipelineApi.generateOutline(projectId, {
        volume_count: plannedVolumeCount,
        chapters_per_volume: plannedChaptersPerVolume,
      })
    },
    onSuccess: () => {
      setOutlineReady(true)
      refetchCockpit()
      queryClient.invalidateQueries({ queryKey: ['preparation-status', projectId] })
      queryClient.invalidateQueries({ queryKey: ['chapters', projectId] })
    },
    onError: (err: Error) => {
      console.error('Outline failed:', err)
      alert(`生成大纲失败: ${err.message}`)
    },
  })

  const [runStatus, setRunStatus] = useState<string>('')

  const runMutation = useMutation({
    mutationFn: async () => {
      if (productionLocked) {
        throw new Error('24H Worker 正在运行，不能同时启动手动 Pipeline。请先暂停。')
      }
      // 如果没有章节，先自动生成大纲
      if (!chapters || chapters.length === 0) {
        setRunStatus(hasUploadedOutline ? '正在解析大纲...' : '正在生成大纲...')
        await pipelineApi.generateOutline(projectId, {
          volume_count: plannedVolumeCount,
          chapters_per_volume: plannedChaptersPerVolume,
        })
        await queryClient.invalidateQueries({ queryKey: ['chapters', projectId] })
      }
      setRunStatus('正在生成正文，预计需要 3-5 分钟，请耐心等待...')
      return pipelineApi.run(projectId, {
        target_chapters: 1,
        mode: 'auto',
      })
    },
    onMutate: () => {
      if (chapters && chapters.length > 0) {
        setRunStatus('正在生成正文，预计需要 3-5 分钟，请耐心等待...')
      }
    },
    onSuccess: (data: any) => {
      const result = data?.result || {}
      const chapters = result.chapters || []
      const ch = chapters[0] || {}
      if (ch.status === 'approved' || ch.status === 'review') {
        setRunStatus(`✅ 第 ${ch.chapter_no} 章生成完成！状态: ${ch.status}，评分: ${ch.score}`)
      } else if (ch.status === 'failed') {
        setRunStatus(`❌ 第 ${ch.chapter_no} 章生成失败: ${ch.error || '未知错误'}`)
      } else {
        setRunStatus(`第 ${ch.chapter_no} 章处理完成，状态: ${ch.status}`)
      }
      refetchCockpit()
    },
    onError: (err: Error) => {
      console.error('Pipeline run failed:', err)
      setRunStatus(`❌ 写作失败: ${err.message}`)
    },
  })

  const resumeMutation = useMutation({
    mutationFn: () => pipelineApi.resumeSession(projectId),
    onSuccess: () => refetchCockpit(),
    onError: (err: Error) => {
      console.error('Resume failed:', err)
      alert(`恢复失败: ${err.message}`)
    },
  })

  const takeoverMutation = useMutation({
    mutationFn: () => cockpitApi.takeover(projectId),
    onSuccess: async () => {
      await Promise.all([
        refetchCockpit(),
        queryClient.invalidateQueries({ queryKey: ['continuous-status', projectId] }),
      ])
      beginManualEdit()
    },
    onError: (err: Error) => {
      console.error('Takeover failed:', err)
      alert(`接管失败: ${err.message}`)
    },
  })

  // ===== 创作指令（自定义系统提示词，类似 Gemini Gems） =====
  const { data: customPromptData, isLoading: customPromptLoading } = useQuery({
    queryKey: ['custom-prompt', projectId],
    queryFn: () => projectsApi.getCustomPrompt(projectId),
    enabled: !!projectId,
  })

  const savePromptMutation = useMutation({
    mutationFn: (text: string) => projectsApi.updateCustomPrompt(projectId, text),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['custom-prompt', projectId] })
      setShowPromptModal(false)
    },
    onError: (err: Error) => {
      alert(`保存失败: ${err.message}`)
    },
  })

  // 打开创作指令编辑模态框
  const handleOpenPromptModal = () => {
    if (customPromptLoading) return
    setPromptEditing(customPromptData?.text ?? '')
    setShowPromptModal(true)
  }

  const closePromptModal = () => {
    if (savePromptMutation.isPending) return
    const original = customPromptData?.text ?? ''
    if (promptEditing !== original && !window.confirm('项目提示词有未保存修改，确定放弃吗？')) return
    setShowPromptModal(false)
  }

  // ===== 合并 Agent 状态（SSE 实时 + 初始数据） =====
  const mergedAgentStatuses = useMemo(() => {
    const map: Record<string, AgentStatus> = {}
    // 先填入初始数据
    cockpit?.agent_statuses?.forEach((s) => {
      map[s.agent_role] = s
    })
    // SSE 实时覆盖
    Object.values(stream.agentStatuses).forEach((s) => {
      map[s.agent_role] = s
    })
    return ALL_AGENT_ROLES.map((role) =>
      map[role] ?? { agent_role: role, status: 'idle' as const },
    )
  }, [cockpit?.agent_statuses, stream.agentStatuses])

  // 是否有 Agent 正在工作
  const anyWorking = mergedAgentStatuses.some((s) => s.status === 'working')
  const drafterWorking =
    stream.agentStatuses[AgentRole.Drafter]?.status === 'working'

  // 稿件显示内容：流式输出 > 已保存版本
  const displayContent = drafterWorking && stream.streamingContent
    ? stream.streamingContent
    : version?.content ?? ''
  const displayWordCount = manualEditing
    ? manualText.length
    : drafterWorking && stream.streamingContent
      ? stream.streamingContent.length
      : version?.word_count ?? currentChapter?.word_count ?? displayContent.length

  // 标记问题段落（当前章节有审阅项时）
  const chapterReviewItems = useMemo(
    () => reviewItems?.filter((r) => r.chapter_id === currentChapter?.id) ?? [],
    [reviewItems, currentChapter?.id],
  )

  const providerStatus =
    cockpit?.agent_statuses?.some((s) => s.status === 'error') ? 'degraded' : 'online'
    const createdFromNewProject = Boolean(
    (location.state as { created?: boolean } | null)?.created,
  )
  const acceptedChapterCount = (chapters ?? []).filter((chapter) =>
    ['approved', 'published', 'finalized'].includes(chapter.status),
  ).length
  const remainingForAutopilot = continuousStatus?.remaining_chapters ?? Math.max(
    0,
    targetChapterCount - acceptedChapterCount,
  )
  const showPreparation =
    !preparationDismissed &&
    (createdFromNewProject || (Array.isArray(chapters) && chapters.length === 0))
  const persistedBibleReady = bibleReady || Boolean(preparationStatus?.world_bible_ready)
  const persistedOutlineReady =
    outlineReady || Boolean(preparationStatus?.outline_ready) || Boolean(chapters?.length)

  return (
    <AppShell>
      <TopBar
        currentChapter={currentChapter?.chapter_number}
        providerStatus={providerStatus as 'online' | 'offline' | 'degraded'}
      />

      {showPreparation && project ? (
        <>
        <PreparationDesk
          project={project}
          hasUploadedOutline={hasUploadedOutline}
          outlineName={outlineInfo?.filename}
          outlineUploadLoading={uploadOutlineMutation.isPending}
          outlineUploadError={uploadOutlineMutation.error?.message}
          onUploadOutline={(file) => uploadOutlineMutation.mutate(file)}
          hasCustomPrompt={Boolean(customPromptData?.text?.trim())}
          customPromptLength={customPromptData?.text?.length ?? 0}
          onConfigurePrompt={() => {
            handleOpenPromptModal()
          }}
          bibleReady={persistedBibleReady}
          outlineReady={persistedOutlineReady}
          volumeCount={plannedVolumeCount}
          chaptersPerVolume={plannedChaptersPerVolume}
          bibleLoading={bibleMutation.isPending}
          outlineLoading={outlineMutation.isPending}
          bibleError={bibleMutation.error?.message}
          outlineError={outlineMutation.error?.message}
          onGenerateBible={() => bibleMutation.mutate()}
          onGenerateOutline={() => outlineMutation.mutate()}
          onEnterCockpit={() => setPreparationDismissed(true)}
        />
        {showPromptModal && (
          <PreparationPromptEditor
            value={promptEditing}
            loading={savePromptMutation.isPending || customPromptLoading}
            onChange={setPromptEditing}
            onClose={closePromptModal}
            onSave={() => savePromptMutation.mutate(promptEditing)}
          />
        )}
        </>
      ) : (
      <>
      <AppShellBody className="relative">
        {(leftOpen || rightOpen) && (
          <button
            type="button"
            aria-label="关闭侧栏"
            onClick={() => {
              setLeftOpen(false)
              setRightOpen(false)
            }}
            className="absolute inset-0 z-30 bg-black/55 backdrop-blur-[1px] md:hidden"
          />
        )}
        {/* ============ Left Dock —— AI 团队 ============ */}
        <AgentDock
          open={leftOpen}
          onToggle={() => setLeftOpen((v) => !v)}
          agents={mergedAgentStatuses}
          connected={stream.connected}
          chapters={chapters}
          currentChapterId={currentChapter?.id}
          onSelectChapter={(id) => {
            setSelectedChapterId(id)
            if (typeof window !== 'undefined' && window.innerWidth < 768) setLeftOpen(false)
          }}
        />

        {/* ============ Manuscript Desk —— 稿件主角 ============ */}
        <main className="manuscript-desk flex min-w-0 flex-1 flex-col">
          {/* 操作工具栏 */}
          <div className="no-scrollbar flex shrink-0 items-center gap-1.5 overflow-x-auto border-b border-ink-700 bg-ink-900/50 px-4 py-1.5">
            <ToolbarButton
              icon={<PanelLeft size={13} />}
              label={leftOpen ? '收起团队' : 'AI 团队'}
              mobileLabel="团队"
              onClick={() => setLeftOpen((value) => !value)}
            />
            <ToolbarButton
              icon={<BookOpen size={13} />}
              label="生成世界观"
              onClick={() => bibleMutation.mutate()}
              loading={bibleMutation.isPending}
              disabled={productionLocked}
              disabledReason="24H 自动生产正在运行，请先在总控台暂停"
            />
            <ToolbarButton
              icon={<ListTree size={13} />}
              label={hasUploadedOutline ? '解析大纲' : '生成大纲'}
              onClick={() => outlineMutation.mutate()}
              loading={outlineMutation.isPending}
              disabled={productionLocked}
              disabledReason="24H 自动生产正在运行，请先在总控台暂停"
            />
            <input
              type="file"
              accept=".docx,.txt,.md,.markdown"
              className="sr-only"
              id="cockpit-outline-upload"
              onChange={(event) => {
                const file = event.target.files?.[0]
                event.target.value = ''
                if (file) uploadOutlineMutation.mutate(file)
              }}
            />
            <ToolbarButton
              icon={<Paperclip size={13} />}
              label={hasUploadedOutline ? '更换大纲' : '上传大纲'}
              mobileLabel="大纲"
              onClick={() => document.getElementById('cockpit-outline-upload')?.click()}
              loading={uploadOutlineMutation.isPending}
              disabled={productionLocked}
              disabledReason="24H 自动生产正在运行，请先在总控台暂停"
            />
            <ToolbarButton
              icon={<Settings size={13} />}
              label="项目提示词"
              mobileLabel="提示词"
              onClick={handleOpenPromptModal}
            />
            <ToolbarButton
              icon={<Play size={13} />}
              label="开始写作"
              onClick={() => runMutation.mutate()}
              loading={runMutation.isPending}
              disabled={productionLocked}
              disabledReason="24H 自动生产正在运行，请先在总控台暂停"
              variant="primary"
            />
            <ToolbarButton
              icon={<RotateCcw size={13} />}
              label="恢复"
              onClick={() => resumeMutation.mutate()}
              loading={resumeMutation.isPending}
              disabled={productionLocked}
              disabledReason="24H 自动生产已经在运行，无需启动手动恢复"
            />
            <ToolbarButton
              icon={<Pause size={13} />}
              label="接管"
              onClick={() => takeoverMutation.mutate()}
              loading={takeoverMutation.isPending}
            />
            <ToolbarButton
              icon={<Pencil size={13} />}
              label={manualEditing ? '编辑中' : '人工编辑'}
              onClick={beginManualEdit}
              disabled={productionLocked || manualEditing || !version?.id}
              disabledReason={productionLocked ? '24H 自动生产正在运行，请先暂停或点击接管' : undefined}
            />
            <ToolbarButton
              icon={<ShieldAlert size={13} />}
              label={`质检中心${reviewItems?.length ? ` (${reviewItems.length})` : ''}`}
              mobileLabel="质检"
              onClick={() => setRightOpen(true)}
            />

            <div className="ml-auto flex items-center gap-2 text-xs text-gray-500">
              {runStatus && (
                <span className={`flex items-center gap-1 ${runStatus.startsWith('❌') ? 'text-red-400' : runStatus.startsWith('✅') ? 'text-green-400' : 'text-blue-400'}`}>
                  {runMutation.isPending && <Loader2 size={12} className="animate-spin" />}
                  {runStatus}
                </span>
              )}
              {anyWorking && !runStatus && (
                <span className="flex items-center gap-1 text-blue-400">
                  <Loader2 size={12} className="animate-spin" />
                  智能体工作中…
                </span>
              )}
              {stream.connected ? (
                <span className="flex items-center gap-1 text-green-400">
                  <Wifi size={12} />
                  实时连接
                </span>
              ) : (
                <span className="flex items-center gap-1 text-gray-600">
                  <WifiOff size={12} />
                  未连接
                </span>
              )}
            </div>
          </div>

          <AutopilotControlCenter
            projectId={projectId}
            displayedChapter={currentChapter?.chapter_number}
            remainingChapters={remainingForAutopilot}
            projectAutonomyLevel={project?.autonomy_level ?? String(project?.config?.autonomy_level ?? '')}
            onOpenReview={() => setRightOpen(true)}
          />

          {/* 正文区域 */}
          <div className="min-h-0 flex-1 overflow-y-auto">
            {currentChapter ? (
              <article className="mx-auto px-4 py-8 sm:px-8 sm:py-12">
                {/* 章节标题 */}
                <header className="mx-auto mb-10 max-w-manuscript text-center">
                  <p className="mb-2 text-xs uppercase tracking-widest text-gray-600">
                    第 {currentChapter.chapter_number} 章
                  </p>
                  <h1 className="font-serif text-2xl font-semibold text-gray-100">
                    {currentChapter.title}
                  </h1>
                  <div className="mt-3 flex items-center justify-center gap-3 text-xs text-gray-500">
                    <Badge
                      variant={
                        CHAPTER_STATUS_MAP[currentChapter.status]?.color as 'gray' | 'blue' | 'green'
                      }
                    >
                      {getChapterStatusMeta(currentChapter.status).label}
                    </Badge>
                    <span>{displayWordCount.toLocaleString()} 字</span>
                    <span>·</span>
                    <span>目标 {currentChapter.target_words ?? 3000} 字</span>
                    {chapterReviewItems.length > 0 && (
                      <>
                        <span>·</span>
                        <span className="text-amber-400">
                          {chapterReviewItems.length} 项待审阅
                        </span>
                      </>
                    )}
                  </div>
                    {drafterWorking && !manualEditing && (
                    <div className="mt-2 flex items-center justify-center gap-1.5 text-xs text-blue-400">
                      <Loader2 size={11} className="animate-spin" />
                      起草者正在生成正文…
                    </div>
                  )}
                  {chapterReviewItems.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setRightOpen(true)}
                      className={cn(
                        'mx-auto mt-4 flex items-center gap-2 rounded-xl border px-3 py-2 text-xs transition-colors',
                        qualityHoldActive
                          ? 'border-amber-400/20 bg-amber-400/8 text-amber-100 hover:bg-amber-400/12'
                          : 'border-blue-400/20 bg-blue-400/8 text-blue-100 hover:bg-blue-400/12',
                      )}
                    >
                      <ShieldAlert size={13} />
                      {qualityHoldActive
                        ? '本章被质量闸门暂停；打开质检中心处理后才能继续'
                        : '自动质检与重写正在运行；可打开质检中心查看版本证据'}
                    </button>
                  )}
                </header>

                  {/* 正文（纸质书排版） */}
                  <div className={cn('manuscript-text', manualEditing && 'max-w-none')}>
                    {manualEditing ? (
                      <div className="mx-auto max-w-4xl">
                        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-blue-400/20 bg-blue-400/[0.045] px-3 py-2 text-xs text-blue-100/80">
                          <Hand size={13} /> 人工接管模式 · 基于版本 {manualBaseVersion} · 保存时使用乐观锁并自动进入重新质检队列
                        </div>
                        <textarea
                          value={manualText}
                          onChange={(event) => setManualText(event.target.value)}
                          rows={28}
                          autoFocus
                          className="w-full resize-y rounded-2xl border border-ink-600 bg-ink-950/70 px-4 py-4 font-serif text-[15px] leading-8 text-gray-200 focus:border-blue-400/50 focus:outline-none"
                        />
                        {manualSaveMutation.isError && (
                          <p className="mt-2 rounded-lg border border-red-400/20 bg-red-400/8 p-2 text-xs text-red-200">{(manualSaveMutation.error as Error).message}</p>
                        )}
                        <div className="mt-3 flex items-center justify-between gap-3">
                          <span className="text-xs text-gray-600">{manualText.length.toLocaleString()} 字符</span>
                          <div className="flex gap-2">
                            <button type="button" onClick={cancelManualEdit} disabled={manualSaveMutation.isPending} className="h-9 rounded-lg px-4 text-xs text-gray-400 hover:bg-ink-700 disabled:opacity-50">放弃修改</button>
                            <button type="button" onClick={() => manualSaveMutation.mutate()} disabled={manualSaveMutation.isPending || !manualText.trim()} className="inline-flex h-9 items-center gap-2 rounded-lg bg-blue-300 px-4 text-xs font-semibold text-blue-950 disabled:opacity-50">
                              {manualSaveMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <ShieldAlert size={13} />}
                              保存新版本并提交质检
                            </button>
                          </div>
                        </div>
                      </div>
                    ) : displayContent ? (
                      renderManuscript(displayContent)
                  ) : (
                    <p className="text-gray-500">
                      本章尚未开始创作。点击上方「开始写作」或通过下方指令栏让智能体起草。
                    </p>
                  )}
                  {drafterWorking && !manualEditing && (
                    <span className="inline-block h-4 w-0.5 animate-pulse bg-gold-500 align-middle" />
                  )}
                </div>
              </article>
            ) : (
              <EmptyState
                icon={<ScrollText size={28} />}
                title="尚无章节"
                description="点击「生成世界观」和「生成大纲」构建故事骨架，或通过指令栏让故事架构师开始。"
                className="h-full"
              />
            )}
          </div>
        </main>

        {/* ============ Context Lens —— 上下文透镜 ============ */}
        <ContextLens
          open={rightOpen}
          onToggle={() => setRightOpen((v) => !v)}
          projectId={projectId}
          currentChapter={currentChapter}
          reviewItems={reviewItems ?? []}
          reviewBusy={reviewDecisionMutation.isPending}
          onReviewAction={openReviewAction}
        />

        {/* 浮动展开按钮（面板收起时显示） */}
        {!leftOpen && (
          <button
            onClick={() => setLeftOpen(true)}
            className="absolute left-2 top-3 z-20 hidden h-8 w-8 items-center justify-center rounded-md border border-ink-700 bg-ink-850/80 text-gray-400 backdrop-blur hover:text-gray-200 md:flex"
            title="展开 AI 团队"
            aria-label="展开 AI 团队"
          >
            <PanelLeft size={16} />
          </button>
        )}
        {!rightOpen && (
          <button
            onClick={() => setRightOpen(true)}
            className="absolute right-2 top-3 z-20 hidden h-8 w-8 items-center justify-center rounded-md border border-ink-700 bg-ink-850/80 text-gray-400 backdrop-blur hover:text-gray-200 md:flex"
            title="展开上下文透镜"
            aria-label="展开上下文透镜"
          >
            <PanelRight size={16} />
          </button>
        )}

        {reviewAction && (
          <ReviewDecisionModal
            item={reviewAction.item}
            kind={reviewAction.kind}
            revisionMode={revisionMode}
            text={revisionMode === 'content' ? reviewContent : reviewInstruction}
            notes={reviewNotes}
            loading={reviewDecisionMutation.isPending}
            error={reviewDecisionMutation.error as Error | null}
            onClose={() => {
              if (!reviewDecisionMutation.isPending) setReviewAction(null)
            }}
            onRevisionModeChange={setRevisionMode}
            onTextChange={revisionMode === 'content' ? setReviewContent : setReviewInstruction}
            onNotesChange={setReviewNotes}
            sourceChapter={reviewChapter}
            sourceVersion={reviewVersion}
            sourceLoading={reviewChapterLoading || reviewVersionLoading}
            sourceError={(reviewChapterError ?? reviewVersionError) as Error | null}
            onSubmit={() =>
              reviewDecisionMutation.mutate({
                item: reviewAction.item,
                kind: reviewAction.kind,
                mode: revisionMode,
                text: revisionMode === 'content' ? reviewContent : reviewInstruction,
                notes: reviewNotes,
              })
            }
          />
        )}

        {/* 创作指令编辑模态框（类似 Gemini Gems 自定义系统提示词） */}
        {showPromptModal && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
            onClick={closePromptModal}
          >
            <div
              className="w-full max-w-xl rounded-lg border border-ink-700 bg-ink-850 p-6 shadow-xl"
              onClick={(e) => e.stopPropagation()}
            >
              {/* 模态框标题 */}
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h3 className="flex items-center gap-2 text-base font-medium text-gray-100">
                    <Settings size={16} className="text-gold-400" />
                    AI 创作指令
                  </h3>
                  <p className="mt-1 text-xs text-gray-500">
                    类似 Gemini Gems / Custom GPTs，定义 AI 的角色、写作风格、行为准则。这些指令会注入到所有 Agent 的系统提示词中。
                  </p>
                </div>
                <button
                  onClick={closePromptModal}
                  className="text-gray-500 hover:text-gray-300"
                >
                  <X size={18} />
                </button>
              </div>

              {/* 编辑文本框 */}
              <textarea
                value={promptEditing}
                onChange={(e) => setPromptEditing(e.target.value)}
                maxLength={20_000}
                rows={10}
                autoFocus
                placeholder="输入你希望 AI 遵循的创作指令...&#10;例如：你是一位擅长写热血玄幻的作家，语言风格要简洁有力，多用短句，注重动作描写，少用心理独白"
                className="w-full resize-none rounded-md border border-ink-600 bg-ink-900 px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:border-gold-500/60 focus:outline-none focus:ring-1 focus:ring-gold-500/30"
              />

              {/* 底部操作按钮 */}
              <div className="mt-4 flex items-center justify-between">
                <span className={cn('text-xs', promptEditing.length >= 20_000 ? 'text-red-300' : 'text-gray-600')}>
                  {promptEditing.length.toLocaleString()} / 20,000 字符
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={closePromptModal}
                    className="rounded-md px-3 py-1.5 text-xs text-gray-400 transition-colors hover:bg-ink-700 hover:text-gray-200"
                  >
                    取消
                  </button>
                  <button
                    onClick={() => savePromptMutation.mutate(promptEditing)}
                    disabled={savePromptMutation.isPending || customPromptLoading || promptEditing.length > 20_000}
                    className="flex items-center gap-1.5 rounded-md bg-gold-500 px-4 py-1.5 text-xs font-medium text-ink-950 transition-colors hover:bg-gold-400 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {savePromptMutation.isPending && (
                      <Loader2 size={12} className="animate-spin" />
                    )}
                    保存
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </AppShellBody>

      <BossCommandBar />
      </>
      )}
    </AppShell>
  )
}

/* ============================================================
 * New-project preparation desk
 * ============================================================ */
function PreparationDesk({
  project,
  hasUploadedOutline,
  outlineName,
  outlineUploadLoading,
  outlineUploadError,
  onUploadOutline,
  hasCustomPrompt,
  customPromptLength,
  onConfigurePrompt,
  bibleReady,
  outlineReady,
  volumeCount,
  chaptersPerVolume,
  bibleLoading,
  outlineLoading,
  bibleError,
  outlineError,
  onGenerateBible,
  onGenerateOutline,
  onEnterCockpit,
}: {
  project: Project
  hasUploadedOutline: boolean
  outlineName?: string
  outlineUploadLoading: boolean
  outlineUploadError?: string
  onUploadOutline: (file: File) => void
  hasCustomPrompt: boolean
  customPromptLength: number
  onConfigurePrompt: () => void
  bibleReady: boolean
  outlineReady: boolean
  volumeCount: number
  chaptersPerVolume: number
  bibleLoading: boolean
  outlineLoading: boolean
  bibleError?: string
  outlineError?: string
  onGenerateBible: () => void
  onGenerateOutline: () => void
  onEnterCockpit: () => void
}) {
  const config = project.config ?? {}
  const configText = (key: string) => {
    const value = config[key]
    if (Array.isArray(value)) return value.filter(Boolean).join('、')
    return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
  }
  const summary =
    project.description ||
    project.synopsis ||
    configText('logline') ||
    configText('premise') ||
    '创作简报已经保存，接下来把它转化为可审阅的世界观与卷章骨架。'
  const target = (project.target_chapters ?? Number(config.target_chapters)) || 0

  return (
    <main className="subtle-grid min-h-0 flex-1 overflow-y-auto bg-ink-950 px-4 py-8 sm:px-8 sm:py-12">
      <div className="mx-auto max-w-5xl">
        <div className="mb-8 flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
          <div className="max-w-3xl">
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-emerald-400/20 bg-emerald-400/8 px-3 py-1.5 text-xs text-emerald-200">
              <CheckCircle2 size={13} />
              项目与创作简报已保存
            </div>
            <h1 className="font-serif text-3xl font-semibold tracking-tight text-gray-50 sm:text-4xl">
              先把故事准备好，再开始写
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-gray-400">
              这里是新项目的准备台。你可以先审阅 AI 对世界与结构的理解，再生成第一章；系统不会在设定尚未确认时直接连续写作。
            </p>
          </div>
          <button
            type="button"
            onClick={onEnterCockpit}
            className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-xl border border-ink-600 bg-ink-850 px-4 text-sm font-medium text-gray-300 transition-colors hover:border-ink-500 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300/50"
          >
            进入高级创作舱 <ArrowRight size={15} />
          </button>
        </div>

        <section className="mb-6 overflow-hidden rounded-2xl border border-ink-700 bg-ink-900/85 shadow-[0_24px_80px_rgba(0,0,0,0.18)]">
          <div className="border-b border-ink-700 px-5 py-4 sm:px-6">
            <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-emerald-300/70">
              Creative brief
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <h2 className="font-serif text-2xl font-semibold text-gray-100">{project.title}</h2>
              {project.genre && (
                <span className="rounded-full border border-gold-400/20 bg-gold-400/8 px-2.5 py-1 text-xs text-gold-300">
                  {project.genre}
                </span>
              )}
              {hasUploadedOutline && (
                <span className="rounded-full border border-blue-400/20 bg-blue-400/8 px-2.5 py-1 text-xs text-blue-300">
                  已附大纲
                </span>
              )}
            </div>
          </div>
          <div className="grid gap-px bg-ink-700 sm:grid-cols-[1.4fr_0.6fr]">
            <div className="bg-ink-900 px-5 py-5 sm:px-6">
              <p className="text-xs text-gray-600">故事承诺</p>
              <p className="mt-2 text-sm leading-7 text-gray-300">{summary}</p>
              {(configText('themes') || configText('tone')) && (
                <div className="mt-4 flex flex-wrap gap-2 text-xs text-gray-400">
                  {configText('themes') && <span>主题：{configText('themes')}</span>}
                  {configText('tone') && <span>语气：{configText('tone')}</span>}
                </div>
              )}
            </div>
            <dl className="grid grid-cols-2 gap-4 bg-ink-900 px-5 py-5 text-sm sm:grid-cols-1 sm:px-6">
              <div>
                <dt className="text-xs text-gray-600">计划规模</dt>
                <dd className="mt-1 text-gray-200">{target ? `${target} 章` : '待确定'}</dd>
              </div>
              <div>
                <dt className="text-xs text-gray-600">协作模式</dt>
                <dd className="mt-1 text-gray-200">{configText('autonomy_level') || 'L2 · 平衡协作'}</dd>
              </div>
            </dl>
          </div>
        </section>

        <section className="mb-6 grid gap-4 md:grid-cols-2" aria-label="项目资料与提示词">
          <article className="rounded-2xl border border-ink-700 bg-ink-900/75 p-5">
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-400/10 text-blue-300">
                <BookOpen size={17} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-sm font-semibold text-gray-100">已有故事大纲</h2>
                  <span
                    className={cn(
                      'rounded-full border px-2 py-0.5 text-[10px]',
                      hasUploadedOutline
                        ? 'border-blue-400/20 bg-blue-400/8 text-blue-300'
                        : 'border-ink-600 bg-ink-800 text-gray-500',
                    )}
                  >
                    {hasUploadedOutline ? '已上传' : '可选'}
                  </span>
                </div>
                <p className="mt-1 text-xs leading-5 text-gray-500">
                  {hasUploadedOutline
                    ? `当前文件：${outlineName || '已保存的大纲'}。生成卷章骨架时会优先解析这份内容。`
                    : '如果你已经有 DOCX、TXT 或 Markdown 大纲，可在这里上传，系统会保存原文件并据此生成卷章骨架。'}
                </p>
              </div>
            </div>
            {outlineUploadError && (
              <p className="mt-3 rounded-lg border border-red-400/15 bg-red-400/5 px-3 py-2 text-xs text-red-300">
                {outlineUploadError}
              </p>
            )}
            <label
              className={cn(
                'mt-4 inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded-lg border border-blue-400/20 bg-blue-400/8 px-3 text-xs font-medium text-blue-200 transition-colors hover:bg-blue-400/12',
                outlineUploadLoading && 'pointer-events-none opacity-60',
              )}
            >
              {outlineUploadLoading ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <BookOpen size={13} />
              )}
              {outlineUploadLoading
                ? '正在上传并解析…'
                : hasUploadedOutline
                  ? '更换大纲文件'
                  : '上传大纲文件'}
              <input
                type="file"
                accept=".docx,.txt,.md,.markdown"
                className="sr-only"
                disabled={outlineUploadLoading}
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  event.target.value = ''
                  if (file) onUploadOutline(file)
                }}
              />
            </label>
          </article>

          <article className="rounded-2xl border border-emerald-400/20 bg-emerald-400/[0.035] p-5">
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-400/10 text-emerald-300">
                <Settings size={17} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-sm font-semibold text-gray-100">项目提示词</h2>
                  <span
                    className={cn(
                      'rounded-full border px-2 py-0.5 text-[10px]',
                      hasCustomPrompt
                        ? 'border-emerald-400/20 bg-emerald-400/8 text-emerald-300'
                        : 'border-amber-400/20 bg-amber-400/8 text-amber-300',
                    )}
                  >
                    {hasCustomPrompt ? '已配置' : '建议配置'}
                  </span>
                </div>
                <p className="mt-1 text-xs leading-5 text-gray-500">
                  定义所有 Agent 必须遵守的写作角色、文风、人物一致性、大纲约束和内容边界。
                  {hasCustomPrompt ? ` 当前共 ${customPromptLength} 字符。` : ''}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={onConfigurePrompt}
              className="mt-4 inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-emerald-400/20 bg-emerald-400/10 px-3 text-xs font-medium text-emerald-200 transition-colors hover:bg-emerald-400/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40"
            >
              <Settings size={13} />
              {hasCustomPrompt ? '查看并修改提示词' : '配置项目提示词'}
            </button>
          </article>
        </section>

        <div className="grid gap-4 lg:grid-cols-3">
          <PreparationStep
            index="01"
            title="审阅创作简报"
            description="标题、故事发动机、人物欲望、世界规则、篇幅与内容边界已经持久化。"
            done
            actionLabel="简报已保存"
            onAction={() => window.history.back()}
            disabled
          />
          <PreparationStep
            index="02"
            title="生成世界观圣经"
            description="把创作简报转成可检查、可锁定的法则、势力、资源、社会制度与人物约束。"
            done={bibleReady}
            loading={bibleLoading}
            error={bibleError}
            actionLabel={bibleReady ? '重新生成' : '生成世界观'}
            onAction={onGenerateBible}
          />
          <PreparationStep
            index="03"
            title="生成卷章骨架"
            description={`按当前篇幅规划约 ${volumeCount} 卷，每卷 ${chaptersPerVolume} 章；生成后仍可审阅调整。`}
            done={outlineReady}
            loading={outlineLoading}
            error={outlineError}
            actionLabel={
              outlineReady
                ? '查看创作舱'
                : !bibleReady
                  ? '先完成世界观'
                  : hasUploadedOutline
                    ? '解析并生成大纲'
                    : '生成卷章大纲'
            }
            onAction={outlineReady ? onEnterCockpit : onGenerateOutline}
            disabled={!outlineReady && !bibleReady}
          />
        </div>
      </div>
    </main>
  )
}

function PreparationStep({
  index,
  title,
  description,
  done = false,
  loading = false,
  error,
  actionLabel,
  onAction,
  disabled = false,
}: {
  index: string
  title: string
  description: string
  done?: boolean
  loading?: boolean
  error?: string
  actionLabel: string
  onAction: () => void
  disabled?: boolean
}) {
  return (
    <section className="flex min-h-64 flex-col rounded-2xl border border-ink-700 bg-ink-900/75 p-5">
      <div className="flex items-center justify-between">
        <span className="font-mono text-xs tracking-wider text-gray-600">{index}</span>
        {done ? (
          <CheckCircle2 size={18} className="text-emerald-300" aria-label="已完成" />
        ) : (
          <Circle size={18} className="text-gray-700" aria-label="待完成" />
        )}
      </div>
      <h3 className="mt-6 text-base font-semibold text-gray-100">{title}</h3>
      <p className="mt-2 flex-1 text-xs leading-6 text-gray-500">{description}</p>
      {error && (
        <p className="mb-3 rounded-lg border border-red-400/15 bg-red-400/5 px-2.5 py-2 text-[11px] leading-5 text-red-200" role="alert">
          {error}
        </p>
      )}
      <button
        type="button"
        onClick={onAction}
        disabled={loading || disabled}
        className={cn(
          'mt-4 inline-flex h-10 items-center justify-center gap-2 rounded-xl border px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300/50 disabled:cursor-wait disabled:opacity-60',
          done
            ? 'border-ink-600 bg-ink-850 text-gray-300 hover:text-white'
            : 'border-emerald-300/30 bg-emerald-300 text-emerald-950 hover:bg-emerald-200',
        )}
      >
        {loading && <Loader2 size={14} className="animate-spin" />}
        {actionLabel}
      </button>
    </section>
  )
}

/* ============================================================
 * Toolbar Button
 * ============================================================ */
function PreparationPromptEditor({
  value,
  loading,
  onChange,
  onClose,
  onSave,
}: {
  value: string
  loading: boolean
  onChange: (value: string) => void
  onClose: () => void
  onSave: () => void
}) {
  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 p-4" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section role="dialog" aria-modal="true" className="w-full max-w-2xl rounded-2xl border border-ink-700 bg-ink-900 p-5 shadow-2xl sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="flex items-center gap-2 text-base font-semibold text-gray-100"><Settings size={16} className="text-gold-400" /> 项目级创作提示词</h2>
            <p className="mt-1 text-xs leading-5 text-gray-500">该提示词会与经过 holdout 的自主进化规则组合，并注入每个写作 Agent。</p>
          </div>
          <button type="button" onClick={onClose} disabled={loading} className="rounded-lg p-2 text-gray-500 hover:bg-ink-700 hover:text-gray-200"><X size={16} /></button>
        </div>
        <textarea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          maxLength={20_000}
          rows={14}
          autoFocus
          className="mt-5 w-full resize-y rounded-xl border border-ink-600 bg-ink-950 px-4 py-3 text-sm leading-7 text-gray-200 focus:border-gold-400/50 focus:outline-none"
          placeholder="定义文风、角色边界、设定规则、禁用写法和质量标准……"
        />
        <div className="mt-3 flex items-center justify-between gap-3">
          <span className={cn('text-xs', value.length >= 20_000 ? 'text-red-300' : 'text-gray-600')}>{value.length.toLocaleString()} / 20,000</span>
          <div className="flex gap-2">
            <button type="button" onClick={onClose} disabled={loading} className="h-9 rounded-lg px-4 text-xs text-gray-400 hover:bg-ink-700">取消</button>
            <button type="button" onClick={onSave} disabled={loading} className="inline-flex h-9 items-center gap-2 rounded-lg bg-gold-500 px-4 text-xs font-semibold text-ink-950 disabled:opacity-50">{loading && <Loader2 size={12} className="animate-spin" />}保存提示词</button>
          </div>
        </div>
      </section>
    </div>
  )
}

function ToolbarButton({
  icon,
  label,
  mobileLabel,
  onClick,
  loading,
  disabled = false,
  disabledReason,
  variant = 'ghost',
}: {
  icon: React.ReactNode
  label: string
  mobileLabel?: string
  onClick: () => void
  loading?: boolean
  disabled?: boolean
  disabledReason?: string
  variant?: 'ghost' | 'primary' | 'danger'
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading || disabled}
      aria-label={label}
      title={disabled ? disabledReason : label}
      className={cn(
        'flex h-7 shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2.5 text-xs font-medium transition-colors',
        variant === 'primary'
          ? 'bg-gold-500 text-ink-950 hover:bg-gold-400'
          : variant === 'danger'
            ? 'bg-red-600/20 text-red-400 hover:bg-red-600/30'
            : 'text-gray-300 hover:bg-ink-700',
        'disabled:cursor-not-allowed disabled:opacity-40',
      )}
    >
      {loading ? <Loader2 size={12} className="animate-spin" /> : icon}
      {mobileLabel && <span className="sm:hidden">{mobileLabel}</span>}
      <span className="hidden sm:inline">{label}</span>
    </button>
  )
}

/* ============================================================
 * Agent Dock —— 左侧 AI 团队状态面板
 * ============================================================ */
function AgentDock({
  open,
  onToggle,
  agents,
  connected,
  chapters,
  currentChapterId,
  onSelectChapter,
}: {
  open: boolean
  onToggle: () => void
  agents: AgentStatus[]
  connected: boolean
  chapters?: Chapter[]
  currentChapterId?: string
  onSelectChapter?: (chapterId: string) => void
}) {
  return (
    <aside
      className={cn(
        'transition-panel absolute inset-y-0 left-0 z-40 h-full shrink-0 overflow-hidden border-r border-ink-700 bg-ink-900 shadow-2xl md:relative md:z-auto md:shadow-none',
        open ? 'w-[min(85vw,20rem)] md:w-60' : 'w-0 border-r-0',
      )}
    >
      <div className="flex h-full w-[min(85vw,20rem)] flex-col md:w-60">
        {/* 头部 */}
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-ink-700 px-3">
          <span className="flex items-center gap-1.5 text-xs font-medium text-gray-300">
            <Users size={14} />
            AI 团队
          </span>
          <div className="flex items-center gap-2">
            <Circle
              size={7}
              className={cn('fill-current', connected ? 'text-green-400' : 'text-gray-600')}
            />
            <button
              type="button"
              onClick={onToggle}
              className="text-gray-500 hover:text-gray-300"
              aria-label="收起 AI 团队"
            >
              <PanelLeft size={15} />
            </button>
          </div>
        </div>

        {/* Agent 列表 */}
        <div className="flex-1 overflow-y-auto py-2">
          {agents.map((agent) => (
            <AgentRow key={agent.agent_role} agent={agent} />
          ))}
        </div>

        {/* 章节快速导航 */}
        <div className="shrink-0 border-t border-ink-700">
          <div className="px-3 py-1.5 text-xs font-medium text-gray-500">章节导航</div>
          <div className="max-h-32 overflow-y-auto pb-2">
            {chapters && chapters.length > 0 ? (
              chapters.map((c) => (
                <button
                  key={c.id}
                  onClick={() => onSelectChapter?.(c.id)}
                  className={cn(
                    'flex w-full items-center gap-2 px-3 py-1 text-left text-sm transition-colors',
                    c.id === currentChapterId
                      ? 'bg-ink-800 text-gold-400'
                      : 'text-gray-400 hover:bg-ink-800 hover:text-gray-200',
                  )}
                >
                  <ChevronRight size={13} className="text-gray-600" />
                  <span className="text-xs text-gray-600">{c.chapter_number}</span>
                  <span className="truncate">{c.title}</span>
                </button>
              ))
            ) : (
              <p className="px-3 py-2 text-xs text-gray-600">暂无章节</p>
            )}
          </div>
        </div>

        {/* 工具入口 */}
        <div className="shrink-0 border-t border-ink-700 p-2">
          <DockToolItem icon={<GitBranch size={14} />} label="生命线" to="/storyline" />
          <DockToolItem icon={<Sparkles size={14} />} label="大脑" to="/brain" />
        </div>
      </div>
    </aside>
  )
}

function AgentRow({ agent }: { agent: AgentStatus }) {
  const statusConfig = {
    idle: { color: 'text-gray-500', bg: 'bg-gray-600/20', label: '空闲' },
    working: { color: 'text-blue-400', bg: 'bg-blue-600/20', label: '工作中' },
    error: { color: 'text-red-400', bg: 'bg-red-600/20', label: '异常' },
  }
  const cfg = statusConfig[agent.status]

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 hover:bg-ink-800/50">
      <span
        className={cn(
          'flex h-6 w-6 shrink-0 items-center justify-center rounded text-[10px] font-medium',
          cfg.bg,
          cfg.color,
        )}
      >
        {agent.status === 'working' ? (
          <Loader2 size={11} className="animate-spin" />
        ) : agent.status === 'error' ? (
          <AlertTriangle size={11} />
        ) : (
          <Circle size={6} className="fill-current" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between">
          <span className="truncate text-xs font-medium text-gray-300">
            {AGENT_ROLES[agent.agent_role]}
          </span>
          <span className={cn('text-[10px]', cfg.color)}>{cfg.label}</span>
        </div>
        {agent.message && (
          <p className="truncate text-[10px] text-gray-600">{agent.message}</p>
        )}
      </div>
    </div>
  )
}

function DockToolItem({ icon, label, to }: { icon: React.ReactNode; label: string; to: string }) {
  return (
    <a
      href={to}
      className="flex items-center gap-2 rounded px-2 py-1.5 text-xs text-gray-400 hover:bg-ink-800 hover:text-gray-200"
    >
      {icon}
      {label}
    </a>
  )
}

/* ============================================================
 * Context Lens —— 右侧上下文透镜
 * ============================================================ */
function ContextLens({
  open,
  onToggle,
  projectId,
  currentChapter,
  reviewItems,
  reviewBusy,
  onReviewAction,
}: {
  open: boolean
  onToggle: () => void
  projectId: string
  currentChapter: Chapter | null
  reviewItems: ReviewQueueItem[]
  reviewBusy: boolean
  onReviewAction: (item: ReviewQueueItem, kind: ReviewActionKind) => void
}) {
  const [qualityChapterNo, setQualityChapterNo] = useState<number | null>(null)
  const [qualityOpen, setQualityOpen] = useState(false)
  const { data: brain } = useQuery({
    queryKey: ['brain-overview', projectId],
    queryFn: () => brainApi.get(projectId),
    enabled: !!projectId && open,
  })

  const { data: canonFacts } = useQuery({
    queryKey: ['canon-facts-lens', projectId],
    queryFn: () => canonFactsApi.list(projectId),
    enabled: !!projectId && open,
  })

  const characters = brain?.characters ?? []
  const summaries = brain?.summaries ?? []
  const currentState = brain?.current_state
  const resolvedQualityChapterNo =
    qualityChapterNo ?? currentChapter?.chapter_number ?? reviewItems[0]?.chapter_no ?? null
  const qualityQuery = useQuery({
    queryKey: ['chapter-quality', projectId, resolvedQualityChapterNo],
    queryFn: () => pipelineApi.chapterQuality(projectId, resolvedQualityChapterNo!),
    enabled: Boolean(projectId && open && resolvedQualityChapterNo),
  })

  const openQualityEvidence = (chapterNo?: number | null) => {
    if (chapterNo) setQualityChapterNo(chapterNo)
    setQualityOpen(true)
  }

  return (
    <>
    <aside
      className={cn(
        'transition-panel absolute inset-y-0 right-0 z-40 h-full shrink-0 overflow-hidden border-l border-ink-700 bg-ink-900 shadow-2xl md:relative md:z-auto md:shadow-none',
        open ? 'w-[min(92vw,24rem)] md:w-72' : 'w-0 border-l-0',
      )}
    >
      <div className="flex h-full w-[min(92vw,24rem)] flex-col md:w-72">
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-ink-700 px-3">
          <span className="flex items-center gap-1.5 text-xs font-medium text-gray-300">
            <ShieldAlert size={14} />
            质检与上下文
          </span>
          <button onClick={onToggle} className="text-gray-500 hover:text-gray-300">
            <PanelRight size={15} />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
          {/* 当前状态 */}
          {currentState && (
            <LensSection title="当前状态">
              <div className="space-y-1 text-xs text-gray-500">
                {currentState.location && (
                  <div>
                    <span className="text-gray-600">地点:</span> {currentState.location}
                  </div>
                )}
                {currentState.time_of_day && (
                  <div>
                    <span className="text-gray-600">时间:</span> {currentState.time_of_day}
                  </div>
                )}
                {currentState.mood && (
                  <div>
                    <span className="text-gray-600">氛围:</span> {currentState.mood}
                  </div>
                )}
                {currentState.last_events && currentState.last_events.length > 0 && (
                  <div>
                    <span className="text-gray-600">最近事件:</span>
                    <ul className="mt-0.5 list-inside list-disc">
                      {currentState.last_events.slice(0, 3).map((e, i) => (
                        <li key={i}>{e}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </LensSection>
          )}

          {/* 在场角色 */}
          <LensSection title="角色">
            {characters.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {characters.slice(0, 8).map((c) => (
                  <Badge key={c.id} variant={c.role === '主角' ? 'gold' : 'outline'}>
                    {c.name}
                  </Badge>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-600">暂无角色数据</p>
            )}
          </LensSection>

          {/* 前文摘要 */}
          <LensSection title="前文摘要">
            {summaries.length > 0 ? (
              <p className="text-xs leading-relaxed text-gray-500">
                {summaries[summaries.length - 1]?.summary ?? '暂无摘要'}
              </p>
            ) : (
              <p className="text-xs text-gray-600">暂无前文摘要</p>
            )}
          </LensSection>

          {/* 设定事实 */}
          <LensSection title="设定事实">
            {canonFacts && canonFacts.length > 0 ? (
              <div className="space-y-1.5">
                {canonFacts.slice(0, 5).map((f) => (
                  <div key={f.id} className="text-xs">
                    <span className="text-gray-300">{f.subject_name || f.subject_id}</span>
                    <span className="text-gray-600"> → </span>
                    <span className="text-gray-400">{f.object_value}</span>
                  </div>
                ))}
                {canonFacts.length > 5 && (
                  <p className="text-[10px] text-gray-600">还有 {canonFacts.length - 5} 条…</p>
                )}
              </div>
            ) : (
              <p className="text-xs text-gray-600">暂无设定事实</p>
            )}
          </LensSection>

          <LensSection title="质量证据">
            <QualitySnapshot
              chapterNo={resolvedQualityChapterNo}
              detail={qualityQuery.data}
              loading={qualityQuery.isLoading || qualityQuery.isFetching}
              error={qualityQuery.error as Error | null}
              onOpen={() => openQualityEvidence(resolvedQualityChapterNo)}
            />
          </LensSection>

          {/* 待审阅项 */}
          {reviewItems.length > 0 && (
            <LensSection title={`待审阅 · ${reviewItems.length}`}>
              <div className="space-y-2">
                {[...reviewItems]
                  .sort((a, b) => Number(b.chapter_id === currentChapter?.id) - Number(a.chapter_id === currentChapter?.id))
                  .map((r) => (
                  <div
                    key={r.id}
                    className={cn(
                      'rounded-lg border p-2.5 text-xs',
                      r.chapter_id === currentChapter?.id
                        ? 'border-amber-400/25 bg-amber-400/[0.055]'
                        : 'border-ink-700 bg-ink-900/70',
                    )}
                  >
                    <div className="flex items-center gap-1.5">
                      <Badge
                        variant={
                          r.severity === 'critical' ? 'red' : r.severity === 'warning' ? 'amber' : 'gray'
                        }
                      >
                        {r.type}
                      </Badge>
                      <span className="text-gray-400">{r.title}</span>
                    </div>
                    {r.chapter_no && <p className="mt-1 text-[9px] text-gray-600">第 {r.chapter_no} 章</p>}
                    {r.description && (
                      <p className="mt-1 text-[10px] leading-4 text-gray-600">{r.description}</p>
                    )}
                    {r.chapter_no && (
                      <button
                        type="button"
                        onClick={() => openQualityEvidence(r.chapter_no)}
                        className="mt-2 inline-flex items-center gap-1 text-[10px] font-medium text-blue-300 hover:text-blue-200"
                      >
                        <ShieldAlert size={10} /> 查看本章完整质量证据
                      </button>
                    )}
                    <div className="mt-2 grid grid-cols-2 gap-1.5 border-t border-ink-700/70 pt-2">
                      <ReviewMiniButton icon={<ThumbsUp size={10} />} label="批准" disabled={reviewBusy} onClick={() => onReviewAction(r, 'approve')} tone="green" />
                      <ReviewMiniButton icon={<Pencil size={10} />} label="修改重审" disabled={reviewBusy} onClick={() => onReviewAction(r, 'revise')} tone="amber" />
                      <ReviewMiniButton icon={<Ban size={10} />} label="驳回" disabled={reviewBusy} onClick={() => onReviewAction(r, 'reject')} tone="red" />
                      <ReviewMiniButton icon={<Hand size={10} />} label="人工接管" disabled={reviewBusy} onClick={() => onReviewAction(r, 'takeover')} tone="gray" />
                    </div>
                  </div>
                ))}
              </div>
            </LensSection>
          )}
        </div>
      </div>
    </aside>
    {qualityOpen && resolvedQualityChapterNo && (
      <QualityEvidenceModal
        chapterNo={resolvedQualityChapterNo}
        detail={qualityQuery.data}
        loading={qualityQuery.isLoading || qualityQuery.isFetching}
        error={qualityQuery.error as Error | null}
        onRetry={() => qualityQuery.refetch()}
        onClose={() => setQualityOpen(false)}
      />
    )}
    </>
  )
}

function QualitySnapshot({
  chapterNo,
  detail,
  loading,
  error,
  onOpen,
}: {
  chapterNo: number | null
  detail?: ChapterQualityDetail
  loading: boolean
  error: Error | null
  onOpen: () => void
}) {
  if (!chapterNo) return <p className="text-xs text-gray-600">选择章节后查看质量账本</p>
  if (loading && !detail) {
    return <p className="flex items-center gap-2 text-xs text-gray-500"><Loader2 size={12} className="animate-spin" /> 正在读取第 {chapterNo} 章质量账本…</p>
  }
  if (error && !detail) {
    return <p className="text-xs leading-5 text-red-300">质量证据读取失败：{error.message}</p>
  }
  if (!detail) return <p className="text-xs text-gray-600">本章尚无质量证据</p>

  return (
    <div>
      <div className="grid grid-cols-3 gap-1.5 text-center">
        <div className="rounded-lg bg-ink-950/70 px-1 py-2">
          <p className={cn('text-sm font-semibold', detail.summary.quality_passed ? 'text-emerald-300' : 'text-amber-300')}>
            {detail.summary.latest_score == null ? '—' : Math.round(detail.summary.latest_score)}
          </p>
          <p className="mt-0.5 text-[9px] text-gray-600">最新评分</p>
        </div>
        <div className="rounded-lg bg-ink-950/70 px-1 py-2">
          <p className="text-sm font-semibold text-amber-300">{detail.summary.open_issue_count}</p>
          <p className="mt-0.5 text-[9px] text-gray-600">未解决</p>
        </div>
        <div className="rounded-lg bg-ink-950/70 px-1 py-2">
          <p className="text-sm font-semibold text-blue-300">{detail.summary.revision_attempt_count}</p>
          <p className="mt-0.5 text-[9px] text-gray-600">返工轮次</p>
        </div>
      </div>
      <button
        type="button"
        onClick={onOpen}
        className="mt-2 flex h-8 w-full items-center justify-center gap-1.5 rounded-lg border border-blue-400/15 text-[10px] font-medium text-blue-200 hover:bg-blue-400/8"
      >
        <ShieldAlert size={11} /> 查看评估、问题、版本与修订链
      </button>
    </div>
  )
}

function QualityEvidenceModal({
  chapterNo,
  detail,
  loading,
  error,
  onRetry,
  onClose,
}: {
  chapterNo: number
  detail?: ChapterQualityDetail
  loading: boolean
  error: Error | null
  onRetry: () => void
  onClose: () => void
}) {
  const issueTone = (severity: string) =>
    ['critical', 'high'].includes(severity) ? 'red' : severity === 'medium' ? 'amber' : 'gray'

  return (
    <div
      className="fixed inset-0 z-[85] flex items-end justify-center bg-black/75 backdrop-blur-sm sm:items-center sm:p-4"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}
    >
      <section role="dialog" aria-modal="true" aria-labelledby="quality-evidence-title" className="flex h-[94dvh] w-full max-w-5xl flex-col overflow-hidden rounded-t-3xl border border-ink-700 bg-ink-900 shadow-2xl sm:h-[min(90vh,900px)] sm:rounded-3xl">
        <header className="flex items-start gap-3 border-b border-ink-700 px-4 py-4 sm:px-6">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-blue-400/20 bg-blue-400/8 text-blue-200"><ShieldAlert size={17} /></span>
          <div className="min-w-0 flex-1">
            <h2 id="quality-evidence-title" className="text-base font-semibold text-gray-100">
              第 {chapterNo} 章 · 质量证据账本
            </h2>
            <p className="mt-1 text-[11px] leading-5 text-gray-500">只展示后端已持久化的评估、精确位置、版本引用与修订尝试；没有定位证据时不会推测段落。</p>
          </div>
          <button type="button" onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-lg text-gray-500 hover:bg-ink-700 hover:text-gray-200" aria-label="关闭质量证据"><X size={16} /></button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
          {loading && !detail ? (
            <div className="flex min-h-72 items-center justify-center gap-2 text-sm text-gray-500"><Loader2 size={17} className="animate-spin" /> 正在读取质量账本…</div>
          ) : error && !detail ? (
            <div className="flex min-h-72 flex-col items-center justify-center text-center">
              <AlertTriangle size={25} className="text-red-300" />
              <p className="mt-3 text-sm text-red-100">{error.message}</p>
              <button type="button" onClick={onRetry} className="mt-4 h-9 rounded-xl border border-ink-600 px-4 text-xs text-gray-300 hover:bg-ink-800">重新读取</button>
            </div>
          ) : detail ? (
            <div className="space-y-5">
              <section className="rounded-2xl border border-ink-700 bg-ink-950/45 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-gray-100">{detail.chapter.title}</p>
                    <p className="mt-1 text-[10px] text-gray-600">{detail.chapter.word_count.toLocaleString()} 字 · 状态 {detail.chapter.status}</p>
                  </div>
                  <Badge variant={detail.summary.quality_passed ? 'green' : detail.summary.quality_passed === false ? 'amber' : 'gray'}>
                    {detail.summary.latest_score == null ? '尚无终审分' : `${Math.round(detail.summary.latest_score)} 分 · ${detail.summary.latest_verdict ?? '未判定'}`}
                  </Badge>
                </div>
                <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <EvidenceMetric label="评估记录" value={detail.summary.assessment_count} />
                  <EvidenceMetric label="全部问题" value={detail.summary.issue_count} />
                  <EvidenceMetric label="未解决问题" value={detail.summary.open_issue_count} danger={detail.summary.open_issue_count > 0} />
                  <EvidenceMetric label="修订尝试" value={detail.summary.revision_attempt_count} />
                </div>
              </section>

              <section>
                <h3 className="mb-2 text-xs font-semibold text-gray-300">不可变版本引用</h3>
                <div className="flex flex-wrap gap-2">
                  {detail.version_refs.length ? detail.version_refs.map((version) => (
                    <span key={version.id} className={cn('rounded-lg border px-2.5 py-1.5 text-[10px]', version.is_current ? 'border-emerald-400/25 bg-emerald-400/8 text-emerald-200' : 'border-ink-700 bg-ink-850 text-gray-500')}>
                      V{version.version_no} · {version.word_count.toLocaleString()} 字 · {version.created_by_agent || '未知来源'}{version.is_current ? ' · 当前' : ''}
                    </span>
                  )) : <p className="text-xs text-gray-600">尚无版本快照</p>}
                </div>
              </section>

              <section>
                <h3 className="mb-2 text-xs font-semibold text-gray-300">结构化评估</h3>
                <div className="grid gap-2 lg:grid-cols-2">
                  {detail.assessments.length ? detail.assessments.map((assessment) => (
                    <article key={assessment.id} className="rounded-xl border border-ink-700 bg-ink-850/75 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={assessment.passed ? 'green' : 'amber'}>{assessment.assessor}</Badge>
                        <span className="text-[10px] text-gray-600">第 {assessment.round_no} 轮 · {assessment.assessment_type}</span>
                        <span className="ml-auto text-sm font-semibold text-gray-200">{assessment.overall_score == null ? '—' : Math.round(assessment.overall_score)}</span>
                      </div>
                      {Object.keys(assessment.dimension_scores ?? {}).length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {Object.entries(assessment.dimension_scores).map(([name, score]) => (
                            <span key={name} className="rounded bg-ink-950 px-1.5 py-1 text-[9px] text-gray-500">{name}: {String(score)}</span>
                          ))}
                        </div>
                      )}
                      <p className="mt-2 text-[10px] text-gray-600">{assessment.model_name || '规则闸门'} · {assessment.verdict}</p>
                    </article>
                  )) : <p className="text-xs text-gray-600">尚无评估记录</p>}
                </div>
              </section>

              <section>
                <h3 className="mb-2 text-xs font-semibold text-gray-300">可追踪问题</h3>
                <div className="space-y-2">
                  {detail.issues.length ? detail.issues.map((issue) => (
                    <article key={issue.id} className="rounded-xl border border-ink-700 bg-ink-850/75 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={issueTone(issue.severity)}>{issue.severity}</Badge>
                        <span className="text-xs font-medium text-gray-300">{issue.category}</span>
                        <span className={cn('ml-auto text-[10px]', issue.status === 'open' ? 'text-amber-300' : 'text-emerald-300')}>{issue.status}</span>
                      </div>
                      <p className="mt-2 text-xs leading-5 text-gray-400">{issue.description}</p>
                      {(issue.block_no != null || issue.location || issue.quoted_text) && (
                        <div className="mt-2 rounded-lg border border-blue-400/10 bg-blue-400/[0.035] p-2 text-[10px] leading-5 text-blue-100/70">
                          {issue.block_no != null && <p>正文块：{issue.block_no}</p>}
                          {issue.location && <p>位置：{issue.location}</p>}
                          {issue.quoted_text && <p className="mt-1 border-l border-blue-300/30 pl-2 font-serif">“{issue.quoted_text}”</p>}
                        </div>
                      )}
                      {issue.suggestion && <p className="mt-2 text-[10px] leading-5 text-emerald-200/70">建议：{issue.suggestion}</p>}
                    </article>
                  )) : <p className="text-xs text-gray-600">没有持久化质量问题</p>}
                </div>
              </section>

              <section>
                <h3 className="mb-2 text-xs font-semibold text-gray-300">修订链</h3>
                <div className="space-y-2">
                  {detail.revision_attempts.length ? detail.revision_attempts.map((revision) => (
                    <article key={revision.id} className="rounded-xl border border-ink-700 bg-ink-850/75 p-3">
                      <div className="flex flex-wrap items-center gap-2 text-[10px]">
                        <Badge variant={revision.status === 'completed' ? 'green' : revision.error ? 'red' : 'blue'}>第 {revision.round_no} 轮 · {revision.status}</Badge>
                        <span className="text-gray-500">{revision.score_before ?? '—'} → {revision.score_after ?? '—'} 分</span>
                        <span className="text-gray-600">{revision.instruction_source}</span>
                      </div>
                      {revision.instruction && <p className="mt-2 text-xs leading-5 text-gray-400">{revision.instruction}</p>}
                      {revision.diff_summary && <p className="mt-2 text-[10px] leading-5 text-blue-200/70">变更摘要：{revision.diff_summary}</p>}
                      {revision.error && <p className="mt-2 text-[10px] text-red-300">{revision.error}</p>}
                    </article>
                  )) : <p className="text-xs text-gray-600">尚无修订尝试</p>}
                </div>
              </section>
            </div>
          ) : null}
        </div>
      </section>
    </div>
  )
}

function EvidenceMetric({ label, value, danger = false }: { label: string; value: number; danger?: boolean }) {
  return (
    <div className="rounded-xl border border-ink-700 bg-ink-900/70 px-3 py-2">
      <p className={cn('text-lg font-semibold', danger ? 'text-amber-300' : 'text-gray-100')}>{value}</p>
      <p className="mt-0.5 text-[9px] text-gray-600">{label}</p>
    </div>
  )
}

function ReviewMiniButton({
  icon,
  label,
  disabled,
  onClick,
  tone,
}: {
  icon: React.ReactNode
  label: string
  disabled: boolean
  onClick: () => void
  tone: 'green' | 'amber' | 'red' | 'gray'
}) {
  const tones = {
    green: 'border-emerald-400/15 text-emerald-200 hover:bg-emerald-400/8',
    amber: 'border-amber-400/15 text-amber-200 hover:bg-amber-400/8',
    red: 'border-red-400/15 text-red-200 hover:bg-red-400/8',
    gray: 'border-ink-600 text-gray-400 hover:bg-ink-700',
  }
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'flex min-h-7 items-center justify-center gap-1 rounded-md border px-1.5 text-[9px] font-medium transition-colors disabled:opacity-40',
        tones[tone],
      )}
    >
      {icon} {label}
    </button>
  )
}

function ReviewDecisionModal({
  item,
  kind,
  revisionMode,
  text,
  notes,
  loading,
  error,
  onClose,
  onRevisionModeChange,
  onTextChange,
  onNotesChange,
  sourceChapter,
  sourceVersion,
  sourceLoading,
  sourceError,
  onSubmit,
}: {
  item: ReviewQueueItem
  kind: ReviewActionKind
  revisionMode: RevisionMode
  text: string
  notes: string
  loading: boolean
  error: Error | null
  onClose: () => void
  onRevisionModeChange: (mode: RevisionMode) => void
  onTextChange: (value: string) => void
  onNotesChange: (value: string) => void
  sourceChapter?: Chapter
  sourceVersion?: ChapterVersion
  sourceLoading: boolean
  sourceError: Error | null
  onSubmit: () => void
}) {
  const chapterLabel = item.chapter_no ? `第 ${item.chapter_no} 章` : '待审章节'
  const meta = {
    approve: {
      title: `批准${chapterLabel}`,
      description: '把当前版本设为正式版本，并用真实模型更新章节摘要、Canon 与长期学习记忆。',
      label: '批准并更新记忆',
      icon: <ThumbsUp size={17} />,
      tone: 'text-emerald-200 border-emerald-400/20 bg-emerald-400/8',
    },
    revise: {
      title: '修改并重新质检',
      description: '生成独立修订版本，然后重新执行 Critic、ContinuityGuard 与 ChiefEditor；未达标仍会留在审阅队列。',
      label: revisionMode === 'content' ? '保存正文并重新质检' : '调用 Rewriter 并重新质检',
      icon: <Pencil size={17} />,
      tone: 'text-amber-200 border-amber-400/20 bg-amber-400/8',
    },
    reject: {
      title: '驳回并阻断章节',
      description: '章节将标记为 blocked，24 小时任务会保持暂停；驳回理由会进入反馈学习账本。',
      label: '确认驳回',
      icon: <Ban size={17} />,
      tone: 'text-red-200 border-red-400/20 bg-red-400/8',
    },
    takeover: {
      title: '人工接管',
      description: '真正暂停持久化 Worker 和当前 WorkSession，把本章交给人工编辑。',
      label: '暂停并接管',
      icon: <Hand size={17} />,
      tone: 'text-blue-200 border-blue-400/20 bg-blue-400/8',
    },
  }[kind]
  const sourceReady = Boolean(
    item.chapter_id &&
      sourceChapter?.id === item.chapter_id &&
      sourceVersion?.chapter_id === item.chapter_id,
  )
  const revisionInvalid = kind === 'revise' && (!text.trim() || !sourceReady)

  return (
    <div className="fixed inset-0 z-[80] flex items-end justify-center bg-black/75 backdrop-blur-sm sm:items-center sm:p-4" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section role="dialog" aria-modal="true" aria-labelledby="review-decision-title" className="flex max-h-[94dvh] w-full max-w-3xl flex-col overflow-hidden rounded-t-3xl border border-ink-700 bg-ink-900 shadow-[0_30px_100px_rgba(0,0,0,0.65)] sm:rounded-3xl">
        <header className="flex items-start gap-3 border-b border-ink-700 px-4 py-4 sm:px-6">
          <span className={cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border', meta.tone)}>{meta.icon}</span>
          <div className="min-w-0 flex-1">
            <h2 id="review-decision-title" className="text-base font-semibold text-gray-100">{meta.title}</h2>
            <p className="mt-1 text-[11px] leading-5 text-gray-500">{meta.description}</p>
          </div>
          <button type="button" onClick={onClose} disabled={loading} className="flex h-8 w-8 items-center justify-center rounded-lg text-gray-500 hover:bg-ink-700 hover:text-gray-200 disabled:opacity-40" aria-label="关闭">
            <X size={16} />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 sm:p-6">
          <div className="rounded-xl border border-ink-700 bg-ink-950/55 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={item.severity === 'critical' ? 'red' : 'amber'}>{item.type}</Badge>
              {item.chapter_no && <span className="text-[10px] text-gray-600">第 {item.chapter_no} 章</span>}
              <span className="text-xs font-medium text-gray-300">{item.title}</span>
            </div>
            {item.description && <p className="mt-2 text-[11px] leading-5 text-gray-500">{item.description}</p>}
          </div>

          {kind === 'revise' && (
            <>
                <div className="rounded-xl border border-blue-400/15 bg-blue-400/[0.035] px-3 py-2 text-[11px] text-blue-100/80">
                  {sourceLoading ? (
                    <span className="inline-flex items-center gap-2"><Loader2 size={12} className="animate-spin" /> 正在读取待审章节原稿，完成前不能提交</span>
                  ) : sourceError ? (
                    <span className="inline-flex items-center gap-2 text-red-200"><AlertTriangle size={12} /> 原稿读取失败：{sourceError.message}</span>
                  ) : !sourceReady ? (
                    <span className="inline-flex items-center gap-2 text-amber-200"><AlertTriangle size={12} /> 审阅项未关联到可验证的章节版本，已禁止提交</span>
                  ) : sourceChapter && sourceVersion ? (
                    <span>
                      编辑目标：第 {sourceChapter.chapter_number} 章「{sourceChapter.title}」
                      {' · '}版本 V{sourceVersion.version_number}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-2 text-amber-200"><AlertTriangle size={12} /> 审阅原稿尚未就绪，已禁止提交</span>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2 rounded-xl bg-ink-950/60 p-1">
                  <button type="button" onClick={() => onRevisionModeChange('instruction')} className={cn('rounded-lg px-3 py-2 text-xs font-medium', revisionMode === 'instruction' ? 'bg-amber-400/12 text-amber-100' : 'text-gray-500 hover:text-gray-300')}>AI 按要求重写</button>
                  <button type="button" onClick={() => onRevisionModeChange('content')} className={cn('rounded-lg px-3 py-2 text-xs font-medium', revisionMode === 'content' ? 'bg-amber-400/12 text-amber-100' : 'text-gray-500 hover:text-gray-300')}>直接编辑完整正文</button>
                </div>
              <div>
                <label className="text-xs font-medium text-gray-300">{revisionMode === 'instruction' ? '重写要求' : '修订后的完整正文'}</label>
                <textarea
                  value={text}
                  onChange={(event) => onTextChange(event.target.value)}
                  disabled={sourceLoading || !sourceReady}
                  rows={revisionMode === 'content' ? 16 : 7}
                  autoFocus
                  placeholder={revisionMode === 'instruction' ? '例如：保留现有事件顺序，补足主角做出决定的动机；修复第三段时间线冲突；结尾钩子更克制。' : '在这里直接编辑当前章节完整正文…'}
                  className={cn('mt-2 w-full resize-y rounded-xl border border-ink-600 bg-ink-950 px-3 py-3 text-sm leading-7 text-gray-200 placeholder:text-gray-700 focus:border-amber-400/50 focus:outline-none', revisionMode === 'content' && 'font-serif')}
                />
                <p className="mt-1 text-[10px] text-gray-600">{text.length.toLocaleString()} 字符 · 原稿与新稿会分别保留为不可变版本</p>
              </div>
            </>
          )}

          <div>
            <label className="text-xs font-medium text-gray-300">决策备注 <span className="text-gray-600">（可选，将进入审计与学习证据）</span></label>
            <textarea value={notes} onChange={(event) => onNotesChange(event.target.value)} rows={3} placeholder="说明批准、驳回或接管的原因…" className="mt-2 w-full resize-none rounded-xl border border-ink-600 bg-ink-950 px-3 py-2 text-sm leading-6 text-gray-200 placeholder:text-gray-700 focus:border-emerald-400/50 focus:outline-none" />
          </div>

          {error && <div className="flex items-start gap-2 rounded-xl border border-red-400/20 bg-red-400/8 p-3 text-xs text-red-100"><AlertTriangle size={13} className="mt-0.5 shrink-0" /> {error.message}</div>}
        </div>

        <footer className="flex flex-col-reverse gap-2 border-t border-ink-700 bg-ink-900/95 px-4 py-3 sm:flex-row sm:items-center sm:justify-end sm:px-6">
          <button type="button" disabled={loading} onClick={onClose} className="h-9 rounded-xl px-4 text-xs text-gray-400 hover:bg-ink-700 hover:text-gray-100 disabled:opacity-40">取消</button>
          <button type="button" disabled={loading || revisionInvalid || (kind === 'revise' && sourceLoading)} onClick={onSubmit} className={cn('flex h-9 items-center justify-center gap-2 rounded-xl border px-4 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-40', meta.tone)}>
            {loading && <Loader2 size={13} className="animate-spin" />}
            {meta.label}
          </button>
        </footer>
      </section>
    </div>
  )
}

function LensSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="mb-2 text-xs font-medium text-gray-400">{title}</h4>
      <div className="rounded-md border border-ink-700 bg-ink-850 p-2.5">{children}</div>
    </div>
  )
}

/* ============================================================
 * 稿件渲染 —— 按段落渲染，问题段落用琥珀色竖线标记
 * ============================================================ */
function renderManuscript(content: string) {
  const paragraphs = content.split(/\n+/).filter(Boolean)
  // 只有后端提供精确 block/range 时才做内联标注；当前审阅项在右侧
  // 质检中心展示，绝不再把问题“均匀分配”到无关段落制造假定位。
  return paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)
}
