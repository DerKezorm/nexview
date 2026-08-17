import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { AppSettings, MailSecurity, TestResult } from '../../api/types'
import { Button, Card, ErrorBanner, Field, Spinner } from '../../components/ui'

type Draft = {
  smtp_host: string
  smtp_port: string
  smtp_security: MailSecurity
  smtp_username: string
  smtp_password: string
  smtp_from_address: string
  smtp_from_name: string
}

const EMPTY_DRAFT: Draft = {
  smtp_host: '',
  smtp_port: '587',
  smtp_security: 'starttls',
  smtp_username: '',
  smtp_password: '',
  smtp_from_address: '',
  smtp_from_name: 'Nexview',
}

const SECURITIES: MailSecurity[] = ['starttls', 'ssl', 'none']

/** Übliche Ports je Verschlüsselung – als Vorschlag beim Umschalten. */
const PORT_FOR: Record<MailSecurity, string> = {
  starttls: '587',
  ssl: '465',
  none: '25',
}

/**
 * E-Mail-Einstellungen: SMTP-Zugang, Verbindungstest und eine Testnachricht.
 *
 * Das Passwort verlässt den Server nie im Klartext – es wird verschlüsselt
 * gespeichert und hier nur maskiert angezeigt.
 */
export function AdminMailSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  const [mailResult, setMailResult] = useState<TestResult | null>(null)
  const [recipient, setRecipient] = useState('')

  const settingsQuery = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
  })
  const settings = settingsQuery.data

  /**
   * Das Formular wird nur **einmal** vorbelegt.
   *
   * Vorher hing das an den Abfragedaten - und die kommen bei jedem
   * Hintergrund-Abgleich als neues Objekt zurück (etwa wenn das Browserfenster
   * den Fokus wiederbekommt). Dann wurde mitten im Tippen alles auf den
   * gespeicherten Stand zurückgesetzt.
   */
  const vorbelegt = useRef(false)

  function uebernehmen(daten: AppSettings) {
    setDraft({
      ...EMPTY_DRAFT,
      smtp_host: daten.smtp_host,
      smtp_port: String(daten.smtp_port),
      smtp_security: daten.smtp_security,
      smtp_username: daten.smtp_username,
      smtp_from_address: daten.smtp_from_address,
      smtp_from_name: daten.smtp_from_name,
      // Das Passwortfeld bleibt bewusst leer: ein versehentliches Speichern
      // soll das hinterlegte Passwort nicht durch den maskierten Wert ersetzen.
    })
  }

  useEffect(() => {
    if (!settings || vorbelegt.current) return
    vorbelegt.current = true
    uebernehmen(settings)
  }, [settings])

  function update(patch: Partial<Draft>) {
    setDraft((current) => ({ ...current, ...patch }))
    setMessage(null)
    setTestResult(null)
  }

  /** Beim Wechsel der Verschlüsselung den üblichen Port vorschlagen – aber nur,
      solange dort noch ein anderer Standardwert steht. */
  function changeSecurity(security: MailSecurity) {
    const istStandardPort = Object.values(PORT_FOR).includes(draft.smtp_port)
    update({
      smtp_security: security,
      ...(istStandardPort ? { smtp_port: PORT_FOR[security] } : {}),
    })
  }

  const saveMutation = useMutation({
    mutationFn: (patch: Record<string, unknown>) =>
      api.put<AppSettings>('/api/settings', patch),
    onSuccess: (data) => {
      queryClient.setQueryData(['settings'], data)
      // Nach dem Speichern ist der Serverstand der richtige.
      uebernehmen(data)
      setMessage({ ok: true, text: t('settings.saved') })
    },
    onError: (error) =>
      setMessage({
        ok: false,
        text: error instanceof ApiError ? error.message : t('settings.saveFailed'),
      }),
  })

  function saveServer() {
    saveMutation.mutate({
      smtp_host: draft.smtp_host.trim(),
      smtp_port: Number(draft.smtp_port) || 587,
      smtp_security: draft.smtp_security,
      smtp_username: draft.smtp_username.trim(),
      smtp_password: draft.smtp_password,
      smtp_from_address: draft.smtp_from_address.trim(),
      smtp_from_name: draft.smtp_from_name.trim(),
    })
  }

  const testMutation = useMutation({
    mutationFn: () =>
      api.post<TestResult>('/api/settings/test/smtp', {
        host: draft.smtp_host.trim() || null,
        port: Number(draft.smtp_port) || null,
        security: draft.smtp_security,
        username: draft.smtp_username.trim() || null,
        password: draft.smtp_password || null,
      }),
    onMutate: () => setTestResult(null),
    onSuccess: setTestResult,
    onError: (error) =>
      setTestResult({
        ok: false,
        message: error instanceof ApiError ? error.message : t('errors.generic'),
      }),
  })

  const mailMutation = useMutation({
    mutationFn: () =>
      api.post<TestResult>('/api/settings/test-mail', { recipient: recipient.trim() }),
    onMutate: () => setMailResult(null),
    onSuccess: setMailResult,
    onError: (error) =>
      setMailResult({
        ok: false,
        message: error instanceof ApiError ? error.message : t('errors.generic'),
      }),
  })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    saveServer()
  }

  if (settingsQuery.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }

  const bereit = settings?.mail_configured ?? false

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold">{t('mail.serverSection')}</h2>
          <p className="mt-1 text-sm text-mist-500">{t('mail.intro')}</p>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <div className="sm:col-span-2">
            <Field
              label={t('mail.host')}
              value={draft.smtp_host}
              onChange={(event) => update({ smtp_host: event.target.value })}
              placeholder="smtp.beispiel.de"
              autoComplete="off"
            />
          </div>
          <Field
            label={t('mail.port')}
            type="number"
            min={1}
            max={65535}
            value={draft.smtp_port}
            onChange={(event) => update({ smtp_port: event.target.value })}
            autoComplete="off"
          />
        </div>

        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-mist-300">{t('mail.security')}</span>
          <select
            value={draft.smtp_security}
            onChange={(event) => changeSecurity(event.target.value as MailSecurity)}
            className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
          >
            {SECURITIES.map((value) => (
              <option key={value} value={value}>
                {t(`mail.security_${value}`)}
              </option>
            ))}
          </select>
          <span className="text-xs text-mist-500">{t('mail.securityHint')}</span>
        </label>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label={t('mail.username')}
            value={draft.smtp_username}
            onChange={(event) => update({ smtp_username: event.target.value })}
            hint={t('mail.usernameHint')}
            autoComplete="off"
          />
          <Field
            label={t('mail.password')}
            type="password"
            value={draft.smtp_password}
            onChange={(event) => update({ smtp_password: event.target.value })}
            placeholder={settings?.smtp_password_set ? settings.smtp_password : ''}
            hint={
              settings?.smtp_password_set ? t('settings.keySetHint') : t('mail.passwordHint')
            }
            autoComplete="new-password"
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label={t('mail.fromAddress')}
            type="email"
            value={draft.smtp_from_address}
            onChange={(event) => update({ smtp_from_address: event.target.value })}
            placeholder="nexview@beispiel.de"
            hint={t('mail.fromAddressHint')}
            autoComplete="off"
          />
          <Field
            label={t('mail.fromName')}
            value={draft.smtp_from_name}
            onChange={(event) => update({ smtp_from_name: event.target.value })}
            placeholder="Nexview"
            autoComplete="off"
          />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="submit"
            loading={saveMutation.isPending}
          >
            {t('common.save')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => testMutation.mutate()}
            loading={testMutation.isPending}
            disabled={!draft.smtp_host.trim()}
          >
            {t('mail.testConnection')}
          </Button>
          {testResult && (
            <span
              className={
                'text-sm ' + (testResult.ok ? 'text-ok-500' : 'text-bad-500')
              }
            >
              {testResult.message}
            </span>
          )}
        </div>

        {message && !message.ok && <ErrorBanner message={message.text} />}
        {message?.ok && (
          <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
            {message.text}
          </p>
        )}
      </Card>

      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold">{t('mail.testSection')}</h2>
          <p className="mt-1 text-sm text-mist-500">
            {bereit ? t('mail.testIntro') : t('mail.testBlocked')}
          </p>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1">
            <Field
              label={t('mail.recipient')}
              type="email"
              value={recipient}
              onChange={(event) => {
                setRecipient(event.target.value)
                setMailResult(null)
              }}
              placeholder="du@beispiel.de"
              autoComplete="off"
            />
          </div>
          {/* Kein type="submit": das Formular speichert, dieser Knopf verschickt. */}
          <Button
            type="button"
            onClick={() => mailMutation.mutate()}
            loading={mailMutation.isPending}
            disabled={!bereit || recipient.trim() === ''}
            className="mb-0.5"
          >
            {t('mail.sendTest')}
          </Button>
        </div>

        {mailResult && (
          <p
            className={
              'rounded-xl border px-4 py-3 text-sm ' +
              (mailResult.ok
                ? 'border-ok-500/40 bg-ok-500/10 text-ok-500'
                : 'border-accent-600/50 bg-accent-700/15 text-accent-400')
            }
          >
            {mailResult.message}
          </p>
        )}
      </Card>
    </form>
  )
}
