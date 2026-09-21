'use client'

import * as React from 'react'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import { AgentStateBadge } from '@/components/ui/badge'
import * as Icons from 'lucide-react'

export interface AgentIndicatorProps {
  name: string
  icon: string
  state: string
  stateLabel: string
  description?: string
  lastRun?: string
  progress?: { stage: string; detail?: string }
  onClick?: () => void
  compact?: boolean
}

const stateColor: Record<string, string> = {
  idle: 'bg-muted-foreground/40',
  preparing: 'bg-info',
  profiling: 'bg-info',
  analyzing: 'bg-info',
  generating: 'bg-info',
  validating: 'bg-info',
  'back-testing': 'bg-info',
  'waiting-approval': 'bg-warning',
  retrying: 'bg-warning',
  completed: 'bg-success',
  warning: 'bg-warning',
  failed: 'bg-destructive',
  'insufficient-data': 'bg-muted-foreground/40',
}

export function AgentIndicator({
  name,
  icon,
  state,
  stateLabel,
  description,
  lastRun,
  progress,
  onClick,
  compact,
}: AgentIndicatorProps) {
  const Icon = (Icons as any)[icon] ?? Icons.Sparkles
  const dotColor = stateColor[state] ?? 'bg-muted-foreground/40'
  const isActive = ['preparing', 'profiling', 'analyzing', 'generating', 'validating', 'back-testing', 'retrying'].includes(state)

  return (
    <Card
      padding={compact ? 'sm' : 'default'}
      onClick={onClick}
      className={cn(
        'relative overflow-hidden transition-all',
        onClick && 'cursor-pointer hover:border-border hover:bg-accent/30',
        isActive && 'ring-1 ring-info/30'
      )}
    >
      {/* subtle progress bar across top */}
      {isActive && <div className="absolute inset-x-0 top-0 h-px shimmer" />}
      <div className="flex items-start gap-3">
        <div className={cn(
          'flex size-9 shrink-0 items-center justify-center rounded-lg',
          'bg-primary/10 text-primary ring-1 ring-inset ring-primary/15'
        )}>
          <Icon className="size-4.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-base font-semibold leading-tight truncate">{name}</h4>
            <AgentStateBadge state={state} label={stateLabel} />
          </div>
          {!compact && description && (
            <p className="mt-1 text-sm text-muted-foreground leading-relaxed line-clamp-2">{description}</p>
          )}
          <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground/80">
            <span className={cn('size-1.5 rounded-full', dotColor, isActive && 'pulse-ring')} />
            {progress ? (
              <span className="text-info">{progress.stage}{progress.detail ? ` · ${progress.detail}` : ''}</span>
            ) : (
              <span>{lastRun}</span>
            )}
          </div>
        </div>
      </div>
    </Card>
  )
}
