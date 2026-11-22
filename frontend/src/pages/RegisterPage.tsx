import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import * as api from '../api/endpoints'
import { AuthLayout } from '../components/Layout'
import { Alert, Button, ErrorAlert, Field, Input, fieldError } from '../components/ui'

export default function RegisterPage() {
  const { t } = useTranslation()
  const [form, setForm] = useState({ email: '', first_name: '', last_name: '', password: '' })
  const register = useMutation({ mutationFn: () => api.register(form) })
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value })

  if (register.isSuccess) {
    return (
      <AuthLayout title={t('auth.registerTitle')}>
        <div className="stack">
          <Alert tone="success">{t('auth.registerDone', { email: form.email })}</Alert>
          <Link className="btn btn-primary" to="/login">
            {t('auth.loginLink')}
          </Link>
        </div>
      </AuthLayout>
    )
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    register.mutate()
  }
  const error = register.error
  return (
    <AuthLayout title={t('auth.registerTitle')}>
      <form className="stack" onSubmit={submit}>
        <div className="row-2">
          <Field label={t('auth.firstName')} error={fieldError(error, 'first_name')}>
            <Input required autoComplete="given-name" value={form.first_name} onChange={set('first_name')} />
          </Field>
          <Field label={t('auth.lastName')} error={fieldError(error, 'last_name')}>
            <Input required autoComplete="family-name" value={form.last_name} onChange={set('last_name')} />
          </Field>
        </div>
        <Field label={t('common.email')} error={fieldError(error, 'email')}>
          <Input type="email" required autoComplete="email" value={form.email} onChange={set('email')} />
        </Field>
        <Field label={t('common.password')} error={fieldError(error, 'password')}>
          <Input type="password" required autoComplete="new-password" value={form.password} onChange={set('password')} />
        </Field>
        <ErrorAlert error={error} />
        <Button type="submit" loading={register.isPending}>
          {t('auth.registerSubmit')}
        </Button>
        <p className="center muted">
          {t('auth.haveAccount')} <Link to="/login">{t('auth.loginLink')}</Link>
        </p>
      </form>
    </AuthLayout>
  )
}
