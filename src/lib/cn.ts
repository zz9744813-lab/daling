/**
 * 简单的 classnames 合并工具函数
 * 过滤掉 false / null / undefined，并将剩余值用空格拼接。
 */
export type ClassValue = string | number | null | false | undefined | ClassValue[]

export function cn(...inputs: ClassValue[]): string {
  const out: string[] = []
  for (const v of inputs) {
    if (!v) continue
    if (Array.isArray(v)) {
      const inner = cn(...v)
      if (inner) out.push(inner)
    } else {
      out.push(String(v))
    }
  }
  return out.join(' ')
}
