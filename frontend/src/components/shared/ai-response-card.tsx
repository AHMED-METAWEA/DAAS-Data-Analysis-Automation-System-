'use client'

import * as React from 'react'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import { GroundingBadge } from '@/components/ui/badge'
import { Sparkles, CheckCircle2, FileText, Brain } from 'lucide-react'

export interface AIResponseCardProps {
  prompt?: string
  response: string
  sources?: string[]
  grounding?: 'grounded' | 'partial' | 'review'
  variant?: 'default' | 'inline' | 'featured'
  className?: string
  children?: React.ReactNode
}

export function AIResponseCard({
  prompt,
  response,
  sources = [],
  grounding = 'grounded',
  variant = 'default',
  className,
  children,
}: AIResponseCardProps) {
  const featured = variant === 'featured'
  return (
    <Card
      padding="default"
      className={cn(
        'relative overflow-hidden',
        featured && 'glass',
        className
      )}
    >
      {/* gradient accent */}
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/40 to-transparent" />

      <div className="flex items-start gap-3">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/12 text-primary ring-1 ring-inset ring-primary/20">
          <Sparkles className="size-4" />
        </div>
        <div className="min-w-0 flex-1 space-y-2.5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 text-sm font-medium text-primary">
              DAAS
              <span className="text-muted-foreground/60 font-normal">· AI interpretation</span>
            </div>
            <GroundingBadge status={grounding} />
          </div>
          {prompt && (
            <div className="rounded-lg bg-muted/30 px-3 py-2 text-sm text-muted-foreground italic border-l-2 border-primary/40">
              “{prompt}”
            </div>
          )}
          <div className="text-base leading-relaxed text-foreground/90 whitespace-pre-wrap">{response}</div>
          {sources.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 pt-1.5 border-t border-border/60">
              <span className="inline-flex items-center gap-1 text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">
                <FileText className="size-3" /> Sources
              </span>
              {sources.map((s, i) => (
                <span key={i} className="inline-flex items-center gap-1 rounded-md bg-muted/40 px-1.5 py-0.5 text-xs text-muted-foreground">
                  <CheckCircle2 className="size-2.5 text-success" /> {s}
                </span>
              ))}
            </div>
          )}
          {children}
        </div>
      </div>
    </Card>
  )
}

export function AIInsightCallout({
  label,
  body,
  icon = 'Brain',
  className,
}: {
  label: string
  body: string
  icon?: string
  className?: string
}) {
  return (
    <div className={cn(
      'relative rounded-lg border border-primary/20 bg-primary/[0.04] p-3.5',
      className
    )}>
      <div className="absolute left-0 top-0 bottom-0 w-0.5 rounded-l bg-primary/60" />
      <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-primary">
        <Brain className="size-3" />
        {label}
      </div>
      <p className="mt-1 text-sm leading-relaxed text-foreground/85">{body}</p>
    </div>
  )
}
