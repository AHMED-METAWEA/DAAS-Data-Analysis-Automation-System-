'use client'

import * as React from 'react'
import { NextIntlClientProvider } from 'next-intl'
import { messages, type Locale } from '@/messages'

const STORAGE_KEY = 'daas-locale'

type LocaleContextValue = { locale: Locale; setLocale: (l: Locale) => void }
const LocaleContext = React.createContext<LocaleContextValue | null>(null)

export function useLocale() {
  const ctx = React.useContext(LocaleContext)
  if (!ctx) throw new Error('useLocale must be used within LocaleProvider')
  return ctx
}

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = React.useState<Locale>('en')

  React.useEffect(() => {
    // Must render the SSR-safe 'en' default first, then upgrade to the
    // persisted preference post-mount — reading localStorage during the
    // lazy useState initializer would make the client's first render differ
    // from the server's, mismatching actual translated text (not just an
    // attribute `suppressHydrationWarning` can paper over).
    const stored = localStorage.getItem(STORAGE_KEY)
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (stored === 'en' || stored === 'ar') setLocaleState(stored)
  }, [])

  React.useEffect(() => {
    document.documentElement.lang = locale
    document.documentElement.dir = locale === 'ar' ? 'rtl' : 'ltr'
  }, [locale])

  const setLocale = React.useCallback((l: Locale) => {
    setLocaleState(l)
    localStorage.setItem(STORAGE_KEY, l)
  }, [])

  return (
    <LocaleContext.Provider value={{ locale, setLocale }}>
      <NextIntlClientProvider locale={locale} messages={messages[locale]} timeZone="UTC" onError={() => {}}>
        {children}
      </NextIntlClientProvider>
    </LocaleContext.Provider>
  )
}
