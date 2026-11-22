import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { useUser } from '../auth/AuthContext'
import { formatDateTime } from '../lib/format'
import { Alert } from './ui'

/** Banners for things that block withdrawals: no 2FA, a security cooldown, an unverified email. */
export function SecurityNotices({ forOutgoing = false }: { forOutgoing?: boolean }) {
  const { t, i18n } = useTranslation()
  const user = useUser()
  const lockedUntil = user.withdrawals_locked_until && new Date(user.withdrawals_locked_until) > new Date()
    ? user.withdrawals_locked_until
    : null
  return (
    <>
      {!user.is_verified && !forOutgoing && (
        <Alert tone="warning" title={t('dashboard.unverifiedTitle')}>
          {t('dashboard.unverifiedBody')}
        </Alert>
      )}
      {!user.two_factor_enabled && (
        <Alert
          tone="warning"
          title={t('dashboard.enable2faTitle')}
          action={
            <Link className="btn btn-secondary" to="/security">
              {t('dashboard.enable2faAction')}
            </Link>
          }
        >
          {t('dashboard.enable2faBody')}
        </Alert>
      )}
      {lockedUntil && (
        <Alert tone="danger" title={t('dashboard.lockedTitle')}>
          {t('dashboard.lockedBody', { until: formatDateTime(lockedUntil, i18n.language) })}
        </Alert>
      )}
    </>
  )
}
