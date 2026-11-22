import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import * as api from '../api/endpoints'
import type { TwoFactorSetup, User } from '../api/types'
import { useAuth, useUser } from '../auth/AuthContext'
import {
  Alert, Badge, Button, Card, CopyButton, EmptyState, ErrorAlert, Field, Input, OtpInput, PageHeader, Spinner, SvgImage,
  fieldError,
} from '../components/ui'
import { formatDateTime, shorten } from '../lib/format'
import { useSavedAddresses } from '../lib/hooks'

export default function SecurityPage() {
  const { t } = useTranslation()
  return (
    <>
      <PageHeader title={t('security.title')} />
      <TwoFactorCard />
      <SavedAddressesCard />
      <WhitelistCard />
    </>
  )
}

function useUpdateUser() {
  const { setUser } = useAuth()
  return (user: User) => setUser(user)
}

// ------------------------------------------------------------------ 2FA
function TwoFactorCard() {
  const { t } = useTranslation()
  const user = useUser()
  return (
    <Card
      title={t('security.twoFactor')}
      actions={<Badge tone={user.two_factor_enabled ? 'success' : 'warning'}>{t(user.two_factor_enabled ? 'security.twoFactorOn' : 'security.twoFactorOff')}</Badge>}
    >
      <p className="muted">{t('security.twoFactorIntro')}</p>
      {user.two_factor_enabled ? <DisableTwoFactor /> : <SetUpTwoFactor />}
    </Card>
  )
}

function SetUpTwoFactor() {
  const { t } = useTranslation()
  const updateUser = useUpdateUser()
  const [password, setPassword] = useState('')
  const [setup, setSetup] = useState<TwoFactorSetup | null>(null)
  const [code, setCode] = useState('')

  const start = useMutation({ mutationFn: () => api.startTwoFactorSetup(password), onSuccess: setSetup })
  const enable = useMutation({
    mutationFn: () => api.enableTwoFactor(code),
    onSuccess: ({ user }) => updateUser(user),
    onError: () => setCode(''),
  })

  if (!setup) {
    return (
      <form
        className="stack narrow"
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          start.mutate()
        }}
      >
        <Field label={t('security.setupPassword')}>
          <Input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorAlert error={start.error} />
        <Button type="submit" loading={start.isPending}>
          {t('security.setup')}
        </Button>
      </form>
    )
  }
  return (
    <form
      className="setup-2fa"
      onSubmit={(e: FormEvent) => {
        e.preventDefault()
        enable.mutate()
      }}
    >
      <div className="stack">
        <strong>{t('security.scanQr')}</strong>
        <SvgImage svg={setup.qr_code_svg} alt="QR code" size={190} />
        <span className="muted small">{t('security.manualKey')}</span>
        <div className="inline">
          <code className="address-box">{setup.secret.replace(/(.{4})/g, '$1 ').trim()}</code>
          <CopyButton value={setup.secret} />
        </div>
      </div>
      <div className="stack">
        <strong>{t('security.enterCode')}</strong>
        <OtpInput value={code} onChange={setCode} autoFocus />
        <ErrorAlert error={enable.error} />
        <Button type="submit" loading={enable.isPending} disabled={code.length !== 6}>
          {t('security.enable')}
        </Button>
      </div>
    </form>
  )
}

function DisableTwoFactor() {
  const { t } = useTranslation()
  const updateUser = useUpdateUser()
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const disable = useMutation({
    mutationFn: () => api.disableTwoFactor(password, code),
    onSuccess: ({ user }) => updateUser(user),
    onSettled: () => setCode(''),
  })

  if (!open) {
    return (
      <Button variant="secondary" onClick={() => setOpen(true)}>
        {t('security.disable')}
      </Button>
    )
  }
  return (
    <form
      className="stack narrow"
      onSubmit={(e: FormEvent) => {
        e.preventDefault()
        disable.mutate()
      }}
    >
      <Alert tone="warning">{t('security.disableWarning')}</Alert>
      <Field label={t('common.password')}>
        <Input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <Field label={t('common.otpCode')}>
        <OtpInput value={code} onChange={setCode} />
      </Field>
      <ErrorAlert error={disable.error} />
      <div className="inline">
        <Button type="submit" variant="danger" loading={disable.isPending} disabled={code.length !== 6}>
          {t('security.disable')}
        </Button>
        <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
          {t('common.cancel')}
        </Button>
      </div>
    </form>
  )
}

