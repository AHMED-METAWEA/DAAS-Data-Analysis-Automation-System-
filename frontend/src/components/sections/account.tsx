'use client'

import * as React from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/lib/store'
import { useSignOut } from '@/components/shared/session-sync'
import {
  useProfile, useUpdateProfile, useChangePassword,
  useApiKeys, useCreateApiKey, useRevokeApiKey,
} from '@/lib/queries/account'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogClose,
} from '@/components/ui/dialog'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import { LoadingState, ErrorState } from '@/components/shared/states'
import * as Icons from 'lucide-react'

const sectionIcons = [
  { id: 'profile', icon: 'User' },
  { id: 'organization', icon: 'Building2' },
  { id: 'security', icon: 'ShieldCheck' },
  { id: 'api-keys', icon: 'KeyRound' },
  { id: 'activity', icon: 'History' },
  { id: 'billing', icon: 'CreditCard' },
]

const sectionNavKey: Record<string, string> = {
  profile: 'profile', organization: 'organization', security: 'security',
  'api-keys': 'apiKeys', activity: 'activity', billing: 'billing',
}

export function Account() {
  const t = useTranslations('account')
  const sections = sectionIcons.map((s) => ({ ...s, label: t(`nav.${sectionNavKey[s.id]}`) }))
  const [active, setActive] = React.useState('profile')

  return (
    <SectionScroll>
      <SectionHeader
        title={t('header.title')}
        description={t('header.description')}
        icon={<Icons.UserCog className="size-4.5" />}
      />

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        {/* Side nav */}
        <Card padding="sm" className="lg:col-span-1 h-fit sticky top-0">
          <nav className="space-y-0.5">
            {sections.map((s) => {
              const Icon = (Icons as any)[s.icon]
              const isActive = active === s.id
              return (
                <button
                  key={s.id}
                  onClick={() => setActive(s.id)}
                  className={cn(
                    'flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-start',
                    isActive ? 'bg-primary/12 text-primary' : 'text-muted-foreground hover:bg-accent/40 hover:text-foreground'
                  )}
                >
                  <Icon className="size-4" />
                  {s.label}
                </button>
              )
            })}
          </nav>
        </Card>

        <div className="lg:col-span-3 space-y-4">
          {active === 'profile' && <ProfileSection />}
          {active === 'organization' && <OrganizationSection />}
          {active === 'security' && <SecuritySection />}
          {active === 'api-keys' && <ApiKeysSection />}
          {active === 'activity' && <ActivitySection />}
          {active === 'billing' && <BillingSection />}
        </div>
      </div>
    </SectionScroll>
  )
}

function ProfileSection() {
  const t = useTranslations('account')
  const profileQuery = useProfile()

  if (profileQuery.isLoading) return <Card padding="none"><LoadingState label={t('profile.loading')} /></Card>
  if (profileQuery.isError) return <Card padding="none"><ErrorState body={t('profile.loadFailed')} onRetry={() => profileQuery.refetch()} /></Card>
  if (!profileQuery.data) return null

  return <ProfileForm profile={profileQuery.data} />
}

