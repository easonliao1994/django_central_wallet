import { useTranslation } from 'react-i18next'

import type { Balance } from '../api/types'
import { formatAmount } from '../lib/format'
import { Field, Select } from './ui'

/** Pick a coin on a chain, showing the available balance. */
export function CoinSelect({ balances, value, onChange, error }: {
  balances: Balance[]
  value: number | ''
  onChange: (coinInfo: number) => void
  error?: string[]
}) {
  const { t, i18n } = useTranslation()
  return (
    <Field label={t('common.coin')} error={error}>
      <Select value={value} onChange={(e) => onChange(Number(e.target.value))} required>
        <option value="" disabled>
          —
        </option>
        {balances.map((b) => (
          <option key={b.coin_info} value={b.coin_info}>
            {b.symbol} · {b.blockchain} ({formatAmount(b.balance, i18n.language)})
          </option>
        ))}
      </Select>
    </Field>
  )
}

/** Amount input with the available balance and a Max button. */
export function AmountField({ balance, value, onChange, error }: {
  balance: Balance | undefined
  value: string
  onChange: (v: string) => void
  error?: string | string[]
}) {
  const { t, i18n } = useTranslation()
  return (
    <Field
      label={t('common.amount')}
      error={error}
      aside={
        balance && (
          <button type="button" className="link-button" onClick={() => onChange(balance.balance)}>
            {t('common.available', { amount: formatAmount(balance.balance, i18n.language), symbol: balance.symbol })} ·{' '}
            {t('common.max')}
          </button>
        )
      }
    >
      <input
        className="input mono"
        inputMode="decimal"
        autoComplete="off"
        placeholder="0.00"
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/[^\d.]/g, ''))}
      />
    </Field>
  )
}
