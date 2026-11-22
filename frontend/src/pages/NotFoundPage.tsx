import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { Card, PageHeader } from '../components/ui'

export default function NotFoundPage() {
  const { t } = useTranslation()
  return (
    <>
      <PageHeader title="404" />
      <Card>
        <p>{t('errors.not_found')}</p>
        <Link to="/">{t('nav.dashboard')}</Link>
      </Card>
    </>
  )
}
