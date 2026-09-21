'use client'

import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset transition-colors',
  {
    variants: {
      variant: {
        default: 'bg-muted/40 text-muted-foreground ring-border',
        brand: 'bg-primary/12 text-primary ring-primary/25',
        success: 'bg-success/12 text-success ring-success/25',
        warning: 'bg-warning/12 text-warning ring-warning/25',
        danger: 'bg-destructive/12 text-destructive ring-destructive/25',
        info: 'bg-info/12 text-info ring-info/25',
        outline: 'bg-transparent text-muted-foreground ring-border',
      },
    },
    defaultVariants: { variant: 'default' },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}

// ---- Specialized badges ----

export function ConfidenceBadge({ score, className }: { score: number; className?: string }) {
  const variant = score >= 0.9 ? 'success' : score >= 0.7 ? 'warning' : 'danger'
  const label = score >= 0.9 ? 'High confidence' : score >= 0.7 ? 'Medium confidence' : 'Low confidence'
  return (
    <Badge variant={variant} className={className}>
      <span className="size-1.5 rounded-full bg-current" />
      {label} · {Math.round(score * 100)}%
    </Badge>
  )
}

export function GroundingBadge({
  status,
  className,
}: {
  status: 'grounded' | 'partial' | 'review'
  className?: string
}) {
  const map = {
    grounded: { variant: 'success' as const, label: 'Grounded', dot: true },
    partial: { variant: 'warning' as const, label: 'Partially grounded', dot: true },
    review: { variant: 'danger' as const, label: 'Review recommended', dot: true },
  }
  const m = map[status]
  return (
    <Badge variant={m.variant} className={className}>
      <span className="size-1.5 rounded-full bg-current" />
      {m.label}
    </Badge>
  )
}

export function AgentStateBadge({ state, label }: { state: string; label: string }) {
  const map: Record<string, { variant: VariantProps<typeof badgeVariants>['variant']; pulse?: boolean }> = {
    idle: { variant: 'outline' },
    preparing: { variant: 'info', pulse: true },
    profiling: { variant: 'info', pulse: true },
    analyzing: { variant: 'info', pulse: true },
    generating: { variant: 'info', pulse: true },
    validating: { variant: 'info', pulse: true },
    'back-testing': { variant: 'info', pulse: true },
    'waiting-approval': { variant: 'warning' },
    retrying: { variant: 'warning', pulse: true },
    completed: { variant: 'success' },
    warning: { variant: 'warning' },
    failed: { variant: 'danger' },
    'insufficient-data': { variant: 'outline' },
  }
  const m = map[state] ?? { variant: 'outline' }
  return (
    <Badge variant={m.variant}>
      {m.pulse && <span className="size-1.5 rounded-full bg-current pulse-ring" />}
      {label}
    </Badge>
  )
}

export function RiskBadge({ level, className }: { level: 'low' | 'medium' | 'high'; className?: string }) {
  const map = { low: 'success', medium: 'warning', high: 'danger' } as const
  return (
    <Badge variant={map[level]} className={className}>
      {level} risk
    </Badge>
  )
}
