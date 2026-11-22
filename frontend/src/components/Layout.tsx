import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { LANGUAGES } from '../i18n'
import { Logo } from './Logo'

const NAV = [
  { to: '/', key: 'dashboard', icon: '◧', end: true },
  { to: '/deposit', key: 'deposit', icon: '↓' },
  { to: '/withdraw', key: 'withdraw', icon: '↑' },
  { to: '/transfer', key: 'transfer', icon: '⇄' },
  { to: '/history', key: 'history', icon: '☰' },
  { to: '/security', key: 'security', icon: '⛨' },
  { to: '/settings', key: 'settings', icon: '⚙' },
] as const

export function LanguageSelect() {
  const { t, i18n } = useTranslation()
  return (
    <select
      className="lang-select"
      aria-label={t('nav.language')}
      value={i18n.language}
      onChange={(e) => i18n.changeLanguage(e.target.value)}
    >
      {LANGUAGES.map((l) => (
        <option key={l.code} value={l.code}>
          {l.label}
        </option>
      ))}
    </select>
  )
}

export function AppLayout() {
  const { t } = useTranslation()
  const { user, signOut } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)

  return (
    <div className="shell">
      <aside className={`sidebar ${open ? 'sidebar-open' : ''}`}>
        <div className="sidebar-brand">
          <Logo />
          <span>{t('app.name')}</span>
        </div>
        <nav className="sidebar-nav" onClick={() => setOpen(false)}>
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={'end' in item} className="nav-link">
              <span className="nav-icon" aria-hidden>
                {item.icon}
              </span>
              {t(`nav.${item.key}`)}
            </NavLink>
          ))}
        </nav>
      </aside>
      {open && <div className="sidebar-backdrop" onClick={() => setOpen(false)} />}
      <div className="main">
        <header className="topbar">
          <button className="menu-button" aria-label={t('nav.menu')} onClick={() => setOpen(!open)}>
            ☰
          </button>
          <div className="topbar-spacer" />
          <LanguageSelect />
          <span className="topbar-user" title={user?.email}>
            {user?.email}
          </span>
          <button
            className="btn btn-ghost"
            onClick={async () => {
              await signOut()
              navigate('/login')
            }}
          >
            {t('nav.logout')}
          </button>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

/** Centered card layout for login, registration and password pages. */
export function AuthLayout({ title, children }: { title: string; children: ReactNode }) {
  const { t } = useTranslation()
  return (
    <div className="auth-page">
      <div className="auth-top">
        <LanguageSelect />
      </div>
      <div className="auth-card">
        <div className="auth-brand">
          <Logo size={40} />
          <div>
            <div className="auth-app">{t('app.name')}</div>
            <div className="muted small">{t('app.tagline')}</div>
          </div>
        </div>
        <h1 className="auth-title">{title}</h1>
        {children}
      </div>
    </div>
  )
}
