/**
 * Eine Sicherung einspielen - zweistufig, mit Vorschau.
 *
 * ⚠️ **Erst ansehen, dann ersetzen.** Wiederherstellen tauscht die ganze
 * Datenbank aus. Wer den Knopf drückt, soll vorher gesehen haben, *was* er
 * einspielt: aus welcher Fassung, von wann, mit welcher Notiz. Deshalb zwei
 * Schritte und nicht einer — die Prüfung fasst nichts an, sie liest nur.
 *
 * Dasselbe Bauteil bedient beide Wege. Sie unterscheiden sich nur in der
 * Adresse und darin, wer sie aufrufen darf:
 *
 * - **Im Assistenten** (`/api/setup/sicherung`) ohne Anmeldung, aber nur
 *   solange es überhaupt kein Konto gibt.
 * - **In den Einstellungen** (`/api/admin/sicherungen`) nur für
 *   Administratoren, dafür jederzeit.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, api } from '../api/client'
import { Betont } from './Betont'
import { Button, ErrorBanner, Field } from './ui'

type Steckbrief = {
  version: string
  erstellt: string
  art: string
  kommentar: string
  einspielbar: boolean
  grund: string
  schluessel_aus_umgebung: boolean
  schluessel_im_archiv: boolean
}

/**
 * Welche Warnung zum Schlüssel gilt — oder keine.
 *
 * ⚠️ **Zwei Fragen, nicht eine.** Hier stand nur die halbe: ob *diese*
 * Installation ihren Schlüssel aus der Umgebung nimmt. Ob im Archiv einer
 * liegt, wurde nie gefragt — und genau die Kombination „Archiv ohne Schlüssel,
 * Ziel ohne Variable" ist die schlimmste: Nexview erzeugt beim Einspielen
 * einen neuen, und danach ist kein gespeicherter Zugang mehr lesbar. Die
 * Vorschau sah dabei beruhigend aus.
 */
function schluesselWarnung(brief: Steckbrief): { text: string; schwer: boolean } | null {
  if (!brief.schluessel_im_archiv) {
    return brief.schluessel_aus_umgebung
      ? { text: 'restore.keyOnlyFromEnv', schwer: false }
      : { text: 'restore.noKeyAtAll', schwer: true }
  }
  // Schlüssel liegt bei — dann zählt nur noch, ob die Variable ihn übergeht.
  return brief.schluessel_aus_umgebung ? { text: 'restore.envKeyWarning', schwer: false } : null
}

