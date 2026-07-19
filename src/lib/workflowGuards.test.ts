import { describe, expect, it } from 'vitest'
import type { OutlineInspection } from '../types'
import {
  clampInteger,
  formatTrackedCost,
  getOutlineInspectionIssue,
  isOutlineInspectionAccepted,
} from './workflowGuards'

function inspection(
  overrides: Partial<OutlineInspection> = {},
): OutlineInspection {
  return {
    ok: true,
    filename: 'outline.docx',
    extension: '.docx',
    size_bytes: 1024,
    char_count: 1200,
    line_count: 80,
    chapter_heading_count: 10,
    volume_heading_count: 1,
    chapter_headings: [],
    volume_headings: [],
    preview: 'preview',
    text: 'full outline',
    exact_source_covered: true,
    ...overrides,
  }
}

describe('clampInteger', () => {
  it('enforces both bounds instead of relying on HTML input attributes', () => {
    expect(clampInteger(-4, 1, 5000)).toBe(1)
    expect(clampInteger(999_999, 1, 5000)).toBe(5000)
  })

  it('rounds finite values and uses the requested fallback for invalid input', () => {
    expect(clampInteger(12.6, 1, 100)).toBe(13)
    expect(clampInteger('not-a-number', 1, 100, 20)).toBe(20)
  })
})

describe('tracked model cost', () => {
  it('does not misreport real requests with unconfigured rates as free', () => {
    expect(formatTrackedCost(0, 12)).toBe('费率未配置')
  })

  it('distinguishes no calls and formats explicit configured amounts', () => {
    expect(formatTrackedCost(0, 0)).toBe('尚无调用')
    expect(formatTrackedCost(1.25, 3)).toBe('$1.25')
    expect(formatTrackedCost(50)).toBe('$50.00')
  })
})

describe('outline inspection evidence guard', () => {
  it('accepts a verified lossless inspection', () => {
    expect(isOutlineInspectionAccepted(inspection())).toBe(true)
    expect(getOutlineInspectionIssue(inspection())).toBeNull()
  })

  it('rejects responses that omit the source-coverage proof', () => {
    const unproven = inspection({ exact_source_covered: undefined })
    expect(isOutlineInspectionAccepted(unproven)).toBe(false)
    expect(getOutlineInspectionIssue(unproven)).toContain('完整覆盖')
  })

  it('rejects an explicit parser failure', () => {
    const failed = inspection({ ok: false })
    expect(isOutlineInspectionAccepted(failed)).toBe(false)
    expect(getOutlineInspectionIssue(failed)).toContain('未通过校验')
  })

  it('rejects incomplete source coverage even when parsing returned HTTP success', () => {
    const incomplete = inspection({ exact_source_covered: false })
    expect(isOutlineInspectionAccepted(incomplete)).toBe(false)
    expect(getOutlineInspectionIssue(incomplete)).toContain('完整覆盖')
  })

  it('does not treat a missing inspection as evidence', () => {
    expect(isOutlineInspectionAccepted(null)).toBe(false)
    expect(getOutlineInspectionIssue(null)).toContain('尚未成功解析')
  })
})