function ProfileForm({ profile }: { profile: NonNullable<ReturnType<typeof useProfile>['data']> }) {
  const t = useTranslations('account')
  const updateProfile = useUpdateProfile()
  const updateStoreUser = useAppStore((s) => s.updateUser)
  const storeUser = useAppStore((s) => s.user)
  const logout = useSignOut()

  const [name, setName] = React.useState(profile.name)
  const [role, setRole] = React.useState(profile.role)
  const [organization, setOrganization] = React.useState(profile.organization ?? '')
  const [timezone, setTimezone] = React.useState(profile.timezone ?? '')
  const [bio, setBio] = React.useState(profile.bio ?? '')
  const [saved, setSaved] = React.useState(false)

  const save = () => {
    updateProfile.mutate(
      { name, role, organization, timezone, bio },
      {
        onSuccess: () => {
          updateStoreUser({ name, role })
          setSaved(true)
          setTimeout(() => setSaved(false), 2000)
        },
      }
    )
  }

  return (
    <Card padding="default">
      <h2 className="text-md font-semibold mb-1">{t('profile.title')}</h2>
      <p className="text-sm text-muted-foreground mb-5">{t('profile.description')}</p>

      {/* Avatar + identity */}
      <div className="flex items-center gap-4 mb-6 pb-6 border-b border-border/60">
        <div className="flex size-16 items-center justify-center rounded-xl bg-gradient-to-br from-primary/40 to-info/30 text-xl font-semibold text-primary-foreground ring-1 ring-inset ring-primary/30">
          {storeUser?.initials ?? 'U'}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-md font-semibold">{profile.name}</p>
          <p className="text-sm text-muted-foreground">{profile.email}</p>
          <div className="mt-1.5 flex items-center gap-1.5">
            <Badge variant="outline">{profile.role}</Badge>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <Label className="text-sm">{t('profile.fullName')}</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} className="mt-1.5 h-9" />
        </div>
        <div>
          <Label className="text-sm">{t('profile.email')}</Label>
          <Input value={profile.email} disabled className="mt-1.5 h-9" />
          <p className="mt-1 text-2xs text-muted-foreground/70">{t('profile.emailHint')}</p>
        </div>
        <div>
          <Label className="text-sm">{t('profile.role')}</Label>
          <Select value={role} onValueChange={setRole}>
            <SelectTrigger className="mt-1.5 h-9 text-sm"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="Owner">{t('profile.roles.owner')}</SelectItem>
              <SelectItem value="Admin">{t('profile.roles.admin')}</SelectItem>
              <SelectItem value="Analyst">{t('profile.roles.analyst')}</SelectItem>
              <SelectItem value="Viewer">{t('profile.roles.viewer')}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label className="text-sm">{t('profile.timezone')}</Label>
          <Select value={timezone || undefined} onValueChange={setTimezone}>
            <SelectTrigger className="mt-1.5 h-9 text-sm"><SelectValue placeholder={t('profile.timezonePlaceholder')} /></SelectTrigger>
            <SelectContent>
              <SelectItem value="Africa/Cairo">Africa/Cairo (UTC+02)</SelectItem>
              <SelectItem value="Asia/Dubai">Asia/Dubai (UTC+04)</SelectItem>
              <SelectItem value="Europe/London">Europe/London (UTC+00)</SelectItem>
              <SelectItem value="America/New_York">America/New_York (UTC-05)</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label className="text-sm">{t('profile.organizationLabel')}</Label>
          <Input value={organization} onChange={(e) => setOrganization(e.target.value)} className="mt-1.5 h-9" placeholder={t('profile.organizationPlaceholder')} />
        </div>
        <div className="md:col-span-2">
          <Label className="text-sm">{t('profile.shortBio')}</Label>
          <Textarea value={bio} onChange={(e) => setBio(e.target.value)} className="mt-1.5 min-h-[72px]" />
        </div>
      </div>

      {updateProfile.isError && (
        <p className="mt-3 text-sm text-destructive">{t('profile.saveFailed')}</p>
      )}

      <div className="mt-5 flex items-center justify-between pt-4 border-t border-border/60">
        <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={logout}>
          <Icons.LogOut className="size-3.5" /> {t('profile.signOut')}
        </Button>
        <div className="flex items-center gap-2">
          {saved && (
            <span className="inline-flex items-center gap-1 text-xs text-success animate-fade-up">
              <Icons.CheckCircle2 className="size-3.5" /> {t('profile.saved')}
            </span>
          )}
          <Button size="sm" onClick={save} disabled={updateProfile.isPending}>
            {updateProfile.isPending ? <Icons.Loader2 className="size-3.5 animate-spin" /> : <Icons.Check className="size-3.5" />} {t('profile.saveChanges')}
          </Button>
        </div>
      </div>
    </Card>
  )
}

function OrganizationSection() {
  const t = useTranslations('account')
  return (
    <Card padding="default">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-base font-semibold">{t('organization.title')}</h3>
            <Badge variant="warning" className="text-2xs">{t('organization.futureReady')}</Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            {t.rich('organization.description', { field: (chunks) => <span className="font-medium text-foreground">{chunks}</span> })}
          </p>
        </div>
      </div>
      <div className="rounded-lg border border-dashed border-border/80 bg-muted/20 p-4 text-center">
        <Icons.Users className="size-6 text-muted-foreground/60 mx-auto mb-2" />
        <p className="text-sm font-medium">{t('organization.comingSoonTitle')}</p>
        <p className="mt-0.5 text-xs text-muted-foreground max-w-sm mx-auto">
          {t('organization.comingSoonBody')}
        </p>
      </div>
    </Card>
  )
}

