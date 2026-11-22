import { useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '../api/client'

export function Card({ title, actions, children, className = '' }: {
  title?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-header">
          {title && <h2 className="card-title">{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="page-header">
      <h1>{title}</h1>
      {subtitle && <p className="muted">{subtitle}</p>}
    </div>
  )
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  loading?: boolean
}

export function Button({ variant = 'primary', loading, disabled, children, className = '', ...rest }: ButtonProps) {
  return (
    <button className={`btn btn-${variant} ${className}`} disabled={disabled || loading} {...rest}>
      {loading && <span className="spinner spinner-sm" aria-hidden />}
      {children}
    </button>
  )
}

interface FieldProps {
  label: string
  hint?: ReactNode
  error?: string | string[]
  children: ReactNode
  aside?: ReactNode
}

export function Field({ label, hint, error, children, aside }: FieldProps) {
  const errors = Array.isArray(error) ? error : error ? [error] : []
  return (
    <label className={`field ${errors.length ? 'field-invalid' : ''}`}>
      <span className="field-label">
        <span>{label}</span>
        {aside && <span className="field-aside">{aside}</span>}
      </span>
      {children}
      {hint && !errors.length && <span className="field-hint">{hint}</span>}
      {errors.map((e) => (
        <span key={e} className="field-error">
          {e}
        </span>
      ))}
    </label>
  )
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input className="input" {...props} />
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className="input" {...props} />
}

/** 6-digit code input tuned for authenticator codes (numeric keypad, one-time-code autofill). */
export function OtpInput({ value, onChange, autoFocus }: { value: string; onChange: (v: string) => void; autoFocus?: boolean }) {
  return (
    <input
      className="input input-otp"
      inputMode="numeric"
      autoComplete="one-time-code"
      pattern="[0-9]{6}"
      maxLength={6}
      placeholder="000000"
      value={value}
      autoFocus={autoFocus}
      onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, 6))}
    />
  )
}

export function Alert({ tone = 'info', title, children, action }: {
  tone?: 'info' | 'success' | 'warning' | 'danger'
  title?: ReactNode
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className={`alert alert-${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      <div className="alert-body">
        {title && <strong className="alert-title">{title}</strong>}
        {children && <div>{children}</div>}
      </div>
      {action && <div className="alert-action">{action}</div>}
    </div>
  )
}

/** Show an API error in the user's language (field errors are shown next to their fields). */
export function ErrorAlert({ error }: { error: unknown }) {
  const { t, i18n } = useTranslation()
  if (!error) return null
  const code = error instanceof ApiError ? error.code : 'unknown'
  const key = `errors.${code}`
  let message = i18n.exists(key) ? t(key) : t('errors.unknown')
  if (error instanceof ApiError && code === 'validation') {
    // Errors not tied to a visible field (e.g. non_field_errors) are listed here.
    const general = error.fields.non_field_errors ?? error.fields.errors
    if (general?.length) message = general.join(' ')
  } else if (error instanceof ApiError && !i18n.exists(key) && error.detail) {
    message = error.detail
  }
  return <Alert tone="danger">{message}</Alert>
}

/** Field error helper: messages for one field from an ApiError. */
export function fieldError(error: unknown, name: string): string[] | undefined {
  return error instanceof ApiError ? error.fields[name] : undefined
}

export function Spinner({ label }: { label?: string }) {
  const { t } = useTranslation()
  return (
    <div className="loading">
      <span className="spinner" aria-hidden />
      <span>{label ?? t('common.loading')}</span>
    </div>
  )
}

export function CopyButton({ value }: { value: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  return (
    <Button
      type="button"
      variant="secondary"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        } catch {
          // Clipboard unavailable (e.g. insecure context): the value is still selectable.
        }
      }}
    >
      {copied ? t('common.copied') : t('common.copy')}
    </Button>
  )
}

export function Badge({ tone = 'neutral', children }: { tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info'; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function EmptyState({ children }: { children?: ReactNode }) {
  const { t } = useTranslation()
  return <p className="empty">{children ?? t('common.empty')}</p>
}

/** Render an SVG string from the backend (QR codes) as an image, so it cannot run scripts. */
export function SvgImage({ svg, alt, size = 200 }: { svg: string; alt: string; size?: number }) {
  const src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
  return <img className="qr" src={src} alt={alt} width={size} height={size} />
}
