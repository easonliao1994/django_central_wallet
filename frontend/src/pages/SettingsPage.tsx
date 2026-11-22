import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import * as api from '../api/endpoints'
import { useAuth, useUser } from '../auth/AuthContext'
import { Alert, Button, Card, ErrorAlert, Field, Input, PageHeader, fieldError } from '../components/ui'
import { formatDateTime } from '../lib/format'

export default function SettingsPage() {
  const { t } = useTranslation()
  return (
    <>
      <PageHeader title={t('settings.title')} />
      <div className="grid-2">
        <ProfileCard />
        <PasswordCard />
      </div>
    </>
  )
}

function ProfileCard() {
  const { t, i18n } = useTranslation()
  const user = useUser()
  const { setUser } = useAuth()
  const [form, setForm] = useState({ first_name: user.first_name, last_name: user.last_name })
  const save = useMutation({ mutationFn: () => api.updateMe(form), onSuccess: setUser })

  return (
    <Card title={t('settings.profile')}>
      <form
        className="stack"
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <Field label={t('common.email')}>
          <Input value={user.email} disabled />
        </Field>
        <div className="row-2">
          <Field label={t('auth.firstName')} error={fieldError(save.error, 'first_name')}>
            <Input value={form.first_name} onChange={(e) => setForm({ ...form, first_name: e.target.value })} />
          </Field>
          <Field label={t('auth.lastName')} error={fieldError(save.error, 'last_name')}>
            <Input value={form.last_name} onChange={(e) => setForm({ ...form, last_name: e.target.value })} />
          </Field>
        </div>
        <p className="muted small">
          {t('settings.memberSince')}: {formatDateTime(user.created_at, i18n.language)}
        </p>
        <ErrorAlert error={save.error} />
        {save.isSuccess && <Alert tone="success">{t('settings.profileSaved')}</Alert>}
        <Button type="submit" loading={save.isPending}>
          {t('common.save')}
        </Button>
      </form>
    </Card>
  )
}

function PasswordCard() {
  const { t } = useTranslation()
  const { replaceToken } = useAuth()
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [mismatch, setMismatch] = useState(false)
  const change = useMutation({
    mutationFn: () => api.changePassword(oldPassword, newPassword),
    onSuccess: ({ token }) => {
      replaceToken(token) // the old token was revoked
      setOldPassword('')
      setNewPassword('')
      setConfirm('')
    },
  })

  return (
    <Card title={t('settings.changePassword')}>
      <form
        className="stack"
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          setMismatch(newPassword !== confirm)
          if (newPassword === confirm) change.mutate()
        }}
      >
        <Field label={t('settings.oldPassword')}>
          <Input type="password" required autoComplete="current-password" value={oldPassword} onChange={(e) => setOldPassword(e.target.value)} />
        </Field>
        <Field label={t('auth.newPassword')} error={fieldError(change.error, 'new_password')}>
          <Input type="password" required autoComplete="new-password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
        </Field>
        <Field label={t('auth.confirmPassword')} error={mismatch ? t('auth.passwordMismatch') : undefined}>
          <Input type="password" required autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </Field>
        <ErrorAlert error={change.error} />
        {change.isSuccess && <Alert tone="success">{t('settings.passwordChanged')}</Alert>}
        <Button type="submit" loading={change.isPending}>
          {t('settings.changePassword')}
        </Button>
      </form>
    </Card>
  )
}
