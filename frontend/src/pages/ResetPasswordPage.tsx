import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'

import * as api from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'
import { AuthLayout } from '../components/Layout'
import { Alert, Button, ErrorAlert, Field, Input, fieldError } from '../components/ui'

/** Target of the reset email: set PASSWORD_RESET_URL=<site>/reset-password?uid={uid}&token={token}. */
export default function ResetPasswordPage() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const { token: session, signOut } = useAuth()
  const uid = params.get('uid') ?? ''
  const token = params.get('token') ?? ''
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [mismatch, setMismatch] = useState(false)

  const reset = useMutation({
    mutationFn: () => api.resetPassword(uid, token, password),
    // Every session was revoked by the backend; drop ours too.
    onSuccess: () => (session ? signOut() : undefined),
  })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setMismatch(password !== confirm)
    if (password === confirm) reset.mutate()
  }

  if (!uid || !token) {
    return (
      <AuthLayout title={t('auth.resetTitle')}>
        <Alert tone="danger">{t('auth.resetInvalidLink')}</Alert>
      </AuthLayout>
    )
  }
  return (
    <AuthLayout title={t('auth.resetTitle')}>
      {reset.isSuccess ? (
        <div className="stack">
          <Alert tone="success">{t('auth.resetDone')}</Alert>
          <Link className="btn btn-primary" to="/login">
            {t('auth.loginLink')}
          </Link>
        </div>
      ) : (
        <form className="stack" onSubmit={submit}>
          <Field label={t('auth.newPassword')} error={fieldError(reset.error, 'new_password')}>
            <Input type="password" required autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Field label={t('auth.confirmPassword')} error={mismatch ? t('auth.passwordMismatch') : undefined}>
            <Input type="password" required autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </Field>
          <ErrorAlert error={reset.error} />
          <Button type="submit" loading={reset.isPending}>
            {t('auth.resetSubmit')}
          </Button>
        </form>
      )}
    </AuthLayout>
  )
}
