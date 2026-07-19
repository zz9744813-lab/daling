import type { OutlineInspection } from '../types'

/**
 * HTML number-input bounds are advisory: users, restored drafts, and AI updates
 * can still place out-of-range values in state. Clamp again before state or API use.
 */
export function clampInteger(
  value: unknown,
  min: number,
  max: number,
  fallback = min,
): number {
  const numeric = Number(value)
  const normalized = Number.isFinite(numeric) ? Math.round(numeric) : fallback
  return Math.min(max, Math.max(min, normalized))
}

/**
 * A successful HTTP response is not sufficient evidence that an outline can be
 * trusted. Fail closed unless the backend explicitly proves full source coverage.
 */
export function getOutlineInspectionIssue(
  inspection: OutlineInspection | null | undefined,
): string | null {
  if (!inspection) return '大纲尚未成功解析，请重新选择文件。'
  if (!inspection.ok) return '大纲解析结果未通过校验，请更换文件或重新解析。'
  if (inspection.exact_source_covered !== true) {
    return '大纲预检未确认全文索引完整覆盖，请更换文件或重新解析。'
  }
  return null
}

export function isOutlineInspectionAccepted(
  inspection: OutlineInspection | null | undefined,
): boolean {
  return getOutlineInspectionIssue(inspection) == null
}

/**
 * Cost rows are derived from configured provider rates. A persisted zero after
 * real requests is therefore "unknown rate", not evidence that the calls were
 * free. Limits pass no request count and are formatted as explicit amounts.
 */
export function formatTrackedCost(
  value: number | null | undefined,
  requestCount?: number | null,
): string {
  if (value == null || !Number.isFinite(value)) return '—'
  if (requestCount != null && requestCount <= 0) return '尚无调用'
  if (requestCount != null && requestCount > 0 && value === 0) return '费率未配置'
  return `$${value.toFixed(value >= 1 ? 2 : 5)}`
}
