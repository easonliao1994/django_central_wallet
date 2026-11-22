// Thin fetch wrapper for the Django REST API.
//
// The backend reports errors with HTTP 200 and a body like
//   {"success": false, "error": "invalid_otp", "error_content": "invalid_otp"}
// or, for validation errors, {"success": false, "email": ["Enter a valid email address."]}.
// request() turns every such body into an ApiError, so callers only see successful data.

const TOKEN_KEY = 'cw.token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

/** Error codes that mean the stored token no longer works. */
export const AUTH_ERRORS = new Set(['not_authenticated', 'authentication_failed'])

export class ApiError extends Error {
  /** Machine-readable code from the backend (e.g. "invalid_otp"), or "validation" / "network". */
  code: string
  /** Field errors from DRF validation, e.g. {"amount": ["This field is required."]}. */
  fields: Record<string, string[]>
  /** Human-readable detail from the backend, when it sent one (e.g. throttling wait time). */
  detail?: string

  constructor(code: string, fields: Record<string, string[]> = {}, detail?: string) {
    super(detail || code)
    this.code = code
    this.fields = fields
    this.detail = detail
  }
}

type Listener = () => void
const unauthorizedListeners = new Set<Listener>()

/** Called when a request fails because the token is missing or revoked. */
export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener)
  return () => unauthorizedListeners.delete(listener)
}

const META_KEYS = new Set(['success', 'error', 'error_content', 'detail'])

/** Convert a failed response body into an ApiError. Exported for tests. */
export function toApiError(body: Record<string, unknown>): ApiError {
  const fields: Record<string, string[]> = {}
  for (const [key, value] of Object.entries(body)) {
    if (META_KEYS.has(key)) continue
    if (Array.isArray(value)) fields[key] = value.map(String)
    else if (typeof value === 'string') fields[key] = [value]
    else if (value && typeof value === 'object') {
      // Nested errors (e.g. {"errors": [...]}) are flattened to their messages.
      fields[key] = Object.values(value as Record<string, unknown>).flat().map(String)
    }
  }
  const code = typeof body.error === 'string' ? body.error : Object.keys(fields).length ? 'validation' : 'unknown'
  const detail = typeof body.error_content === 'string' && body.error_content !== code ? body.error_content : undefined
  return new ApiError(code, fields, detail)
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Record<string, string | number | undefined | null>
  /** Send without the Authorization header (login, registration, password reset). */
  anonymous?: boolean
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, anonymous } = options
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const url = params.size ? `${path}?${params}` : path

  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const token = getToken()
  if (token && !anonymous) headers.Authorization = `Token ${token}`

  let response: Response
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'omit', // token auth only: no session cookie, so no CSRF exposure
    })
  } catch {
    throw new ApiError('network')
  }

  if (response.status === 204) return undefined as T
  let data: unknown
  try {
    data = await response.json()
  } catch {
    throw new ApiError(response.ok ? 'invalid_response' : `http_${response.status}`)
  }
  if (data && typeof data === 'object' && !Array.isArray(data) && (data as { success?: unknown }).success === false) {
    const error = toApiError(data as Record<string, unknown>)
    if (AUTH_ERRORS.has(error.code) && token && !anonymous) unauthorizedListeners.forEach((fn) => fn())
    throw error
  }
  if (!response.ok) throw new ApiError(`http_${response.status}`)
  return data as T
}
