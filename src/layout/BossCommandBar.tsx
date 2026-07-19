import React, { useState } from 'react'
import { Send, Sparkles, Loader2, CheckCircle2, AlertCircle, X } from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { cockpitApi, continuousApi } from '../api/client'
import { useProjectStore } from '../store/projectStore'
import { cn } from '../lib/cn'
import type { CommandResult } from '../types'
import {
  executeContinuousCommand,
  QUICK_CONTINUOUS_COMMANDS,
  resolveContinuousCommandIntent,
  type ContinuousCommandIntent,
} from './bossContinuousCommand'

interface BossCommandBarProps {
  /** 自定义普通 Boss 指令回调；24H 指令始终使用连续生产合约 API。 */
  onSubmit?: (command: string) => void
  disabled?: boolean
  className?: string
}

/**
 * Boss Command Bar —— 底部自然语言指令输入栏（v5.0）
 * 普通指令调用 cockpitApi.postCommand()；24H 指令直接调用 ContinuousProduction
 * 合约 API，且必须确认持久化 run 状态后才显示成功。
 */
export function BossCommandBar({ onSubmit, disabled, className }: BossCommandBarProps) {
  const queryClient = useQueryClient()
  const [value, setValue] = useState('')
  const [result, setResult] = useState<CommandResult | null>(null)
  const [showResult, setShowResult] = useState(false)
  const project = useProjectStore((s) => s.currentProject)
  const projectId = project?.id ?? ''
  const commandDisabled = Boolean(disabled || (!onSubmit && !projectId))
  const continuousCommandDisabled = Boolean(disabled || !projectId)
  const intentLabels: Record<string, string> = {
    start: '启动',
    pause: '暂停',
    resume: '继续',
    stop: '停止',
    rewrite: '创建返工审阅',
    modify: '创建修改审阅',
    skip: '创建跳过审阅',
    status: '查询状态',
  }

  const commandMutation = useMutation({
    mutationFn: ({
      command,
      continuousIntent,
    }: {
      command: string
      continuousIntent: ContinuousCommandIntent | null
    }) => {
      if (continuousIntent) {
        if (!project) throw new Error('请先选择项目，再操作 24H 持久化任务。')
        return executeContinuousCommand(continuousApi, project, continuousIntent)
      }
      return cockpitApi.postCommand(projectId, command)
    },
    onSuccess: (data) => {
      setResult(data)
      setShowResult(true)
      if (data.data?.transport === 'continuous_production') {
        const continuousStatus = data.data.continuous_status
        if (continuousStatus) {
          queryClient.setQueryData(['continuous-status', projectId], continuousStatus)
        }
        void Promise.all([
          queryClient.invalidateQueries({ queryKey: ['continuous-status', projectId] }),
          queryClient.invalidateQueries({ queryKey: ['continuous-events', projectId] }),
          queryClient.invalidateQueries({ queryKey: ['cockpit', projectId] }),
          queryClient.invalidateQueries({ queryKey: ['chapters', projectId] }),
        ])
      }
    },
    onError: (err: Error) => {
      setResult({ ok: false, message: err.message })
      setShowResult(true)
    },
  })

  const dispatchCommand = (
    cmd: string,
    explicitContinuousIntent?: ContinuousCommandIntent,
  ) => {
    const normalized = cmd.trim()
    if (!normalized || disabled || commandMutation.isPending) return
    const continuousIntent =
      explicitContinuousIntent ?? resolveContinuousCommandIntent(normalized)
    setShowResult(false)
    if (continuousIntent) {
      if (!projectId) {
        setResult({ ok: false, intent: continuousIntent, message: '请先选择项目，再操作 24H 持久化任务。' })
        setShowResult(true)
        return
      }
      commandMutation.mutate({ command: normalized, continuousIntent })
    } else if (onSubmit) {
      onSubmit(normalized)
    } else if (projectId) {
      commandMutation.mutate({ command: normalized, continuousIntent: null })
    }
    setValue('')
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    dispatchCommand(value)
  }

  return (
    <div className={cn('shrink-0', className)}>
      {/* 内联结果提示 */}
      {showResult && result && (
        <div
          className={cn(
            'flex items-start gap-2 border-t px-4 py-2 text-xs',
            result.ok
              ? 'border-green-600/30 bg-green-600/10 text-green-300'
              : 'border-red-600/30 bg-red-600/10 text-red-300',
          )}
          role={result.ok ? 'status' : 'alert'}
          aria-live={result.ok ? 'polite' : 'assertive'}
        >
          {result.ok ? <CheckCircle2 size={13} className="mt-0.5 shrink-0" /> : <AlertCircle size={13} className="mt-0.5 shrink-0" />}
          <span className="min-w-0 flex-1 break-words leading-5">
            {result.message || (result.ok ? '指令已执行' : '执行失败')}
            {result.intent && (
              <span className="ml-1 text-gray-500">
                · 动作: {intentLabels[result.intent] ?? result.intent}
              </span>
            )}
          </span>
          <button
            type="button"
            onClick={() => setShowResult(false)}
            className="shrink-0 rounded text-gray-500 hover:text-gray-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40"
            aria-label="关闭指令结果"
          >
            <X size={12} />
          </button>
        </div>
      )}

      <div className="border-t border-ink-700 bg-ink-900 px-3 py-2 sm:px-4">
        <div className="mb-2 flex min-w-0 items-center gap-2" aria-label="24H 持久化快捷控制">
          <span className="inline-flex h-6 shrink-0 items-center rounded-md border border-emerald-400/20 bg-emerald-400/8 px-2 text-[10px] font-bold tracking-wide text-emerald-200">
            <span className="sm:hidden">24H</span>
            <span className="hidden sm:inline">24H 持久化控制</span>
          </span>
          <div className="no-scrollbar flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto">
            {QUICK_CONTINUOUS_COMMANDS.map(({ label, intent }) => (
              <button
                key={label}
                type="button"
                aria-label={label}
                disabled={continuousCommandDisabled || commandMutation.isPending}
                onClick={() => dispatchCommand(label, intent)}
                className="inline-flex h-7 shrink-0 items-center justify-center rounded-md border border-ink-600 px-2.5 text-[11px] font-medium text-gray-300 transition-colors hover:border-gold-500/40 hover:text-gold-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40 disabled:cursor-not-allowed disabled:opacity-35"
              >
                <span className="sm:hidden">{intentLabels[intent]}</span>
                <span className="hidden sm:inline">{label}</span>
              </button>
            ))}
          </div>
        </div>
        <form onSubmit={handleSubmit} className="flex items-center gap-2">
          <Sparkles size={16} className="shrink-0 text-gold-500" />
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            id="boss-command-input"
            placeholder="输入可执行指令（支持返工、改写与 24H 控制）"
            disabled={commandDisabled}
            aria-label="输入给写作系统的可执行指令"
            className="h-7 flex-1 bg-transparent text-sm text-gray-200 placeholder:text-gray-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={commandDisabled || !value.trim() || commandMutation.isPending}
            className={cn(
              'flex h-7 items-center gap-1 rounded-md px-2.5 text-xs font-medium transition-colors',
              'bg-gold-500 text-ink-950 hover:bg-gold-400',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gold-300/60',
              'disabled:cursor-not-allowed disabled:opacity-40',
            )}
          >
            {commandMutation.isPending ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Send size={12} />
            )}
            执行
          </button>
        </form>
      </div>
    </div>
  )
}