function SecuritySection() {
  const t = useTranslations('account')
  const changePassword = useChangePassword()
  const [currentPassword, setCurrentPassword] = React.useState('')
  const [newPassword, setNewPassword] = React.useState('')
  const [success, setSuccess] = React.useState(false)

  const submit = () => {
    setSuccess(false)
    changePassword.mutate(
      { current_password: currentPassword, new_password: newPassword },
      {
        onSuccess: () => {
          setCurrentPassword('')
          setNewPassword('')
          setSuccess(true)
          setTimeout(() => setSuccess(false), 3000)
        },
      }
    )
  }

  return (
    <Card padding="default">
      <h2 className="text-md font-semibold mb-1">{t('security.title')}</h2>
      <p className="text-sm text-muted-foreground mb-5">{t('security.description')}</p>

      {/* Password */}
      <div className="rounded-lg border border-border/60 bg-background/40 p-4 mb-3">
        <p className="text-base font-medium mb-3">{t('security.changePassword')}</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
          <Input
            type="password" placeholder={t('security.currentPasswordPlaceholder')} className="h-9 text-sm"
            value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)}
          />
          <Input
            type="password" placeholder={t('security.newPasswordPlaceholder')} className="h-9 text-sm"
            value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
          />
          <Button
            variant="outline" size="sm" className="h-9" onClick={submit}
            disabled={changePassword.isPending || !currentPassword || newPassword.length < 8}
          >
            {changePassword.isPending ? <Icons.Loader2 className="size-3.5 animate-spin" /> : t('security.updatePassword')}
          </Button>
        </div>
        {changePassword.isError && (
          <p className="mt-2 text-xs text-destructive">
            {(changePassword.error as Error)?.message ?? t('security.updateFailed')}
          </p>
        )}
        {success && (
          <p className="mt-2 text-xs text-success flex items-center gap-1">
            <Icons.CheckCircle2 className="size-3.5" /> {t('security.passwordUpdated')}
          </p>
        )}
      </div>

      {/* 2FA — not yet configured */}
      <div className="rounded-lg border border-dashed border-border/80 bg-muted/20 p-4 mb-3">
        <div className="flex items-start gap-3">
          <div className="flex size-9 items-center justify-center rounded-lg bg-muted/40 text-muted-foreground">
            <Icons.Smartphone className="size-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <p className="text-base font-medium">{t('security.twoFactor')}</p>
              <Badge variant="outline" className="text-2xs">{t('security.notConfigured')}</Badge>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">{t('security.twoFactorBody')}</p>
          </div>
        </div>
      </div>

      {/* Sessions — not yet configured */}
      <div className="rounded-lg border border-dashed border-border/80 bg-muted/20 p-4">
        <div className="flex items-start gap-3">
          <div className="flex size-9 items-center justify-center rounded-lg bg-muted/40 text-muted-foreground">
            <Icons.MonitorSmartphone className="size-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <p className="text-base font-medium">{t('security.activeSessions')}</p>
              <Badge variant="outline" className="text-2xs">{t('security.notConfigured')}</Badge>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">
              {t('security.sessionsBody')}
            </p>
          </div>
        </div>
      </div>
    </Card>
  )
}

