'use client'

import * as React from 'react'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import { Sparkline } from '@/components/ui/sparkline'
import { ArrowDownRight, ArrowUpRight } from 'lucide-react'

export interface KPICardProps {
  label: string
  value: string
  delta?: string
  trend?: 'up' | 'down' | 'flat'
  sub?: string
  spark?: number[]
  danger?: boolean
  onClick?: () => void
  className?: string
}

export function KPICard({ label, value, delta, trend, sub, spark, danger, onClick, className }: KPICardProps) {
  const positive = trend === 'up'
  const isGood = danger ? trend === 'down' : trend === 'up'

  return (
    <Card
      padding="default"
      onClick={onClick}
      className={cn(
        'relative overflow-hidden group',
        onClick && 'cursor-pointer hover:border-border hover:bg-accent/30',
        className
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1 min-w-0">
          <p className="text-sm font-medium text-muted-foreground truncate">{label}</p>
          <p className="text-xl font-semibold tracking-tight tabular-nums">{value}</p>
        </div>
        {delta && (
          <span
            className={cn(
              'inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-xs font-medium',
              isGood
                ? 'bg-success/12 text-success'
                : 'bg-destructive/12 text-destructive'
            )}
          >
            {positive && <ArrowUpRight className="size-3" />}
            {trend === 'down' && <ArrowDownRight className="size-3" />}
            {trend === 'flat' && <span className="size-1 rounded-full bg-current" />}
            {delta}
          </span>
        )}
      </div>
      <div className="mt-2 flex items-end justify-between gap-3">
        <p className="text-xs text-muted-foreground/80">{sub}</p>
        {spark && (
          <div className="h-8 w-24 shrink-0">
            <Sparkline data={spark} danger={danger} height={32} width={96} />
          </div>
        )}
      </div>
    </Card>
  )
}
