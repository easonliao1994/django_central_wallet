import { describe, expect, it } from 'vitest'

import { toApiError } from './client'

describe('toApiError', () => {
  it('uses the backend error code', () => {
    const error = toApiError({ success: false, error: 'invalid_otp', error_content: 'invalid_otp' })
    expect(error.code).toBe('invalid_otp')
    expect(error.detail).toBeUndefined()
    expect(error.fields).toEqual({})
  })

  it('keeps a human-readable detail that differs from the code', () => {
    const error = toApiError({ success: false, error: 'throttled', error_content: 'Request was throttled. Expected available in 60 seconds.' })
    expect(error.code).toBe('throttled')
    expect(error.detail).toContain('60 seconds')
  })

  it('collects DRF field errors as validation errors', () => {
    const error = toApiError({ success: false, email: ['Enter a valid email address.'], new_password: ['Too short.', 'Too common.'] })
    expect(error.code).toBe('validation')
    expect(error.fields).toEqual({ email: ['Enter a valid email address.'], new_password: ['Too short.', 'Too common.'] })
  })

  it('flattens wrapped list errors', () => {
    const error = toApiError({ success: false, errors: ['Something is wrong.'] })
    expect(error.fields.errors).toEqual(['Something is wrong.'])
  })
})
