import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import * as api from '../api/endpoints'
import { AuthLayout } from '../components/Layout'
import { Alert, Button, ErrorAlert, Field, Input, fieldError } from '../components/ui'

export default function ForgotPasswordPage() {
  const { t } = useTranslation()
  const [email, setEmail] = useState('')
  const forgot = useMutation({ mutationFn: () => api.forgotPassword(email) })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    forgot.mutate()
  }
  return (
    <AuthLayout title={t('auth.forgotTitle')}>
      {forgot.isSuccess ? (
        <div className="stack">
          <Alert tone="success">{t('auth.forgotDone')}</Alert>
          <Link to="/login">{t('auth.loginLink')}</Link>
        </div>
      ) : (
        <form className="stack" onSubmit={submit}>
          <p className="muted">{t('auth.forgotPrompt')}</p>
          <Field label={t('common.email')} error={fieldError(forgot.error, 'email')}>
            <Input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <ErrorAlert error={forgot.error} />
          <Button type="submit" loading={forgot.isPending}>
            {t('auth.forgotSubmit')}
          </Button>
          <Link className="center" to="/login">
            {t('common.back')}
          </Link>
        </form>
      )}
    </AuthLayout>
  )
}
