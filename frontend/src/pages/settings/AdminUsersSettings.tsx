import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type {
  ArrOptions,
  Invitation,
  InvitationCreated,
  QuotaPeriod,
  Role,
  User,
} from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { Avatar } from '../../components/Avatar'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { REGION_OPTIONS } from '../../components/media/FilterBar'
import { Button, Card, ErrorBanner, Field, Spinner } from '../../components/ui'
import { useConfig } from '../../hooks/useConfig'
import { formatDate } from '../../lib/format'

const PERIODS: QuotaPeriod[] = ['day', 'week', 'month']

/** Leeres Feld bedeutet "unbegrenzt" - deshalb Text statt Zahl im Zustand. */
type QuotaDraft = { movies: string; series: string }

function periodLabel(period: QuotaPeriod): string {
  return `adminUsers.period${period.charAt(0).toUpperCase()}${period.slice(1)}`
}

/** "2/3" bei begrenztem Kontingent, sonst nur die verbrauchte Anzahl. */
function verbrauchText(used: number, limit: number | null): string {
  return limit === null ? String(used) : `${used}/${limit}`
}

export function AdminUsersSettings() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const { user: me } = useAuth()
  const { data: config } = useConfig()
  const minPassword = config?.min_password_length ?? 4
  /**
   * Einladen geht nur mit beidem: ohne öffentliche Adresse zeigt der Link ins
   * Leere, ohne Mailserver kommt er nicht an. Das Backend weist es ebenfalls
   * ab - hier wird es nur früh und verständlich sichtbar.
   */
  const kannEinladen = (config?.mail_configured ?? false) && (config?.public_url_set ?? false)

  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [resetting, setResetting] = useState<number | null>(null)
  const [newPassword, setNewPassword] = useState('')
  const [quotaDrafts, setQuotaDrafts] = useState<Record<number, QuotaDraft>>({})
  const [ageDrafts, setAgeDrafts] = useState<Record<number, string>>({})
  /** Welcher Benutzer ist gerade aufgeklappt? Nur einer zur Zeit. */
  const [editing, setEditing] = useState<number | null>(null)
  const [deleting, setDeleting] = useState<User | null>(null)
  /** Wessen Kontingent soll zurückgesetzt werden? Steuert die Rückfrage. */
  const [quotaReset, setQuotaReset] = useState<User | null>(null)

  const [invite, setInvite] = useState({ email: '', role: 'user' as Role })
  /** Link zum Weitergeben, falls der Mailversand nicht geklappt hat. */
  const [manualLink, setManualLink] = useState<{ link: string; grund: string } | null>(null)

  const usersQuery = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get<User[]>('/api/users'),
  })

  const invitationsQuery = useQuery({
    queryKey: ['invitations'],
    queryFn: () => api.get<Invitation[]>('/api/users/invitations'),
  })

  // Profile aus Radarr/Sonarr - nur verfügbar, wenn die Dienste eingerichtet sind.
  const movieProfiles = useQuery({
    queryKey: ['arr-options', 'movie'],
    queryFn: () => api.get<ArrOptions>('/api/arr/movie/options'),
    enabled: config?.radarr_configured ?? false,
    retry: false,
  })
  const seriesProfiles = useQuery({
    queryKey: ['arr-options', 'tv'],
    queryFn: () => api.get<ArrOptions>('/api/arr/tv/options'),
    enabled: config?.sonarr_configured ?? false,
    retry: false,
  })

  function toggleProfile(user: User, media: 'movie' | 'tv', id: number, checked: boolean) {
    const field = media === 'movie' ? 'blocked_movie_profiles' : 'blocked_series_profiles'
    const current = user[field]
    const next = checked ? [...current, id] : current.filter((entry) => entry !== id)
    updateMutation.mutate({ id: user.id, patch: { [field]: next } as Partial<User> })
  }

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ['users'] })
    void queryClient.invalidateQueries({ queryKey: ['invitations'] })
    void queryClient.invalidateQueries({ queryKey: ['quota'] })
  }

  function fail(caught: unknown, fallback: string) {
    setMessage(null)
    setError(caught instanceof ApiError ? caught.message : fallback)
  }

  const inviteMutation = useMutation({
    mutationFn: () =>
      api.post<InvitationCreated>('/api/users/invitations', {
        email: invite.email.trim(),
        role: invite.role,
      }),
    onSuccess: (angelegt) => {
      setInvite({ email: '', role: 'user' })
      setError(null)
      // Klappt der Versand nicht, bekommt der Admin den Link zum Weitergeben -
      // sonst blockiert ein kaputter Mailserver die ganze Verwaltung.
      if (angelegt.mail_sent) {
        setMessage(t('adminUsers.inviteSent', { email: angelegt.email }))
      } else if (angelegt.manual_link) {
        setManualLink({
          link: angelegt.manual_link,
          grund: angelegt.mail_error ?? t('adminUsers.mailFailed'),
        })
      }
      refresh()
    },
    onMutate: () => {
      resetMessages()
      setManualLink(null)
    },
    onError: (caught) => fail(caught, t('errors.generic')),
  })

  const withdrawMutation = useMutation({
    mutationFn: (id: number) => api.delete<void>(`/api/users/invitations/${id}`),
    onSuccess: () => {
      setError(null)
      setMessage(t('adminUsers.inviteWithdrawn'))
      refresh()
    },
    onMutate: resetMessages,
    onError: (caught) => fail(caught, t('errors.generic')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Partial<User> }) =>
      api.patch<User>(`/api/users/${id}`, patch),
    onSuccess: () => {
      setError(null)
      setMessage(t('adminUsers.saved'))
      refresh()
    },
    onMutate: resetMessages,
    onError: (caught) => fail(caught, t('errors.generic')),
  })

  const passwordMutation = useMutation({
    mutationFn: ({ id, password }: { id: number; password: string }) =>
      api.post<void>(`/api/users/${id}/password`, { password }),
    onSuccess: () => {
      setResetting(null)
      setNewPassword('')
      setError(null)
      setMessage(t('adminUsers.passwordReset'))
    },
    onMutate: resetMessages,
    onError: (caught) => fail(caught, t('errors.generic')),
  })

  const resetQuotaMutation = useMutation({
    mutationFn: (id: number) => api.post<User>(`/api/users/${id}/quota/reset`),
    onSuccess: () => {
      setQuotaReset(null)
      setError(null)
      setMessage(t('adminUsers.quotaResetDone'))
      refresh()
      // Der Betroffene sieht sein Kontingent sofort neu, wenn er die Seite hat.
      void queryClient.invalidateQueries({ queryKey: ['quota'] })
    },
    onMutate: resetMessages,
    onError: (caught) => fail(caught, t('errors.generic')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete<void>(`/api/users/${id}`),
    onSuccess: () => {
      setDeleting(null)
      setError(null)
      setMessage(t('adminUsers.saved'))
      refresh()
    },
    onMutate: resetMessages,
    onError: (caught) => fail(caught, t('errors.generic')),
  })

  /**
   * Vor jeder Aktion beide Meldungen leeren.
   *
   * Sonst bleibt eine alte grüne "Gespeichert"-Meldung stehen, während gerade
   * etwas fehlschlägt - und behauptet das Gegenteil.
   */
  function resetMessages() {
    setError(null)
    setMessage(null)
  }

  function handleInvite(event: FormEvent) {
    event.preventDefault()
    resetMessages()
    inviteMutation.mutate()
  }

  /** Kontingent-Eingabe: leer = unbegrenzt (null ans Backend). */
  function quotaValue(user: User, kind: keyof QuotaDraft): string {
    const draft = quotaDrafts[user.id]?.[kind]
    if (draft !== undefined) return draft
    const stored = kind === 'movies' ? user.quota_movies_limit : user.quota_series_limit
    return stored === null ? '' : String(stored)
  }

  function setQuotaDraft(user: User, kind: keyof QuotaDraft, value: string) {
    setQuotaDrafts((current) => ({
      ...current,
      [user.id]: {
        movies: kind === 'movies' ? value : quotaValue(user, 'movies'),
        series: kind === 'series' ? value : quotaValue(user, 'series'),
      },
    }))
  }

  function parseQuota(raw: string): number | null | undefined {
    const text = raw.trim()
    if (text === '') return null
    const parsed = Number(text)
    return Number.isInteger(parsed) && parsed >= 0 ? parsed : undefined
  }

  /** Gibt es ungespeicherte Kontingent-Änderungen für diesen Benutzer? */
  function quotaChanged(user: User): boolean {
    const draft = quotaDrafts[user.id]
    if (!draft) return false
    return (
      parseQuota(draft.movies) !== user.quota_movies_limit ||
      parseQuota(draft.series) !== user.quota_series_limit
    )
  }

  function saveQuota(user: User) {
    const movies = parseQuota(quotaValue(user, 'movies'))
    const series = parseQuota(quotaValue(user, 'series'))
    if (movies === undefined || series === undefined) {
      setError(t('adminUsers.quotaInvalid'))
      return
    }

    setQuotaDrafts((current) => {
      const next = { ...current }
      delete next[user.id]
      return next
    })
    updateMutation.mutate({
      id: user.id,
      patch: { quota_movies_limit: movies, quota_series_limit: series },
    })
  }

  /** Alterseingabe - wie beim Kontingent erst auf Knopfdruck gespeichert. */
  function ageValue(user: User): string {
    const draft = ageDrafts[user.id]
    if (draft !== undefined) return draft
    return user.age === null ? '' : String(user.age)
  }

  function setAgeDraft(user: User, value: string) {
    setAgeDrafts((current) => ({ ...current, [user.id]: value }))
  }

  function parseAge(raw: string): number | undefined {
    const parsed = Number(raw.trim())
    return Number.isInteger(parsed) && parsed >= 0 && parsed <= 21 ? parsed : undefined
  }

  function ageChanged(user: User): boolean {
    const draft = ageDrafts[user.id]
    return draft !== undefined && parseAge(draft) !== user.age
  }

  function saveAge(user: User) {
    const alter = parseAge(ageValue(user))
    if (alter === undefined) {
      setError(t('adminUsers.ageInvalid'))
      return
    }
    setAgeDrafts((current) => {
      const next = { ...current }
      delete next[user.id]
      return next
    })
    updateMutation.mutate({ id: user.id, patch: { age: alter } })
  }

  /** Kurzfassung der Rechte für die zugeklappte Zeile. */
  function summary(user: User): string {
    const rollen: Record<Role, string> = {
      admin: 'adminUsers.roleAdmin',
      approver: 'adminUsers.roleApprover',
      user: 'adminUsers.roleUser',
    }
    const teile = [t(rollen[user.role])]

    teile.push(
      t(user.effective_auto_approve ? 'adminUsers.summaryAuto' : 'adminUsers.summaryApproval'),
    )

    const zeitraum = t(periodLabel(user.quota_period))
    const grenze = (limit: number | null, key: string) =>
      limit === null ? null : `${t(key)} ${limit} ${zeitraum}`
    const grenzen = [
      grenze(user.quota_movies_limit, 'common.movies'),
      grenze(user.quota_series_limit, 'common.series'),
    ].filter(Boolean)
    teile.push(grenzen.length > 0 ? grenzen.join(', ') : t('adminUsers.summaryUnlimited'))

    const gesperrt = user.blocked_movie_profiles.length + user.blocked_series_profiles.length
    if (gesperrt > 0) teile.push(t('adminUsers.summaryBlocked', { count: gesperrt }))

    return teile.join(' · ')
  }

  const users = usersQuery.data ?? []
  const invitations = invitationsQuery.data ?? []

  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <p className="text-sm text-mist-500">{t('adminUsers.intro')}</p>

      {error && <ErrorBanner message={error} />}
      {message && !error && (
        <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {message}
        </p>
      )}

      {/* Konten entstehen nur über eine Einladung: der Eingeladene wählt
          Benutzername, Namen und Passwort selbst. So kennt niemand sonst sein
          Passwort - und der Administrator muss keines weitergeben. */}
      <Card>
        <h2 className="text-lg font-semibold">{t('adminUsers.inviteTitle')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('adminUsers.inviteIntro')}</p>

        {!kannEinladen && (
          <p className="mt-3 rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
            {t('adminUsers.inviteBlocked')}
          </p>
        )}

        <form onSubmit={handleInvite} className="mt-4 grid gap-4 sm:grid-cols-2">
          <Field
            label={t('adminUsers.email')}
            type="email"
            value={invite.email}
            onChange={(event) => setInvite({ ...invite, email: event.target.value })}
            placeholder="name@beispiel.de"
            autoComplete="off"
            required
          />
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-mist-300">{t('adminUsers.role')}</span>
            <select
              value={invite.role}
              onChange={(event) => setInvite({ ...invite, role: event.target.value as Role })}
              className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 focus:border-accent-500 focus:outline-none"
            >
              <option value="user">{t('adminUsers.roleUser')}</option>
              <option value="approver">{t('adminUsers.roleApprover')}</option>
              <option value="admin">{t('adminUsers.roleAdmin')}</option>
            </select>
          </label>

          <div className="sm:col-span-2">
            <Button type="submit" loading={inviteMutation.isPending} disabled={!kannEinladen}>
              {t('adminUsers.sendInvite')}
            </Button>
            <p className="mt-2 text-xs text-mist-600">{t('adminUsers.inviteHint')}</p>
          </div>

          {manualLink && (
            <div className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 sm:col-span-2">
              <p className="text-sm font-medium text-warn-500">{t('adminUsers.mailFailedTitle')}</p>
              <p className="mt-1 text-xs text-mist-400">{manualLink.grund}</p>
              <p className="mt-2 text-xs text-mist-500">{t('adminUsers.manualLinkHint')}</p>
              <code className="mt-1 block break-all rounded-lg bg-ink-900 px-3 py-2 text-xs text-mist-300">
                {manualLink.link}
              </code>
              <Button
                variant="ghost"
                className="mt-2"
                onClick={() => void navigator.clipboard?.writeText(manualLink.link)}
              >
                {t('adminUsers.copyLink')}
              </Button>
            </div>
          )}
        </form>
      </Card>

      {invitations.length > 0 && (
        <Card className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold">
            {t('adminUsers.openInvites', { count: invitations.length })}
          </h2>
          {invitations.map((eintrag) => (
            <div
              key={eintrag.id}
              className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/50 p-3"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{eintrag.email}</p>
                <p className="text-xs text-mist-600">
                  {t(
                    `adminUsers.role${eintrag.role === 'admin' ? 'Admin' : eintrag.role === 'approver' ? 'Approver' : 'User'}`,
                  )}
                  {' · '}
                  {t('adminUsers.inviteExpires', {
                    date: formatDate(eintrag.expires_at.slice(0, 10), i18n.language),
                  })}
                </p>
              </div>
              <Button
                variant="ghost"
                onClick={() => withdrawMutation.mutate(eintrag.id)}
                loading={withdrawMutation.isPending && withdrawMutation.variables === eintrag.id}
              >
                {t('adminUsers.withdrawInvite')}
              </Button>
            </div>
          ))}
        </Card>
      )}

      {usersQuery.isPending && (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t('common.loading')}
        </p>
      )}

      <div className="flex flex-col gap-3">
        {users.map((user) => {
          const isMe = user.id === me?.id
          const offen = editing === user.id
          return (
            <Card key={user.id} className="flex flex-col gap-4">
              {/* Zusammenfassung in einer Zeile - Details erst auf Klick. */}
              <div className="flex flex-wrap items-center gap-3">
                <Avatar
                  url={user.avatar_url}
                  name={user.display_name ?? user.username}
                  className="h-10 w-10"
                />
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">
                    {user.display_name ?? user.username}
                    <span className="ml-2 text-sm font-normal text-mist-600">@{user.username}</span>
                  </p>
                  <p className="text-xs text-mist-600">{summary(user)}</p>
                </div>

                {!user.is_active && (
                  <span className="rounded-full bg-ink-900 px-2.5 py-1 text-xs text-mist-500 ring-1 ring-ink-700">
                    {t('adminUsers.inactive')}
                  </span>
                )}

                <Button
                  variant="ghost"
                  onClick={() => setEditing(offen ? null : user.id)}
                  aria-expanded={offen}
                >
                  {t(offen ? 'adminUsers.close' : 'adminUsers.edit')}
                </Button>
              </div>

              {offen && (
                <>
                  <div className="flex flex-wrap items-center gap-3 border-t border-ink-700 pt-4">
                    <p className="flex-1 text-xs text-mist-600">
                      {t('adminUsers.lastLogin')}:{' '}
                      {user.last_login_at
                        ? formatDate(user.last_login_at.slice(0, 10), i18n.language)
                        : t('adminUsers.never')}
                    </p>

                    <select
                      value={user.role}
                      disabled={isMe}
                      onChange={(event) =>
                        updateMutation.mutate({
                          id: user.id,
                          patch: { role: event.target.value as Role },
                        })
                      }
                      className="rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-100 disabled:opacity-50"
                    >
                      <option value="user">{t('adminUsers.roleUser')}</option>
                      <option value="approver">{t('adminUsers.roleApprover')}</option>
                      <option value="admin">{t('adminUsers.roleAdmin')}</option>
                    </select>

                    <label className="flex cursor-pointer items-center gap-2 text-sm text-mist-300">
                      <input
                        type="checkbox"
                        checked={user.is_active}
                        disabled={isMe}
                        onChange={(event) =>
                          updateMutation.mutate({
                            id: user.id,
                            patch: { is_active: event.target.checked },
                          })
                        }
                        className="h-4 w-4 accent-accent-500"
                      />
                      {user.is_active ? t('adminUsers.active') : t('adminUsers.inactive')}
                    </label>
                  </div>

                  <div className="grid gap-3 border-t border-ink-700 pt-4 sm:grid-cols-4">
                    {/* Wer selbst freigeben darf, gibt sich nicht erst selbst frei -
                    der Haken hat für ihn keine Wirkung und ist deshalb gesperrt. */}
                    <label
                      className={
                        'flex items-center gap-2 text-sm sm:col-span-4 ' +
                        (user.can_approve ? 'text-mist-600' : 'text-mist-300')
                      }
                    >
                      <input
                        type="checkbox"
                        checked={user.effective_auto_approve}
                        disabled={user.can_approve}
                        onChange={(event) =>
                          updateMutation.mutate({
                            id: user.id,
                            patch: { auto_approve: event.target.checked },
                          })
                        }
                        className="h-4 w-4 accent-accent-500 disabled:opacity-60"
                      />
                      {t('adminUsers.autoApprove')}
                      {user.can_approve && (
                        <span className="text-xs text-mist-600">
                          ({t('adminUsers.autoApproveAdmin')})
                        </span>
                      )}
                    </label>

                    {/* "Unbegrenzt" steht als eigener Haken da. Vorher war es das
                    leere Feld - man sah nie, wie man wieder dorthin kommt. */}
                    {(['movies', 'series'] as const).map((kind) => {
                      const unbegrenzt = quotaValue(user, kind) === ''
                      return (
                        <div key={kind} className="flex flex-col gap-1.5">
                          <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
                            {t(
                              kind === 'movies'
                                ? 'adminUsers.quotaMovies'
                                : 'adminUsers.quotaSeries',
                            )}
                          </span>
                          <input
                            type="number"
                            min={0}
                            value={quotaValue(user, kind)}
                            disabled={unbegrenzt}
                            placeholder={t('adminUsers.quotaUnlimited')}
                            aria-label={t(
                              kind === 'movies'
                                ? 'adminUsers.quotaMovies'
                                : 'adminUsers.quotaSeries',
                            )}
                            onChange={(event) => setQuotaDraft(user, kind, event.target.value)}
                            className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none disabled:opacity-50"
                          />
                          <label className="flex items-center gap-2 text-xs text-mist-500">
                            <input
                              type="checkbox"
                              checked={unbegrenzt}
                              onChange={(event) =>
                                setQuotaDraft(user, kind, event.target.checked ? '' : '1')
                              }
                              className="h-3.5 w-3.5 accent-accent-500"
                            />
                            {t('adminUsers.quotaUnlimited')}
                          </label>
                        </div>
                      )
                    })}

                    <label className="flex flex-col gap-1.5">
                      <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
                        {t('adminUsers.quotaPeriod')}
                      </span>
                      <select
                        value={user.quota_period}
                        onChange={(event) =>
                          updateMutation.mutate({
                            id: user.id,
                            patch: { quota_period: event.target.value as QuotaPeriod },
                          })
                        }
                        className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
                      >
                        {PERIODS.map((period) => (
                          <option key={period} value={period}>
                            {t(periodLabel(period))}
                          </option>
                        ))}
                      </select>
                    </label>

                    {/* Kontingente werden bewusst erst auf Knopfdruck gespeichert -
                    beim Tippen zu speichern wäre unvorhersehbar. */}
                    {quotaChanged(user) && (
                      <div className="flex items-end gap-2 sm:col-span-4">
                        <Button onClick={() => saveQuota(user)} loading={updateMutation.isPending}>
                          {t('adminUsers.saveQuota')}
                        </Button>
                        <span className="pb-2 text-xs text-warn-500">
                          {t('adminUsers.unsaved')}
                        </span>
                      </div>
                    )}

                    {/* Verbrauch im laufenden Zeitraum - und der Weg, ihn wieder
                    freizugeben, ohne die Anfragen zu löschen. */}
                    <div className="flex flex-wrap items-center gap-3 sm:col-span-4">
                      <span className="text-sm text-mist-500">
                        {t('adminUsers.usedNow', {
                          movies: verbrauchText(user.quota_movies_used, user.quota_movies_limit),
                          series: verbrauchText(user.quota_series_used, user.quota_series_limit),
                        })}
                      </span>
                      <Button
                        variant="ghost"
                        onClick={() => setQuotaReset(user)}
                        disabled={user.quota_movies_used === 0 && user.quota_series_used === 0}
                      >
                        {t('adminUsers.resetQuota')}
                      </Button>
                      {user.quota_reset_at && (
                        <span className="text-xs text-mist-600">
                          {t('adminUsers.lastReset', {
                            date: formatDate(user.quota_reset_at.slice(0, 10), i18n.language),
                          })}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Altersbeschränkung. Ein Schalter dafür, *ob* überhaupt, und
                  dann das Alter - "wie alt ist das Kind" ist die Frage, die
                  sich beantworten lässt, nicht "bis zu welcher FSK-Stufe". */}
                  <div className="border-t border-ink-700 pt-4">
                    <label className="flex cursor-pointer items-center gap-2 text-sm text-mist-300">
                      <input
                        type="checkbox"
                        checked={user.age !== null}
                        onChange={(event) =>
                          updateMutation.mutate({
                            id: user.id,
                            // -1 hebt die Beschränkung auf; null hieße nur
                            // "nicht mitgeschickt" und änderte gar nichts.
                            patch: { age: event.target.checked ? 12 : -1 },
                          })
                        }
                        className="h-4 w-4 accent-accent-500"
                      />
                      {t('adminUsers.ageRestricted')}
                    </label>

                    {user.age !== null && (
                      <div className="mt-3 grid gap-3 sm:grid-cols-4">
                        <label className="flex flex-col gap-1.5">
                          <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
                            {t('adminUsers.age')}
                          </span>
                          <input
                            type="number"
                            min={0}
                            max={21}
                            value={ageValue(user)}
                            onChange={(event) => setAgeDraft(user, event.target.value)}
                            className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
                          />
                        </label>

                        <label className="flex flex-col gap-1.5">
                          <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
                            {t('adminUsers.ageRegion')}
                          </span>
                          <select
                            value={user.rating_region ?? ''}
                            onChange={(event) =>
                              updateMutation.mutate({
                                id: user.id,
                                patch: { rating_region: event.target.value },
                              })
                            }
                            className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
                          >
                            <option value="">{t('adminUsers.ageRegionDefault')}</option>
                            {REGION_OPTIONS.map((eintrag) => (
                              <option key={eintrag} value={eintrag}>
                                {eintrag}
                              </option>
                            ))}
                          </select>
                        </label>

                        {ageChanged(user) && (
                          <div className="flex items-end gap-2 sm:col-span-2">
                            <Button
                              onClick={() => saveAge(user)}
                              loading={updateMutation.isPending}
                            >
                              {t('common.save')}
                            </Button>
                            <span className="pb-2 text-xs text-warn-500">
                              {t('adminUsers.unsaved')}
                            </span>
                          </div>
                        )}

                        {/* Ohne diesen Schalter ist "Entdecken" für ein
                        beschränktes Konto fast leer: neue Titel sind meist
                        noch nirgends eingestuft (gemessen 20 → 2). */}
                        <label className="flex cursor-pointer items-start gap-2 text-sm text-mist-300 sm:col-span-4">
                          <input
                            type="checkbox"
                            checked={user.hide_unrated}
                            onChange={(event) =>
                              updateMutation.mutate({
                                id: user.id,
                                patch: { hide_unrated: event.target.checked },
                              })
                            }
                            className="mt-0.5 h-4 w-4 accent-accent-500"
                          />
                          <span>
                            {t('adminUsers.hideUnrated')}
                            <span className="mt-0.5 block text-xs text-mist-600">
                              {t('adminUsers.hideUnratedHint')}
                            </span>
                          </span>
                        </label>

                        <p className="text-xs leading-relaxed text-mist-600 sm:col-span-4">
                          {t('adminUsers.ageHint')}
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Kein Haken = alle Profile erlaubt. So muss man nur dort etwas
                  einstellen, wo wirklich begrenzt werden soll. */}
                  {(
                    [
                      ['movie', movieProfiles.data, 'adminUsers.allowedMovieProfiles'],
                      ['tv', seriesProfiles.data, 'adminUsers.allowedSeriesProfiles'],
                    ] as const
                  ).map(([media, daten, labelKey]) =>
                    daten && daten.quality_profiles.length > 0 ? (
                      <div key={media} className="border-t border-ink-700 pt-4">
                        <p className="text-xs font-medium tracking-wide text-mist-600 uppercase">
                          {t(labelKey)}
                        </p>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {daten.quality_profiles.map((profil) => {
                            const gewaehlt = (
                              media === 'movie'
                                ? user.blocked_movie_profiles
                                : user.blocked_series_profiles
                            ).includes(profil.id)
                            return (
                              <label
                                key={profil.id}
                                className={
                                  'flex cursor-pointer items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition-colors ' +
                                  (gewaehlt
                                    ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                                    : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
                                }
                              >
                                <input
                                  type="checkbox"
                                  checked={gewaehlt}
                                  onChange={(event) =>
                                    toggleProfile(user, media, profil.id, event.target.checked)
                                  }
                                  className="h-3.5 w-3.5 accent-accent-500"
                                />
                                {profil.name}
                              </label>
                            )
                          })}
                        </div>
                        <p className="mt-1.5 text-xs text-mist-600">
                          {(media === 'movie'
                            ? user.blocked_movie_profiles
                            : user.blocked_series_profiles
                          ).length === 0
                            ? t('adminUsers.noProfilesBlocked')
                            : t('adminUsers.someProfilesBlocked')}
                        </p>
                      </div>
                    ) : null,
                  )}

                  <div className="flex flex-wrap items-center gap-2 border-t border-ink-700 pt-4">
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setResetting(resetting === user.id ? null : user.id)
                        setNewPassword('')
                      }}
                    >
                      {t('adminUsers.resetPassword')}
                    </Button>
                    {!isMe && (
                      <Button variant="ghost" onClick={() => setDeleting(user)}>
                        {t('adminUsers.delete')}
                      </Button>
                    )}

                    {resetting === user.id && (
                      <div className="flex w-full flex-wrap items-center gap-2 pt-2">
                        <input
                          type="password"
                          value={newPassword}
                          onChange={(event) => setNewPassword(event.target.value)}
                          placeholder={t('adminUsers.newPassword')}
                          aria-label={t('adminUsers.newPassword')}
                          minLength={minPassword}
                          className="min-w-0 flex-1 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
                        />
                        <Button
                          onClick={() =>
                            passwordMutation.mutate({ id: user.id, password: newPassword })
                          }
                          loading={passwordMutation.isPending}
                          disabled={newPassword.length < minPassword}
                        >
                          {t('common.save')}
                        </Button>
                      </div>
                    )}
                  </div>
                </>
              )}
            </Card>
          )
        })}
      </div>

      <ConfirmDialog
        open={deleting !== null}
        title={t('adminUsers.deleteTitle')}
        description={t('adminUsers.deleteText', { name: deleting?.username ?? '' })}
        warning={t('adminUsers.deleteWarning')}
        confirmLabel={t('adminUsers.deleteConfirm')}
        loading={deleteMutation.isPending}
        onCancel={() => setDeleting(null)}
        onConfirm={() => deleting && deleteMutation.mutate(deleting.id)}
      />

      <ConfirmDialog
        open={quotaReset !== null}
        title={t('adminUsers.resetQuotaTitle')}
        description={t('adminUsers.resetQuotaText', { name: quotaReset?.username ?? '' })}
        warning={t('adminUsers.resetQuotaHint')}
        confirmLabel={t('adminUsers.resetQuota')}
        loading={resetQuotaMutation.isPending}
        onCancel={() => setQuotaReset(null)}
        onConfirm={() => quotaReset && resetQuotaMutation.mutate(quotaReset.id)}
      />
    </div>
  )
}
