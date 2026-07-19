import { describe, expect, it } from 'vitest'
import { getProjectDisplayTitle } from './projectDisplayTitle'

describe('getProjectDisplayTitle', () => {
  const projectId = '3e67cf8e-b30d-4d6d-b3ea-3eff88f9a4f1'
  const fallback = '未命名项目 · 3e67cf8e'

  it.each([
    '',
    '   ',
    '????????',
    '？？？？？？',
    '﹖﹖﹖',
    '人间�',
    '锟斤拷锟斤拷',
    'äººé—´ç§',
    `broken${String.fromCharCode(0x0081)}title`,
  ])('replaces a high-confidence corrupt title: %j', (title) => {
    expect(getProjectDisplayTitle(title, projectId)).toBe(fallback)
  })

  it.each([
    '《人间种》正式验收',
    'The Last Archive',
    '人间种 · 24H production acceptance',
    'Café No. 7',
    'Résumé Café',
  ])('preserves a legitimate title verbatim: %s', (title) => {
    expect(getProjectDisplayTitle(title, projectId)).toBe(title)
  })

  it('keeps the fallback identifiable when the project id is short or absent', () => {
    expect(getProjectDisplayTitle('????', 'abc-123')).toBe('未命名项目 · abc')
    expect(getProjectDisplayTitle(undefined, undefined)).toBe('未命名项目 · 未知ID')
  })
})
