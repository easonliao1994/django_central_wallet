import type { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { useAuth } from './auth/AuthContext'
import { AppLayout } from './components/Layout'
import { Spinner } from './components/ui'
import DashboardPage from './pages/DashboardPage'
import DepositPage from './pages/DepositPage'
import ForgotPasswordPage from './pages/ForgotPasswordPage'
import HistoryPage from './pages/HistoryPage'
import LoginPage from './pages/LoginPage'
import NotFoundPage from './pages/NotFoundPage'
import RegisterPage from './pages/RegisterPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import SecurityPage from './pages/SecurityPage'
import SettingsPage from './pages/SettingsPage'
import TransferPage from './pages/TransferPage'
import WithdrawPage from './pages/WithdrawPage'

function RequireAuth({ children }: { children: ReactNode }) {
  const { token, user, loading } = useAuth()
  const location = useLocation()
  if (!token) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  if (loading || !user) return <Spinner />
  return children
}

function GuestOnly({ children }: { children: ReactNode }) {
  const { token } = useAuth()
  return token ? <Navigate to="/" replace /> : children
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<GuestOnly><LoginPage /></GuestOnly>} />
      <Route path="/register" element={<GuestOnly><RegisterPage /></GuestOnly>} />
      <Route path="/forgot-password" element={<GuestOnly><ForgotPasswordPage /></GuestOnly>} />
      {/* Reachable while signed in too: the emailed link should always work. */}
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
        <Route index element={<DashboardPage />} />
        <Route path="deposit" element={<DepositPage />} />
        <Route path="withdraw" element={<WithdrawPage />} />
        <Route path="transfer" element={<TransferPage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="security" element={<SecurityPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
