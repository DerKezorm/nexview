import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { User } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { REGION_OPTIONS } from '../../components/media/optionen'
import { Button, Card } from '../../components/ui'
import { useConfig } from '../../hooks/useConfig'
import { changeLanguage as spracheAnwenden, SUPPORTED_LANGUAGES } from '../../i18n'
import type { Language } from '../../i18n'
import { istTheme, themeAnwenden } from '../../lib/theme'
import type { Theme } from '../../lib/theme'

/**
 * Sprache und Region des Benutzers.
 *
 * Zwei Einstellungen, die oft verwechselt werden:
 *
 * - Die **Sprache** gilt für die Oberfläche *und* für Titel und Handlungen von
 *   TMDB. Beides hängt bewusst zusammen - genau wie in Overseerr, das die
 *   Anzeigesprache ebenfalls als Sprache der Metadaten verwendet. Wer die
 *   Oberfläche auf Englisch stellt, liest "Days of Thunder" statt "Tage des
 *   Donners".
 * - Die **Region** sagt, wo jemand sitzt: Kinostarts, Verfügbarkeit, später
 *   die Altersfreigabe. Sie stellt die Sprache absichtlich *nicht* um - sonst
 *   bekäme ein Österreicher (Region AT) keine deutschen Texte mehr.
 *
 * Die Region ist beim Entdecken zusätzlich schon vorausgewählt, lässt sich
 * dort aber jederzeit ändern.
 *
 * Bewusst *nicht* hier: die Originalsprache. Eine Vorbelegung gab es kurz -
 * sie ist wieder raus, weil "Deutsch" dort "auf Deutsch gedreht" heißt und
 * solche Titel so selten sind, dass die Entdecken-Seite leer blieb (gemessen
 * 23 Titel gegen 0). In der Filterleiste steht sie weiterhin zur Verfügung.
 */
export function DiscoverDefaults() {
  const { t, i18n } = useTranslation()
  const { user, updateUser } = useAuth()
  const { data: config } = useConfig()

  const [region, setRegion] = useState('')
  const [sprache, setSprache] = useState<Language>(i18n.language as Language)
  const [darstellung, setDarstellung] = useState<Theme>('dark')
  const [gespeichert, setGespeichert] = useState(false)
  const [fehler, setFehler] = useState<string | null>(null)

  // Nur einmal vorbelegen - sonst überschreibt ein Nachladen im Hintergrund
  // die gerade getroffene Auswahl.
  const vorbelegt = useRef(false)
  useEffect(() => {
    if (!user || vorbelegt.current) return
    vorbelegt.current = true
    setRegion(user.discover_region ?? '')
    setSprache((user.language as Language) ?? (i18n.language as Language))
    setDarstellung(istTheme(user.theme) ? user.theme : 'dark')
  }, [user, i18n.language])

  const speichern = useMutation({
    mutationFn: () =>
      api.patch<User>('/api/auth/me', {
        discover_region: region,
        language: sprache,
        theme: darstellung,
      }),
    onMutate: () => {
      setGespeichert(false)
      setFehler(null)
    },
    onSuccess: (aktualisiert) => {
      updateUser(aktualisiert)
      setRegion(aktualisiert.discover_region ?? '')
      // Erst jetzt umschalten: die Auswahl oben ist nur ein Vorschlag, bis
      // gespeichert wird. Sonst spränge die Oberfläche schon beim Aufklappen
      // der Liste um, und der Knopf daneben wäre sinnlos.
      spracheAnwenden(aktualisiert.language as Language)
      if (istTheme(aktualisiert.theme)) themeAnwenden(aktualisiert.theme)
      setGespeichert(true)
    },
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  if (!user) return null

  const vorgabe = config?.default_region ?? 'DE'
  const geaendert =
    region !== (user.discover_region ?? '') ||
    sprache !== user.language ||
    darstellung !== user.theme

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold">{t('profile.langRegion')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('profile.langRegionIntro')}</p>
      </div>

      {/* Wer beschränkt ist, soll das wissen. Ohne diesen Hinweis wirkte
          Nexview für ihn einfach lückenhaft, und er würde Titel suchen, die er
          nie zu Gesicht bekommt. Nur Anzeige - ändern darf es der Admin. */}
      {user.age !== null && (
        <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {t('profile.ageRestricted', { age: user.age })}
        </p>
      )}

      <label className="flex max-w-xs flex-col gap-1.5">
        <span className="text-sm font-medium text-mist-300">{t('language.label')}</span>
        <select
          value={sprache}
          onChange={(event) => {
            setSprache(event.target.value as Language)
            setGespeichert(false)
          }}
          disabled={speichern.isPending}
          className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-50"
        >
          {SUPPORTED_LANGUAGES.map((kuerzel) => (
            <option key={kuerzel} value={kuerzel}>
              {t(`language.name.${kuerzel}`)}
            </option>
          ))}
        </select>
        <span className="text-xs leading-relaxed text-mist-600">{t('profile.languageHint')}</span>
      </label>

      {/* Dieselbe Wahl wie der Schalter oben in der Kopfzeile. Dort wirkt
          sie sofort, hier erst mit dem Speichern-Knopf - wie Sprache und
          Region auch. Gespeichert wird sie am Konto, nicht am Browser:
          jeder im Haushalt hat so seine eigene Voreinstellung. */}
      <label className="flex max-w-xs flex-col gap-1.5">
        <span className="text-sm font-medium text-mist-300">{t('theme.label')}</span>
        <select
          value={darstellung}
          onChange={(event) => {
            setDarstellung(event.target.value as Theme)
            setGespeichert(false)
          }}
          disabled={speichern.isPending}
          className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-50"
        >
          <option value="dark">{t('theme.dark')}</option>
          <option value="light">{t('theme.light')}</option>
        </select>
        <span className="text-xs leading-relaxed text-mist-600">{t('profile.themeHint')}</span>
      </label>

      <label className="flex max-w-xs flex-col gap-1.5">
        <span className="text-sm font-medium text-mist-300">{t('filters.region')}</span>
        <select
          value={region}
          onChange={(event) => {
            setRegion(event.target.value)
            setGespeichert(false)
          }}
          disabled={speichern.isPending}
          className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-50"
        >
          {/* Der leere Eintrag stellt auf die Vorgabe des Admins zurück. */}
          <option value="">{t('profile.regionDefault', { region: vorgabe })}</option>
          {REGION_OPTIONS.map((eintrag) => (
            <option key={eintrag} value={eintrag}>
              {eintrag}
            </option>
          ))}
        </select>
        <span className="text-xs leading-relaxed text-mist-600">
          {t('profile.regionHint', { region: vorgabe })}
        </span>
      </label>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          onClick={() => speichern.mutate()}
          loading={speichern.isPending}
          disabled={!geaendert}
        >
          {t('common.save')}
        </Button>

        {gespeichert && !geaendert && (
          <span className="text-sm text-ok-500">{t('profile.discoverSaved')}</span>
        )}
        {geaendert && !speichern.isPending && (
          <span className="text-sm text-mist-600">{t('common.unsaved')}</span>
        )}
        {fehler && <span className="text-sm text-accent-400">{fehler}</span>}
      </div>
    </Card>
  )
}
