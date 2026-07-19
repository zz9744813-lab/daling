import { describe, expect, it } from 'vitest'
import type { ContinuousStatus } from '../../types'
import { contractFromStatus } from './AutopilotControlCenter'

function statusWith(overrides: Record<string, unknown>): ContinuousStatus {
  return {
    target_chapters: null,
    remaining_chapters: 0,
    autonomy_level: 'L3',
    ...overrides,
  } as unknown as ContinuousStatus
}

describe('contractFromStatus', () => {
  it('does not hydrate a stopped legacy run into an empty target or zero correction contract', () => {
    const contract = contractFromStatus(statusWith({
      policy: {
        minimum_rewrite_cycles: 0,
        max_rewrite_rounds: 0,
      },
    }))

    expect(contract.target_chapters).toBe(1)
    expect(contract.minimum_rewrite_cycles).toBe(1)
    expect(contract.max_rewrite_rounds).toBe(1)
  })

  it('uses the remaining chapter count as the next bounded production target', () => {
    const contract = contractFromStatus(statusWith({ remaining_chapters: 12 }))

    expect(contract.target_chapters).toBe(12)
    expect(contract.minimum_rewrite_cycles).toBeGreaterThanOrEqual(1)
  })

  it('keeps the correction minimum achievable inside the selected target', () => {
    const contract = contractFromStatus(statusWith({
      target_chapters: 3,
      policy: {
        minimum_rewrite_cycles: 9,
        max_rewrite_rounds: 2,
      },
    }))

    expect(contract.target_chapters).toBe(3)
    expect(contract.minimum_rewrite_cycles).toBe(3)
  })
})
