'use client'

import * as React from 'react'
import { useTranslations } from 'next-intl'
import { useAppStore } from '@/lib/store'
import {
  streamAskCopilot,
  type CopilotMessage,
  type PlanStepInfo,
} from '@/lib/queries/copilot'
import { useApi } from '@/lib/api/use-api'
import { ChatMessage, type ChatMessageData, type PlanStep } from '@/components/shared/chat-message'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { SectionHeader } from '@/components/shared/layout'
import * as Icons from 'lucide-react'

export function Copilot() {
  const t = useTranslations('copilot')
  const activeProjectId = useAppStore((s) => s.activeProjectId)
  const pendingAskQuestion = useAppStore((s) => s.pendingAskQuestion)
  const setPendingAskQuestion = useAppStore((s) => s.setPendingAskQuestion)
  const { accessToken } = useApi()
  const [messages, setMessages] = React.useState<ChatMessageData[]>([])
  const [historyPairs, setHistoryPairs] = React.useState<CopilotMessage[]>([])
  const [conversationId, setConversationId] = React.useState<string | null>(null)
  const [input, setInput] = React.useState('')
  const [streaming, setStreaming] = React.useState(false)
  const abortRef = React.useRef<AbortController | null>(null)
  const lastQuestionRef = React.useRef<string>('')

  // A conversation (and its cached follow-up context) belongs to exactly one
  // project — switching the active project mid-chat via the sidebar switcher
  // (without navigating away from this page) must not let old messages or a
  // stale conversation_id linger and get treated as follow-ups on unrelated data.
  React.useEffect(() => {
    abortRef.current?.abort()
    /* eslint-disable react-hooks/set-state-in-effect */
    setMessages([])
    setHistoryPairs([])
    setConversationId(null)
    setStreaming(false)
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [activeProjectId])

  // Patch the most recent assistant message in place as the stream progresses.
  // Only ever the trailing placeholder is touched — send is disabled while
  // streaming, so no newer message can slip in ahead of it.
  const patchLastAssistant = React.useCallback((patch: Partial<ChatMessageData>) => {
    setMessages((m) => {
      const copy = [...m]
      for (let i = copy.length - 1; i >= 0; i--) {
        if (copy[i].role === 'assistant') {
          copy[i] = { ...copy[i], ...patch }
          break
        }
      }
      return copy
    })
  }, [])

  const send = React.useCallback((question: string, historyOverride?: CopilotMessage[]) => {
    if (!question.trim() || streaming) return
    lastQuestionRef.current = question
    // `historyOverride` lets Regenerate re-ask with the previous exchange
    // already trimmed off — the setState above hasn't flushed yet, so this
    // callback's `historyPairs` closure would otherwise still include it.
    const history = historyOverride ?? historyPairs
    setMessages((m) => [
      ...m,
      { role: 'user', content: question },
      { role: 'assistant', content: '', streaming: true },
    ])
    setInput('')
    setStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller
    let acc = ''

    void streamAskCopilot({
      projectId: activeProjectId,
      accessToken,
      signal: controller.signal,
      body: { message: question, history, conversation_id: conversationId },
      onMeta: (meta) => {
        const plan: PlanStep[] = meta.plan.map((p: PlanStepInfo) => ({ ...p, status: 'pending' }))
        patchLastAssistant({ plan })
      },
      onStep: (step) => {
        setMessages((m) => {
          const copy = [...m]
          for (let i = copy.length - 1; i >= 0; i--) {
            if (copy[i].role === 'assistant') {
              const plan = (copy[i].plan ?? []).map((p, idx) =>
                idx === step.index ? { ...p, status: step.status } : p,
              )
              copy[i] = { ...copy[i], plan }
              break
            }
          }
          return copy
        })
      },
      onToken: (text) => {
        acc += text
        patchLastAssistant({ content: acc })
      },
      onDone: (res) => {
        const finalText = res.answer || acc
        patchLastAssistant({
          content: finalText,
          artifacts: res.artifacts,
          plan: res.artifacts.length > 1
            ? res.artifacts.map((a) => ({ route: a.route, route_label: a.route_label, status: 'done' as const }))
            : undefined,
          streaming: false,
        })
        setHistoryPairs((h) => [
          ...h,
          { role: 'user', content: question },
          { role: 'assistant', content: finalText },
        ])
        setConversationId(res.conversation_id)
        setStreaming(false)
        abortRef.current = null
      },
      onError: (msg) => {
        patchLastAssistant({ content: acc || msg || t('errorRetry'), streaming: false })
        setStreaming(false)
        abortRef.current = null
      },
    })
  }, [activeProjectId, accessToken, historyPairs, conversationId, streaming, patchLastAssistant, t])

  const stop = React.useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    patchLastAssistant({ streaming: false })
    setStreaming(false)
  }, [patchLastAssistant])

  const regenerate = React.useCallback(() => {
    if (streaming || !lastQuestionRef.current) return
    // Drop the previous assistant answer (and its user turn) and re-ask the same
    // question against the current data. The trimmed history is passed to send
    // explicitly so the re-ask doesn't see its own previous answer.
    const trimmed = historyPairs.slice(0, -2)
    setMessages((m) => {
      const copy = [...m]
      if (copy.length && copy[copy.length - 1].role === 'assistant') copy.pop()
      if (copy.length && copy[copy.length - 1].role === 'user') copy.pop()
      return copy
    })
    setHistoryPairs(trimmed)
    send(lastQuestionRef.current, trimmed)
  }, [streaming, historyPairs, send])

  React.useEffect(() => {
    if (!pendingAskQuestion) return
    const question = pendingAskQuestion
    queueMicrotask(() => {
      setPendingAskQuestion(null)
      send(question)
    })
  }, [pendingAskQuestion, send, setPendingAskQuestion])

  const suggestions = [t('suggestion1'), t('suggestion2'), t('suggestion3'), t('suggestion4')]
  const lastAssistantIdx = messages.map((m) => m.role).lastIndexOf('assistant')

  return (
    <div className="flex h-full flex-col px-6 py-6 max-w-[900px] mx-auto w-full">
      <SectionHeader
        title={t('title')}
        description={t('description')}
        icon={<Icons.Sparkles className="size-4.5" />}
      />
      <Card padding="default" className="flex-1 min-h-0 flex flex-col">
        <ScrollArea className="flex-1 min-h-0 pe-2">
          <div className="space-y-4">
            {messages.length === 0 && (
              <div className="space-y-3">
                <p className="text-sm text-muted-foreground">{t('emptyHint')}</p>
                <div className="flex flex-col gap-2">
                  <span className="text-xs font-medium text-muted-foreground/80">{t('suggestionsTitle')}</span>
                  <div className="flex flex-wrap gap-2">
                    {suggestions.map((s, i) => (
                      <button
                        key={i}
                        onClick={() => send(s)}
                        className="rounded-full border border-border/60 bg-background/40 px-3 py-1.5 text-xs text-left hover:border-primary/50 hover:bg-primary/5 transition-colors"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <ChatMessage
                key={i}
                data={m}
                projectId={activeProjectId}
                onRegenerate={!streaming && i === lastAssistantIdx ? regenerate : undefined}
              />
            ))}
          </div>
        </ScrollArea>
        <div className="flex items-center gap-2 pt-3 mt-3 border-t border-border/60">
          <Input
            placeholder={t('placeholder')}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') send(input) }}
            disabled={streaming}
          />
          {streaming ? (
            <Button size="sm" variant="outline" onClick={stop}>
              <Icons.Square className="size-3.5" /> {t('stop')}
            </Button>
          ) : (
            <Button size="sm" onClick={() => send(input)} disabled={!input.trim()}>
              <Icons.Send className="size-3.5 rtl:-scale-x-100" />
            </Button>
          )}
        </div>
      </Card>
    </div>
  )
}
