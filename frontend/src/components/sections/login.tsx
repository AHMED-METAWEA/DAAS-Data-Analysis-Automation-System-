'use client'

import * as React from 'react'
import { useRouter } from 'next/navigation'
import { signIn } from 'next-auth/react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import * as Icons from 'lucide-react'

const DEMO_EMAIL = 'demo@daas.app'
const DEMO_PASSWORD = 'demo-workspace-2026'

// Mirrors the AuthErrorCode union thrown from authorize() in auth-options.ts —
// NextAuth passes an Error's message straight through as signIn()'s res.error.
// 'Configuration' is NextAuth's own code, raised before authorize() ever runs.
const ERROR_CODES = [
  'EmailAlreadyRegistered', 'WeakPassword', 'InvalidInput',
  'InvalidCredentials', 'AccountDisabled', 'ServerUnreachable', 'AuthFailed',
  'Configuration',
] as const

/**
 * signIn() assumes the auth route always answers with JSON containing a `url`.
 * When the route itself 500s — a misconfigured NEXTAUTH_SECRET is the usual
 * cause — it answers with an HTML error page instead, so `res.json()` rejects
 * and the returned promise never resolves. Without this wrapper the submit
 * handler's `setLoading(false)` never ran and the button span forever with no
 * message at all. Collapsing that to a code keeps every failure path visible.
 */
async function signInSafely(
  ...args: Parameters<typeof signIn>
): Promise<{ ok: boolean; error?: string | null }> {
  try {
    const res = await signIn(...args)
    return { ok: Boolean(res?.ok), error: res?.error }
  } catch {
    return { ok: false, error: 'Configuration' }
  }
}

