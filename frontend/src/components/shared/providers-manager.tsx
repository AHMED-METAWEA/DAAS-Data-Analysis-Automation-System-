'use client'

import * as React from 'react'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useProvidersStatus, useModelPreferences, useUpdateModelPreferences } from '@/lib/queries/settings'
import { LoadingState, ErrorState } from '@/components/shared/states'
import * as Icons from 'lucide-react'

export function ProvidersManager() {
  const providersQuery = useProvidersStatus()
  const modelsQuery = useModelPreferences()
  const updatePrefs = useUpdateModelPreferences()
  const [savedToast, setSavedToast] = React.useState(false)

  const flashSaved = () => {
    setSavedToast(true)
    setTimeout(() => setSavedToast(false), 2200)
  }

  const setPreference = (purpose: string, model: string) => {
    updatePrefs.mutate(
      { [purpose]: model === '__default__' ? null : model },
      { onSuccess: flashSaved }
    )
  }

  return (
    <>
      <Card padding="default">
        <div className="flex items-start justify-between gap-3 mb-3">
          <div>
            <h2 className="text-md font-semibold">AI Providers</h2>
            <p className="text-sm text-muted-foreground mt-0.5">
              Fixed fallback chain, configured server-side via environment variables. Requests try each
              configured provider in order until one succeeds.
            </p>
          </div>
          <Button size="sm" variant="outline" disabled>
            <Icons.Plus className="size-3.5" /> Add provider
          </Button>
        </div>

        {providersQuery.isLoading && <LoadingState label="Checking provider configuration…" />}
        {providersQuery.isError && (
          <ErrorState body="Could not load provider status." onRetry={() => providersQuery.refetch()} />
        )}

        {providersQuery.data && (
          <>
            <div className="mb-3 flex items-center gap-2 flex-wrap text-2xs text-muted-foreground">
              <Icons.Layers className="size-3.5 text-primary" />
              <span className="uppercase tracking-wider font-semibold">Fallback chain:</span>
              {providersQuery.data.fallback_order.map((p, i) => (
                <React.Fragment key={p.id}>
                  <span className={cn(
                    'inline-flex items-center gap-1 rounded px-1.5 py-0.5',
                    p.configured ? 'bg-primary/12 text-primary' : 'bg-muted/40 text-muted-foreground line-through'
                  )}>
                    {i + 1}. {p.name}
                  </span>
                  {i < providersQuery.data!.fallback_order.length - 1 && <Icons.ChevronRight className="size-3 text-muted-foreground/40" />}
                </React.Fragment>
              ))}
            </div>

            <div className="space-y-2">
              {providersQuery.data.fallback_order.map((p, i) => (
                <div
                  key={p.id}
                  className={cn(
                    'rounded-lg border bg-background/40 p-3 transition-all',
                    p.configured ? 'border-border/60' : 'border-border/40 opacity-70',
                    i === 0 && p.configured && 'ring-1 ring-primary/20'
                  )}
                >
                  <div className="flex items-center gap-3">
                    <span className="text-2xs font-mono text-muted-foreground w-4 text-center shrink-0">{i + 1}</span>
                    <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted/40 text-2xs font-bold text-muted-foreground">
                      {p.name.slice(0, 2).toUpperCase()}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <p className="text-base font-semibold">{p.name}</p>
                        <Badge variant={p.configured ? 'success' : 'outline'} className="text-2xs">
                          {p.configured ? 'Configured' : 'Not configured'}
                        </Badge>
                        {i === 0 && p.configured && <Badge variant="brand" className="text-2xs">Primary</Badge>}
                        {p.honors_model_override && <Badge variant="outline" className="text-2xs">Per-agent model override</Badge>}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {p.honors_model_override
                          ? 'Honors the per-agent model selection below.'
                          : 'Always serves a fixed model per agent purpose (not user-selectable).'}
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        <div className="mt-4 rounded-lg border border-info/25 bg-info/[0.05] p-3 flex items-start gap-2.5">
          <Icons.Info className="size-4 text-info shrink-0 mt-0.5" />
          <div className="text-xs text-muted-foreground leading-relaxed">
            <span className="text-foreground font-medium">API keys are server-side secrets.</span> Provider keys
            are read from environment variables at startup and are never sent to or editable from the browser.
            To add or change a provider key, set the corresponding <code className="text-2xs">*_API_KEY</code> variable
            and restart the backend.
          </div>
        </div>
      </Card>

      {/* Per-agent model selection */}
      <Card padding="default">
        <h3 className="text-base font-semibold mb-1">Per-agent model selection</h3>
        <p className="text-sm text-muted-foreground mb-3">
          Only takes effect when Groq is the provider that serves the request — every other provider in the
          fallback chain always uses its own fixed model for that agent.
        </p>

        {modelsQuery.isLoading && <LoadingState label="Loading preferences…" />}
        {modelsQuery.isError && (
          <ErrorState body="Could not load model preferences." onRetry={() => modelsQuery.refetch()} />
        )}

        {modelsQuery.data && (
          <div className="space-y-2.5">
            {modelsQuery.data.purposes.map((p) => {
              const current = modelsQuery.data!.preferences[p.purpose] ?? '__default__'
              return (
                <div key={p.purpose} className="flex items-center justify-between gap-3">
                  <span className="text-sm font-medium">{p.label}</span>
                  <Select value={current} onValueChange={(v) => setPreference(p.purpose, v)}>
                    <SelectTrigger className="h-8 w-[280px] text-sm"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__default__">Default ({p.default_model})</SelectItem>
                      {modelsQuery.data!.available_models.map((m) => (
                        <SelectItem key={m} value={m}>{m}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )
            })}
          </div>
        )}
      </Card>

      {savedToast && (
        <div className="fixed bottom-6 right-6 z-50 animate-fade-up">
          <div className="flex items-center gap-2 rounded-lg border border-success/30 bg-card px-3.5 py-2.5 shadow-floating">
            <Icons.CheckCircle2 className="size-4 text-success" />
            <span className="text-sm font-medium">Preference saved</span>
          </div>
        </div>
      )}
    </>
  )
}
