import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { User } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { REGION_OPTIONS } from '../../components/media/FilterBar'
import { Button, Card } from '../../components/ui'
import { useConfig } from '../../hooks/useConfig'

/**
 * Womit die Filterleiste beim Entdecken startet.
 *
 * Bewusst nur eine *Vorbelegung*: beim Entdecken lässt sich weiterhin alles
 * umstellen. Wer immer dasselbe sucht, muss es nur nicht jedes Mal neu
 * einstellen.
 *
 * Und bewusst nur die Region. Eine Vorbelegung der Originalsprache gab es hier
 * kurz auch - sie ist wieder raus: "Deutsch" heißt dort "auf Deutsch gedreht",
 * und solche Titel sind so selten, dass die Entdecken-Seite leer blieb
 * (gemessen 23 Titel gegen 0). Als Dauereinstellung war das eine Falle; in der
 * Filterleiste steht der Filter weiterhin zur Verfügung.
 */
export function DiscoverDefaults() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  const { data: config } = useConfig()

  const [region, setRegion] = useState('')
  const [gespeichert, setGespeichert] = useState(false)
  const [fehler, setFehler] = useState<string | null>(null)

  // Nur einmal vorbelegen - sonst überschreibt ein Nachladen im Hintergrund
  // die gerade getroffene Auswahl.
  const vorbelegt = useRef(false)
  useEffect(() => {
    if (!user || vorbelegt.current) return
    vorbelegt.current = true
    setRegion(user.discover_region ?? '')
  }, [user])

  const speichern = useMutation({
    mutationFn: () => api.patch<User>('/api/auth/me', { discover_region: region }),
    onMutate: () => {
      setGespeichert(false)
      setFehler(null)
    },
    onSuccess: (aktualisiert) => {
      updateUser(aktualisiert)
      setRegion(aktualisiert.discover_region ?? '')
      setGespeichert(true)
    },
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  if (!user) return null

  const vorgabe = config?.default_region ?? 'DE'
  const geaendert = region !== (user.discover_region ?? '')

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold">{t('profile.discoverDefaults')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('profile.discoverDefaultsIntro')}</p>
      </div>

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
