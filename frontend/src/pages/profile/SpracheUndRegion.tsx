import { useTranslation } from 'react-i18next'

import { useConfig } from '../../hooks/useConfig'
import { useRegionen } from '../../hooks/useRegionen'
import { SUPPORTED_LANGUAGES } from '../../i18n'
import type { Language } from '../../i18n'
import type { Theme } from '../../lib/theme'

/**
 * Sprache, Region und Darstellung - als Felder, ohne eigenen Speichern-Knopf.
 *
 * Hieß früher „Voreinstellung beim Entdecken" und saß in einem eigenen Reiter.
 * Beides war überholt: Die Entdecken-Seite gibt es seit 0.17 nicht mehr, und
 * ein Reiter für drei Auswahlfelder war ein Klick für nichts. Der Inhalt steht
 * jetzt unter „Konto" und wird mit allem anderen zusammen gespeichert - eine
 * Seite, ein Knopf.
 *
 * Zwei Einstellungen, die oft verwechselt werden:
 *
 * - Die **Sprache** gilt für die Oberfläche *und* für Titel und Handlungen von
 *   TMDB. Beides hängt bewusst zusammen - wer die Oberfläche auf Englisch
 *   stellt, liest "Days of Thunder" statt "Tage des Donners".
 * - Die **Region** sagt, wo jemand sitzt: Kinostarts, Streaming-Verfügbarkeit,
 *   die eigenen Abos. Sie stellt die Sprache absichtlich *nicht* um - sonst
 *   bekäme ein Österreicher (Region AT) keine deutschen Texte mehr.
 */

type Props = {
  region: string
  setRegion: (wert: string) => void
  sprache: Language
  setSprache: (wert: Language) => void
  darstellung: Theme
  setDarstellung: (wert: Theme) => void
  /** Alter des Kontos, falls beschränkt - nur zur Anzeige. */
  alter: number | null
  disabled?: boolean
}

const FELD =
  'rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-50'

export function SpracheUndRegion({
  region,
  setRegion,
  sprache,
  setSprache,
  darstellung,
  setDarstellung,
  alter,
  disabled = false,
}: Props) {
  const { t } = useTranslation()
  const { data: config } = useConfig()
  const regionen = useRegionen()

  const vorgabe = config?.default_region ?? 'DE'
  const vorgabeName =
    (regionen.data ?? []).find((eintrag) => eintrag.code === vorgabe)?.name ?? vorgabe

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold">{t('profile.langRegion')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('profile.langRegionIntro')}</p>
      </div>

      {/* Wer beschränkt ist, soll das wissen. Ohne diesen Hinweis wirkte
          Nexview für ihn einfach lückenhaft, und er würde Titel suchen, die er
          nie zu Gesicht bekommt. Nur Anzeige - ändern darf es der Admin. */}
      {alter !== null && (
        <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {t('profile.ageRestricted', { age: alter })}
        </p>
      )}

      <label className="flex flex-col gap-1.5">
        <span className="text-sm font-medium text-mist-300">{t('language.label')}</span>
        <select
          value={sprache}
          onChange={(event) => setSprache(event.target.value as Language)}
          disabled={disabled}
          className={FELD}
        >
          {SUPPORTED_LANGUAGES.map((kuerzel) => (
            <option key={kuerzel} value={kuerzel}>
              {t(`language.name.${kuerzel}`)}
            </option>
          ))}
        </select>
        <span className="text-xs leading-relaxed text-mist-600">
          {t('profile.languageHint')}
        </span>
      </label>

      <label className="flex flex-col gap-1.5">
        <span className="text-sm font-medium text-mist-300">{t('filters.region')}</span>
        <select
          value={region}
          onChange={(event) => setRegion(event.target.value)}
          disabled={disabled || regionen.isLoading}
          className={FELD}
        >
          {/* Der leere Eintrag stellt auf die Vorgabe des Betreibers zurück -
              und wandert dann mit, wenn der sie ändert. */}
          <option value="">{t('profile.regionDefault', { region: vorgabeName })}</option>
          {(regionen.data ?? []).map((eintrag) => (
            <option key={eintrag.code} value={eintrag.code}>
              {eintrag.name}
            </option>
          ))}
        </select>
        <span className="text-xs leading-relaxed text-mist-600">
          {t('profile.regionHint', { region: vorgabeName })}
        </span>
      </label>

      {/* Dieselbe Wahl wie der Schalter oben in der Kopfzeile. Dort wirkt sie
          sofort, hier erst mit dem Speichern - wie Sprache und Region auch.
          Gespeichert am Konto, nicht am Browser: jeder im Haushalt hat so
          seine eigene Voreinstellung. */}
      <label className="flex flex-col gap-1.5">
        <span className="text-sm font-medium text-mist-300">{t('theme.label')}</span>
        <select
          value={darstellung}
          onChange={(event) => setDarstellung(event.target.value as Theme)}
          disabled={disabled}
          className={FELD}
        >
          <option value="dark">{t('theme.dark')}</option>
          <option value="light">{t('theme.light')}</option>
        </select>
        <span className="text-xs leading-relaxed text-mist-600">
          {t('profile.themeHint')}
        </span>
      </label>
    </div>
  )
}
