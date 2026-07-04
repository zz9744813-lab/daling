import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Shield,
  Server,
  Cpu,
  Circle,
  Plus,
  Loader2,
  X,
  Zap,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react'
import { TopBar } from '../layout/TopBar'
import { BossCommandBar } from '../layout/BossCommandBar'
import { AppShell, AppShellBody } from '../layout/AppShell'
import { governanceApi } from '../api/client'
import { useProjectStore } from '../store/projectStore'
import { Button, Input, Tabs } from '../components/ui'
import { Badge } from '../components/Badge'
import { EmptyState } from '../components/EmptyState'
import { AGENT_ROLES, AgentRole } from '../types'
import type { ProviderTestResult } from '../types'

type TabKey = 'providers' | 'bindings'

const PROVIDER_TYPES = ['openai', 'anthropic', 'azure', 'ollama', 'custom']
const MODEL_SUGGESTIONS: Record<string, string[]> = {
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'],
  anthropic: ['claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022', 'claude-3-opus-20240229'],
  azure: ['gpt-4o', 'gpt-4', 'gpt-35-turbo'],
  ollama: ['llama3', 'qwen2', 'mistral'],
  custom: [],
}

/**
 * GovernancePage —— 治理
 * Provider 列表 + 添加 Provider 表单 + 测试连接 + Model Bindings 表格
 */
export default function GovernancePage() {
  const project = useProjectStore((s) => s.currentProject)
  const projectId = project?.id
  const [tab, setTab] = useState<TabKey>('providers')

  const { data: providers } = useQuery({
    queryKey: ['providers'],
    queryFn: governanceApi.listProviders,
  })

  const { data: bindings } = useQuery({
    queryKey: ['model-bindings', projectId],
    queryFn: () => governanceApi.listBindings(projectId),
  })

  const providerStatus = (providers?.length ?? 0) > 0 ? 'online' : 'offline'

  return (
    <AppShell>
      <TopBar providerStatus={providerStatus as 'online' | 'offline'} />
      <AppShellBody className="flex-col">
        <div className="border-b border-ink-700 px-6 py-3">
          <h1 className="flex items-center gap-2 text-base font-medium text-gray-200">
            <Shield size={18} className="text-gold-500" />
            治理
          </h1>
          <p className="mt-0.5 text-xs text-gray-500">Provider 管理与模型绑定</p>
        </div>

        <div className="border-b border-ink-700 px-6">
          <Tabs
            active={tab}
            onChange={(k) => setTab(k as TabKey)}
            items={[
              {
                key: 'providers',
                label: (
                  <span className="flex items-center gap-1">
                    <Server size={13} /> Providers
                  </span>
                ),
              },
              {
                key: 'bindings',
                label: (
                  <span className="flex items-center gap-1">
                    <Cpu size={13} /> Model Bindings
                  </span>
                ),
              },
            ]}
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          {tab === 'providers' && (
            <ProvidersTab providers={providers} />
          )}
          {tab === 'bindings' && (
            <ModelBindingsTab
              bindings={bindings}
              providers={providers}
              projectId={projectId}
            />
          )}
        </div>
      </AppShellBody>
      <BossCommandBar />
    </AppShell>
  )
}

/* ============================================================
 * Providers Tab
 * ============================================================ */
