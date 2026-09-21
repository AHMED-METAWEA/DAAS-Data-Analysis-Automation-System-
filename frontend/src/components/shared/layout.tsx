'use client'

import * as React from 'react'
import { cn } from '@/lib/utils'
import { ScrollArea } from '@/components/ui/scroll-area'

export function SectionScroll({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <ScrollArea className={cn('h-full', className)}>
      <div className="px-6 py-6 max-w-[1600px] mx-auto">{children}</div>
    </ScrollArea>
  )
}

export function SectionHeader({
  title,
  description,
  icon,
  actions,
  className,
}: {
  title: string
  description?: string
  icon?: React.ReactNode
  actions?: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-wrap items-end justify-between gap-3 mb-5', className)}>
      <div className="flex items-start gap-3 min-w-0">
        {icon && (
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary ring-1 ring-inset ring-primary/15">
            {icon}
          </div>
        )}
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight leading-tight">{title}</h1>
          {description && <p className="mt-0.5 text-base text-muted-foreground">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  )
}

export function ContextPanel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <aside className={cn('hidden xl:flex w-[300px] shrink-0 flex-col border-l border-border bg-card/30', className)}>
      {children}
    </aside>
  )
}
