import { describe, expect, it, vi } from 'vitest'
import type { EvidenceListResponse, OutlineEvidenceNode } from '../../types'
import {
  evidenceMatchesSearch,
  fetchAllOutlineNodeEvidence,
  nextEvidenceVisibleCount,
} from './IntelligenceEvidenceConsole'

function outlineNode(ordinal: number): OutlineEvidenceNode {
  return {
    id: `node-${ordinal}`,
    ordinal,
    parent_id: null,
    depth: 0,
    node_type: 'paragraph',
    title: `节点 ${ordinal}`,
    path: ['第一卷', `第 ${ordinal} 章`],
    char_start: ordinal * 10,
    char_end: ordinal * 10 + 9,
    content: `第 ${ordinal} 条完整来源证据`,
    content_hash: `hash-${ordinal}`,
    analysis: {},
    coverage_status: 'indexed',
    coverage: {},
  }
}

function page(
  items: OutlineEvidenceNode[],
  total?: number,
): EvidenceListResponse<OutlineEvidenceNode> {
  return {
    project_id: 'project-1',
    ...(total == null ? {} : { total }),
    items,
  }
}

describe('complete outline evidence pagination', () => {
  it('reads every server page instead of stopping at the first 500 nodes', async () => {
    const nodes = Array.from({ length: 1201 }, (_, index) => outlineNode(index + 1))
    const fetchPage = vi.fn(async (
      _projectId: string,
      params: { offset: number; limit: number },
    ) => page(nodes.slice(params.offset, params.offset + params.limit), nodes.length))

    const result = await fetchAllOutlineNodeEvidence('project-1', fetchPage)

    expect(result.items).toHaveLength(1201)
    expect(result.items[result.items.length - 1]?.id).toBe('node-1201')
    expect(result.total).toBe(1201)
    expect(fetchPage.mock.calls.map(([, params]) => params)).toEqual([
      { offset: 0, limit: 500 },
      { offset: 500, limit: 500 },
      { offset: 1000, limit: 500 },
    ])
  })

  it('continues until a short page when an older server omits total', async () => {
    const nodes = Array.from({ length: 502 }, (_, index) => outlineNode(index + 1))
    const fetchPage = vi.fn(async (
      _projectId: string,
      params: { offset: number; limit: number },
    ) => page(nodes.slice(params.offset, params.offset + params.limit)))

    const result = await fetchAllOutlineNodeEvidence('project-1', fetchPage)

    expect(result.items).toHaveLength(502)
    expect(result.total).toBe(502)
    expect(fetchPage).toHaveBeenCalledTimes(2)
  })

  it('fails closed when the server ends before its declared total', async () => {
    const nodes = Array.from({ length: 500 }, (_, index) => outlineNode(index + 1))
    const fetchPage = vi.fn(async (
      _projectId: string,
      params: { offset: number; limit: number },
    ) => params.offset === 0 ? page(nodes, 501) : page([], 501))

    await expect(fetchAllOutlineNodeEvidence('project-1', fetchPage)).rejects.toThrow('提前结束')
  })

  it('fails closed when a broken offset implementation repeats a node', async () => {
    const nodes = Array.from({ length: 500 }, (_, index) => outlineNode(index + 1))
    const fetchPage = vi.fn(async () => page(nodes, 1000))

    await expect(fetchAllOutlineNodeEvidence('project-1', fetchPage)).rejects.toThrow('被重复返回')
  })
})

describe('evidence console search and progressive rendering', () => {
  it('searches nested Chinese evidence fields with AND semantics', () => {
    const evidence = {
      stage: '场景重写',
      provider: { model: 'stepfun-ai/step-3.7-flash' },
      source_path: ['第三卷', '第十二章'],
    }

    expect(evidenceMatchesSearch('场景 step-3.7', evidence)).toBe(true)
    expect(evidenceMatchesSearch('第三卷 第十二章', evidence)).toBe(true)
    expect(evidenceMatchesSearch('场景 不存在', evidence)).toBe(false)
    expect(evidenceMatchesSearch('   ', evidence)).toBe(true)
  })

  it('advances in bounded batches and can reach the exact final item', () => {
    expect(nextEvidenceVisibleCount(80, 497)).toBe(160)
    expect(nextEvidenceVisibleCount(480, 497)).toBe(497)
    expect(nextEvidenceVisibleCount(497, 497)).toBe(497)
  })
})
