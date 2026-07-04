import React, { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  TrendingUp,
  FlaskConical,
  Wand2,
  FileBarChart,
  Play,
  Loader2,
  Trophy,
  AlertTriangle,
  Lightbulb,
} from 'lucide-react'
import { TopBar } from '../layout/TopBar'
import { BossCommandBar } from '../layout/BossCommandBar'
import { AppShell, AppShellBody } from '../layout/AppShell'
import { evolutionApi, governanceApi } from '../api/client'
import { useProjectStore } from '../store/projectStore'
import { Tabs, Card, Button, TextArea } from '../components/ui'
import { Badge } from '../components/Badge'
import { EmptyState } from '../components/EmptyState'
import { cn } from '../lib/cn'
import type { PromptExperimentResult, SkillTestResult, LearningReport } from '../types'

type TabKey = 'prompts' | 'skills' | 'reports'

const SKILL_OPTIONS = [
  'drafting',
  'continuity_check',
  'character_extraction',
  'summary_generation',
  'style_analysis',
  'plot_planning',
]

/**
 * EvolutionPage —— 进化
 * Tab 切换：Prompt 实验 / 技能实验 / 学习报告
 */
export default function EvolutionPage() {
  const project = useProjectStore((s) => s.currentProject)
  const projectId = project?.id ?? ''
  const [tab, setTab] = useState<TabKey>('prompts')

  const { data: providers } = useQuery({
    queryKey: ['providers'],
    queryFn: governanceApi.listProviders,
  })

  const providerStatus = (providers?.length ?? 0) > 0 ? 'online' : 'offline'

  return (
    <AppShell>
      <TopBar providerStatus={providerStatus as 'online' | 'offline'} />
      <AppShellBody className="flex-col">
        <div className="border-b border-ink-700 px-6 py-3">
          <h1 className="flex items-center gap-2 text-base font-medium text-gray-200">
            <TrendingUp size={18} className="text-gold-500" />
            进化
          </h1>
          <p className="mt-0.5 text-xs text-gray-500">提示词与技能的自我优化实验</p>
        </div>

        <div className="border-b border-ink-700 px-6">
          <Tabs
            active={tab}
            onChange={(k) => setTab(k as TabKey)}
            items={[
              {
                key: 'prompts',
                label: (
                  <span className="flex items-center gap-1">
                    <Wand2 size={13} /> Prompt 实验
                  </span>
                ),
              },
              {
                key: 'skills',
                label: (
                  <span className="flex items-center gap-1">
                    <FlaskConical size={13} /> 技能实验
                  </span>
                ),
              },
              {
                key: 'reports',
                label: (
                  <span className="flex items-center gap-1">
                    <FileBarChart size={13} /> 学习报告
                  </span>
                ),
              },
            ]}
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          {tab === 'prompts' && <PromptExperimentTab projectId={projectId} />}
          {tab === 'skills' && <SkillTestTab projectId={projectId} />}
          {tab === 'reports' && <LearningReportTab projectId={projectId} />}
        </div>
      </AppShellBody>
      <BossCommandBar />
    </AppShell>
  )
}

/* ============================================================
 * Prompt 实验 Tab
 * ============================================================ */
