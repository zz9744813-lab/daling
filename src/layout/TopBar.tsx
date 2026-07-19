import React from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { ChevronLeft, Circle, Compass, GitBranch, Brain, TrendingUp, Shield } from 'lucide-react'
import { useProjectStore } from '../store/projectStore'
import { AUTONOMY_LEVELS } from '../types'
import { cn } from '../lib/cn'

interface TopBarProps {
  /** 当前章节序号 */
  currentChapter?: number
  /** Provider 在线状态 */
  providerStatus?: 'online' | 'offline' | 'degraded' | 'unknown'
  className?: string
}

const NAV_ITEMS = [
  { path: '/cockpit', label: '创作舱', icon: Compass },
  { path: '/storyline', label: '生命线', icon: GitBranch },
  { path: '/brain', label: '大脑', icon: Brain },
  { path: '/evolution', label: '进化', icon: TrendingUp },
  { path: '/governance', label: '治理', icon: Shield },
]

/**
 * 顶部栏（v5.0 规范）
 * 显示：返回入口、作品名、导航、当前章 X/目标、自动等级、Provider 状态灯
 */
export function TopBar({ currentChapter, providerStatus = 'offline', className }: TopBarProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const project = useProjectStore((s) => s.currentProject)

  const statusColor =
    providerStatus === 'online'
      ? 'text-green-400'
      : providerStatus === 'degraded'
        ? 'text-amber-400'
        : providerStatus === 'offline'
          ? 'text-red-400'
          : 'text-gray-500'

  const autonomyLevel = project?.autonomy_level ?? project?.config?.autonomy_level
  const autonomy = autonomyLevel
    ? AUTONOMY_LEVELS[autonomyLevel]
    : '未设置'

  const target = project?.target_chapters ?? project?.config?.target_chapters ?? 0
  const providerLabel = providerStatus === 'online'
    ? 'Provider 在线'
    : providerStatus === 'degraded'
      ? 'Provider 降级'
      : providerStatus === 'offline'
        ? 'Provider 离线'
        : 'Provider 状态未知'
  const compactProviderLabel = providerStatus === 'online'
    ? '在线'
    : providerStatus === 'degraded'
      ? '降级'
      : providerStatus === 'offline'
        ? '离线'
        : '未知'

  return (
    <header
      className={cn(
        'flex h-12 shrink-0 items-center gap-1 border-b border-ink-700 bg-ink-900 px-1.5 sm:gap-3 sm:px-4',
        className,
      )}
    >
      <button
        type="button"
        onClick={() => navigate('/')}
        className="flex shrink-0 items-center gap-0.5 rounded-md px-1 py-1 text-[10px] font-medium text-gray-300 transition-colors hover:text-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40 sm:gap-1 sm:text-sm"
        title="返回项目选择"
        aria-label="返回项目选择"
      >
        <ChevronLeft size={16} />
        <span className="sm:hidden">返回</span>
        <span className="hidden sm:inline">项目</span>
      </button>

      <div className="hidden h-4 w-px bg-ink-700 sm:block" />

      {/* 作品名 */}
      <div className="hidden min-w-0 items-center gap-2 sm:flex">
        <span className="truncate text-sm font-medium text-gray-200">
          {project?.title || '未选择项目'}
        </span>
      </div>

      {/* 导航 */}
      <nav className="no-scrollbar ml-0 flex min-w-0 items-center gap-0.5 overflow-x-auto sm:ml-4" aria-label="项目工作区">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon
          const active = location.pathname.startsWith(item.path)
          return (
            <Link
              key={item.path}
              to={item.path}
              aria-label={item.label}
              aria-current={active ? 'page' : undefined}
              title={item.label}
              className={cn(
                'flex h-8 shrink-0 items-center gap-1 rounded-md px-1.5 text-[10px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40 sm:px-2 sm:text-xs',
                active
                  ? 'bg-ink-700 text-gold-400'
                  : 'text-gray-400 hover:bg-ink-800 hover:text-gray-200',
              )}
            >
              <Icon size={13} />
              <span className="sm:hidden lg:inline">{item.label}</span>
            </Link>
          )
        })}
      </nav>

      <div className="ml-auto flex shrink-0 items-center gap-1.5 text-xs text-gray-300 sm:gap-4">
        {/* 当前章 / 目标 */}
        <span className="hidden items-center gap-1 sm:flex">
          <span className="text-gray-500">当前章</span>
          <span className="font-medium text-gray-300">
            {currentChapter ?? project?.current_chapter ?? 0}
          </span>
          <span className="text-gray-600">/</span>
          <span>{target || '—'}</span>
        </span>

        <div className="hidden h-3 w-px bg-ink-700 sm:block" />

        {/* 自动等级 */}
        <span className="hidden items-center gap-1 md:flex">
          <span className="text-gray-500">自主</span>
          <span className="font-medium text-gold-400">{autonomy}</span>
        </span>

        <div className="hidden h-3 w-px bg-ink-700 md:block" />

        {/* Provider 状态灯 */}
        <span className="flex items-center gap-1.5" title={providerLabel} aria-label={providerLabel}>
          <Circle size={8} className={cn('fill-current', statusColor)} />
          <span className="text-[10px] sm:hidden">{compactProviderLabel}</span>
          <span className="hidden lg:inline">{providerLabel}</span>
        </span>
      </div>
    </header>
  )
}
