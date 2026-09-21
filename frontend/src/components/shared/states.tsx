'use client'

import * as React from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import * as Icons from 'lucide-react'
import { Button } from '@/components/ui/button'

export interface EmptyStateProps {
  icon?: string
  title: string
  description?: string
  actionLabel?: string
  onAction?: () => void
  secondaryLabel?: string
  onSecondary?: () => void
  className?: string
  size?: 'sm' | 'md' | 'lg'
}

export function EmptyState({
  icon = 'Inbox',
  title,
  description,
  actionLabel,
  onAction,
  secondaryLabel,
  onSecondary,
  className,
  size = 'md',
}: EmptyStateProps) {
  const Icon = (Icons as any)[icon] ?? Icons.Inbox
  const dims = {
    sm: { icon: 'size-8', pad: 'py-6' },
    md: { icon: 'size-10', pad: 'py-10' },
    lg: { icon: 'size-12', pad: 'py-16' },
  }[size]

  return (
    <div className={cn(
      'flex flex-col items-center justify-center text-center px-6',
      dims.pad,
      className
    )}>
      <div className="flex items-center justify-center rounded-2xl bg-muted/40 ring-1 ring-inset ring-border p-3 mb-4">
        <Icon className={cn(dims.icon, 'text-muted-foreground')} />
      </div>
      <h3 className="text-md font-semibold tracking-tight">{title}</h3>
      {description && (
        <p className="mt-1.5 max-w-sm text-sm text-muted-foreground leading-relaxed">{description}</p>
      )}
      {(actionLabel || secondaryLabel) && (
        <div className="mt-4 flex items-center gap-2">
          {actionLabel && (
            <Button size="sm" onClick={onAction}>{actionLabel}</Button>
          )}
          {secondaryLabel && (
            <Button size="sm" variant="outline" onClick={onSecondary}>{secondaryLabel}</Button>
          )}
        </div>
      )}
    </div>
  )
}

export function LoadingState({ label, stage, className }: { label?: string; stage?: string; className?: string }) {
  const t = useTranslations('common')
  return (
    <div className={cn('flex flex-col items-center justify-center py-12 px-6', className)}>
      <div className="relative">
        <div className="size-10 rounded-full border-2 border-muted-foreground/20 border-t-primary animate-spin" />
      </div>
      <p className="mt-3 text-base font-medium">{label ?? t('loading')}</p>
      {stage && <p className="mt-1 text-xs text-muted-foreground">{stage}</p>}
    </div>
  )
}

export function ErrorState({ title, body, onRetry, className }: { title?: string; body?: string; onRetry?: () => void; className?: string }) {
  const t = useTranslations('common')
  return (
    <div className={cn('flex flex-col items-center justify-center text-center px-6 py-10', className)}>
      <div className="flex items-center justify-center rounded-2xl bg-destructive/10 ring-1 ring-inset ring-destructive/25 p-3 mb-4">
        <Icons.AlertTriangle className="size-8 text-destructive" />
      </div>
      <h3 className="text-md font-semibold">{title ?? t('somethingWrong')}</h3>
      {body && <p className="mt-1.5 max-w-sm text-sm text-muted-foreground">{body}</p>}
      {onRetry && (
        <Button size="sm" variant="outline" className="mt-4" onClick={onRetry}>
          <Icons.RotateCcw className="size-3.5" /> {t('tryAgain')}
        </Button>
      )}
    </div>
  )
}