function PromptExperimentTab({ projectId }: { projectId: string }) {
  const [promptA, setPromptA] = useState('')
  const [promptB, setPromptB] = useState('')
  const [testInput, setTestInput] = useState('')
  const [result, setResult] = useState<PromptExperimentResult | null>(null)

  const mutation = useMutation({
    mutationFn: () =>
      evolutionApi.promptExperiment(projectId, {
        prompt_a: promptA,
        prompt_b: promptB,
        test_input: testInput,
      }),
    onSuccess: (data) => setResult(data),
  })

  const canRun = promptA.trim() && promptB.trim() && testInput.trim() && !!projectId

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <Card>
        <h3 className="mb-3 text-sm font-medium text-gray-200">Prompt A/B 测试</h3>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <label className="mb-1.5 flex items-center gap-1.5 text-xs text-gray-400">
              <Badge variant="blue">A</Badge> Prompt A
            </label>
            <TextArea
              value={promptA}
              onChange={(e) => setPromptA(e.target.value)}
              rows={5}
              placeholder="输入 Prompt A…"
            />
          </div>
          <div>
            <label className="mb-1.5 flex items-center gap-1.5 text-xs text-gray-400">
              <Badge variant="amber">B</Badge> Prompt B
            </label>
            <TextArea
              value={promptB}
              onChange={(e) => setPromptB(e.target.value)}
              rows={5}
              placeholder="输入 Prompt B…"
            />
          </div>
        </div>
        <div className="mt-3">
          <label className="mb-1.5 block text-xs text-gray-400">测试输入</label>
          <TextArea
            value={testInput}
            onChange={(e) => setTestInput(e.target.value)}
            rows={2}
            placeholder="输入用于测试两个 Prompt 的相同输入…"
          />
        </div>
        <div className="mt-3 flex items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            onClick={() => mutation.mutate()}
            disabled={!canRun || mutation.isPending}
          >
            {mutation.isPending ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Play size={13} />
            )}
            运行实验
          </Button>
          {mutation.isError && (
            <span className="text-xs text-red-400">{(mutation.error as Error).message}</span>
          )}
        </div>
      </Card>

      {/* 结果展示 */}
      {result && (
        <Card>
          <div className="mb-3 flex items-center gap-2">
            <Trophy size={16} className="text-gold-500" />
            <h3 className="text-sm font-medium text-gray-200">实验结果</h3>
            {result.winner && (
              <Badge variant="gold">
                胜者: {result.winner === 'tie' ? '平局' : `Prompt ${result.winner}`}
              </Badge>
            )}
          </div>

          {/* 分数对比 */}
          {result.scores && (
            <div className="mb-4 grid grid-cols-2 gap-3">
              <ScoreBar label="Prompt A" score={result.scores.a} color="blue" />
              <ScoreBar label="Prompt B" score={result.scores.b} color="amber" />
            </div>
          )}

          {/* 输出对比 */}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {result.output_a && (
              <div>
                <p className="mb-1 text-xs text-gray-400">Prompt A 输出</p>
                <div className="rounded-md border border-ink-700 bg-ink-900 p-2 text-xs text-gray-400">
                  {result.output_a}
                </div>
              </div>
            )}
            {result.output_b && (
              <div>
                <p className="mb-1 text-xs text-gray-400">Prompt B 输出</p>
                <div className="rounded-md border border-ink-700 bg-ink-900 p-2 text-xs text-gray-400">
                  {result.output_b}
                </div>
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  )
}

function ScoreBar({
  label,
  score,
  color,
}: {
  label: string
  score?: number
  color: 'blue' | 'amber'
}) {
  const pct = score != null ? Math.min(100, Math.max(0, score * 100)) : 0
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-gray-400">{label}</span>
        <span className="font-medium text-gray-300">
          {score != null ? score.toFixed(2) : '—'}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-ink-700">
        <div
          className={cn(
            'h-full rounded-full transition-all',
            color === 'blue' ? 'bg-blue-500' : 'bg-amber-500',
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

/* ============================================================
 * 技能实验 Tab
 * ============================================================ */
function SkillTestTab({ projectId }: { projectId: string }) {
  const [skillName, setSkillName] = useState(SKILL_OPTIONS[0])
  const [testCasesText, setTestCasesText] = useState('')
  const [result, setResult] = useState<SkillTestResult | null>(null)

  const mutation = useMutation({
    mutationFn: () => {
      const test_cases = testCasesText
        .split(/\n+/)
        .filter(Boolean)
        .map((line) => {
          const [input, expected] = line.split('||').map((s) => s.trim())
          return { input: input || line, expected }
        })
      return evolutionApi.skillTest(projectId, { skill_name: skillName, test_cases })
    },
    onSuccess: (data) => setResult(data),
  })

  const canRun = skillName && testCasesText.trim() && !!projectId

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <Card>
        <h3 className="mb-3 text-sm font-medium text-gray-200">技能测试</h3>
        <div className="grid grid-cols-1 gap-3">
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">选择技能</label>
            <select
              value={skillName}
              onChange={(e) => setSkillName(e.target.value)}
              className="h-9 w-full rounded-md border border-ink-600 bg-ink-900 px-3 text-sm text-gray-200 focus:border-gold-500/60 focus:outline-none focus:ring-1 focus:ring-gold-500/30"
            >
              {SKILL_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">
              测试用例（每行一个，可用 || 分隔输入和期望输出）
            </label>
            <TextArea
              value={testCasesText}
              onChange={(e) => setTestCasesText(e.target.value)}
              rows={5}
              placeholder={'示例:\n写一段雨天场景 || 包含雨声描写\n写一段战斗场景'}
            />
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={() => mutation.mutate()}
              disabled={!canRun || mutation.isPending}
            >
              {mutation.isPending ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Play size={13} />
              )}
              运行测试
            </Button>
            {mutation.isError && (
              <span className="text-xs text-red-400">{(mutation.error as Error).message}</span>
            )}
          </div>
        </div>
      </Card>

      {/* 结果展示 */}
      {result && (
        <Card>
          <div className="mb-3 flex items-center gap-2">
            <FlaskConical size={16} className="text-gold-500" />
            <h3 className="text-sm font-medium text-gray-200">
              测试结果: {result.skill_name ?? skillName}
            </h3>
            {result.pass_rate != null && (
              <Badge variant={result.pass_rate >= 0.8 ? 'green' : result.pass_rate >= 0.5 ? 'amber' : 'red'}>
                通过率: {Math.round(result.pass_rate * 100)}%
              </Badge>
            )}
          </div>

          {result.results && result.results.length > 0 && (
            <div className="space-y-2">
              {result.results.map((r, i) => (
                <div
                  key={i}
                  className={cn(
                    'rounded-md border px-3 py-2 text-xs',
                    r.passed
                      ? 'border-green-600/30 bg-green-600/5 text-gray-400'
                      : 'border-red-600/30 bg-red-600/5 text-gray-400',
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        'flex h-4 w-4 items-center justify-center rounded-full text-[10px]',
                        r.passed ? 'bg-green-600/20 text-green-400' : 'bg-red-600/20 text-red-400',
                      )}
                    >
                      {r.passed ? '✓' : '✗'}
                    </span>
                    <span className="truncate">{r.case}</span>
                  </div>
                  {r.output && (
                    <p className="mt-1 ml-6 text-[10px] text-gray-600">{r.output}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  )
}

/* ============================================================
 * 学习报告 Tab
 * ============================================================ */
function LearningReportTab({ projectId }: { projectId: string }) {
  const { data: report, isLoading } = useQuery({
    queryKey: ['learning-report', projectId],
    queryFn: () => evolutionApi.learningReport(projectId),
    enabled: !!projectId,
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-gray-500">
        <Loader2 className="animate-spin" size={20} />
      </div>
    )
  }

  if (!report) {
    return (
      <EmptyState
        icon={<FileBarChart size={26} />}
        title="暂无学习报告"
        description="记忆管家将定期总结进化成果并生成报告。通过指令栏触发反思。"
      />
    )
  }

  const trend = report.quality_trend ?? []
  const maxScore = trend.length > 0 ? Math.max(...trend.map((t) => t.score)) : 1

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      {/* 质量趋势图 */}
      {trend.length > 0 && (
        <Card>
          <h3 className="mb-4 flex items-center gap-1.5 text-sm font-medium text-gray-200">
            <TrendingUp size={15} className="text-gold-500" />
            质量趋势
          </h3>
          <div className="flex items-end gap-2" style={{ height: '160px' }}>
            {trend.map((t, i) => {
              const heightPct = (t.score / maxScore) * 100
              return (
                <div
                  key={i}
                  className="flex flex-1 flex-col items-center justify-end"
                  title={`第 ${t.chapter} 章: ${t.score.toFixed(2)}`}
                >
                  <span className="mb-1 text-[10px] text-gray-500">{t.score.toFixed(1)}</span>
                  <div
                    className={cn(
                      'w-full rounded-t transition-all',
                      t.score >= 0.8
                        ? 'bg-green-600/60'
                        : t.score >= 0.5
                          ? 'bg-amber-600/60'
                          : 'bg-red-600/60',
                    )}
                    style={{ height: `${heightPct}%`, minHeight: '4px' }}
                  />
                  <span className="mt-1 text-[10px] text-gray-600">第{t.chapter}章</span>
                </div>
              )
            })}
          </div>
        </Card>
      )}

      {/* 常见问题 */}
      {report.common_issues && report.common_issues.length > 0 && (
        <Card>
          <h3 className="mb-3 flex items-center gap-1.5 text-sm font-medium text-gray-200">
            <AlertTriangle size={15} className="text-amber-500" />
            常见问题
          </h3>
          <ul className="space-y-1.5">
            {report.common_issues.map((issue, i) => (
              <li
                key={i}
                className="flex items-start gap-2 rounded-md border-l-2 border-amber-500/50 bg-amber-500/5 px-3 py-1.5 text-xs text-gray-400"
              >
                <span className="text-amber-500">•</span>
                {issue}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* 改进建议 */}
      {report.suggestions && report.suggestions.length > 0 && (
        <Card>
          <h3 className="mb-3 flex items-center gap-1.5 text-sm font-medium text-gray-200">
            <Lightbulb size={15} className="text-gold-500" />
            改进建议
          </h3>
          <ul className="space-y-1.5">
            {report.suggestions.map((s, i) => (
              <li
                key={i}
                className="flex items-start gap-2 rounded-md border-l-2 border-gold-500/50 bg-gold-500/5 px-3 py-1.5 text-xs text-gray-400"
              >
                <span className="text-gold-500">•</span>
                {s}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* 报告摘要 */}
      {(report.summary || report.content) && (
        <Card>
          <h3 className="mb-2 text-sm font-medium text-gray-200">报告摘要</h3>
          <p className="text-xs leading-relaxed text-gray-500">
            {report.summary || report.content}
          </p>
          {report.created_at && (
            <p className="mt-2 text-[10px] text-gray-600">
              生成时间: {new Date(report.created_at).toLocaleString()}
            </p>
          )}
        </Card>
      )}
    </div>
  )
}
