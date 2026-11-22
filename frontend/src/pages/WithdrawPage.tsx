import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import * as api from '../api/endpoints'
import type { InternalTransfer, Withdrawal } from '../api/types'
import { useUser } from '../auth/AuthContext'
import { AmountField, CoinSelect } from '../components/CoinSelect'
import { SecurityNotices } from '../components/SecurityNotices'
import { WithdrawalStatusBadge } from '../components/status'
import {
  Alert, Button, Card, EmptyState, ErrorAlert, Field, Input, OtpInput, PageHeader, Select, Spinner, fieldError,
} from '../components/ui'
import { compareAmounts, formatAmount, formatDateTime, isEvmAddress, isValidAmount, shorten } from '../lib/format'
import { MONEY_KEYS, useBalances, useSavedAddresses } from '../lib/hooks'

export default function WithdrawPage() {
  const { t, i18n } = useTranslation()
  const user = useUser()
  const queryClient = useQueryClient()
  const balances = useBalances()
  const saved = useSavedAddresses()
  const recent = useQuery({ queryKey: ['withdrawals'], queryFn: () => api.getWithdrawals(5) })

  const [coinInfo, setCoinInfo] = useState<number | ''>('')
  const [toAddress, setToAddress] = useState('')
  const [amount, setAmount] = useState('')
  const [otp, setOtp] = useState('')
  const [touched, setTouched] = useState(false)
  const [result, setResult] = useState<Withdrawal | InternalTransfer | null>(null)

  const balance = balances.data?.find((b) => b.coin_info === coinInfo)
  const addressError = touched && toAddress && !isEvmAddress(toAddress) ? t('withdraw.invalidAddress') : undefined
  let amountError: string | undefined
  if (touched && amount && balance) {
    if (!isValidAmount(amount, balance.decimal)) amountError = t('withdraw.invalidAmount')
    else if (compareAmounts(amount, balance.balance) > 0) amountError = t('withdraw.overBalance')
  }

  const withdraw = useMutation({
    mutationFn: () => api.createWithdrawal({ coin_info: Number(coinInfo), to_address: toAddress.trim(), amount, otp_code: otp }),
    onSuccess: (data) => {
      setResult(data)
      setAmount('')
      setOtp('')
      setTouched(false)
      MONEY_KEYS.forEach((key) => queryClient.invalidateQueries({ queryKey: key }))
    },
    onSettled: () => setOtp(''), // codes are single use
  })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    setResult(null)
    if (!isEvmAddress(toAddress) || !balance || !isValidAmount(amount, balance.decimal)) return
    if (compareAmounts(amount, balance.balance) > 0) return
    withdraw.mutate()
  }

  const usableSaved = (saved.data ?? []).filter((a) => a.active)
  const blocked = !user.two_factor_enabled

  return (
    <>
      <PageHeader title={t('withdraw.title')} subtitle={t('withdraw.subtitle')} />
      <SecurityNotices forOutgoing />
      <div className="grid-2">
        <Card>
          {balances.isPending ? (
            <Spinner />
          ) : (
            <form className="stack" onSubmit={submit}>
              {user.withdrawal_whitelist_enabled && <Alert tone="info">{t('withdraw.whitelistOn')}</Alert>}
              <CoinSelect balances={balances.data ?? []} value={coinInfo} onChange={setCoinInfo} error={fieldError(withdraw.error, 'coin_info')} />
              {usableSaved.length > 0 && (
                <Field label={t('withdraw.savedAddresses')}>
                  <Select value="" onChange={(e) => setToAddress(e.target.value)}>
                    <option value="">{t('withdraw.chooseSaved')}</option>
                    {usableSaved.map((a) => (
                      <option key={a.id} value={a.address}>
                        {a.label ? `${a.label} · ` : ''}
                        {shorten(a.address, 10, 8)}
                      </option>
                    ))}
                  </Select>
                </Field>
              )}
              <Field label={t('withdraw.toAddress')} error={addressError ?? fieldError(withdraw.error, 'to_address')}>
                <Input
                  className="input mono"
                  placeholder="0x…"
                  autoComplete="off"
                  spellCheck={false}
                  value={toAddress}
                  onChange={(e) => setToAddress(e.target.value)}
                />
              </Field>
              <AmountField balance={balance} value={amount} onChange={setAmount} error={amountError ?? fieldError(withdraw.error, 'amount')} />
              <Field label={t('common.otpCode')} hint={t('common.otpHint')}>
                <OtpInput value={otp} onChange={setOtp} />
              </Field>
              <ErrorAlert error={withdraw.error} />
              {result && <WithdrawResult result={result} />}
              <Button type="submit" loading={withdraw.isPending} disabled={blocked || otp.length !== 6 || !coinInfo}>
                {t('withdraw.submit')}
              </Button>
              {blocked && <p className="muted small">{t('withdraw.requires2fa')}</p>}
            </form>
          )}
        </Card>
        <Card title={t('withdraw.recent')}>
          {recent.isPending ? (
            <Spinner />
          ) : recent.error ? (
            <ErrorAlert error={recent.error} />
          ) : !recent.data.results.length ? (
            <EmptyState />
          ) : (
            <ul className="activity-list">
              {recent.data.results.map((w) => (
                <li key={w.id} className="activity-row">
                  <div>
                    <div>
                      <strong className="mono">{formatAmount(w.amount, i18n.language)}</strong> {w.symbol}
                      <span className="muted small"> · {w.blockchain}</span>
                    </div>
                    <div className="muted small">
                      <span className="mono">→ {shorten(w.to_address, 8, 6)}</span>
                    </div>
                    <div className="muted small">{formatDateTime(w.created_at, i18n.language)}</div>
                  </div>
                  <div className="activity-side">
                    <WithdrawalStatusBadge status={w.status} />
                    {w.tx_url && (
                      <a href={w.tx_url} target="_blank" rel="noreferrer" className="small">
                        {t('history.viewTx')}
                      </a>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  )
}

function WithdrawResult({ result }: { result: Withdrawal | InternalTransfer }) {
  const { t } = useTranslation()
  if ('type' in result && result.type === 'internal_transfer') return <Alert tone="success">{t('withdraw.routed')}</Alert>
  const status = (result as Withdrawal).status
  return <Alert tone="success">{status === 'pending_review' ? t('withdraw.pendingReview') : t('withdraw.approved')}</Alert>
}