function ProvidersTab({ providers }: { providers: any[] | undefined }) {
  const queryClient = useQueryClient()
  const [showAdd, setShowAdd] = useState(false)

  const addMutation = useMutation({
    mutationFn: (data: any) => governanceApi.createProvider(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] })
      setShowAdd(false)
    },
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-gray-500">
          已配置 {providers?.length ?? 0} 个 Provider
        </p>
        <Button variant="primary" size="sm" onClick={() => setShowAdd(true)}>
          <Plus size={14} />
          添加 Provider
        </Button>
      </div>

      {!providers || providers.length === 0 ? (
        <EmptyState
          icon={<Server size={26} />}
          title="暂无 Provider"
          description="添加 OpenAI / Anthropic / Ollama 等 LLM 提供方，开始 AI 创作。"
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-ink-700">
          <table className="w-full text-sm">
            <thead className="bg-ink-850 text-xs text-gray-400">
              <tr>
                <th className="px-4 py-2.5 text-left font-medium">名称</th>
                <th className="px-4 py-2.5 text-left font-medium">类型</th>
                <th className="px-4 py-2.5 text-left font-medium">Base URL</th>
                <th className="px-4 py-2.5 text-left font-medium">模型</th>
                <th className="px-4 py-2.5 text-left font-medium">状态</th>
                <th className="px-4 py-2.5 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-700">
              {providers.map((p) => (
                <ProviderRow key={p.id} provider={p} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showAdd && (
        <AddProviderModal
          onClose={() => setShowAdd(false)}
          onSubmit={(data) => addMutation.mutate(data)}
          loading={addMutation.isPending}
          error={addMutation.error?.message}
        />
      )}
    </div>
  )
}

function ProviderRow({ provider }: { provider: any }) {
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null)
  const [testing, setTesting] = useState(false)

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await governanceApi.testProvider({
        provider_type: provider.type ?? provider.provider_type ?? 'openai',
        base_url: provider.base_url ?? '',
        api_key: '',
        model: provider.models?.[0] ?? '',
      })
      setTestResult(result)
    } catch (err) {
      setTestResult({ ok: false, message: (err as Error).message })
    } finally {
      setTesting(false)
    }
  }

  return (
    <tr className="hover:bg-ink-850">
      <td className="px-4 py-2.5 text-gray-200">{provider.name}</td>
      <td className="px-4 py-2.5">
        <Badge variant="outline">{provider.type ?? provider.provider_type ?? '—'}</Badge>
      </td>
      <td className="px-4 py-2.5 text-xs text-gray-500">{provider.base_url || '—'}</td>
      <td className="px-4 py-2.5">
        <div className="flex flex-wrap gap-1">
          {provider.models?.slice(0, 2).map((m: string) => (
            <Badge key={m} variant="outline">
              {m}
            </Badge>
          )) ?? <span className="text-gray-600">—</span>}
        </div>
      </td>
      <td className="px-4 py-2.5">
        <ProviderStatusBadge status={provider.status} testResult={testResult} />
      </td>
      <td className="px-4 py-2.5 text-right">
        <button
          onClick={handleTest}
          disabled={testing}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-gold-500 hover:bg-ink-700 disabled:opacity-40"
        >
          {testing ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
          测试
        </button>
      </td>
    </tr>
  )
}

function ProviderStatusBadge({
  status,
  testResult,
}: {
  status?: string
  testResult?: ProviderTestResult | null
}) {
  if (testResult) {
    return testResult.ok ? (
      <Badge variant="green">
        <CheckCircle2 size={10} /> 正常
      </Badge>
    ) : (
      <Badge variant="red">
        <AlertCircle size={10} /> 异常
      </Badge>
    )
  }
  if (status === 'active') return <Badge variant="green"><Circle size={7} className="fill-current" /> 在线</Badge>
  if (status === 'error') return <Badge variant="red"><Circle size={7} className="fill-current" /> 异常</Badge>
  return <Badge variant="gray"><Circle size={7} className="fill-current" /> 未启用</Badge>
}

