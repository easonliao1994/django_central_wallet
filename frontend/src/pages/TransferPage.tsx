import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import * as api from '../api/endpoints'
import type { InternalTransfer } from '../api/types'
import { useUser } from '../auth/AuthContext'
import { AmountField, CoinSelect } from '../components/CoinSelect'
import { SecurityNotices } from '../components/SecurityNotices'
import { Alert, Badge, Button, Card, EmptyState, ErrorAlert, Field, Input, OtpInput, PageHeader, Spinner, fieldError } from '../components/ui'
import { compareAmounts, formatAmount, formatDateTime, isValidAmount } from '../lib/format'
import { MONEY_KEYS, useBalances } from '../lib/hooks'

export default function TransferPage() {
  const { t, i18n } = useTranslation()
  const user = useUser()
  const queryClient = useQueryClient()
  const balances = useBalances()
  const recent = useQuery({ queryKey: ['transfers'], queryFn: () => api.getTransfers(8) })

  const [coinInfo, setCoinInfo] = useState<number | ''>('')
  const [to, setTo] = useState('')
  const [amount, setAmount] = useState('')
  const [memo, setMemo] = useState('')
  const [otp, setOtp] = useState('')
  const [touched, setTouched] = useState(false)
  const [done, setDone] = useState<InternalTransfer | null>(null)

  const balance = balances.data?.find((b) => b.coin_info === coinInfo)
  let amountError: string | undefined
  if (touched && amount && balance) {
    if (!isValidAmount(amount, balance.decimal)) amountError = t('withdraw.invalidAmount')
    else if (compareAmounts(amount, balance.balance) > 0) amountError = t('withdraw.overBalance')
  }

  const transfer = useMutation({
    mutationFn: () => api.createTransfer({ coin_info: Number(coinInfo), to: to.trim(), amount, memo, otp_code: otp }),
    onSuccess: (data) => {
      setDone(data)
      setAmount('')
      setMemo('')
      setTouched(false)
      MONEY_KEYS.forEach((key) => queryClient.invalidateQueries({ queryKey: key }))
    },
    onSettled: () => setOtp(''),
  })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    setDone(null)
    if (!balance || !to.trim() || !isValidAmount(amount, balance.decimal) || compareAmounts(amount, balance.balance) > 0) return
    transfer.mutate()
  }

  return (
    <>
      <PageHeader title={t('transfer.title')} subtitle={t('transfer.subtitle')} />
      <SecurityNotices forOutgoing />
      <div className="grid-2">
        <Card>
          {balances.isPending ? (
            <Spinner />
          ) : (
            <form className="stack" onSubmit={submit}>
              <CoinSelect balances={balances.data ?? []} value={coinInfo} onChange={setCoinInfo} error={fieldError(transfer.error, 'coin_info')} />
              <Field label={t('transfer.to')} hint={t('transfer.toHint')} error={fieldError(transfer.error, 'to')}>
                <Input required autoComplete="off" spellCheck={false} value={to} onChange={(e) => setTo(e.target.value)} />
              </Field>
              <AmountField balance={balance} value={amount} onChange={setAmount} error={amountError ?? fieldError(transfer.error, 'amount')} />
              <Field label={`${t('transfer.memo')} (${t('common.optional')})`} error={fieldError(transfer.error, 'memo')}>
                <Input maxLength={140} value={memo} onChange={(e) => setMemo(e.target.value)} />
              </Field>
              <Field label={t('common.otpCode')} hint={t('common.otpHint')}>
                <OtpInput value={otp} onChange={setOtp} />
              </Field>
              <ErrorAlert error={transfer.error} />
              {done && (
                <Alert tone="success">
                  {t('transfer.done', { amount: formatAmount(done.amount, i18n.language), symbol: done.symbol, to: done.to ?? '' })}
                </Alert>
              )}
              <Button type="submit" loading={transfer.isPending} disabled={!user.two_factor_enabled || otp.length !== 6 || !coinInfo}>
                {t('transfer.submit')}
              </Button>
            </form>
          )}
        </Card>
        <Card title={t('transfer.recent')}>
          {recent.isPending ? (
            <Spinner />
          ) : recent.error ? (
            <ErrorAlert error={recent.error} />
          ) : !recent.data.results.length ? (
            <EmptyState />
          ) : (
            <ul className="activity-list">
              {recent.data.results.map((tr) => (
                <li key={tr.id} className="activity-row">
                  <div>
                    <div>
                      <strong className={`mono ${tr.flow === 'in' ? 'positive' : ''}`}>
                        {tr.flow === 'in' ? '+' : '−'}
                        {formatAmount(tr.amount, i18n.language)}
                      </strong>{' '}
                      {tr.symbol}
                    </div>
                    <div className="muted small">
                      {tr.flow === 'out' ? t('transfer.sentTo', { to: tr.to }) : t('transfer.received')}
                      {tr.memo && ` · “${tr.memo}”`} · {formatDateTime(tr.created_at, i18n.language)}
                    </div>
                  </div>
                  <Badge tone={tr.flow === 'in' ? 'success' : 'neutral'}>{tr.blockchain}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  )
}