export function SicherungEinspielen({
  basis,
  onFertig,
  /** Im Assistenten gibt es noch nichts zu verlieren - dort ist der Ton anders. */
  frischeInstallation = false,
}: {
  basis: string
  onFertig: () => void
  frischeInstallation?: boolean
}) {
  const { t } = useTranslation()

  const [datei, setDatei] = useState<File | null>(null)
  const [passwort, setPasswort] = useState('')
  const [brief, setBrief] = useState<Steckbrief | null>(null)
  const [laeuft, setLaeuft] = useState(false)
  const [fehler, setFehler] = useState('')

  const schluessel = brief === null ? null : schluesselWarnung(brief)

  function formular(): FormData {
    const daten = new FormData()
    daten.append('datei', datei as File)
    daten.append('passwort', passwort)
    return daten
  }

  async function pruefen() {
    setFehler('')
    setLaeuft(true)
    try {
      setBrief(await api.post<Steckbrief>(`${basis}/pruefen`, formular()))
    } catch (e) {
      setBrief(null)
      setFehler(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLaeuft(false)
    }
  }

  async function einspielen() {
    setFehler('')
    setLaeuft(true)
    try {
      await api.post(`${basis}/einspielen`, formular())
      onFertig()
    } catch (e) {
      setFehler(e instanceof ApiError ? e.message : String(e))
      setLaeuft(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label className="text-sm font-medium text-mist-100" htmlFor="sicherung-datei">
          {t('restore.fileLabel')}
        </label>
        <input
          id="sicherung-datei"
          type="file"
          accept=".zip,application/zip"
          onChange={(e) => {
            setDatei(e.target.files?.[0] ?? null)
            setBrief(null)
            setFehler('')
          }}
          className="rounded-xl border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-mist-100 file:mr-3 file:rounded-full file:border-0 file:bg-ink-800 file:px-3 file:py-1 file:text-sm file:text-mist-300"
        />
      </div>

      <Field
        label={t('restore.passwordLabel')}
        type="password"
        autoComplete="off"
        value={passwort}
        onChange={(e) => {
          setPasswort(e.target.value)
          setBrief(null)
        }}
        hint={t('restore.passwordHint')}
      />

      {fehler && <ErrorBanner message={fehler} />}

      {/* --- Schritt 1: ansehen ------------------------------------------- */}
      {brief === null && (
        <Button
          type="button"
          disabled={!datei || passwort.length === 0}
          loading={laeuft}
          onClick={() => void pruefen()}
        >
          {t('restore.check')}
        </Button>
      )}

      {/* --- Schritt 2: entscheiden --------------------------------------- */}
      {brief !== null && (
        <div className="flex flex-col gap-4 rounded-xl border border-ink-700 bg-ink-900 p-4">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-mist-600">{t('restore.fromWhen')}</dt>
            <dd className="text-mist-100">{new Date(brief.erstellt).toLocaleString()}</dd>
            <dt className="text-mist-600">{t('restore.fromVersion')}</dt>
            <dd className="text-mist-100">{brief.version || '—'}</dd>
            <dt className="text-mist-600">{t('restore.note')}</dt>
            <dd className="text-mist-100">{brief.kommentar || '—'}</dd>
          </dl>

          {!brief.einspielbar && (
            <p className="rounded-xl border border-bad-500/40 bg-bad-500/10 px-4 py-3 text-sm leading-relaxed text-bad-500">
              <Betont
                text={t(
                  brief.grund === 'backup_newer' ? 'restore.tooNew' : 'restore.unknownVersion',
                  { version: brief.version },
                )}
              />
            </p>
          )}

          {/* ⚠️ **Nexview kann nur sich selbst zurueckholen.**
              Beim Auflösen eines Kontos entfernt Nexview dessen Filme auch aus
              Radarr. Spielt man danach eine Sicherung ein, stehen die Anfragen
              wieder als offen in der Datenbank - in Radarr aber nicht mehr.
              Der nächste Abgleich merkt das und storniert sie ein zweites Mal,
              mit neuen Benachrichtigungen. Wer das nicht vorher weiß, hält es
              für einen Fehler. */}
          <p className="rounded-xl border border-ink-700 px-4 py-3 text-sm leading-relaxed text-mist-500">
            <Betont text={t('restore.outsideWarning')} />
          </p>

          {/* ⚠️ Die Falle, bei der man nachher lange sucht. */}
          {schluessel !== null && (
            <p
              className={
                'rounded-xl px-4 py-3 text-sm leading-relaxed ' +
                (schluessel.schwer
                  ? 'border border-bad-500/40 bg-bad-500/10 text-bad-500'
                  : 'border border-warn-500/40 bg-warn-500/10 text-warn-500')
              }
            >
              <Betont text={t(schluessel.text)} />
            </p>
          )}

          {brief.einspielbar && (
            <p
              className={
                'rounded-xl px-4 py-3 text-sm leading-relaxed ' +
                (frischeInstallation
                  ? 'border border-ink-700 text-mist-500'
                  : 'border border-bad-500/40 bg-bad-500/10 text-bad-500')
              }
            >
              <Betont
                text={t(frischeInstallation ? 'restore.freshNote' : 'restore.replaceWarning')}
              />
            </p>
          )}

          <div className="flex flex-wrap gap-3">
            <Button type="button" variant="ghost" onClick={() => setBrief(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              disabled={!brief.einspielbar}
              loading={laeuft}
              onClick={() => void einspielen()}
            >
              {t('restore.restoreNow')}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
