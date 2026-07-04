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

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const cmd = value.trim()
    if (!cmd || disabled) return

    if (onSubmit) {
      onSubmit(cmd)
    } else if (projectId) {
      commandMutation.mutate(cmd)
    }
    setValue('')
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
            {result.intent && <span className="ml-1 text-gray-500">· 意图: {result.intent}</span>}
          </span>
          <button
            onClick={() => setShowResult(false)}
            className="text-gray-500 hover:text-gray-300"
          >
            <X size={12} />
          </button>
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        className="flex items-center gap-2 border-t border-ink-700 bg-ink-900 px-4 py-2.5"
      >
        <Sparkles size={16} className="shrink-0 text-gold-500" />
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="向智能体下达指令…（例如：续写下一章、检查本章连续性、生成第 12 章大纲）"
          disabled={disabled}
          className="h-7 flex-1 bg-transparent text-sm text-gray-200 placeholder:text-gray-500 focus:outline-none"
        />
        <button
          type="submit"
          disabled={disabled || !value.trim() || commandMutation.isPending}
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
  )
}
