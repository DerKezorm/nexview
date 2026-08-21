import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { StorageOverview } from '../../api/types'
import { Button, Card, ErrorBanner, Spinner } from '../../components/ui'
import { formatSize } from '../../lib/format'

type Einstellungen = { storage_enabled: boolean }

/**
 * Speicher-Kontingente ein- und ausschalten.
 *
 * Der Schalter ist ein **Hauptschalter**: Ist er aus, verhaelt sich Nexview
 * wie vor dem Einbau - kein Reiter im Profil, keine Karte bei den Anfragen,
 * keine Verteilung in der Statistik, und gemessen wird auch nicht. Die
 * Funktion existiert dann schlicht nicht.
 */
export function AdminStorageSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const abfrage = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<Einstellungen>('/api/settings'),
  })

  const [an, setAn] = useState<boolean | null>(null)
  useEffect(() => {
    if (abfrage.data && an === null) setAn(abfrage.data.storage_enabled)
  }, [abfrage.data, an])

  const speichern = useMutation({
    mutationFn: (wert: boolean) =>
      api.put<Einstellungen>('/api/settings', { storage_enabled: wert }),
    onSuccess: () => {
      // Der Schalter aendert, was auf mehreren Seiten ueberhaupt existiert -
      // deshalb alles neu laden, nicht nur die Einstellungen.
      void queryClient.invalidateQueries()
    },
  })

  if (abfrage.isLoading || an === null) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    )
  }

  const gespeichert = abfrage.data?.storage_enabled ?? false
  const geaendert = an !== gespeichert

  return (
    <div className="flex max-w-2xl flex-col gap-5">
      <Card className="flex flex-col gap-4 p-5">
        <div>
          <h2 className="text-lg font-semibold">{t('storageAdmin.title')}</h2>
          <p className="mt-1 text-sm text-mist-500">{t('storageAdmin.intro')}</p>
        </div>

        <label className="flex cursor-pointer items-start gap-3">
          <input
            type="checkbox"
            checked={an}
            onChange={(e) => setAn(e.target.checked)}
            className="mt-0.5 h-4 w-4 accent-accent-500"
          />
          <span>
            <span className="font-medium">{t('storageAdmin.enable')}</span>
            <span className="mt-0.5 block text-sm text-mist-500">
              {t('storageAdmin.enableHint')}
            </span>
          </span>
        </label>

        {/* Was das Einschalten konkret bedeutet - lieber vorher sagen als
            hinterher erklaeren. */}
        <ul className="flex flex-col gap-1.5 border-t border-ink-700 pt-4 text-sm text-mist-500">
          <li>· {t('storageAdmin.pointMeasure')}</li>
          <li>· {t('storageAdmin.pointHouse')}</li>
          <li>· {t('storageAdmin.pointNoLimit')}</li>
        </ul>

        {speichern.isError && (
          <ErrorBanner
            message={
              speichern.error instanceof ApiError
                ? speichern.error.message
                : t('errors.generic')
            }
          />
        )}

        <div className="flex items-center gap-3 border-t border-ink-700 pt-4">
          <Button onClick={() => speichern.mutate(an)} disabled={!geaendert}>
            {speichern.isPending ? t('common.saving') : t('common.save')}
          </Button>
          {geaendert ? (
            <span className="text-sm text-warn-500">{t('common.unsaved')}</span>
          ) : (
            speichern.isSuccess && (
              <span className="text-sm text-ok-500">{t('storageAdmin.saved')}</span>
            )
          )}
        </div>
      </Card>

      {gespeichert && <Bestand />}
    </div>
  )
}

/** Was gerade erfasst ist - damit der Admin sieht, ob die Messung laeuft. */
function Bestand() {
  const { t, i18n } = useTranslation()

  const abfrage = useQuery({
    queryKey: ['storage-overview'],
    queryFn: () => api.get<StorageOverview>('/api/storage/overview'),
  })

  if (!abfrage.data) return null
  const daten = abfrage.data

  // Solange die erste Messung laeuft, ist alles null - dann sagen wir das,
  // statt eine Reihe von Nullen zu zeigen, die nach einem Fehler aussieht.
  if (daten.total_bytes === 0) {
    return (
      <Card className="p-5">
        <p className="text-sm text-mist-500">{t('storageAdmin.pending')}</p>
      </Card>
    )
  }

  const personen = daten.shares.filter((a) => a.user_id !== null)

  return (
    <Card className="flex flex-col gap-3 p-5">
      <h3 className="font-medium">{t('storageAdmin.stateTitle')}</h3>
      <dl className="flex flex-col gap-2 text-sm">
        <div className="flex justify-between gap-4">
          <dt className="text-mist-500">{t('storage.totalLabel')}</dt>
          <dd className="tabular-nums">{formatSize(daten.total_bytes, i18n.language)}</dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt className="text-mist-500">{t('storage.houseLabel')}</dt>
          <dd className="tabular-nums">
            {formatSize(daten.house_bytes, i18n.language)}
            <span className="ml-1.5 text-mist-600">
              ({t('storage.itemCount', { count: daten.house_items })})
            </span>
          </dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt className="text-mist-500">{t('storageAdmin.assigned')}</dt>
          <dd className="tabular-nums">
            {formatSize(
              personen.reduce((summe, a) => summe + a.used_bytes, 0),
              i18n.language,
            )}
            <span className="ml-1.5 text-mist-600">
              ({t('storageAdmin.people', { count: personen.length })})
            </span>
          </dd>
        </div>
      </dl>
    </Card>
  )
}
