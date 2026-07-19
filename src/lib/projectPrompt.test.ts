import { describe, expect, it } from 'vitest'
import {
  PROJECT_PROMPT_PRESETS,
  hasProjectPromptPreset,
  mergeProjectPrompt,
} from './projectPrompt'

describe('project prompt contracts', () => {
  it('appends a selected contract without replacing author instructions', () => {
    const preset = PROJECT_PROMPT_PRESETS[0]
    const merged = mergeProjectPrompt('保持第三人称限制视角。', preset.text)

    expect(merged).toContain('保持第三人称限制视角。')
    expect(merged).toContain(preset.text)
    expect(hasProjectPromptPreset(merged, preset)).toBe(true)
  })

  it('is idempotent when the same contract is selected more than once', () => {
    const preset = PROJECT_PROMPT_PRESETS[3]
    const once = mergeProjectPrompt('', preset.text)
    const twice = mergeProjectPrompt(once, preset.text)

    expect(twice).toBe(once)
    expect(twice.match(/【质量纠错】/g)).toHaveLength(1)
  })

  it('normalizes windows line endings without collapsing custom content', () => {
    expect(mergeProjectPrompt('第一行\r\n第二行', '')).toBe('第一行\n第二行')
  })
})
