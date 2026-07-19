const PLACEHOLDER_ONLY_RE = /^[\s?？﹖�]+$/u
const C1_CONTROL_RE = /[\u0080-\u009f]/u
const COMMON_MOJIBAKE_RE = /锟斤拷|ï»¿/u
const LATIN1_MOJIBAKE_TOKEN_RE =
  /(?:[ÃÂ][\u0080-\u00ff]|â(?:€|€™|€œ|€|€“|€”|€¦)|[äåæçéèï][\u00a0-\u00ff\u2010-\u2027]{1,3})/gu

function shortProjectId(projectId: string | null | undefined) {
  const normalized = projectId?.trim() ?? ''
  if (!normalized) return '未知ID'

  const firstSegment = normalized.split('-')[0]
  return (firstSegment || normalized).slice(0, 8)
}

function isObviouslyMojibake(title: string) {
  if (title.includes('\uFFFD') || C1_CONTROL_RE.test(title) || COMMON_MOJIBAKE_RE.test(title)) {
    return true
  }

  const tokens = title.match(LATIN1_MOJIBAKE_TOKEN_RE)
  return (tokens?.length ?? 0) >= 2
}

/**
 * Returns a safe presentation title without mutating the persisted project.
 * High-confidence corruption is replaced with a stable, identifiable fallback;
 * legitimate Chinese, English and mixed-language titles are returned verbatim.
 */
export function getProjectDisplayTitle(
  title: string | null | undefined,
  projectId: string | null | undefined,
) {
  const candidate = title ?? ''
  if (!candidate.trim() || PLACEHOLDER_ONLY_RE.test(candidate) || isObviouslyMojibake(candidate)) {
    return `未命名项目 · ${shortProjectId(projectId)}`
  }

  return candidate
}
