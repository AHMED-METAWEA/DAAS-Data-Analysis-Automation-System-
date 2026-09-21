'use client'

import { useLocale } from '@/components/locale-provider'
import { cn } from '@/lib/utils'
import * as Icons from 'lucide-react'

export function LanguageToggle({ collapsed }: { collapsed?: boolean }) {
  const { locale, setLocale } = useLocale()
  const isArabic = locale === 'ar'

  return (
    <button
      onClick={() => setLocale(isArabic ? 'en' : 'ar')}
      className={cn(
        'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm font-medium text-muted-foreground outline-none transition-colors hover:bg-accent/40 hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50',
        collapsed && 'justify-center px-0'
      )}
      aria-label={isArabic ? 'Switch to English' : 'التبديل إلى العربية'}
    >
      <Icons.Languages className="size-4" />
      {!collapsed && <span>{isArabic ? 'English' : 'العربية'}</span>}
    </button>
  )
}
