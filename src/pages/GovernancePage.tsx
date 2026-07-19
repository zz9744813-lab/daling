import React, { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  AlertTriangle,
  BrainCircuit,
  CheckCircle2,
  Circle,
  Cpu,
  Gauge,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  Power,
  Server,
  Shield,
  Trash2,
  X,
  Zap,
} from 'lucide-react'
import { governanceApi } from '../api/client'
import { Badge } from '../components/Badge'
import { EmptyState } from '../components/EmptyState'
import { Button, Input, Tabs } from '../components/ui'
import { AppShell, AppShellBody } from '../layout/AppShell'
import { BossCommandBar } from '../layout/BossCommandBar'
import { TopBar } from '../layout/TopBar'
import { useDialogFocus } from '../lib/useDialogFocus'
import { useProjectStore } from '../store/projectStore'
import { AGENT_ROLES, AgentRole } from '../types'
import type {
  ModelBinding,
  ModelBindingCreateData,
  ModelBindingUpdateData,
  Provider,
  ProviderCreateData,
  ProviderTestResult,
  ProviderUpdateData,
} from '../types'

type TabKey = 'providers' | 'bindings'
type ProviderMutationInput = { providerId: string; data: ProviderUpdateData }
type ProviderDeleteInput = { providerId: string; force: boolean }
type BindingMutationInput = { bindingId: string; data: ModelBindingUpdateData }

const PROVIDER_TYPES = [
  'openai_compatible',
  'openai',
  'anthropic',
  'azure',
  'ollama',
  'custom',
]

const MODEL_SUGGESTIONS: Record<string, string[]> = {
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo'],
  anthropic: ['claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022'],
  azure: ['gpt-4o', 'gpt-4', 'gpt-35-turbo'],
  ollama: ['llama3', 'qwen2', 'mistral'],
  openai_compatible: [],
  custom: [],
}

const SELECT_CLASS =
  'h-9 w-full rounded-lg border border-ink-600 bg-ink-950 px-3 text-sm text-gray-200 focus:border-emerald-400/50 focus:outline-none focus:ring-2 focus:ring-emerald-400/10 disabled:cursor-not-allowed disabled:opacity-50'

function getErrorMessage(error: unknown): string {
  const response = (
    error as {
      response?: {
        data?: {
          detail?: string | { message?: string; code?: string }
          message?: string
        }
      }
      message?: string
    }
  )?.response?.data
  const detail = response?.detail

  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  if (response?.message) return response.message
  if (error instanceof Error) return error.message
  return '操作失败，请检查配置后重试。'
}

function isProviderEnabled(provider: Provider): boolean {
  if (typeof provider.is_active === 'boolean') return provider.is_active
  return provider.status !== 'inactive'
}

function invalidateGovernance(queryClient: ReturnType<typeof useQueryClient>, projectId?: string) {
  void queryClient.invalidateQueries({ queryKey: ['providers'] })
  void queryClient.invalidateQueries({ queryKey: ['model-bindings', projectId] })
}

export default function GovernancePage() {
  const project = useProjectStore((state) => state.currentProject)
  const projectId = project?.id
  const [tab, setTab] = useState<TabKey>('providers')

  const providersQuery = useQuery({
    queryKey: ['providers'],
    queryFn: governanceApi.listProviders,
  })
  const bindingsQuery = useQuery({
    queryKey: ['model-bindings', projectId],
    queryFn: () => governanceApi.listBindings(projectId),
  })

  const providers = providersQuery.data ?? []
  const bindings = bindingsQuery.data ?? []
  const roleCount = Object.values(AgentRole).length
  const boundRoleCount = new Set(
    bindings
      .map((binding) => binding.agent_role)
      .filter((role): role is AgentRole => Boolean(role)),
  ).size
  const providerStatus = providersQuery.isLoading || providersQuery.isError
    ? 'unknown'
    : providers.some((provider) => provider.status === 'active')
      ? 'online'
      : providers.length > 0
        ? 'degraded'
        : 'offline'

  return (
    <AppShell>
      <TopBar providerStatus={providerStatus} />
      <AppShellBody className="flex-col">
        <header className="border-b border-ink-700 px-4 py-4 sm:px-6">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h1 className="flex items-center gap-2 text-base font-medium text-gray-100">
                <Shield size={18} className="text-emerald-300" />
                模型治理
              </h1>
              <p className="mt-1 text-xs text-gray-500">
                管理 Provider 健康状态，并为八个生产智能体分配独立模型预算。
              </p>
            </div>
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <Badge variant={providers.some(isProviderEnabled) ? 'green' : 'amber'}>
                {providers.filter(isProviderEnabled).length} 个可用 Provider
              </Badge>
              <Badge variant={boundRoleCount === roleCount ? 'green' : 'outline'}>
                {boundRoleCount} / {roleCount} 角色已绑定
              </Badge>
            </div>
          </div>
        </header>

        <div className="border-b border-ink-700 px-4 sm:px-6">
          <Tabs
            active={tab}
            onChange={(key) => setTab(key as TabKey)}
            items={[
              {
                key: 'providers',
                label: (
                  <span className="flex items-center gap-1.5">
                    <Server size={13} /> Provider
                  </span>
                ),
              },
              {
                key: 'bindings',
                label: (
                  <span className="flex items-center gap-1.5">
                    <Cpu size={13} /> 角色绑定
                  </span>
                ),
              },
            ]}
          />
        </div>

        <main className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
          {(providersQuery.isError || bindingsQuery.isError) && (
            <InlineError
              message={getErrorMessage(providersQuery.error ?? bindingsQuery.error)}
              actionLabel="重新加载"
              onAction={() => {
                void providersQuery.refetch()
                void bindingsQuery.refetch()
              }}
            />
          )}

          {(providersQuery.isLoading || bindingsQuery.isLoading) && (
            <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-gray-500">
              <Loader2 size={16} className="animate-spin" />
              正在读取治理配置…
            </div>
          )}

          {!providersQuery.isLoading && !bindingsQuery.isLoading && tab === 'providers' && (
            <ProvidersTab providers={providers} bindings={bindings} projectId={projectId} />
          )}
          {!providersQuery.isLoading && !bindingsQuery.isLoading && tab === 'bindings' && (
            <ModelBindingsTab
              bindings={bindings}
              providers={providers}
              projectId={projectId}
            />
          )}
        </main>
      </AppShellBody>
      <BossCommandBar />
    </AppShell>
  )
}

