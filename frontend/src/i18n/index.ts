import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import en from './en'
import zhTW from './zh-TW'

export const LANGUAGES = [
  { code: 'zh-TW', label: '繁體中文' },
  { code: 'en', label: 'English' },
] as const

const STORAGE_KEY = 'cw.lang'

function initialLanguage(): string {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored && LANGUAGES.some((l) => l.code === stored)) return stored
  return 'zh-TW' // default, whatever the browser says
}

i18n.use(initReactI18next).init({
  resources: { 'zh-TW': { translation: zhTW }, en: { translation: en } },
  lng: initialLanguage(),
  fallbackLng: 'en',
  interpolation: { escapeValue: false }, // React escapes already
})

i18n.on('languageChanged', (lng) => {
  localStorage.setItem(STORAGE_KEY, lng)
  document.documentElement.lang = lng
})
document.documentElement.lang = i18n.language

export default i18n
