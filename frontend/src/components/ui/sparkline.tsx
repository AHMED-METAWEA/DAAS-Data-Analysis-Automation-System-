'use client'

import * as React from 'react'
import { cn } from '@/lib/utils'

export function Sparkline({
  data,
  color = 'var(--primary)',
  className,
  height = 32,
  width = 100,
  danger = false,
}: {
  data: number[]
  color?: string
  className?: string
  height?: number
  width?: number
  danger?: boolean
}) {
  const ref = React.useRef<SVGSVGElement>(null)
  const id = React.useId()
  if (data.length === 0) return null

  const max = Math.max(...data)
  const min = Math.min(...data)
  const range = max - min || 1
  const stepX = width / (data.length - 1)

  const points = data.map((v, i) => {
    const x = i * stepX
    const y = height - ((v - min) / range) * (height - 4) - 2
    return [x, y] as const
  })

  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0]},${p[1]}`).join(' ')
  const areaPath = `${linePath} L${width},${height} L0,${height} Z`

  return (
    <svg
      ref={ref}
      viewBox={`0 0 ${width} ${height}`}
      className={cn('overflow-visible', className)}
      preserveAspectRatio="none"
      style={{ width: '100%', height }}
    >
      <defs>
        <linearGradient id={`spark-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={danger ? 'var(--destructive)' : color} stopOpacity="0.32" />
          <stop offset="100%" stopColor={danger ? 'var(--destructive)' : color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#spark-${id})`} />
      <path
        d={linePath}
        fill="none"
        stroke={danger ? 'var(--destructive)' : color}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle
        cx={points[points.length - 1][0]}
        cy={points[points.length - 1][1]}
        r="2.4"
        fill={danger ? 'var(--destructive)' : color}
      />
    </svg>
  )
}