// ------------------------------------------------------------------ saved addresses
function SavedAddressesCard() {
  const { t, i18n } = useTranslation()
  const user = useUser()
  const queryClient = useQueryClient()
  const saved = useSavedAddresses()
  const [label, setLabel] = useState('')
  const [address, setAddress] = useState('')
  const [code, setCode] = useState('')

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['saved-addresses'] })
  const add = useMutation({
    mutationFn: () => api.addSavedAddress(label, address.trim(), code),
    onSuccess: () => {
      setLabel('')
      setAddress('')
      refresh()
    },
    onSettled: () => setCode(''),
  })
  const remove = useMutation({ mutationFn: api.deleteSavedAddress, onSuccess: refresh })

  return (
    <Card title={t('security.savedAddresses')}>
      <p className="muted">{t('security.savedIntro')}</p>
      {saved.isPending ? (
        <Spinner />
      ) : saved.error ? (
        <ErrorAlert error={saved.error} />
      ) : !saved.data.length ? (
        <EmptyState>{t('security.noSaved')}</EmptyState>
      ) : (
        <ul className="activity-list">
          {saved.data.map((a) => (
            <li key={a.id} className="activity-row">
              <div>
                <div>
                  <strong>{a.label || '—'}</strong>
                </div>
                <div className="mono small muted" title={a.address}>
                  {shorten(a.address, 12, 10)}
                </div>
              </div>
              <div className="activity-side">
                {a.active ? (
                  <Badge tone="success">{t('security.active')}</Badge>
                ) : (
                  <Badge tone="warning">{t('security.activeFrom', { date: formatDateTime(a.active_from, i18n.language) })}</Badge>
                )}
                <Button
                  variant="ghost"
                  loading={remove.isPending && remove.variables === a.id}
                  onClick={() => window.confirm(t('security.deleteConfirm')) && remove.mutate(a.id)}
                >
                  {t('common.delete')}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {user.two_factor_enabled ? (
        <form
          className="add-address"
          onSubmit={(e: FormEvent) => {
            e.preventDefault()
            add.mutate()
          }}
        >
          <Field label={`${t('security.label')} (${t('common.optional')})`} error={fieldError(add.error, 'label')}>
            <Input maxLength={64} value={label} onChange={(e) => setLabel(e.target.value)} />
          </Field>
          <Field label={t('common.address')} error={fieldError(add.error, 'address')}>
            <Input className="input mono" placeholder="0x…" required spellCheck={false} value={address} onChange={(e) => setAddress(e.target.value)} />
          </Field>
          <Field label={t('common.otpCode')}>
            <OtpInput value={code} onChange={setCode} />
          </Field>
          <Button type="submit" loading={add.isPending} disabled={code.length !== 6}>
            {t('security.add')}
          </Button>
          <div className="add-address-error">
            <ErrorAlert error={add.error} />
          </div>
        </form>
      ) : (
        <Alert tone="info">{t('security.needs2fa')}</Alert>
      )}
    </Card>
  )
}

// ------------------------------------------------------------------ whitelist mode
function WhitelistCard() {
  const { t, i18n } = useTranslation()
  const user = useUser()
  const updateUser = useUpdateUser()
  const [code, setCode] = useState('')
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.setWhitelistMode(enabled, enabled ? undefined : code),
    onSuccess: ({ user }) => updateUser(user),
    onSettled: () => setCode(''),
  })

  const pending = user.whitelist_disable_at
  return (
    <Card
      title={t('security.whitelist')}
      actions={<Badge tone={user.withdrawal_whitelist_enabled ? 'success' : 'neutral'}>{t(user.withdrawal_whitelist_enabled ? 'security.twoFactorOn' : 'security.twoFactorOff')}</Badge>}
    >
      <p className="muted">{t('security.whitelistIntro')}</p>
      {!user.two_factor_enabled ? (
        <Alert tone="info">{t('security.needs2fa')}</Alert>
      ) : !user.withdrawal_whitelist_enabled ? (
        <Button loading={toggle.isPending} onClick={() => toggle.mutate(true)}>
          {t('security.whitelistEnable')}
        </Button>
      ) : pending ? (
        <div className="stack narrow">
          <Alert tone="warning">{t('security.whitelistDisablePending', { date: formatDateTime(pending, i18n.language) })}</Alert>
          <Button variant="secondary" loading={toggle.isPending} onClick={() => toggle.mutate(true)}>
            {t('security.whitelistCancel')}
          </Button>
        </div>
      ) : (
        <form
          className="inline-form"
          onSubmit={(e: FormEvent) => {
            e.preventDefault()
            toggle.mutate(false)
          }}
        >
          <Field label={t('common.otpCode')}>
            <OtpInput value={code} onChange={setCode} />
          </Field>
          <Button type="submit" variant="secondary" loading={toggle.isPending} disabled={code.length !== 6}>
            {t('security.whitelistDisable')}
          </Button>
        </form>
      )}
      <ErrorAlert error={toggle.error} />
    </Card>
  )
}
