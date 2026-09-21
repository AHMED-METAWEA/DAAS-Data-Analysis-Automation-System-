'use client'

import * as React from 'react'
import { cn } from '@/lib/utils'
import * as Icons from 'lucide-react'

export interface PipelineStage {
  id: string
  label: string
  icon: string
}

export interface PipelineProgressProps {
  stages: PipelineStage[]
  current: string
  completed?: string[]
  onStageClick?: (id: string) => void
  className?: string
}

export function PipelineProgress({ stages, current, completed = [], onStageClick, className }: PipelineProgressProps) {
  const currentIdx = stages.findIndex((s) => s.id === current)
  return (
    <div className={cn('flex items-center gap-1 overflow-x-auto no-scrollbar', className)}>
      {stages.map((stage, idx) => {
        const Icon = (Icons as any)[stage.icon] ?? Icons.Circle
        const isCompleted = completed.includes(stage.id) || idx < currentIdx
        const isCurrent = stage.id === current
        const isLast = idx === stages.length - 1
        return (
          <React.Fragment key={stage.id}>
            <button
              type="button"
              onClick={() => onStageClick?.(stage.id)}
              className={cn(
                'group flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors shrink-0',
                isCurrent
                  ? 'bg-primary/12 text-primary ring-1 ring-inset ring-primary/25'
                  : isCompleted
                  ? 'text-foreground hover:bg-accent/40'
                  : 'text-muted-foreground hover:bg-accent/30'
              )}
            >
              <span
                className={cn(
                  'flex size-5 items-center justify-center rounded-md transition-colors',
                  isCurrent
                    ? 'bg-primary text-primary-foreground'
                    : isCompleted
                    ? 'bg-success/15 text-success'
                    : 'bg-muted/60 text-muted-foreground'
                )}
              >
                {isCompleted ? <Icons.Check className="size-3" /> : <Icon className="size-3" />}
              </span>
              {stage.label}
            </button>
            {!isLast && (
              <div className="h-px w-4 sm:w-6 shrink-0 bg-border" />
            )}
          </React.Fragment>
        )
      })}
    </div>
  )
}
