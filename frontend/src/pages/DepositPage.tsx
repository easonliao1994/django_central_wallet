import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import * as api from '../api/endpoints'
import { useUser } from '../auth/AuthContext'
import { Alert, Card, CopyButton, ErrorAlert, PageHeader, Spinner, SvgImage } from '../components/ui'
import { useBalances } from '../lib/hooks'

export default function DepositPage() {
  const { t } = useTranslation()
  const user = useUser()
  const address = useQuery({ queryKey: ['deposit-address'], queryFn: api.getDepositAddress, enabled: user.is_verified })
  const balances = useBalances()

  const coinsByChain = new Map<string, string[]>()
  for (const b of balances.data ?? []) coinsByChain.set(b.blockchain, [...(coinsByChain.get(b.blockchain) ?? []), b.symbol])

  return (
    <>
      <PageHeader title={t('deposit.title')} subtitle={t('deposit.subtitle')} />
      <div className="grid-2">
        <Card title={t('deposit.addressLabel')}>
          {!user.is_verified ? (
            <Alert tone="warning">{t('deposit.notVerified')}</Alert>
          ) : address.isPending ? (
            <Spinner />
          ) : address.error ? (
            <ErrorAlert error={address.error} />
          ) : (
            <div className="deposit">
              <SvgImage svg={address.data.qr_code_svg} alt={address.data.address} size={180} />
              <code className="address-box">{address.data.address}</code>
              <CopyButton value={address.data.address} />
            </div>
          )}
        </Card>
        <Card title={t('deposit.warningTitle')}>
          <ul className="notes">
            <li>
              {t('deposit.warning1')}
              <ul className="chain-coins">
                {[...coinsByChain.entries()].map(([chain, coins]) => (
                  <li key={chain}>
                    <strong>{chain}</strong>: {coins.join(', ')}
                  </li>
                ))}
              </ul>
            </li>
            <li>{t('deposit.warning2')}</li>
            <li>{t('deposit.warning3')}</li>
          </ul>
        </Card>
      </div>
    </>
  )
}
