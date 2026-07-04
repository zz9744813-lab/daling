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
  providerStatus?: 'online' | 'offline' | 'degraded'
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
        : 'text-red-400'

  const autonomy = project?.config?.autonomy_level
    ? AUTONOMY_LEVELS[project.config.autonomy_level]
    : '未设置'

  const target = project?.target_chapters ?? project?.config?.target_chapters ?? 0

  return (
    <header
      className={cn(
        'flex h-12 shrink-0 items-center gap-3 border-b border-ink-700 bg-ink-900 px-4',
        className,
      )}
    >
      <button
        onClick={() => navigate('/')}
        className="flex items-center gap-1 text-sm text-gray-400 transition-colors hover:text-gray-200"
        title="返回项目选择"
      >
        <ChevronLeft size={16} />
        <span className="hidden sm:inline">项目</span>
      </button>

      <div className="h-4 w-px bg-ink-700" />

      {/* 作品名 */}
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-medium text-gray-200">
          {project?.title || '未选择项目'}
        </span>
      </div>

      {/* 导航 */}
      <nav className="ml-4 flex items-center gap-0.5">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon
          const active = location.pathname.startsWith(item.path)
          return (
            <Link
              key={item.path}
              to={item.path}
              className={cn(
                'flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors',
                active
                  ? 'bg-ink-700 text-gold-400'
                  : 'text-gray-400 hover:bg-ink-800 hover:text-gray-200',
              )}
            >
              <Icon size={13} />
              <span className="hidden lg:inline">{item.label}</span>
            </Link>
          )
        })}
      </nav>

      <div className="ml-auto flex items-center gap-4 text-xs text-gray-400">
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
        <span className="flex items-center gap-1.5" title={`Provider ${providerStatus}`}>
          <Circle size={8} className={cn('fill-current', statusColor)} />
          <span className="hidden lg:inline">
            {providerStatus === 'online'
              ? 'Provider 在线'
              : providerStatus === 'degraded'
                ? 'Provider 降级'
                : 'Provider 离线'}
          </span>
        </span>
      </div>
    </header>
  )
}