export function Login({ ssoProviders = { google: false, github: false } }: {
  ssoProviders?: { google: boolean; github: boolean }
}) {
  const t = useTranslations('login')
  const router = useRouter()

  const authErrorMessage = React.useCallback((code: string | null | undefined, mode: 'signin' | 'signup'): string => {
    if (code && (ERROR_CODES as readonly string[]).includes(code)) return t(`errors.${code}`)
    return mode === 'signup' ? t('errors.signupFallback') : t('errors.signinFallback')
  }, [t])
  const [mode, setMode] = React.useState<'signin' | 'signup'>('signin')
  const [email, setEmail] = React.useState('')
  const [password, setPassword] = React.useState('')
  const [name, setName] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [showPassword, setShowPassword] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    const res = await signInSafely('credentials', {
      email,
      password,
      mode,
      name,
      redirect: false,
    })
    setLoading(false)
    if (res.ok) {
      router.push('/command-center')
    } else {
      setError(authErrorMessage(res.error, mode))
    }
  }

  const sso = (provider: 'google' | 'github') => {
    setLoading(true)
    void signIn(provider, { callbackUrl: '/command-center' })
  }

  const demo = async () => {
    setLoading(true)
    setError(null)
    let res = await signInSafely('credentials', {
      email: DEMO_EMAIL,
      password: DEMO_PASSWORD,
      mode: 'signin',
      redirect: false,
    })
    // The demo account is per-database, so on a freshly cloned checkout it does
    // not exist yet — the first sign-in legitimately fails and we create it.
    // Only worth retrying when the credentials were rejected: a server that is
    // down or misconfigured would fail the signup for the same reason.
    if (res.error === 'InvalidCredentials') {
      res = await signInSafely('credentials', {
        email: DEMO_EMAIL,
        password: DEMO_PASSWORD,
        mode: 'signup',
        name: 'Demo Analyst',
        redirect: false,
      })
    }
    setLoading(false)
    if (res.ok) {
      router.push('/command-center')
    } else if (res.error === 'ServerUnreachable' || res.error === 'Configuration') {
      setError(authErrorMessage(res.error, 'signin'))
    } else {
      setError(t('errors.demoFailed'))
    }
  }

  return (
    <div className="min-h-screen flex bg-background text-foreground">
      {/* Left — visual / brand panel */}
      <div className="hidden lg:flex lg:w-[46%] xl:w-[42%] relative overflow-hidden border-r border-border/60">
        {/* Background */}
        <div className="absolute inset-0 bg-grid opacity-40" />
        <div className="absolute inset-0 bg-gradient-to-br from-primary/[0.08] via-transparent to-info/[0.06]" />
        <div className="absolute -top-32 -left-32 h-[420px] w-[420px] rounded-full bg-primary/15 blur-[120px]" />
        <div className="absolute -bottom-40 right-0 h-[420px] w-[420px] rounded-full bg-info/10 blur-[120px]" />

        <div className="relative z-10 flex flex-col justify-between p-10 xl:p-12 w-full">
          {/* Brand */}
          <div className="flex items-center gap-2.5">
            <div className="flex size-8 items-center justify-center rounded-lg bg-primary/15 ring-1 ring-inset ring-primary/25">
              <Icons.Activity className="size-4.5 text-primary" />
            </div>
            <div>
              <p className="text-md font-semibold leading-none tracking-tight">{t('brand')}</p>
              <p className="mt-0.5 text-xs text-muted-foreground leading-none">{t('brandSubtitle')}</p>
            </div>
          </div>

          {/* Middle content */}
          <div className="max-w-md">
            <div className="inline-flex items-center gap-2 rounded-full border border-border/60 bg-card/50 px-3 py-1 text-xs text-muted-foreground mb-6">
              <span className="relative flex size-1.5">
                <span className="absolute inline-flex h-full w-full rounded-full bg-primary opacity-75 animate-ping" />
                <span className="relative inline-flex size-1.5 rounded-full bg-primary" />
              </span>
              {t('trustedBadge')}
            </div>
            <h1 className="text-3xl xl:text-4xl font-semibold leading-[1.05] tracking-[-0.02em]">
              {t('heroTitlePrefix')}<span className="text-gradient-brand">{t('heroTitleHighlight')}</span>
            </h1>
            <p className="mt-4 text-md text-muted-foreground leading-relaxed">
              {t('heroSubtitle')}
            </p>

            {/* Agent chips */}
            <div className="mt-7 flex flex-wrap gap-1.5">
              {[
                { key: 'cleaning', icon: 'Sparkles' },
                { key: 'visualization', icon: 'BarChart3' },
                { key: 'insights', icon: 'Lightbulb' },
                { key: 'forecasting', icon: 'TrendingUp' },
                { key: 'marketing', icon: 'Megaphone' },
                { key: 'churn', icon: 'ShieldAlert' },
              ].map((a) => {
                const Icon = (Icons as any)[a.icon]
                return (
                  <span key={a.key} className="inline-flex items-center gap-1.5 rounded-md border border-border/60 bg-card/40 px-2 py-1 text-xs text-muted-foreground">
                    <Icon className="size-3 text-primary" />
                    {t(`agents.${a.key}`)}
                  </span>
                )
              })}
            </div>
          </div>

          {/* Stat row */}
          <div className="grid grid-cols-3 gap-4 max-w-md">
            {[
              { valueKey: 'agentsValue', labelKey: 'agentsLabel' },
              { valueKey: 'modelsValue', labelKey: 'modelsLabel' },
              { valueKey: 'groundedValue', labelKey: 'groundedLabel' },
            ].map((s) => (
              <div key={s.labelKey}>
                <p className="text-2xl font-semibold tracking-tight text-gradient-brand">{t(`stats.${s.valueKey}`)}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">{t(`stats.${s.labelKey}`)}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Right — form panel */}
      <div className="flex-1 flex flex-col">
        {/* Top bar (mobile brand + back) */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border/60 lg:border-0">
          <div className="flex items-center gap-2.5 lg:hidden">
            <div className="flex size-7 items-center justify-center rounded-lg bg-primary/15 ring-1 ring-inset ring-primary/25">
              <Icons.Activity className="size-4 text-primary" />
            </div>
            <p className="text-md font-semibold">{t('brand')}</p>
          </div>
          <Button variant="ghost" size="sm" onClick={() => router.push('/')} className="ms-auto text-sm text-muted-foreground">
            <Icons.ArrowLeft className="size-3.5 rtl:-scale-x-100" /> {t('backToHome')}
          </Button>
        </div>

        <div className="flex-1 flex items-center justify-center px-6 py-10">
          <div className="w-full max-w-[400px]">
            {/* Header */}
            <div className="mb-6">
              <h2 className="text-2xl font-semibold tracking-tight">
                {mode === 'signin' ? t('signInTitle') : t('signUpTitle')}
              </h2>
              <p className="mt-1.5 text-base text-muted-foreground">
                {mode === 'signin' ? t('signInSubtitle') : t('signUpSubtitle')}
              </p>
            </div>

            {/* SSO — only shown once the corresponding OAuth app credentials
                are configured server-side (see frontend/.env.local.example) */}
            {(ssoProviders.google || ssoProviders.github) && (
              <>
                <div className={cn('grid gap-2 mb-5', ssoProviders.google && ssoProviders.github ? 'grid-cols-2' : 'grid-cols-1')}>
                  {ssoProviders.google && (
                    <Button variant="outline" onClick={() => sso('google')} disabled={loading} className="h-10 text-sm">
                      <Icons.Chrome className="size-4" /> Google
                    </Button>
                  )}
                  {ssoProviders.github && (
                    <Button variant="outline" onClick={() => sso('github')} disabled={loading} className="h-10 text-sm">
                      <Icons.Github className="size-4" /> GitHub
                    </Button>
                  )}
                </div>
                <div className="flex items-center gap-3 my-4">
                  <div className="h-px flex-1 bg-border" />
                  <span className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('continueWithEmail')}</span>
                  <div className="h-px flex-1 bg-border" />
                </div>
              </>
            )}

            {/* Form */}
            <form onSubmit={submit} className="space-y-3.5">
              {mode === 'signup' && (
                <div>
                  <Label htmlFor="name" className="text-sm">{t('fullName')}</Label>
                  <Input
                    id="name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder={t('fullNamePlaceholder')}
                    className="mt-1.5 h-10"
                    required
                  />
                </div>
              )}
              <div>
                <Label htmlFor="email" className="text-sm">{t('workEmail')}</Label>
                <Input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder={t('emailPlaceholder')}
                  className="mt-1.5 h-10"
                  required
                />
              </div>
              <div>
                <div className="flex items-center justify-between">
                  <Label htmlFor="password" className="text-sm">{t('password')}</Label>
                  {mode === 'signin' && (
                    <button type="button" className="text-xs text-primary hover:underline">{t('forgotPassword')}</button>
                  )}
                </div>
                <div className="relative mt-1.5">
                  <Input
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder={t('passwordPlaceholder')}
                    className="h-10 pe-10"
                    minLength={mode === 'signup' ? 8 : undefined}
                    required
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((s) => !s)}
                    className="absolute end-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  >
                    {showPassword ? <Icons.EyeOff className="size-4" /> : <Icons.Eye className="size-4" />}
                  </button>
                </div>
                {mode === 'signup' && (
                  <p className="mt-1.5 text-xs text-muted-foreground">{t('passwordHint')}</p>
                )}
              </div>

              {mode === 'signup' && (
                <label className="flex items-start gap-2 text-xs text-muted-foreground pt-1">
                  <input type="checkbox" required className="mt-0.5 size-3.5 rounded accent-primary" />
                  <span>{t('agreeToTermsPrefix')}<a className="text-primary hover:underline">{t('terms')}</a>{t('and')}<a className="text-primary hover:underline">{t('privacyPolicy')}</a>.</span>
                </label>
              )}

              {error && (
                <p className="rounded-md bg-destructive/10 border border-destructive/30 px-3 py-2 text-sm text-destructive">
                  {error}
                </p>
              )}

              <Button type="submit" className="w-full h-10" disabled={loading}>
                {loading ? (
                  <><Icons.Loader2 className="size-4 animate-spin" /> {mode === 'signin' ? t('signingIn') : t('creatingWorkspace')}</>
                ) : (
                  <>{mode === 'signin' ? t('signIn') : t('createWorkspace')} <Icons.ArrowRight className="size-4 rtl:-scale-x-100" /></>
                )}
              </Button>
            </form>

            {/* Demo skip */}
            <div className="mt-4 rounded-lg border border-dashed border-border/80 bg-muted/20 p-3">
              <div className="flex items-center gap-2 mb-1.5">
                <Icons.Sparkles className="size-3.5 text-primary" />
                <p className="text-xs font-medium">{t('justExploring')}</p>
              </div>
              <p className="text-xs text-muted-foreground leading-relaxed mb-2.5">
                {t.rich('demoDescription', { code: (chunks) => <code className="font-mono">{chunks}</code> })}
              </p>
              <Button variant="outline" size="sm" onClick={demo} disabled={loading} className="w-full h-8 text-sm">
                <Icons.Play className="size-3.5" /> {t('continueWithDemo')}
              </Button>
            </div>

            {/* Mode switch */}
            <p className="mt-5 text-center text-sm text-muted-foreground">
              {mode === 'signin' ? t('noAccount') : t('haveAccount')}
              <button
                onClick={() => setMode(mode === 'signin' ? 'signup' : 'signin')}
                className="text-primary font-medium hover:underline"
              >
                {mode === 'signin' ? t('createOne') : t('signIn')}
              </button>
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-border/60 flex items-center justify-between text-xs text-muted-foreground/70">
          <span>{t('footerCopyright')}</span>
          <div className="flex items-center gap-3">
            <button className="hover:text-foreground">{t('footerTerms')}</button>
            <button className="hover:text-foreground">{t('footerPrivacy')}</button>
            <button className="hover:text-foreground">{t('footerStatus')}</button>
          </div>
        </div>
      </div>
    </div>
  )
}
