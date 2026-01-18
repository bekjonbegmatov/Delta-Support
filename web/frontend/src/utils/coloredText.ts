export type ColoredPart = { text: string; color?: string }

const namedColors: Record<string, string> = {
  white: '#ffffff',
  black: '#000000',
  dark: '#0b1220',
  gray: '#94a3b8',
  red: '#ef4444',
  orange: '#f97316',
  yellow: '#eab308',
  lime: '#84cc16',
  green: '#22c55e',
  teal: '#14b8a6',
  cyan: '#22d3ee',
  blue: '#3b82f6',
  indigo: '#6366f1',
  violet: '#8b5cf6',
  grape: '#a78bfa',
  pink: '#ec4899'
}

function normalizeColorToken(token: string): string | undefined {
  const t = token.trim()
  if (!t) return undefined
  if (/^#[0-9a-fA-F]{6}$/.test(t)) return t
  const key = t.toLowerCase()
  return namedColors[key]
}

export function parseColoredText(input: string): ColoredPart[] {
  const s = input || ''
  const parts: ColoredPart[] = []
  const re = /\{([^}]+)\}/g
  let lastIndex = 0
  let currentColor: string | undefined
  let m: RegExpExecArray | null
  while ((m = re.exec(s))) {
    const start = m.index
    const token = m[1] || ''
    if (start > lastIndex) {
      const text = s.slice(lastIndex, start)
      if (text) parts.push({ text, color: currentColor })
    }
    const nextColor = normalizeColorToken(token)
    currentColor = nextColor
    lastIndex = re.lastIndex
  }
  if (lastIndex < s.length) {
    const tail = s.slice(lastIndex)
    if (tail) parts.push({ text: tail, color: currentColor })
  }
  if (parts.length === 0) return [{ text: s }]
  return parts
}

