import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { ApiError } from '../api/client'
import * as api from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'
import { AuthLayout } from '../components/Layout'
import { Alert, Button, ErrorAlert, Field, Input, OtpInput, fieldError } from '../components/ui'

export default function LoginPage() {
  const { t } = useTranslation()
  const { signIn, expired } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [otp, setOtp] = useState('')
  const [needsOtp, setNeedsOtp] = useState(false)

  const login = useMutation({
    mutationFn: () => api.login(email, password, needsOtp ? otp : undefined),
    onSuccess: ({ token, user }) => {
      signIn(token, user)
      const from = (location.state as { from?: string } | null)?.from
      navigate(from && from !== '/login' ? from : '/', { replace: true })
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === 'otp_required') {
        setNeedsOtp(true)
        login.reset() // asking for the code is a step, not an error
      }
    },
  })
  const resend = useMutation({ mutationFn: () => api.resendVerification(email) })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    login.mutate()
  }
  const notVerified = login.error instanceof ApiError && login.error.code === 'email_not_verified'

  if (needsOtp) {
    return (
      <AuthLayout title={t('auth.otpTitle')}>
        <form className="stack" onSubmit={submit}>
          <p className="muted">{t('auth.otpPrompt')}</p>
          <Field label={t('common.otpCode')}>
            <OtpInput value={otp} onChange={setOtp} autoFocus />
          </Field>
          <ErrorAlert error={login.error} />
          <Button type="submit" loading={login.isPending} disabled={otp.length !== 6}>
            {t('auth.otpSubmit')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setNeedsOtp(false)
              setOtp('')
              login.reset()
            }}
          >
            {t('common.back')}
          </Button>
        </form>
      </AuthLayout>
    )
  }

  return (
    <AuthLayout title={t('auth.loginTitle')}>
      <form className="stack" onSubmit={submit}>
        {expired && <Alert tone="warning">{t('auth.sessionExpired')}</Alert>}
        <Field label={t('common.email')} error={fieldError(login.error, 'email')}>
          <Input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field
          label={t('common.password')}
          error={fieldError(login.error, 'password')}
          aside={<Link to="/forgot-password">{t('auth.forgotLink')}</Link>}
        >
          <Input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        {notVerified ? (
          <Alert
            tone="warning"
            title={t('auth.notVerified')}
            action={
              <Button type="button" variant="secondary" loading={resend.isPending} onClick={() => resend.mutate()}>
                {t('auth.resend')}
              </Button>
            }
          >
            {resend.isSuccess && t('auth.resent')}
          </Alert>
        ) : (
          <ErrorAlert error={login.error} />
        )}
        <Button type="submit" loading={login.isPending}>
          {t('auth.loginSubmit')}
        </Button>
        <p className="center muted">
          {t('auth.noAccount')} <Link to="/register">{t('auth.registerLink')}</Link>
        </p>
      </form>
    </AuthLayout>
  )
}