function AddProviderModal({
  onClose,
  onSubmit,
  loading,
  error,
}: {
  onClose: () => void
  onSubmit: (data: any) => void
  loading: boolean
  error?: string
}) {
  const [name, setName] = useState('')
  const [providerType, setProviderType] = useState('openai')
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !baseUrl.trim()) return
    onSubmit({
      name: name.trim(),
      provider_type: providerType,
      base_url: baseUrl.trim(),
      api_key: apiKey.trim() || undefined,
      model: model.trim() || undefined,
      models: model.trim() ? [model.trim()] : undefined,
    })
  }

  const handleTypeChange = (type: string) => {
    setProviderType(type)
    // 自动填充默认 base_url
    const defaults: Record<string, string> = {
      openai: 'https://api.openai.com/v1',
      anthropic: 'https://api.anthropic.com',
      azure: '',
      ollama: 'http://localhost:11434',
      custom: '',
    }
    setBaseUrl(defaults[type] ?? '')
    // 自动填充默认 model
    const models = MODEL_SUGGESTIONS[type] ?? []
    if (models.length > 0) setModel(models[0])
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-lg border border-ink-700 bg-ink-850 p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-medium text-gray-100">添加 Provider</h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <X size={18} />
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-red-600/30 bg-red-600/10 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">名称 *</label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如：我的 OpenAI"
              autoFocus
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">Provider 类型</label>
            <select
              value={providerType}
              onChange={(e) => handleTypeChange(e.target.value)}
              className="h-9 w-full rounded-md border border-ink-600 bg-ink-900 px-3 text-sm text-gray-200 focus:border-gold-500/60 focus:outline-none focus:ring-1 focus:ring-gold-500/30"
            >
              {PROVIDER_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">Base URL *</label>
            <Input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.openai.com/v1"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">API Key</label>
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-..."
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">默认模型</label>
            <Input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="gpt-4o"
              list="model-suggestions"
            />
            <datalist id="model-suggestions">
              {(MODEL_SUGGESTIONS[providerType] ?? []).map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={onClose} type="button">
              取消
            </Button>
            <Button variant="primary" type="submit" disabled={loading || !name.trim() || !baseUrl.trim()}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : null}
              添加
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}

/* ============================================================
 * Model Bindings Tab — 始终显示 8 个 Agent 角色
 * ============================================================ */
function ModelBindingsTab({
  bindings,
  providers,
  projectId,
}: {
  bindings: any[] | undefined
  providers: any[] | undefined
  projectId?: string
}) {
  const queryClient = useQueryClient()
  const [showAdd, setShowAdd] = useState(false)
  const [presetRole, setPresetRole] = useState<AgentRole | null>(null)

  const addMutation = useMutation({
    mutationFn: (data: any) => governanceApi.createBinding(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['model-bindings', projectId] })
      setShowAdd(false)
      setPresetRole(null)
    },
  })

  // 构建 8 个角色的视图数据
  const allRoles = Object.values(AgentRole)
  const bindingByRole: Record<string, any> = {}
  for (const b of bindings ?? []) {
    if (b.agent_role) bindingByRole[b.agent_role] = b
  }

  const handleConfigure = (role: AgentRole) => {
    setPresetRole(role)
    setShowAdd(true)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-gray-500">
          已配置 {bindings?.length ?? 0} / 8 个智能体角色
          {!projectId && ' · 全局绑定'}
        </p>
        <Button
          variant="primary"
          size="sm"
          onClick={() => {
            setPresetRole(null)
            setShowAdd(true)
          }}
          disabled={!providers?.length}
        >
          <Plus size={14} />
          添加绑定
        </Button>
      </div>

      {!providers?.length ? (
        <EmptyState
          icon={<Cpu size={26} />}
          title="请先添加 Provider"
          description="先在 Providers 标签页添加 LLM 提供方，再配置模型绑定。"
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-ink-700">
          <table className="w-full text-sm">
            <thead className="bg-ink-850 text-xs text-gray-400">
              <tr>
                <th className="px-4 py-2.5 text-left font-medium">智能体角色</th>
                <th className="px-4 py-2.5 text-left font-medium">Provider</th>
                <th className="px-4 py-2.5 text-left font-medium">模型</th>
                <th className="px-4 py-2.5 text-left font-medium">默认</th>
                <th className="px-4 py-2.5 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-700">
              {allRoles.map((role) => {
                const b = bindingByRole[role]
                return (
                  <tr key={role} className="hover:bg-ink-850">
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <span className="text-gray-200">{AGENT_ROLES[role]}</span>
                        {b && (
                          <Circle size={6} className="text-green-500 fill-green-500" />
                        )}
                      </div>
                      <span className="text-xs text-gray-600">{role}</span>
                    </td>
                    <td className="px-4 py-2.5 text-gray-400">
                      {b ? (b.provider_name ?? b.provider_id?.slice(0, 8)) : (
                        <span className="text-gray-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      {b ? (
                        <Badge variant="outline">{b.model}</Badge>
                      ) : (
                        <span className="text-gray-600">未配置</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      {b?.is_default ? (
                        <Badge variant="gold">默认</Badge>
                      ) : (
                        <span className="text-gray-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {!b && (
                        <button
                          onClick={() => handleConfigure(role)}
                          className="text-xs text-gold-400 hover:text-gold-300"
                        >
                          配置
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {showAdd && (
        <AddBindingModal
          providers={providers ?? []}
          projectId={projectId}
          presetRole={presetRole}
          onClose={() => {
            setShowAdd(false)
            setPresetRole(null)
          }}
          onSubmit={(data) => addMutation.mutate(data)}
          loading={addMutation.isPending}
          error={addMutation.error?.message}
        />
      )}
    </div>
  )
}

function AddBindingModal({
  providers,
  projectId,
  presetRole,
  onClose,
  onSubmit,
  loading,
  error,
}: {
  providers: any[]
  projectId?: string
  presetRole: AgentRole | null
  onClose: () => void
  onSubmit: (data: any) => void
  loading: boolean
  error?: string
}) {
  const [agentRole, setAgentRole] = useState<AgentRole>(presetRole ?? AgentRole.Drafter)
  const [providerId, setProviderId] = useState(providers[0]?.id ?? '')
  const [model, setModel] = useState('')
  const [isDefault, setIsDefault] = useState(false)

  const selectedProvider = providers.find((p) => p.id === providerId)
  const availableModels = selectedProvider?.models ?? []

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!providerId || !model.trim()) return
    onSubmit({
      project_id: projectId,
      agent_role: agentRole,
      provider_id: providerId,
      model: model.trim(),
      is_default: isDefault,
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-lg border border-ink-700 bg-ink-850 p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-medium text-gray-100">添加模型绑定</h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <X size={18} />
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-red-600/30 bg-red-600/10 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">智能体角色</label>
            <select
              value={agentRole}
              onChange={(e) => setAgentRole(e.target.value as AgentRole)}
              className="h-9 w-full rounded-md border border-ink-600 bg-ink-900 px-3 text-sm text-gray-200 focus:border-gold-500/60 focus:outline-none focus:ring-1 focus:ring-gold-500/30"
            >
              {Object.values(AgentRole).map((role) => (
                <option key={role} value={role}>
                  {AGENT_ROLES[role]}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">Provider</label>
            <select
              value={providerId}
              onChange={(e) => {
                setProviderId(e.target.value)
                setModel('')
              }}
              className="h-9 w-full rounded-md border border-ink-600 bg-ink-900 px-3 text-sm text-gray-200 focus:border-gold-500/60 focus:outline-none focus:ring-1 focus:ring-gold-500/30"
            >
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.type ?? p.provider_type ?? 'unknown'})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">模型</label>
            <Input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="如：gpt-4o"
              list="binding-model-suggestions"
            />
            <datalist id="binding-model-suggestions">
              {availableModels.map((m: string) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </div>
          <label className="flex items-center gap-2 text-xs text-gray-400">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-ink-600 bg-ink-900"
            />
            设为该角色的默认绑定
          </label>

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={onClose} type="button">
              取消
            </Button>
            <Button variant="primary" type="submit" disabled={loading || !providerId || !model.trim()}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : null}
              添加
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
