import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import * as api from '../api/endpoints'
import type { Transaction } from '../api/types'
import { TransactionStatusBadge, directionLabel } from '../components/status'
import { Button, Card, EmptyState, ErrorAlert, Field, Input, PageHeader, Select, Spinner } from '../components/ui'
import { formatAmount, formatDateTime, shorten } from '../lib/format'
import { useBalances } from '../lib/hooks'

const PAGE_SIZE = 20
const EMPTY_FILTERS = { direction: '', coin_info: '' as number | '', status: '', created_after: '', created_before: '' }

function Amount({ tx }: { tx: Transaction }) {
  const { i18n } = useTranslation()
  const sign = tx.flow === 'in' ? '+' : tx.flow === 'out' ? '−' : ''
  return (
    <span className={`mono ${tx.flow === 'in' ? 'positive' : ''}`}>
      {sign}
      {formatAmount(tx.amount, i18n.language)} <span className="muted">{tx.symbol}</span>
    </span>
  )
}

function Counterparty({ tx }: { tx: Transaction }) {
  const address = tx.flow === 'in' ? tx.from_address : tx.to_address
  return address ? <span className="mono muted small" title={address}>{shorten(address, 8, 6)}</span> : null
}

function TransactionRows({ rows }: { rows: Transaction[] }) {
  const { t, i18n } = useTranslation()
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>{t('history.direction')}</th>
            <th className="right">{t('common.amount')}</th>
            <th>{t('common.chain')}</th>
            <th>{t('common.address')}</th>
            <th>{t('common.status')}</th>
            <th>{t('common.date')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((tx) => (
            <tr key={tx.id}>
              <td>{directionLabel(t, tx)}</td>
              <td className="right nowrap">
                <Amount tx={tx} />
              </td>
              <td>{tx.blockchain}</td>
              <td>
                <Counterparty tx={tx} />
              </td>
              <td>
                <TransactionStatusBadge tx={tx} />
              </td>
              <td className="nowrap">
                {formatDateTime(tx.created_at, i18n.language)}
                {tx.tx_url && (
                  <>
                    {' · '}
                    <a href={tx.tx_url} target="_blank" rel="noreferrer">
                      {t('history.viewTx')}
                    </a>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Latest few transactions, for the overview page. */
export function RecentActivity() {
  const { t } = useTranslation()
  const recent = useQuery({ queryKey: ['transactions', 'recent'], queryFn: () => api.getTransactions({ page_size: 5 }) })
  return (
    <Card title={t('history.title')} actions={<Link to="/history">{t('common.viewAll')} →</Link>}>
      {recent.isPending ? (
        <Spinner />
      ) : recent.error ? (
        <ErrorAlert error={recent.error} />
      ) : recent.data.results.length ? (
        <TransactionRows rows={recent.data.results} />
      ) : (
        <EmptyState />
      )}
    </Card>
  )
}

export default function HistoryPage() {
  const { t } = useTranslation()
  const balances = useBalances()
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [page, setPage] = useState(1)

  const query = useQuery({
    queryKey: ['transactions', filters, page],
    queryFn: () => api.getTransactions({ ...filters, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  })
  const update = (patch: Partial<typeof EMPTY_FILTERS>) => {
    setFilters({ ...filters, ...patch })
    setPage(1)
  }
  const pages = query.data ? Math.max(1, Math.ceil(query.data.count / PAGE_SIZE)) : 1

  return (
    <>
      <PageHeader title={t('history.title')} />
      <Card>
        <div className="filters">
          <Field label={t('history.direction')}>
            <Select value={filters.direction} onChange={(e) => update({ direction: e.target.value })}>
              <option value="">{t('common.all')}</option>
              {(['deposit', 'withdrawal', 'internal'] as const).map((d) => (
                <option key={d} value={d}>
                  {t(`directions.${d}`)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t('common.coin')}>
            <Select value={filters.coin_info} onChange={(e) => update({ coin_info: e.target.value ? Number(e.target.value) : '' })}>
              <option value="">{t('common.all')}</option>
              {(balances.data ?? []).map((b) => (
                <option key={b.coin_info} value={b.coin_info}>
                  {b.symbol} · {b.blockchain}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t('common.status')}>
            <Select value={filters.status} onChange={(e) => update({ status: e.target.value })}>
              <option value="">{t('common.all')}</option>
              {(['pending', 'confirmed', 'failed'] as const).map((s) => (
                <option key={s} value={s}>
                  {t(`statuses.${s}`)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t('history.from')}>
            <Input type="date" value={filters.created_after} onChange={(e) => update({ created_after: e.target.value })} />
          </Field>
          <Field label={t('history.until')}>
            <Input type="date" value={filters.created_before} onChange={(e) => update({ created_before: e.target.value })} />
          </Field>
          <Button type="button" variant="ghost" onClick={() => update(EMPTY_FILTERS)}>
            {t('history.reset')}
          </Button>
        </div>
        {query.isPending ? (
          <Spinner />
        ) : query.error ? (
          <ErrorAlert error={query.error} />
        ) : !query.data.results.length ? (
          <EmptyState />
        ) : (
          <>
            <TransactionRows rows={query.data.results} />
            <div className="pagination">
              <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                {t('common.prev')}
              </Button>
              <span className="muted">
                {t('common.page', { page })} / {pages}
              </span>
              <Button variant="secondary" disabled={!query.data.next} onClick={() => setPage(page + 1)}>
                {t('common.next')}
              </Button>
            </div>
          </>
        )}
      </Card>
    </>
  )
}
