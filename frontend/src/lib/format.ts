/** Shorten an address or hash for display: 0x1234…abcd */
export function shorten(value: string | null | undefined, head = 6, tail = 4): string {
  if (!value) return ''
  return value.length <= head + tail + 1 ? value : `${value.slice(0, head)}…${value.slice(-tail)}`
}

export function formatDateTime(value: string | null | undefined, locale: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

/**
 * Group the integer part of a decimal string without going through floating point
 * (balances can have 18 decimals and exceed Number precision).
 */
export function formatAmount(value: string, locale: string): string {
  const negative = value.startsWith('-')
  const [intPart, fraction] = (negative ? value.slice(1) : value).split('.')
  const separator = new Intl.NumberFormat(locale).formatToParts(1000).find((p) => p.type === 'group')?.value ?? ','
  const decimal = new Intl.NumberFormat(locale).formatToParts(1.5).find((p) => p.type === 'decimal')?.value ?? '.'
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, separator)
  return `${negative ? '-' : ''}${grouped}${fraction ? decimal + fraction : ''}`
}

/** True if a decimal string is a positive amount with at most `decimals` fraction digits. */
export function isValidAmount(value: string, decimals: number): boolean {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value.trim())
  if (!match) return false
  if ((match[2]?.length ?? 0) > decimals) return false
  return /[1-9]/.test(match[1] + (match[2] ?? ''))
}

/** Compare two non-negative decimal strings exactly. Returns -1, 0 or 1. */
export function compareAmounts(a: string, b: string): number {
  const [ai, af = ''] = a.trim().split('.')
  const [bi, bf = ''] = b.trim().split('.')
  const width = Math.max(af.length, bf.length)
  const left = BigInt((ai.replace(/^0+/, '') || '0') + af.padEnd(width, '0'))
  const right = BigInt((bi.replace(/^0+/, '') || '0') + bf.padEnd(width, '0'))
  return left === right ? 0 : left < right ? -1 : 1
}

export const isEvmAddress = (value: string) => /^0x[0-9a-fA-F]{40}$/.test(value.trim())
