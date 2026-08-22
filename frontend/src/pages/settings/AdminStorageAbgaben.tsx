import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { useState } from 'react'

import { ApiError, api } from '../../api/client'
import type {
  PapierkorbStand,
  StorageAbgabe,
  StorageLoeschvorschau,
} from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Button, Card, ErrorBanner, Spinner } from '../../components/ui'
import { formatDateTime, formatSize } from '../../lib/format'

/**
 * Was auf eine Entscheidung wartet.
 *
 * Der Nutzer hat „brauche ich nicht mehr" gewählt – **passiert ist dabei
 * nichts.** Der Titel liegt weiter auf der Platte und zählt weiter auf seinem
 * Konto, bis hier jemand entscheidet. Genau deshalb darf diese Karte nicht
 * unauffällig sein: Wer sie übersieht, lässt jemanden auf einer Belastung
 * sitzen, die er losgeworden zu sein glaubt.
 *
 * Zwei Ausgänge: **Ins Haus** bucht nur um und lässt die Datei liegen.
 * **Löschen** entfernt sie wirklich – der einzige Vorgang in Nexview ohne
 * Rückweg, und deshalb hinter einer Vorschau mit der tatsächlichen Dateiliste.
 */
export function AdminStorageAbgaben() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()

  const abfrage = useQuery({
    queryKey: ['storage-releases'],
    queryFn: () => api.get<StorageAbgabe[]>('/api/storage/releases'),
  })

  /**
   * Der dritte Ausgang: „Nicht mehr folgen" – für Abgaben mit Behalten-Wunsch.
   *
   * Die Folgen bleiben liegen, Sonarr lädt nur keine neuen mehr. Der Posten
   * zählt **weiter** beim Abgebenden – die Dateien sind ja noch da. Nicht
   * zerstörend und in Sonarr jederzeit umkehrbar, deshalb ohne Rückfrage.
   */
  const entfolgen = useMutation({
    mutationFn: (posten: number) =>
      api.post(`/api/storage/entries/${posten}/entfolgen`, {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['storage-releases'] })
      void queryClient.invalidateQueries({ queryKey: ['storage-user'] })
      void queryClient.invalidateQueries({ queryKey: ['storage-mine'] })
    },
  })

  const insHaus = useMutation({
    mutationFn: (posten: number) => api.post(`/api/storage/entries/${posten}/haus`, {}),
    onSuccess: () => {
      // Alle drei Seiten der Rechnung: Die Warteschlange wird kürzer, das
      // Konto leichter, der Hausbestand schwerer.
      void queryClient.invalidateQueries({ queryKey: ['storage-releases'] })
      void queryClient.invalidateQueries({ queryKey: ['storage-user'] })
      void queryClient.invalidateQueries({ queryKey: ['storage-overview'] })
      void queryClient.invalidateQueries({ queryKey: ['storage-house'] })
      void queryClient.invalidateQueries({ queryKey: ['storage-mine'] })
    },
  })

  // Welche Zeile gerade gelöscht werden soll - höchstens eine.
  const [loescht, setLoescht] = useState<StorageAbgabe | null>(null)

  if (abfrage.isLoading) return null
  const zeilen = abfrage.data ?? []
  // Nichts zu entscheiden heißt: keine Karte. Eine leere Warteschlange ist
  // keine Nachricht.
  if (zeilen.length === 0) return null

  return (
    <Card className="flex flex-col gap-3 border-warn-500/40 p-5">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-ink-700 pb-3">
        <h3 className="font-medium">{t('storageReleases.title')}</h3>
        <span className="text-sm font-semibold text-warn-500">
          {t('storageReleases.count', { count: zeilen.length })}
        </span>
      </div>

      <p className="text-sm text-mist-500">{t('storageReleases.intro')}</p>

      {insHaus.error || entfolgen.error ? (
        <ErrorBanner
          message={(() => {
            const fehler = insHaus.error ?? entfolgen.error
            return fehler instanceof ApiError ? fehler.message : t('errors.generic')
          })()}
        />
      ) : null}

      <ul className="flex flex-col">
        {zeilen.map((zeile) => (
          <li
            key={zeile.entry.id}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-ink-800 py-2.5 last:border-b-0"
          >
            <div className="min-w-0 flex-1">
              <p
                className="line-clamp-1 text-sm font-medium"
                title={zeile.entry.path || undefined}
              >
                {zeile.entry.title}
              </p>
              <p className="text-xs text-mist-600">
                {zeile.display_name || zeile.username}
                {zeile.entry.season !== null && (
                  <span className="ml-1.5">
                    {t('storage.season', { number: zeile.entry.season })}
                  </span>
                )}
                {zeile.entry.tier === 'uhd' && (
                  <span className="ml-1.5 text-accent-500">4K</span>
                )}
                {/* Der Wunsch gehört sichtbar an die Zeile: Er ist der halbe
                    Inhalt der Abgabe – ohne ihn entscheidet der Admin an dem
                    vorbei, was sich die Person vorgestellt hat. */}
                <span className="ml-1.5 text-mist-500">
                  ·{' '}
                  {t(
                    zeile.entry.release_wish === 'keep'
                      ? 'storageReleases.wishKeep'
                      : 'storageReleases.wishDelete',
                  )}
                </span>
                {/* Seit wann – ohne das lässt sich nicht erkennen, ob die
                    Warteschlange stockt. */}
                {zeile.released_at && (
                  <span className="ml-1.5">
                    ·{' '}
                    {t('storageReleases.since', {
                      date: formatDateTime(zeile.released_at, i18n.language),
                    })}
                  </span>
                )}
              </p>
            </div>

            <span className="shrink-0 tabular-nums">
              {formatSize(zeile.entry.size_bytes, i18n.language)}
            </span>

            <Button
              onClick={() => insHaus.mutate(zeile.entry.id)}
              disabled={insHaus.isPending}
              className="shrink-0 px-3 py-1 text-xs"
            >
              {t('storage.toHouse')}
            </Button>
            {/* Der zweite Knopf führt den **Wunsch** aus. Löschen ist der
                einzige Schritt ohne Rückweg – deshalb Rot, und deshalb erst
                eine Vorschau mit der tatsächlichen Dateiliste. „Nicht mehr
                folgen" ist umkehrbar und geht direkt. */}
            {zeile.entry.release_wish === 'keep' ? (
              <Button
                variant="ghost"
                onClick={() => entfolgen.mutate(zeile.entry.id)}
                disabled={entfolgen.isPending}
                className="shrink-0 border-warn-500/40 px-3 py-1 text-xs text-warn-500 hover:bg-warn-500/10 hover:text-warn-500"
              >
                {t('storageReleases.unfollow')}
              </Button>
            ) : (
              <Button
                variant="ghost"
                onClick={() => setLoescht(zeile)}
                className="shrink-0 border-bad-500/40 px-3 py-1 text-xs text-bad-500 hover:bg-bad-500/10 hover:text-bad-500"
              >
                {t('storageReleases.delete')}
              </Button>
            )}
          </li>
        ))}
      </ul>

      <p className="text-xs leading-relaxed text-mist-600">
        {t('storageReleases.hint')}
      </p>

      <Loeschdialog
        abgabe={loescht}
        onSchliessen={() => setLoescht(null)}
        onFertig={() => {
          setLoescht(null)
          void queryClient.invalidateQueries({ queryKey: ['storage-releases'] })
          void queryClient.invalidateQueries({ queryKey: ['storage-user'] })
          void queryClient.invalidateQueries({ queryKey: ['storage-overview'] })
          void queryClient.invalidateQueries({ queryKey: ['storage-mine'] })
          void queryClient.invalidateQueries({ queryKey: ['papierkorb-belegung'] })
        }}
      />
    </Card>
  )
}

