import { useTranslation } from 'react-i18next'

import type { Transaction, TxStatus, WithdrawalStatus } from '../api/types'
import { Badge } from './ui'

type Tone = 'neutral' | 'success' | 'warning' | 'danger' | 'info'

const WITHDRAWAL_TONES: Record<WithdrawalStatus, Tone> = {
  pending_review: 'warning',
  approved: 'info',
  broadcast: 'info',
  stuck: 'warning',
  confirmed: 'success',
  failed: 'danger',
  rejected: 'danger',
}

const TX_TONES: Record<TxStatus, Tone> = { pending: 'info', confirmed: 'success', failed: 'danger' }

export function WithdrawalStatusBadge({ status }: { status: WithdrawalStatus }) {
  const { t } = useTranslation()
  return <Badge tone={WITHDRAWAL_TONES[status]}>{t(`statuses.${status === 'failed' ? 'failed_refunded' : status}`)}</Badge>
}

/** A withdrawal's own status is more precise than its log's pending/confirmed/failed. */
export function TransactionStatusBadge({ tx }: { tx: Transaction }) {
  const { t } = useTranslation()
  if (tx.withdrawal_status) return <WithdrawalStatusBadge status={tx.withdrawal_status} />
  return <Badge tone={TX_TONES[tx.status]}>{t(`statuses.${tx.status}`)}</Badge>
}

export function directionLabel(t: (key: string) => string, tx: Pick<Transaction, 'direction' | 'flow'>): string {
  if (tx.direction === 'internal' && tx.flow) return t(`directions.internal_${tx.flow}`)
  return t(`directions.${tx.direction}`)
}
