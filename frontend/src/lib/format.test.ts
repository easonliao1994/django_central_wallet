import { describe, expect, it } from 'vitest'

import { compareAmounts, formatAmount, isEvmAddress, isValidAmount, shorten } from './format'

describe('amounts', () => {
  it('groups digits without losing precision', () => {
    expect(formatAmount('1234567.123456789012345678', 'en')).toBe('1,234,567.123456789012345678')
    expect(formatAmount('12', 'en')).toBe('12')
    expect(formatAmount('-1000.5', 'en')).toBe('-1,000.5')
  })

  it('validates amounts against the coin decimals', () => {
    expect(isValidAmount('1.5', 6)).toBe(true)
    expect(isValidAmount('0.000001', 6)).toBe(true)
    expect(isValidAmount('0.0000001', 6)).toBe(false) // too many decimals
    expect(isValidAmount('0', 6)).toBe(false)
    expect(isValidAmount('0.000', 6)).toBe(false)
    expect(isValidAmount('1e5', 6)).toBe(false)
    expect(isValidAmount('-1', 6)).toBe(false)
    expect(isValidAmount('.5', 6)).toBe(false)
  })

  it('compares decimal strings exactly, beyond float precision', () => {
    expect(compareAmounts('0.1', '0.10')).toBe(0)
    expect(compareAmounts('1.000000000000000001', '1')).toBe(1)
    expect(compareAmounts('99999999999999999999.5', '100000000000000000000')).toBe(-1)
    expect(compareAmounts('007', '7')).toBe(0)
  })
})

describe('addresses', () => {
  it('recognises 0x addresses', () => {
    expect(isEvmAddress('0x' + 'ab'.repeat(20))).toBe(true)
    expect(isEvmAddress('ab'.repeat(20))).toBe(false)
    expect(isEvmAddress('0x1234')).toBe(false)
  })

  it('shortens long values', () => {
    expect(shorten('0x1234567890abcdef1234567890abcdef12345678')).toBe('0x1234…5678')
    expect(shorten('short')).toBe('short')
  })
})
