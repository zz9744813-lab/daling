import React, { useState } from 'react'
import { Send, Sparkles, Loader2, CheckCircle2, AlertCircle, X } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { cockpitApi } from '../api/client'
import { useProjectStore } from '../store/projectStore'
import { cn } from '../lib/cn'
import type { CommandResult } from '../types'

interface BossCommandBarProps {
  /** 自定义提交回调（若提供则不调用默认 API） */
  onSubmit?: (command: string) => void
  disabled?: boolean
  className?: string
}

/**
 * Boss Command Bar —— 底部自然语言指令输入栏（v5.0）
 * 向 Agent 发号施令，调用 cockpitApi.postCommand()，内联显示执行结果。
 */
export function BossCommandBar({ onSubmit, disabled, className }: BossCommandBarProps) {
  const [value, setValue] = useState('')
  const [result, setResult] = useState<CommandResult | null>(null)
  const [showResult, setShowResult] = useState(false)
  const project = useProjectStore((s) => s.currentProject)
  const projectId = project?.id ?? ''
  const commandDisabled = Boolean(disabled || (!onSubmit && !projectId))
  const quickCommands = ['启动 24H 写作', '暂停 24H 写作', '继续 24H 写作', '查看当前状态']
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
    mutationFn: (cmd: string) => cockpitApi.postCommand(projectId, cmd),
    onSuccess: (data) => {
      setResult(data)
      setShowResult(true)
    },
    onError: (err: Error) => {
      setResult({ ok: false, message: err.message })
      setShowResult(true)
    },
  })

  const dispatchCommand = (cmd: string) => {
    const normalized = cmd.trim()
    if (!normalized || commandDisabled) return
    setShowResult(false)
    if (onSubmit) {
      onSubmit(normalized)
    } else if (projectId) {
      commandMutation.mutate(normalized)
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
            'flex items-center gap-2 border-t px-4 py-1.5 text-xs',
            result.ok
              ? 'border-green-600/30 bg-green-600/10 text-green-300'
              : 'border-red-600/30 bg-red-600/10 text-red-300',
          )}
        >
          {result.ok ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
          <span className="flex-1 truncate">
            {result.message || (result.ok ? '指令已执行' : '执行失败')}
            {result.intent && (
              <span className="ml-1 text-gray-500">
                · 动作: {intentLabels[result.intent] ?? result.intent}
              </span>
            )}
          </span>
          <button
            onClick={() => setShowResult(false)}
            className="text-gray-500 hover:text-gray-300"
          >
            <X size={12} />
          </button>
        </div>
      )}

      <div className="border-t border-ink-700 bg-ink-900 px-4 py-2">
        <div className="mb-1.5 flex items-center gap-1.5 overflow-x-auto">
          <span className="mr-1 shrink-0 text-[9px] font-semibold uppercase tracking-[.15em] text-gray-600">
            24H 快捷控制
          </span>
          {quickCommands.map((command) => (
            <button
              key={command}
              type="button"
              disabled={commandDisabled || commandMutation.isPending}
              onClick={() => dispatchCommand(command)}
              className="shrink-0 rounded-md border border-ink-600 px-2 py-0.5 text-[10px] text-gray-400 transition-colors hover:border-gold-500/40 hover:text-gold-300 disabled:cursor-not-allowed disabled:opacity-35"
            >
              {command}
            </button>
          ))}
        </div>
        <form onSubmit={handleSubmit} className="flex items-center gap-2">
          <Sparkles size={16} className="shrink-0 text-gold-500" />
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="输入可执行指令：启动、暂停、继续、停止、查看状态；返工/修改会进入审阅队列"
            disabled={commandDisabled}
            className="h-7 flex-1 bg-transparent text-sm text-gray-200 placeholder:text-gray-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={commandDisabled || !value.trim() || commandMutation.isPending}
            className={cn(
              'flex h-7 items-center gap-1 rounded-md px-2.5 text-xs font-medium transition-colors',
              'bg-gold-500 text-ink-950 hover:bg-gold-400',
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
