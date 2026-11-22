import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { useUser } from '../auth/AuthContext'
import { SecurityNotices } from '../components/SecurityNotices'
import { Card, EmptyState, ErrorAlert, PageHeader, Spinner } from '../components/ui'
import { formatAmount } from '../lib/format'
import { useBalances } from '../lib/hooks'
import { RecentActivity } from './HistoryPage'

export default function DashboardPage() {
  const { t, i18n } = useTranslation()
  const user = useUser()
  const balances = useBalances()
  const name = user.first_name || user.email

  // Group balances by chain, keeping the API order (chain, then coin).
  const chains = new Map<string, NonNullable<typeof balances.data>>()
  for (const b of balances.data ?? []) chains.set(b.blockchain, [...(chains.get(b.blockchain) ?? []), b])

  return (
    <>
      <PageHeader title={t('dashboard.greeting', { name })} />
      <SecurityNotices />
      <div className="quick-actions">
        <Link to="/deposit" className="action-tile">
          <span className="action-icon">↓</span>
          {t('nav.deposit')}
        </Link>
        <Link to="/withdraw" className="action-tile">
          <span className="action-icon">↑</span>
          {t('nav.withdraw')}
        </Link>
        <Link to="/transfer" className="action-tile">
          <span className="action-icon">⇄</span>
          {t('nav.transfer')}
        </Link>
      </div>
      <Card title={t('dashboard.balances')}>
        {balances.isPending ? (
          <Spinner />
        ) : balances.error ? (
          <ErrorAlert error={balances.error} />
        ) : !balances.data.length ? (
          <EmptyState>{t('dashboard.noBalances')}</EmptyState>
        ) : (
          [...chains.entries()].map(([chain, rows]) => (
            <div key={chain} className="balance-group">
              <div className="balance-chain">{chain}</div>
              <ul className="balance-list">
                {rows.map((b) => (
                  <li key={b.coin_info} className="balance-row">
                    <span className="coin-avatar" aria-hidden>
                      {b.symbol.slice(0, 1)}
                    </span>
                    <span className="balance-coin">
                      <strong>{b.symbol}</strong>
                      <span className="muted small">{b.name}</span>
                    </span>
                    <span className="balance-amount mono">{formatAmount(b.balance, i18n.language)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </Card>
      <RecentActivity />
    </>
  )
}