function ApiKeysSection() {
  const t = useTranslations('account')
  const keysQuery = useApiKeys()
  const createKey = useCreateApiKey()
  const revokeKey = useRevokeApiKey()
  const [newKeyName, setNewKeyName] = React.useState('')
  const [revealedKey, setRevealedKey] = React.useState<string | null>(null)
  const [copied, setCopied] = React.useState(false)

  const generate = () => {
    if (!newKeyName.trim()) return
    createKey.mutate(
      { name: newKeyName.trim() },
      { onSuccess: (res) => { setRevealedKey(res.key); setNewKeyName('') } }
    )
  }

  const copyKey = () => {
    if (!revealedKey) return
    navigator.clipboard.writeText(revealedKey)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <>
      <Card padding="default">
        <div className="flex items-center justify-between gap-3 mb-1">
          <h2 className="text-md font-semibold">{t('apiKeys.title')}</h2>
          <div className="flex items-center gap-2">
            <Input
              placeholder={t('apiKeys.namePlaceholder')} value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              className="h-8 w-[160px] text-sm"
              onKeyDown={(e) => { if (e.key === 'Enter') generate() }}
            />
            <Button size="sm" onClick={generate} disabled={createKey.isPending || !newKeyName.trim()}>
              <Icons.Plus className="size-3.5" /> {t('apiKeys.generateButton')}
            </Button>
          </div>
        </div>
        <p className="text-sm text-muted-foreground mb-4">{t('apiKeys.description')}</p>

        <div className="rounded-lg border border-info/25 bg-info/[0.05] p-3 flex items-start gap-2.5 mb-4">
          <Icons.ShieldAlert className="size-4 text-info shrink-0 mt-0.5" />
          <p className="text-xs text-muted-foreground leading-relaxed">
            <span className="text-foreground font-medium">{t('apiKeys.treatLikePasswords')}</span> {t('apiKeys.treatLikePasswordsBody')}
          </p>
        </div>

        {keysQuery.isLoading && <LoadingState label={t('apiKeys.loading')} />}
        {keysQuery.isError && <ErrorState body={t('apiKeys.loadFailed')} onRetry={() => keysQuery.refetch()} />}

        {keysQuery.data && keysQuery.data.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-6">{t('apiKeys.noKeys')}</p>
        )}

        <div className="space-y-2">
          {keysQuery.data?.map((k) => (
            <div key={k.id} className={cn('rounded-lg border bg-background/40 p-3', k.revoked ? 'border-border/40 opacity-60' : 'border-border/60')}>
              <div className="flex items-center gap-3">
                <div className="flex size-8 items-center justify-center rounded-md bg-muted/40 text-muted-foreground">
                  <Icons.KeyRound className="size-3.5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-medium">{k.name}</p>
                    {k.revoked && <Badge variant="outline" className="text-2xs">{t('apiKeys.revokedBadge')}</Badge>}
                  </div>
                  <p className="text-2xs text-muted-foreground font-mono mt-0.5">{k.prefix}••••••••••••••••••••</p>
                </div>
                <div className="text-end text-2xs text-muted-foreground hidden sm:block">
                  <p>{t('apiKeys.createdOn', { date: new Date(k.created_at).toLocaleDateString() })}</p>
                  <p>{k.last_used_at ? t('apiKeys.lastUsedOn', { date: new Date(k.last_used_at).toLocaleDateString() }) : t('apiKeys.neverUsed')}</p>
                </div>
                {!k.revoked && (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="ghost" size="icon" className="size-7 text-destructive shrink-0"><Icons.Trash2 className="size-3.5" /></Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>{t('apiKeys.revokeConfirmTitle')}</AlertDialogTitle>
                        <AlertDialogDescription>
                          {t('apiKeys.revokeConfirmBody', { prefix: `${k.prefix}…` })}
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>{t('apiKeys.cancel')}</AlertDialogCancel>
                        <AlertDialogAction
                          className="bg-destructive text-white hover:bg-destructive/90"
                          onClick={() => revokeKey.mutate(k.id)}
                        >
                          {t('apiKeys.revokeButton')}
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                )}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Dialog open={Boolean(revealedKey)} onOpenChange={(o) => { if (!o) setRevealedKey(null) }}>
        <DialogContent className="sm:max-w-[520px]">
          <DialogHeader>
            <DialogTitle>{t('apiKeys.dialogTitle')}</DialogTitle>
            <DialogDescription>
              {t('apiKeys.dialogDescription')}
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-2 rounded-lg border border-border/60 bg-muted/30 p-3">
            <code className="flex-1 text-sm font-mono break-all">{revealedKey}</code>
            <Button size="icon" variant="outline" className="size-8 shrink-0" onClick={copyKey}>
              {copied ? <Icons.Check className="size-3.5 text-success" /> : <Icons.Copy className="size-3.5" />}
            </Button>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button size="sm">{t('apiKeys.done')}</Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function ActivitySection() {
  const t = useTranslations('account')
  return (
    <Card padding="default">
      <div className="flex items-center gap-2 mb-1">
        <h2 className="text-md font-semibold">{t('activity.title')}</h2>
        <Badge variant="outline" className="text-2xs">{t('activity.notConfigured')}</Badge>
      </div>
      <p className="text-sm text-muted-foreground mb-4">
        {t('activity.description')}
      </p>
      <div className="rounded-lg border border-dashed border-border/80 bg-muted/20 p-4 text-center">
        <Icons.History className="size-6 text-muted-foreground/60 mx-auto mb-2" />
        <p className="text-sm font-medium">{t('activity.notTrackedTitle')}</p>
        <p className="mt-0.5 text-xs text-muted-foreground max-w-sm mx-auto">
          {t('activity.notTrackedBody')}
        </p>
      </div>
    </Card>
  )
}

function BillingSection() {
  const t = useTranslations('account')
  return (
    <>
      <Card padding="default">
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <h2 className="text-md font-semibold">{t('billing.title')}</h2>
            <Badge variant="warning" className="text-2xs">{t('billing.futureReady')}</Badge>
          </div>
        </div>
        <p className="text-sm text-muted-foreground mb-4">
          {t('billing.description')}
        </p>
        <div className="rounded-lg border border-dashed border-border/80 bg-muted/20 p-4 text-center">
          <Icons.CreditCard className="size-6 text-muted-foreground/60 mx-auto mb-2" />
          <p className="text-sm font-medium">{t('billing.noBillingTitle')}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{t('billing.noBillingBody')}</p>
        </div>
      </Card>
    </>
  )
}
