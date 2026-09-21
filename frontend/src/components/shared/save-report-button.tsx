'use client'

import * as React from 'react'
import { useSaveReport } from '@/lib/queries/reports'
import { Button } from '@/components/ui/button'
import * as Icons from 'lucide-react'

export function SaveReportButton({
  projectId, type, title, markdown, grounding,
}: {
  projectId: string
  type: 'insights' | 'forecast' | 'marketing' | 'churn'
  title: string
  markdown: string
  grounding?: Record<string, unknown>
}) {
  const saveReport = useSaveReport(projectId)

  if (saveReport.isSuccess) {
    return (
      <Button size="sm" variant="outline" disabled>
        <Icons.CheckCircle2 className="size-3.5 text-success" /> Saved to Reports
      </Button>
    )
  }

  return (
    <Button
      size="sm"
      variant="outline"
      onClick={() => saveReport.mutate({ type, title, markdown, grounding })}
      disabled={saveReport.isPending}
    >
      {saveReport.isPending
        ? <><Icons.Loader2 className="size-3.5 animate-spin" /> Saving…</>
        : <><Icons.Save className="size-3.5" /> Save to Reports</>}
    </Button>
  )
}