function ProvidersTab({
  providers,
  bindings,
  projectId,
}: {
  providers: Provider[]
  bindings: ModelBinding[]
  projectId?: string
}) {
  const queryClient = useQueryClient()
  const [providerModal, setProviderModal] = useState<Provider | 'new' | null>(null)
  const [confirmTarget, setConfirmTarget] = useState<{
    kind: 'toggle' | 'delete'
    provider: Provider
  } | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const addMutation = useMutation({
    mutationFn: (data: ProviderCreateData) => governanceApi.createProvider(data),
    onSuccess: () => {
      invalidateGovernance(queryClient, projectId)
      setProviderModal(null)
      setActionError(null)
    },
    onError: (error) => setActionError(getErrorMessage(error)),
  })
  const updateMutation = useMutation({
    mutationFn: ({ providerId, data }: ProviderMutationInput) =>
      governanceApi.updateProvider(providerId, data),
    onSuccess: () => {
      invalidateGovernance(queryClient, projectId)
      setProviderModal(null)
      setConfirmTarget(null)
      setActionError(null)
    },
    onError: (error) => setActionError(getErrorMessage(error)),
  })
  const deleteMutation = useMutation({
    mutationFn: ({ providerId, force }: ProviderDeleteInput) =>
      governanceApi.deleteProvider(providerId, force),
    onSuccess: () => {
      invalidateGovernance(queryClient, projectId)
      setConfirmTarget(null)
      setActionError(null)
    },
    onError: (error) => setActionError(getErrorMessage(error)),
  })

  const busyProviderId =
    updateMutation.isPending
      ? updateMutation.variables?.providerId
      : deleteMutation.isPending
        ? deleteMutation.variables?.providerId
        : undefined
  const totalBusy = addMutation.isPending || updateMutation.isPending || deleteMutation.isPending

  const bindingCountFor = (providerId: string) =>
    bindings.filter((binding) => binding.provider_id === providerId).length

  const confirm = confirmTarget
  const confirmBindingCount = confirm ? bindingCountFor(confirm.provider.id) : 0
  const confirmIsDelete = confirm?.kind === 'delete'

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-gray-300">Provider 连接</p>
          <p className="mt-0.5 text-xs text-gray-500">
            编辑不会回显已保存的密钥；API Key 留空即保持原值。
          </p>
        </div>
        <Button
          variant="primary"
          size="sm"
          onClick={() => {
            setActionError(null)
            setProviderModal('new')
          }}
          disabled={totalBusy}
        >
          <Plus size={14} />
          添加 Provider
        </Button>
      </div>

      {actionError && (
        <InlineError message={actionError} onDismiss={() => setActionError(null)} />
      )}

      {providers.length === 0 ? (
        <EmptyState
          icon={<Server size={26} />}
          title="暂无 Provider"
          description="添加一个 OpenAI 兼容或其他 LLM 提供方，再为写作智能体绑定模型。"
        />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-ink-700 bg-ink-900/30">
          <table className="min-w-[880px] w-full text-sm">
            <thead className="bg-ink-850 text-xs text-gray-400">
              <tr>
                <th className="px-4 py-3 text-left font-medium">Provider</th>
                <th className="px-4 py-3 text-left font-medium">连接</th>
                <th className="px-4 py-3 text-left font-medium">模型</th>
                <th className="px-4 py-3 text-left font-medium">健康状态</th>
                <th className="px-4 py-3 text-left font-medium">占用</th>
                <th className="px-4 py-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-700">
              {providers.map((provider) => (
                <ProviderRow
                  key={provider.id}
                  provider={provider}
                  bindingCount={bindingCountFor(provider.id)}
                  busy={busyProviderId === provider.id}
                  globallyBusy={totalBusy}
                  onEdit={() => {
                    setActionError(null)
                    setProviderModal(provider)
                  }}
                  onToggle={() => {
                    setActionError(null)
                    setConfirmTarget({ kind: 'toggle', provider })
                  }}
                  onDelete={() => {
                    setActionError(null)
                    setConfirmTarget({ kind: 'delete', provider })
                  }}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {providerModal && (
        <ProviderModal
          provider={providerModal === 'new' ? undefined : providerModal}
          loading={addMutation.isPending || updateMutation.isPending}
          error={actionError}
          onClose={() => {
            if (!addMutation.isPending && !updateMutation.isPending) setProviderModal(null)
          }}
          onSubmit={(data) => {
            setActionError(null)
            if (providerModal === 'new') {
              addMutation.mutate(data as ProviderCreateData)
            } else {
              updateMutation.mutate({
                providerId: providerModal.id,
                data: data as ProviderUpdateData,
              })
            }
          }}
        />
      )}

      {confirm && (
        <ConfirmDialog
          title={confirmIsDelete ? '删除 Provider' : isProviderEnabled(confirm.provider) ? '停用 Provider' : '启用 Provider'}
          message={
            confirmIsDelete
              ? confirmBindingCount > 0
                ? '该 Provider 正被 ' + confirmBindingCount + ' 个角色绑定使用。继续将同时删除这些绑定，生产前必须重新配置。'
                : '删除后连接配置将无法恢复，但不会删除已生成的小说内容。'
              : isProviderEnabled(confirm.provider)
                ? '停用后，绑定到该 Provider 的智能体将不能发起新的模型请求。'
                : '启用只恢复调度资格，建议随后执行一次真实连接测试。'
          }
          confirmLabel={confirmIsDelete ? '确认删除' : isProviderEnabled(confirm.provider) ? '确认停用' : '确认启用'}
          danger={confirmIsDelete || isProviderEnabled(confirm.provider)}
          loading={updateMutation.isPending || deleteMutation.isPending}
          error={actionError}
          onCancel={() => {
            if (!updateMutation.isPending && !deleteMutation.isPending) setConfirmTarget(null)
          }}
          onConfirm={() => {
            setActionError(null)
            if (confirmIsDelete) {
              deleteMutation.mutate({
                providerId: confirm.provider.id,
                force: confirmBindingCount > 0,
              })
            } else {
              updateMutation.mutate({
                providerId: confirm.provider.id,
                data: { is_active: !isProviderEnabled(confirm.provider) },
              })
            }
          }}
        >
          <div className="rounded-lg border border-ink-700 bg-ink-950/70 px-3 py-2">
            <p className="text-sm font-medium text-gray-200">{confirm.provider.name}</p>
            <p className="mt-0.5 truncate text-xs text-gray-500">{confirm.provider.base_url || '未配置 Base URL'}</p>
          </div>
        </ConfirmDialog>
      )}
    </section>
  )
}

function ProviderRow({
  provider,
  bindingCount,
  busy,
  globallyBusy,
  onEdit,
  onToggle,
  onDelete,
}: {
  provider: Provider
  bindingCount: number
  busy: boolean
  globallyBusy: boolean
  onEdit: () => void
  onToggle: () => void
  onDelete: () => void
}) {
  const queryClient = useQueryClient()
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const enabled = isProviderEnabled(provider)

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await governanceApi.testProvider({
        provider_id: provider.id,
        model: provider.default_model ?? provider.models?.[0],
      })
      setTestResult(result)
      await queryClient.invalidateQueries({ queryKey: ['providers'] })
    } catch (error) {
      setTestResult({ ok: false, message: getErrorMessage(error) })
    } finally {
      setTesting(false)
    }
  }

  const shownModels = provider.models?.length
    ? provider.models
    : provider.default_model
      ? [provider.default_model]
      : []

  return (
    <tr className="transition-colors hover:bg-ink-850/70">
      <td className="px-4 py-3">
        <p className="font-medium text-gray-200">{provider.name}</p>
        <p className="mt-0.5 text-xs text-gray-600">
          {provider.type ?? provider.provider_type ?? 'unknown'}
        </p>
      </td>
      <td className="max-w-[230px] px-4 py-3">
        <p className="truncate text-xs text-gray-400" title={provider.base_url}>
          {provider.base_url || '—'}
        </p>
        <div className="mt-1 flex items-center gap-1 text-[10px] text-gray-600">
          <KeyRound size={10} />
          密钥由服务端安全保存
        </div>
      </td>
      <td className="px-4 py-3">
        <div className="flex max-w-[250px] flex-wrap gap-1">
          {shownModels.slice(0, 2).map((model) => (
            <Badge key={model} variant="outline">{model}</Badge>
          ))}
          {shownModels.length > 2 && <Badge variant="gray">+{shownModels.length - 2}</Badge>}
          {shownModels.length === 0 && <span className="text-gray-600">—</span>}
        </div>
      </td>
      <td className="px-4 py-3">
        <div className="space-y-1">
          <ProviderStatusBadge
            status={provider.status}
            enabled={enabled}
            testResult={testResult}
          />
          {(testResult?.latency_ms ?? provider.latency_ms) != null && (
            <p className="text-[10px] text-gray-600">
              {testResult?.latency_ms ?? provider.latency_ms} ms ·{' '}
              {testResult?.model ?? provider.tested_model ?? provider.default_model ?? '默认模型'}
            </p>
          )}
          {testResult && !testResult.ok && (
            <p className="max-w-[210px] text-[10px] text-red-300" title={testResult.message}>
              {testResult.message || '连接失败'}
            </p>
          )}
        </div>
      </td>
      <td className="px-4 py-3">
        <Badge variant={bindingCount > 0 ? 'blue' : 'outline'}>
          {bindingCount} 个角色
        </Badge>
      </td>
      <td className="px-4 py-3">
        <div className="flex justify-end gap-1">
          <ActionButton
            label="真实测试"
            icon={testing ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
            onClick={handleTest}
            disabled={globallyBusy || testing || !enabled}
          />
          <ActionButton
            label="编辑"
            icon={<Pencil size={13} />}
            onClick={onEdit}
            disabled={globallyBusy || testing}
          />
          <ActionButton
            label={enabled ? '停用' : '启用'}
            icon={busy ? <Loader2 size={13} className="animate-spin" /> : <Power size={13} />}
            onClick={onToggle}
            disabled={globallyBusy || testing}
            tone={enabled ? 'warning' : 'normal'}
          />
          <ActionButton
            label="删除"
            icon={<Trash2 size={13} />}
            onClick={onDelete}
            disabled={globallyBusy || testing}
            tone="danger"
          />
        </div>
      </td>
    </tr>
  )
}

function ProviderStatusBadge({
  status,
  enabled,
  testResult,
}: {
  status?: string
  enabled: boolean
  testResult?: ProviderTestResult | null
}) {
  if (!enabled) {
    return <Badge variant="gray"><Circle size={7} className="fill-current" /> 已停用</Badge>
  }
  if (testResult) {
    return testResult.ok ? (
      <Badge variant="green"><CheckCircle2 size={10} /> 连接正常</Badge>
    ) : (
      <Badge variant="red"><AlertCircle size={10} /> 连接异常</Badge>
    )
  }
  if (status === 'active') {
    return <Badge variant="green"><Circle size={7} className="fill-current" /> 已验证</Badge>
  }
  if (status === 'error') {
    return <Badge variant="red"><Circle size={7} className="fill-current" /> 异常</Badge>
  }
  return <Badge variant="amber"><Circle size={7} className="fill-current" /> 未检测</Badge>
}

function ProviderModal({
  provider,
  loading,
  error,
  onClose,
  onSubmit,
}: {
  provider?: Provider
  loading: boolean
  error?: string | null
  onClose: () => void
  onSubmit: (data: ProviderCreateData | ProviderUpdateData) => void
}) {
  const editing = Boolean(provider)
  const [name, setName] = useState(provider?.name ?? '')
  const [providerType, setProviderType] = useState(
    provider?.provider_type ?? provider?.type ?? 'openai_compatible',
  )
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [modelsText, setModelsText] = useState((provider?.models ?? []).join('\n'))
  const [defaultModel, setDefaultModel] = useState(provider?.default_model ?? '')

  const models = useMemo(
    () =>
      Array.from(
        new Set(
          modelsText
            .split(/[\n,]/)
            .map((model) => model.trim())
            .filter(Boolean),
        ),
      ),
    [modelsText],
  )

  const invalidDefault = Boolean(defaultModel.trim() && !models.includes(defaultModel.trim()))
  const canSubmit =
    name.trim().length > 0 &&
    baseUrl.trim().length > 0 &&
    !invalidDefault &&
    !loading

  const changeType = (type: string) => {
    setProviderType(type)
    if (editing || baseUrl.trim()) return
    const defaults: Record<string, string> = {
      openai: 'https://api.openai.com/v1',
      anthropic: 'https://api.anthropic.com',
      azure: '',
      ollama: 'http://localhost:11434',
      custom: '',
      openai_compatible: '',
    }
    setBaseUrl(defaults[type] ?? '')
  }

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!canSubmit) return
    const normalizedDefault = defaultModel.trim() || models[0]

    if (editing) {
      const data: ProviderUpdateData = {
        name: name.trim(),
        provider_type: providerType,
        base_url: baseUrl.trim(),
        models,
        default_model: normalizedDefault,
      }
      if (apiKey.trim()) data.api_key = apiKey.trim()
      onSubmit(data)
      return
    }

    onSubmit({
      name: name.trim(),
      provider_type: providerType,
      base_url: baseUrl.trim(),
      api_key: apiKey.trim() || undefined,
      model: normalizedDefault,
      models,
    })
  }

  return (
    <ModalFrame
      title={editing ? '编辑 Provider' : '添加 Provider'}
      subtitle={editing ? '更新连接与模型目录。密钥留空将保持服务端现有值。' : '创建后请执行真实连接测试。'}
      onClose={onClose}
      closeDisabled={loading}
    >
      {error && <InlineError message={error} />}
      <form onSubmit={submit} className="mt-4 space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="显示名称" required>
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：生产模型网关"
              autoFocus
              disabled={loading}
            />
          </Field>
          <Field label="Provider 类型" required>
            <select
              value={providerType}
              onChange={(event) => changeType(event.target.value)}
              className={SELECT_CLASS}
              disabled={loading}
            >
              {!PROVIDER_TYPES.includes(providerType) && (
                <option value={providerType}>{providerType}</option>
              )}
              {PROVIDER_TYPES.map((type) => (
                <option key={type} value={type}>{type}</option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="Base URL" required hint="应包含兼容接口的 /v1 前缀（若服务端要求）。">
          <Input
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://gateway.example.com/v1"
            disabled={loading}
          />
        </Field>

        <Field
          label={editing ? '替换 API Key（可选）' : 'API Key（可选）'}
          hint={editing ? '系统不会读取或回显现有密钥；留空可确保密钥完全不变。' : '密钥提交后仅由后端保存。'}
        >
          <Input
            type="password"
            autoComplete="new-password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={editing ? '留空保持原密钥' : 'sk-…'}
            disabled={loading}
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-[1.4fr_1fr]">
          <Field label="可用模型" hint="每行一个，也可使用英文逗号分隔。">
            <textarea
              value={modelsText}
              onChange={(event) => setModelsText(event.target.value)}
              rows={4}
              className="w-full resize-none rounded-lg border border-ink-600 bg-ink-950 px-3 py-2 text-sm leading-6 text-gray-200 placeholder:text-gray-500 focus:border-emerald-400/50 focus:outline-none focus:ring-2 focus:ring-emerald-400/10"
              placeholder="model-a&#10;model-b"
              disabled={loading}
            />
          </Field>
          <Field
            label="默认模型"
            hint={invalidDefault ? '默认模型必须同时存在于可用模型列表。' : '用于连接测试与默认回退。'}
            error={invalidDefault}
          >
            <Input
              value={defaultModel}
              onChange={(event) => setDefaultModel(event.target.value)}
              placeholder={models[0] ?? 'model-name'}
              list="provider-model-suggestions"
              disabled={loading}
            />
            <datalist id="provider-model-suggestions">
              {[...models, ...(MODEL_SUGGESTIONS[providerType] ?? [])].map((model) => (
                <option key={model} value={model} />
              ))}
            </datalist>
          </Field>
        </div>

        <div className="flex justify-end gap-2 border-t border-ink-700 pt-4">
          <Button type="button" variant="ghost" onClick={onClose} disabled={loading}>
            取消
          </Button>
          <Button type="submit" variant="primary" disabled={!canSubmit}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : editing ? <Pencil size={14} /> : <Plus size={14} />}
            {editing ? '保存更改' : '添加 Provider'}
          </Button>
        </div>
      </form>
    </ModalFrame>
  )
}

function ModelBindingsTab({
  bindings,
  providers,
  projectId,
}: {
  bindings: ModelBinding[]
  providers: Provider[]
  projectId?: string
}) {
  const queryClient = useQueryClient()
  const [modal, setModal] = useState<{
    binding?: ModelBinding
    presetRole?: AgentRole
  } | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ModelBinding | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const createMutation = useMutation({
    mutationFn: (data: ModelBindingCreateData) => governanceApi.createBinding(data),
    onSuccess: () => {
      invalidateGovernance(queryClient, projectId)
      setModal(null)
      setActionError(null)
    },
    onError: (error) => setActionError(getErrorMessage(error)),
  })
  const updateMutation = useMutation({
    mutationFn: ({ bindingId, data }: BindingMutationInput) =>
      governanceApi.updateBinding(bindingId, data),
    onSuccess: () => {
      invalidateGovernance(queryClient, projectId)
      setModal(null)
      setActionError(null)
    },
    onError: (error) => setActionError(getErrorMessage(error)),
  })
  const deleteMutation = useMutation({
    mutationFn: (bindingId: string) => governanceApi.deleteBinding(bindingId),
    onSuccess: () => {
      invalidateGovernance(queryClient, projectId)
      setDeleteTarget(null)
      setActionError(null)
    },
    onError: (error) => setActionError(getErrorMessage(error)),
  })

  const allRoles = Object.values(AgentRole)
  const bindingByRole = useMemo(() => {
    const result = new Map<AgentRole, ModelBinding>()
    bindings.forEach((binding) => {
      if (!binding.agent_role) return

      const isProjectBinding = Boolean(projectId && binding.project_id === projectId)
      const isGlobalBinding = binding.project_id == null
      if (!isProjectBinding && !isGlobalBinding) return

      const current = result.get(binding.agent_role)
      const currentIsProjectBinding = Boolean(
        projectId && current?.project_id === projectId,
      )
      if (!current || (isProjectBinding && !currentIsProjectBinding)) {
        result.set(binding.agent_role, binding)
      }
    })
    return result
  }, [bindings, projectId])
  const providerById = useMemo(
    () => new Map(providers.map((provider) => [provider.id, provider])),
    [providers],
  )
  const busy =
    createMutation.isPending || updateMutation.isPending || deleteMutation.isPending

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-gray-300">
            {projectId ? '当前项目角色路由' : '全局角色路由'}
          </p>
          <p className="mt-0.5 text-xs text-gray-500">
            上下文窗口决定可读入的设定容量；最大输出决定单次生成上限。推理模型需显式标记。
          </p>
        </div>
        <Button
          variant="primary"
          size="sm"
          onClick={() => {
            setActionError(null)
            setModal({})
          }}
          disabled={!providers.some(isProviderEnabled) || busy}
        >
          <Plus size={14} />
          添加绑定
        </Button>
      </div>

      {actionError && (
        <InlineError message={actionError} onDismiss={() => setActionError(null)} />
      )}

      {!providers.length ? (
        <EmptyState
          icon={<Cpu size={26} />}
          title="请先配置 Provider"
          description="角色绑定需要一个可用 Provider。请先在 Provider 标签页添加并验证连接。"
        />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-ink-700 bg-ink-900/30">
          <table className="min-w-[980px] w-full text-sm">
            <thead className="bg-ink-850 text-xs text-gray-400">
              <tr>
                <th className="px-4 py-3 text-left font-medium">智能体角色</th>
                <th className="px-4 py-3 text-left font-medium">Provider / 模型</th>
                <th className="px-4 py-3 text-left font-medium">上下文</th>
                <th className="px-4 py-3 text-left font-medium">最大输出</th>
                <th className="px-4 py-3 text-left font-medium">能力</th>
                <th className="px-4 py-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-700">
              {allRoles.map((role) => {
                const binding = bindingByRole.get(role)
                const provider = binding ? providerById.get(binding.provider_id) : undefined
                const providerEnabled = provider ? isProviderEnabled(provider) : false
                const rowBusy =
                  updateMutation.variables?.bindingId === binding?.id ||
                  deleteMutation.variables === binding?.id

                return (
                  <tr key={role} className="transition-colors hover:bg-ink-850/70">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-gray-200">{AGENT_ROLES[role]}</span>
                        {binding ? (
                          <Circle
                            size={7}
                            className={providerEnabled ? 'fill-green-400 text-green-400' : 'fill-amber-400 text-amber-400'}
                          />
                        ) : (
                          <Circle size={7} className="fill-gray-600 text-gray-600" />
                        )}
                      </div>
                      <p className="mt-0.5 text-xs text-gray-600">{role}</p>
                    </td>
                    <td className="px-4 py-3">
                      {binding ? (
                        <div>
                          <div className="flex items-center gap-1.5">
                            <span className="text-xs text-gray-400">
                              {binding.provider_name ?? provider?.name ?? binding.provider_id.slice(0, 8)}
                            </span>
                            {!providerEnabled && <Badge variant="amber">Provider 已停用</Badge>}
                          </div>
                          <div className="mt-1 flex items-center gap-1">
                            <Badge variant="outline">{binding.model ?? binding.model_name}</Badge>
                            {binding.is_default && <Badge variant="gold">默认</Badge>}
                          </div>
                        </div>
                      ) : (
                        <span className="text-xs text-gray-600">尚未配置</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {binding ? (
                        <MetricValue icon={<Gauge size={12} />} value={formatTokens(binding.context_window ?? 8192)} />
                      ) : (
                        <span className="text-gray-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {binding ? (
                        <MetricValue icon={<Zap size={12} />} value={formatTokens(binding.max_output_tokens ?? 4096)} />
                      ) : (
                        <span className="text-gray-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {binding ? (
                        binding.capabilities?.is_reasoning ? (
                          <Badge variant="blue"><BrainCircuit size={11} /> 推理模型</Badge>
                        ) : (
                          <Badge variant="outline">通用模型</Badge>
                        )
                      ) : (
                        <span className="text-gray-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-1">
                        {binding ? (
                          <>
                            <ActionButton
                              label="编辑"
                              icon={rowBusy && updateMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Pencil size={13} />}
                              onClick={() => {
                                setActionError(null)
                                setModal({ binding })
                              }}
                              disabled={busy}
                            />
                            <ActionButton
                              label="删除"
                              icon={rowBusy && deleteMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                              onClick={() => {
                                setActionError(null)
                                setDeleteTarget(binding)
                              }}
                              disabled={busy}
                              tone="danger"
                            />
                          </>
                        ) : (
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => {
                              setActionError(null)
                              setModal({ presetRole: role })
                            }}
                            disabled={busy || !providers.some(isProviderEnabled)}
                          >
                            <Plus size={13} />
                            配置
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {modal && (
        <BindingModal
          binding={modal.binding}
          presetRole={modal.presetRole}
          bindings={bindings}
          providers={providers}
          projectId={projectId}
          loading={createMutation.isPending || updateMutation.isPending}
          error={actionError}
          onClose={() => {
            if (!createMutation.isPending && !updateMutation.isPending) setModal(null)
          }}
          onSubmit={(data) => {
            setActionError(null)
            if (modal.binding) {
              updateMutation.mutate({
                bindingId: modal.binding.id,
                data: data as ModelBindingUpdateData,
              })
            } else {
              createMutation.mutate(data as ModelBindingCreateData)
            }
          }}
        />
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="删除角色绑定"
          message="删除后该智能体将失去项目级模型路由；在重新配置前，连续写作可能无法执行该角色任务。"
          confirmLabel="确认删除绑定"
          danger
          loading={deleteMutation.isPending}
          error={actionError}
          onCancel={() => {
            if (!deleteMutation.isPending) setDeleteTarget(null)
          }}
          onConfirm={() => {
            setActionError(null)
            deleteMutation.mutate(deleteTarget.id)
          }}
        >
          <div className="rounded-lg border border-ink-700 bg-ink-950/70 px-3 py-2">
            <p className="text-sm text-gray-200">
              {deleteTarget.agent_role ? AGENT_ROLES[deleteTarget.agent_role] : '未指定角色'}
            </p>
            <p className="mt-0.5 text-xs text-gray-500">
              {deleteTarget.provider_name ?? deleteTarget.provider_id.slice(0, 8)} · {deleteTarget.model}
            </p>
          </div>
        </ConfirmDialog>
      )}
    </section>
  )
}

function BindingModal({
  binding,
  presetRole,
  bindings,
  providers,
  projectId,
  loading,
  error,
  onClose,
  onSubmit,
}: {
  binding?: ModelBinding
  presetRole?: AgentRole
  bindings: ModelBinding[]
  providers: Provider[]
  projectId?: string
  loading: boolean
  error?: string | null
  onClose: () => void
  onSubmit: (data: ModelBindingCreateData | ModelBindingUpdateData) => void
}) {
  const editing = Boolean(binding)
  const activeProviders = providers.filter(isProviderEnabled)
  const initialProvider =
    providers.find((provider) => provider.id === binding?.provider_id) ??
    activeProviders[0] ??
    providers[0]
  const [agentRole, setAgentRole] = useState<AgentRole>(
    binding?.agent_role ?? presetRole ?? AgentRole.Drafter,
  )
  const [providerId, setProviderId] = useState(initialProvider?.id ?? '')
  const selectedProvider = providers.find((provider) => provider.id === providerId)
  const [model, setModel] = useState(
    binding?.model ??
      binding?.model_name ??
      initialProvider?.default_model ??
      initialProvider?.models?.[0] ??
      '',
  )
  const [contextWindow, setContextWindow] = useState(
    String(binding?.context_window ?? 32768),
  )
  const [maxOutputTokens, setMaxOutputTokens] = useState(
    String(binding?.max_output_tokens ?? 8192),
  )
  const [isReasoning, setIsReasoning] = useState(
    Boolean(binding?.capabilities?.is_reasoning),
  )
  const [isDefault, setIsDefault] = useState(binding?.is_default ?? true)

  useEffect(() => {
    if (!selectedProvider || model.trim()) return
    setModel(selectedProvider.default_model ?? selectedProvider.models?.[0] ?? '')
  }, [model, selectedProvider])

  const contextValue = Number(contextWindow)
  const outputValue = Number(maxOutputTokens)
  const targetScopeProjectId = binding
    ? (binding.project_id ?? null)
    : (projectId ?? null)
  const conflictsInTargetScope = (item: ModelBinding, role: AgentRole) =>
    item.id !== binding?.id &&
    item.agent_role === role &&
    (item.project_id ?? null) === targetScopeProjectId
  const tokenLimitsValid =
    Number.isInteger(contextValue) &&
    contextValue >= 1024 &&
    Number.isInteger(outputValue) &&
    outputValue >= 256 &&
    outputValue <= contextValue
  const roleConflict = bindings.some((item) => conflictsInTargetScope(item, agentRole))
  const providerEnabled = selectedProvider ? isProviderEnabled(selectedProvider) : false
  const canSubmit =
    Boolean(providerId && model.trim()) &&
    tokenLimitsValid &&
    !roleConflict &&
    providerEnabled &&
    !loading

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!canSubmit) return
    const common = {
      project_id: targetScopeProjectId,
      agent_role: agentRole,
      provider_id: providerId,
      model: model.trim(),
      context_window: contextValue,
      max_output_tokens: outputValue,
      is_default: isDefault,
      capabilities: {
        ...(binding?.capabilities ?? {}),
        is_reasoning: isReasoning,
      },
    }
    onSubmit(common)
  }

  return (
    <ModalFrame
      title={editing ? '编辑角色绑定' : '添加角色绑定'}
      subtitle="为单个智能体设置模型路由和生成容量。更改会影响后续请求，不会改写已生成内容。"
      onClose={onClose}
      closeDisabled={loading}
    >
      {error && <InlineError message={error} />}
      <form onSubmit={submit} className="mt-4 space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="智能体角色"
            required
            hint={roleConflict ? '该角色已经存在绑定，请直接编辑已有项。' : undefined}
            error={roleConflict}
          >
            <select
              value={agentRole}
              onChange={(event) => setAgentRole(event.target.value as AgentRole)}
              className={SELECT_CLASS}
              disabled={loading}
            >
              {Object.values(AgentRole).map((role) => (
                <option
                  key={role}
                  value={role}
                  disabled={bindings.some((item) => conflictsInTargetScope(item, role))}
                >
                  {AGENT_ROLES[role]}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Provider"
            required
            hint={!providerEnabled ? '该 Provider 已停用，不能用于生产绑定。' : undefined}
            error={!providerEnabled}
          >
            <select
              value={providerId}
              onChange={(event) => {
                const nextId = event.target.value
                const nextProvider = providers.find((provider) => provider.id === nextId)
                setProviderId(nextId)
                setModel(nextProvider?.default_model ?? nextProvider?.models?.[0] ?? '')
              }}
              className={SELECT_CLASS}
              disabled={loading}
            >
              {providers.map((provider) => (
                <option
                  key={provider.id}
                  value={provider.id}
                  disabled={!isProviderEnabled(provider)}
                >
                  {provider.name}{isProviderEnabled(provider) ? '' : '（已停用）'}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="模型" required hint="可从 Provider 模型目录选择，也可输入兼容模型标识。">
          <Input
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="model-name"
            list="binding-model-suggestions"
            disabled={loading}
          />
          <datalist id="binding-model-suggestions">
            {(selectedProvider?.models ?? []).map((item) => (
              <option key={item} value={item} />
            ))}
          </datalist>
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="上下文窗口" required hint="最小 1,024 tokens。">
            <Input
              type="number"
              min={1024}
              step={1024}
              value={contextWindow}
              onChange={(event) => setContextWindow(event.target.value)}
              disabled={loading}
            />
          </Field>
          <Field
            label="最大输出"
            required
            hint={outputValue > contextValue ? '最大输出不能大于上下文窗口。' : '最小 256 tokens。'}
            error={!tokenLimitsValid}
          >
            <Input
              type="number"
              min={256}
              step={256}
              value={maxOutputTokens}
              onChange={(event) => setMaxOutputTokens(event.target.value)}
              disabled={loading}
            />
          </Field>
        </div>

        <div className="grid gap-3 rounded-xl border border-ink-700 bg-ink-950/60 p-3 sm:grid-cols-2">
          <ToggleOption
            checked={isReasoning}
            onChange={setIsReasoning}
            disabled={loading}
            icon={<BrainCircuit size={16} />}
            title="推理模型"
            description="允许读取 reasoning_content，并启用更长的超时策略。"
          />
          <ToggleOption
            checked={isDefault}
            onChange={setIsDefault}
            disabled={loading}
            icon={<Shield size={16} />}
            title="默认路由"
            description="将此绑定作为该角色在当前项目中的首选模型。"
          />
        </div>

        <div className="rounded-lg border border-blue-500/20 bg-blue-500/5 px-3 py-2 text-xs leading-5 text-blue-200/80">
          配置作用域：{projectId ? '当前项目' : '全局'}。保存后仅影响新的模型请求。
        </div>

        <div className="flex justify-end gap-2 border-t border-ink-700 pt-4">
          <Button type="button" variant="ghost" onClick={onClose} disabled={loading}>
            取消
          </Button>
          <Button type="submit" variant="primary" disabled={!canSubmit}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : editing ? <Pencil size={14} /> : <Plus size={14} />}
            {editing ? '保存绑定' : '添加绑定'}
          </Button>
        </div>
      </form>
    </ModalFrame>
  )
}

function ToggleOption({
  checked,
  onChange,
  disabled,
  icon,
  title,
  description,
}: {
  checked: boolean
  onChange: (value: boolean) => void
  disabled: boolean
  icon: React.ReactNode
  title: string
  description: string
}) {
  return (
    <label className="flex cursor-pointer gap-3 rounded-lg p-2 transition-colors hover:bg-ink-800">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        disabled={disabled}
        className="mt-1 h-4 w-4 rounded border-ink-600 bg-ink-900 accent-emerald-400"
      />
      <span className="min-w-0">
        <span className="flex items-center gap-1.5 text-sm text-gray-200">
          <span className="text-emerald-300">{icon}</span>
          {title}
        </span>
        <span className="mt-0.5 block text-xs leading-5 text-gray-500">{description}</span>
      </span>
    </label>
  )
}

function ModalFrame({
  title,
  subtitle,
  onClose,
  closeDisabled,
  children,
}: {
  title: string
  subtitle?: string
  onClose: () => void
  closeDisabled?: boolean
  children: React.ReactNode
}) {
  const dialogRef = useDialogFocus<HTMLDivElement>(true, () => {
    if (!closeDisabled) onClose()
  })
  return (
    <div
      ref={dialogRef}
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      tabIndex={-1}
    >
      <div className="my-auto w-full max-w-2xl rounded-2xl border border-ink-600 bg-ink-850 p-5 shadow-2xl sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-medium text-gray-100">{title}</h2>
            {subtitle && <p className="mt-1 text-xs leading-5 text-gray-500">{subtitle}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={closeDisabled}
            data-dialog-initial-focus
            aria-label="关闭"
            className="rounded-lg p-1 text-gray-500 transition-colors hover:bg-ink-700 hover:text-gray-200 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

function ConfirmDialog({
  title,
  message,
  confirmLabel,
  danger = false,
  loading,
  error,
  onCancel,
  onConfirm,
  children,
}: {
  title: string
  message: string
  confirmLabel: string
  danger?: boolean
  loading: boolean
  error?: string | null
  onCancel: () => void
  onConfirm: () => void
  children?: React.ReactNode
}) {
  const dialogRef = useDialogFocus<HTMLDivElement>(true, () => {
    if (!loading) onCancel()
  })
  return (
    <div
      ref={dialogRef}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm"
      role="alertdialog"
      aria-modal="true"
      aria-label={title}
      tabIndex={-1}
    >
      <div className="w-full max-w-md rounded-2xl border border-ink-600 bg-ink-850 p-5 shadow-2xl">
        <div className="flex gap-3">
          <div className={danger ? 'text-red-300' : 'text-amber-300'}>
            <AlertTriangle size={20} />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-medium text-gray-100">{title}</h2>
            <p className="mt-1 text-xs leading-5 text-gray-400">{message}</p>
          </div>
        </div>
        {children && <div className="mt-4">{children}</div>}
        {error && <div className="mt-4"><InlineError message={error} /></div>}
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onCancel} disabled={loading} data-dialog-initial-focus>
            取消
          </Button>
          <Button
            type="button"
            variant={danger ? 'danger' : 'primary'}
            onClick={onConfirm}
            disabled={loading}
          >
            {loading && <Loader2 size={14} className="animate-spin" />}
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

function Field({
  label,
  required,
  hint,
  error = false,
  children,
}: {
  label: string
  required?: boolean
  hint?: string
  error?: boolean
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-gray-400">
        {label}{required && <span className="ml-1 text-emerald-400">*</span>}
      </span>
      {children}
      {hint && (
        <span className={'mt-1 block text-[11px] leading-4 ' + (error ? 'text-red-300' : 'text-gray-600')}>
          {hint}
        </span>
      )}
    </label>
  )
}

function InlineError({
  message,
  actionLabel,
  onAction,
  onDismiss,
}: {
  message: string
  actionLabel?: string
  onAction?: () => void
  onDismiss?: () => void
}) {
  return (
    <div className="mb-4 flex items-start gap-2 rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-200">
      <AlertCircle size={14} className="mt-0.5 shrink-0" />
      <span className="min-w-0 flex-1 leading-5">{message}</span>
      {actionLabel && onAction && (
        <button type="button" onClick={onAction} className="shrink-0 font-medium text-red-100 underline">
          {actionLabel}
        </button>
      )}
      {onDismiss && (
        <button type="button" onClick={onDismiss} aria-label="关闭错误提示" className="shrink-0 text-red-300 hover:text-red-100">
          <X size={14} />
        </button>
      )}
    </div>
  )
}

function ActionButton({
  label,
  icon,
  onClick,
  disabled,
  tone = 'normal',
}: {
  label: string
  icon: React.ReactNode
  onClick: () => void
  disabled?: boolean
  tone?: 'normal' | 'warning' | 'danger'
}) {
  const toneClass =
    tone === 'danger'
      ? 'text-red-300 hover:bg-red-500/10 hover:text-red-200'
      : tone === 'warning'
        ? 'text-amber-300 hover:bg-amber-500/10 hover:text-amber-200'
        : 'text-gray-400 hover:bg-ink-700 hover:text-gray-200'
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className={
        'inline-flex h-8 w-8 items-center justify-center rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-35 ' +
        toneClass
      }
    >
      {icon}
    </button>
  )
}

function MetricValue({ icon, value }: { icon: React.ReactNode; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-gray-400">
      <span className="text-gray-600">{icon}</span>
      {value}
    </span>
  )
}

function formatTokens(value: number): string {
  if (value >= 1000) {
    const rounded = value % 1000 === 0 ? String(value / 1000) : (value / 1000).toFixed(1)
    return rounded + 'k'
  }
  return String(value)
}
