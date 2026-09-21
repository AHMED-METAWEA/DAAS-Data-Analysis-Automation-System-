'use client'

import { useMutation } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

export type CopilotMessage = { role: string; content: string }

export type CopilotResult = {
  route: string
  route_label: string
  answer: string
  figure: { data: any[]; layout: Record<string, any> } | null
  table: Record<string, unknown>[] | null
  report_md: string | null
  tool_error: string | null
  grounded: boolean | null
  conversation_id: string
}

export function useAskCopilot(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: { message: string; history: CopilotMessage[]; conversation_id?: string | null }) =>
      call<CopilotResult>(`/api/v1/projects/${projectId}/copilot/ask`, { method: 'POST', json: input }),
  })
}

// ── Streaming (Server-Sent Events) ───────────────────────────────────────────

export type PlanStepInfo = { route: string; route_label: string }

export type CopilotArtifact = {
  route: string
  route_label: string
  figure: { data: any[]; layout: Record<string, any> } | null
  table: Record<string, unknown>[] | null
  report_md: string | null
  grounded: boolean | null
  tool_error: string | null
}

export type CopilotStreamMeta = { plan: PlanStepInfo[]; conversation_id: string }

export type CopilotStreamStep = {
  index: number
  total: number
  route: string
  route_label: string
  status: 'running' | 'done'
}

export type CopilotStreamDone = {
  route: string
  route_label: string
  answer: string
  artifacts: CopilotArtifact[]
  conversation_id: string
}

export type StreamCopilotOptions = {
  projectId: string
  accessToken?: string | null
  body: { message: string; history: CopilotMessage[]; conversation_id?: string | null }
  signal?: AbortSignal
  onMeta?: (meta: CopilotStreamMeta) => void
  onStep?: (step: CopilotStreamStep) => void
  onToken?: (text: string) => void
  onDone?: (result: CopilotStreamDone) => void
  onError?: (message: string) => void
}

/**
 * Consume the SSE stream from `POST /copilot/ask/stream`. Uses `fetch` +
 * ReadableStream (not the browser `EventSource`, which can't do POST or send an
 * Authorization header). Frames are `data: <json>\n\n`; each JSON has a `type`
 * of meta | token | done | error. Never throws — failures are reported via
 * `onError` so the caller's UI can recover.
 */
export async function streamAskCopilot(opts: StreamCopilotOptions): Promise<void> {
  // 127.0.0.1, not localhost — see the note in lib/api/client.ts (IPv4/IPv6 loopback).
  const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000'
  let res: Response
  try {
    res = await fetch(`${API_URL}/api/v1/projects/${opts.projectId}/copilot/ask/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(opts.accessToken ? { Authorization: `Bearer ${opts.accessToken}` } : {}),
      },
      body: JSON.stringify(opts.body),
      signal: opts.signal,
    })
  } catch {
    opts.onError?.('Could not reach the copilot service.')
    return
  }

  if (!res.ok || !res.body) {
    opts.onError?.(`Copilot request failed (${res.status}).`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let sep: number
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        const dataLine = frame.split('\n').find((l) => l.startsWith('data:'))
        if (!dataLine) continue
        const jsonStr = dataLine.slice(5).trim()
        if (!jsonStr) continue

        let evt: any
        try {
          evt = JSON.parse(jsonStr)
        } catch {
          continue
        }

        switch (evt.type) {
          case 'meta':
            opts.onMeta?.(evt as CopilotStreamMeta)
            break
          case 'step':
            opts.onStep?.(evt as CopilotStreamStep)
            break
          case 'token':
            opts.onToken?.(String(evt.text ?? ''))
            break
          case 'done':
            opts.onDone?.(evt as CopilotStreamDone)
            break
          case 'error':
            opts.onError?.(String(evt.message ?? 'The copilot ran into an error.'))
            break
        }
      }
    }
  } catch (err) {
    if ((err as { name?: string })?.name !== 'AbortError') {
      opts.onError?.('The copilot stream was interrupted.')
    }
  }
}