/**
 * Die Rückfrage vor dem Löschen – **mit der tatsächlichen Dateiliste**.
 *
 * ⚠️ Der Administrator bestätigt mit ihr vor Augen und nicht mit einer Zahl:
 * Ein Fehler trifft Dateien, die jemand behalten wollte, und eine Zahl verrät
 * nicht, welche.
 *
 * Der Warnhinweis richtet sich danach, ob für **diese** Instanz ein Papierkorb
 * eingerichtet ist. Ohne ihn ist die Datei sofort weg, und das muss dort
 * stehen, wo entschieden wird – nicht zwei Karten weiter.
 */
function Loeschdialog({
  abgabe,
  onSchliessen,
  onFertig,
}: {
  abgabe: StorageAbgabe | null
  onSchliessen: () => void
  onFertig: () => void
}) {
  const { t, i18n } = useTranslation()

  const vorschau = useQuery({
    queryKey: ['loeschvorschau', abgabe?.entry.id],
    queryFn: () =>
      api.get<StorageLoeschvorschau>(
        `/api/storage/entries/${abgabe?.entry.id}/dateien`,
      ),
    enabled: abgabe !== null,
  })

  const papierkorb = useQuery({
    queryKey: ['papierkorb'],
    queryFn: () => api.get<PapierkorbStand>('/api/settings/recyclebin'),
    enabled: abgabe !== null,
  })

  const loeschen = useMutation({
    mutationFn: () => api.post(`/api/storage/entries/${abgabe?.entry.id}/loeschen`, {}),
    onSuccess: onFertig,
  })

  if (abgabe === null) return null

  const daten = vorschau.data
  const netz = papierkorb.data?.instances.find(
    (i) => i.media_type === abgabe.entry.media_type && i.tier === abgabe.entry.tier,
  )

  return (
    <ConfirmDialog
      open
      title={t('storageReleases.deleteTitle')}
      description={
        vorschau.isLoading ? (
          <div className="flex justify-center py-4">
            <Spinner />
          </div>
        ) : /* ⚠️ Kein Ergebnis heißt **nicht** „darf nicht gelöscht werden".
              Vorher fiel dieser Zweig auf „nur in der 4K-Instanz" zurück,
              sobald die Abfrage aus irgendeinem Grund nichts lieferte – und
              behauptete damit einen Grund, den niemand geprüft hatte. */
        !daten ? (
          <span className="text-bad-500">
            {vorschau.error instanceof ApiError
              ? vorschau.error.message
              : t('errors.generic')}
          </span>
        ) : !daten.deletable ? (
          <span className="text-bad-500">
            {t(
              daten.reason === 'series'
                ? 'storageReleases.deleteReasonSeries'
                : daten.reason === 'unmanaged'
                  ? 'storageReleases.deleteReasonUnmanaged'
                  : 'storageReleases.deleteReasonTier',
            )}
          </span>
        ) : (
          <>
            <p className="font-medium">{abgabe.entry.title}</p>
            <p className="mt-2">
              {t(
                daten.files.length > 1
                  ? 'storageReleases.deleteFilesMany'
                  : 'storageReleases.deleteFiles',
              )}
            </p>
            <ul className="mt-1.5 flex max-h-52 flex-col overflow-y-auto">
              {daten.files.map((datei) => (
                <li
                  key={datei.path}
                  className="flex items-baseline gap-2 border-b border-ink-800 py-1 last:border-b-0"
                >
                  <span className="min-w-0 flex-1 font-mono text-xs break-all text-mist-400">
                    {datei.path}
                  </span>
                  <span className="shrink-0 text-xs tabular-nums text-mist-500">
                    {formatSize(datei.size_bytes, i18n.language)}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )
      }
      warning={
        loeschen.error
          ? loeschen.error instanceof ApiError
            ? loeschen.error.message
            : t('errors.generic')
          : daten?.deletable
            ? netz?.path
              ? t('storageReleases.deleteWithBin', { name: netz.name })
              : t('storageReleases.deleteNoBin')
            : undefined
      }
      confirmLabel={t('storageReleases.deleteConfirm')}
      loading={loeschen.isPending}
      onCancel={onSchliessen}
      onConfirm={() => daten?.deletable && loeschen.mutate()}
    />
  )
}
