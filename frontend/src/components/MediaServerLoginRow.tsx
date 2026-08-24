/**
 * „Anmelden mit" – eine Reihe Logos unter dem Anmeldeformular.
 *
 * ⚠️ **Es steht nur da, was auch geht.** Vorher hing hier ein fest
 * beschrifteter Knopf „Mit Plex anmelden" an der bloßen Frage, ob *irgendein*
 * Medienserver verbunden ist. Auf einer Installation mit nur Jellyfin war das
 * ein Knopf, der beim Klick eine Fehlermeldung warf – der Ablauf dahinter ist
 * auf plex.tv zugeschnitten.
 *
 * Die Wege kommen deshalb vom Server, samt der Art, wie sie funktionieren:
 *
 * * `pin` – Nexview zeigt einen Code, bestätigt wird beim Anbieter. Das
 *   Passwort sieht Nexview nie.
 * * `password` – Benutzername und Passwort gehen direkt an den Server. Für
 *   Anbieter ohne Vermittler, also Jellyfin und später Emby.
 *
 * Als Logoreihe und nicht als Knopfliste, damit ein dritter Anbieter nicht
 * eine dritte Schaltfläche in die Höhe wachsen lässt.
 *
 * Hier heißt es **anmelden**, nicht „verbinden" – verbunden wird im Profil,
 * und das ist ein anderer Vorgang: Dort hängt man eine zweite Identität an ein
 * Konto, das man schon hat.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, api } from '../api/client'
import type { TokenPair } from '../api/client'
import type { LoginWay } from '../api/types'
import { MediaServerLogo } from './MediaServerLogo'
import { MediaServerPrompt } from './MediaServerPrompt'
import { Button, ErrorBanner, Field } from './ui'
import { useMediaServerChallenge } from '../lib/useMediaServerChallenge'

export function MediaServerLoginRow({
  ways,
  onTokens,
}: {
  ways: LoginWay[]
  onTokens: (tokens: TokenPair) => Promise<void> | void
}) {
  const { t } = useTranslation()
  /** Welcher Anbieter ist gerade aufgeklappt? `null` = nur die Logoreihe. */
  const [offen, setOffen] = useState<LoginWay | null>(null)

  const pin = useMediaServerChallenge<{ status: string; tokens: TokenPair | null }>({
    startPfad: '/api/auth/mediaserver/login/start',
    abfragePfad: '/api/auth/mediaserver/login/poll',
    // Vor dem Anmelden gibt es keine Sitzung, die mitgeschickt werden könnte.
    auth: false,
    onFertig: async (ergebnis) => {
      if (!ergebnis.tokens) return
      await onTokens(ergebnis.tokens)
    },
  })

  if (ways.length === 0) return null

  function waehlen(weg: LoginWay) {
    setOffen(weg)
    if (weg.kind === 'pin') void pin.starten()
  }

  function zurueck() {
    pin.abbrechen()
    setOffen(null)
  }

  return (
    <div className="mt-6 border-t border-ink-700 pt-5">
      {offen === null ? (
        <>
          <p className="text-center text-xs uppercase tracking-wider text-mist-600">
            {t('mediaserver.loginWith')}
          </p>
          <div className="mt-3 flex items-center justify-center gap-3">
            {ways.map((weg) => (
              <button
                key={weg.provider}
                type="button"
                onClick={() => waehlen(weg)}
                title={t('mediaserver.signInWith', { name: weg.label })}
                aria-label={t('mediaserver.signInWith', { name: weg.label })}
                className="flex h-11 w-11 items-center justify-center rounded-xl border border-ink-700 bg-ink-900 text-mist-400 transition hover:border-accent-500 hover:text-mist-100 focus-visible:border-accent-500 focus-visible:outline-none"
              >
                <MediaServerLogo provider={weg.provider} className="h-5 w-5" />
              </button>
            ))}
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 text-sm font-medium">
              <MediaServerLogo provider={offen.provider} className="h-4 w-4" />
              {offen.label}
            </span>
            <button
              type="button"
              onClick={zurueck}
              className="text-xs text-mist-600 hover:text-mist-300"
            >
              {t('common.cancel')}
            </button>
          </div>

          {offen.kind === 'password' ? (
            <PasswortAnmeldung weg={offen} onTokens={onTokens} />
          ) : pin.start ? (
            <MediaServerPrompt start={pin.start} onAbbrechen={zurueck} />
          ) : (
            <p className="text-sm text-mist-500">{t('mediaserver.waiting')}</p>
          )}

          {offen.kind === 'pin' && pin.fehler && <ErrorBanner message={pin.fehler} />}
        </div>
      )}
    </div>
  )
}

/**
 * Benutzername und Passwort des Medienservers.
 *
 * ⚠️ Beides geht einmal an Nexview, von dort an den Server – und wird danach
 * verworfen. Aufbewahrt wird nur das Token, das zurückkommt.
 */
function PasswortAnmeldung({
  weg,
  onTokens,
}: {
  weg: LoginWay
  onTokens: (tokens: TokenPair) => Promise<void> | void
}) {
  const { t } = useTranslation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fehler, setFehler] = useState<string | null>(null)
  const [laeuft, setLaeuft] = useState(false)

  async function absenden(event: React.FormEvent) {
    event.preventDefault()
    setFehler(null)
    setLaeuft(true)
    try {
      const ergebnis = await api.post<{ status: string; tokens: TokenPair | null }>(
        '/api/auth/mediaserver/login/password',
        { provider: weg.provider, username, password },
        { auth: false },
      )
      setPassword('')
      if (ergebnis.tokens) await onTokens(ergebnis.tokens)
    } catch (caught) {
      setFehler(caught instanceof ApiError ? caught.message : t('errors.network'))
      setPassword('')
    } finally {
      setLaeuft(false)
    }
  }

  return (
    <form onSubmit={absenden} className="flex flex-col gap-3">
      <Field
        label={t('mediaserver.serverUser', { name: weg.label })}
        value={username}
        onChange={(event) => setUsername(event.target.value)}
        autoComplete="off"
        required
        autoFocus
      />
      <Field
        label={t('login.passwordLabel')}
        type="password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        autoComplete="off"
        required
      />
      {fehler && <ErrorBanner message={fehler} />}
      <Button type="submit" loading={laeuft} className="w-full">
        {t('mediaserver.signInWith', { name: weg.label })}
      </Button>
      {/* Die Falle benennen, bevor jemand hineintappt: Über einen Anbieter
          ohne E-Mail-Adresse entsteht kein Konto - wer schon eines hat, muss
          den umgekehrten Weg gehen. */}
      <p className="text-xs text-mist-600">
        {t('mediaserver.noNewAccountHint', { name: weg.label })}
      </p>
    </form>
  )
}
