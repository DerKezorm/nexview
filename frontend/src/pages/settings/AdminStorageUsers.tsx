import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { StorageOverview, StorageUserPage } from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Pagination } from '../../components/Pagination'
import { Card, ErrorBanner, Spinner } from '../../components/ui'
import { formatSize } from '../../lib/format'

/**
 * Belegung nach Konto – und der Weg, ein Konto zu entlasten.
 *
 * **Der einzige Eingriff hier löscht nichts.** „Ins Haus" ändert ausschließlich,
 * *wem* ein Titel zugerechnet wird; auf der Platte bleibt alles, wie es ist.
 * Genau das macht die Mechanik erträglich: Der Betreiber kann sagen „den
 * Klassiker will hier ohnehin jeder sehen, der soll nicht auf deinem Konto
 * lasten", ohne dass irgendjemand etwas wegwerfen muss.
 *
 * Bewusst hier und nicht in der Nutzerverwaltung: Dort steht, was jemand
 * *darf* – hier, was er *belegt*. Zwei verschiedene Fragen.
 */
export function AdminStorageUsers() {
  const { t, i18n } = useTranslation()
  const [offen, setOffen] = useState<number | null>(null)

  const abfrage = useQuery({
    queryKey: ['storage-overview'],
    queryFn: () => api.get<StorageOverview>('/api/storage/overview'),
  })

  if (!abfrage.data) return null

  // Der Hausbestand steht als eigene Zeile in `shares`, gehört aber niemandem –
  // er hat hier nichts zu suchen und seinen eigenen Reiter im Profil.
  const personen = abfrage.data.shares
    .filter((anteil) => anteil.user_id !== null)
    .sort((a, b) => b.used_bytes - a.used_bytes)

  return (
    <Card className="flex flex-col gap-3 p-5">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-ink-700 pb-3">
        <h3 className="font-medium">{t('storageAdmin.usersTitle')}</h3>
        <span className="text-sm text-mist-600">{t('storageAdmin.usersHint')}</span>
      </div>

      {personen.length === 0 ? (
        <p className="py-4 text-sm text-mist-500">{t('storageAdmin.usersEmpty')}</p>
      ) : (
        <ul className="flex flex-col">
          {personen.map((anteil) => {
            const id = anteil.user_id as number
            const auf = offen === id
            return (
              <li key={id} className="border-b border-ink-800 last:border-b-0">
                <button
                  type="button"
                  onClick={() => setOffen(auf ? null : id)}
                  aria-expanded={auf}
                  className="flex w-full items-center gap-3 py-2.5 text-left hover:text-accent-500"
                >
                  <span className="min-w-0 flex-1 truncate font-medium">
                    {anteil.display_name || anteil.username}
                  </span>
                  <span className="shrink-0 text-sm text-mist-600">
                    {t('storage.itemCount', { count: anteil.items })}
                  </span>
                  {/* „91 von 300 GB" – die blanke Zahl beantwortet nicht, was
                      man hier wissen will: ob jemand nah an seiner Grenze ist.
                      Ohne Grenze bleibt es bei der Zahl; „91 GB von unbegrenzt"
                      wäre eine Formulierung ohne Aussage. */}
                  <span
                    className={
                      'shrink-0 tabular-nums ' +
                      (anteil.limit_bytes !== null && anteil.used_bytes >= anteil.limit_bytes
                        ? 'text-bad-500'
                        : '')
                    }
                  >
                    {anteil.limit_bytes === null
                      ? formatSize(anteil.used_bytes, i18n.language)
                      : t('storage.usedOfLimit', {
                          used: formatSize(anteil.used_bytes, i18n.language),
                          limit: formatSize(anteil.limit_bytes, i18n.language),
                        })}
                  </span>
                </button>
                {auf && <Posten userId={id} />}
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

/** Die einzelnen Titel eines Kontos, das Größte zuerst. */
function Posten({ userId }: { userId: number }) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [suche, setSuche] = useState('')
  const [seite, setSeite] = useState(1)
  // Welche Zeile gerade nachfragt. Höchstens eine – zwei offene Rückfragen
  // nebeneinander wären eine Einladung, die falsche zu bestätigen.
  const [frage, setFrage] = useState<number | null>(null)

  const abfrage = useQuery({
    queryKey: ['storage-user', userId, suche, seite],
    queryFn: () =>
      api.get<StorageUserPage>(
        '/api/storage/user/' + userId + '?page=' + seite + '&q=' + encodeURIComponent(suche),
      ),
    // Beim Tippen und Blättern die alte Seite stehen lassen – sonst blitzt bei
    // jedem Buchstaben der Ladekreis auf.
    placeholderData: (vorher?: StorageUserPage) => vorher,
  })

  const insHaus = useMutation({
    mutationFn: (posten: number) => api.post('/api/storage/entries/' + posten + '/haus', {}),
    onSuccess: () => {
      // Beide Seiten der Rechnung neu holen: Das Konto wird leichter, der
      // Hausbestand schwerer. Nur eines zu erneuern hieße, dem Administrator
      // eine Zahl zu zeigen, die nicht mehr stimmt.
      queryClient.invalidateQueries({ queryKey: ['storage-user', userId] })
      queryClient.invalidateQueries({ queryKey: ['storage-overview'] })
      queryClient.invalidateQueries({ queryKey: ['storage-house'] })
      queryClient.invalidateQueries({ queryKey: ['storage-mine'] })
    },
  })

  if (abfrage.isLoading) {
    return (
      <div className="flex justify-center py-6">
        <Spinner />
      </div>
    )
  }

  // TanStack Query behält alte Daten, wenn ein Nachladen scheitert – ohne
  // failureReason bliebe ein Fehler unsichtbar.
  const fehler = abfrage.error ?? abfrage.failureReason ?? insHaus.error
  const daten = abfrage.data
  if (!daten) return null

  // Der Posten, über den gerade gefragt wird - für den Text des Dialogs.
  const ausgewaehlt = daten.entries.find((eintrag) => eintrag.id === frage)

  return (
    <div className="flex flex-col gap-3 border-t border-ink-800 py-3 pl-3">
      {/* Kein zweites „87 GB von 50 GB" – das steht schon in der Zeile, die
          man angeklickt hat, keine zwanzig Pixel darüber. */}
      {fehler ? (
        <ErrorBanner
          message={fehler instanceof ApiError ? fehler.message : t('errors.generic')}
        />
      ) : null}

      {daten.items === 0 ? (
        <p className="text-sm text-mist-600">{t('storage.userEmpty')}</p>
      ) : (
        <>
          {/* Die Suche erst, wenn es überhaupt etwas zu suchen gibt. Bei fünf
              Titeln wäre ein Suchfeld nur ein Bedienelement ohne Anlass. */}
          {daten.items > daten.per_page && (
            <input
              type="search"
              value={suche}
              onChange={(event) => {
                setSuche(event.target.value)
                // Nach einer neuen Suche wieder vorn anfangen – sonst steht man
                // auf Seite 7 einer Liste, die nur noch drei Zeilen hat.
                setSeite(1)
              }}
              placeholder={t('storage.searchPlaceholder')}
              className="w-full rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none sm:max-w-sm"
            />
          )}

          {daten.entries.length === 0 ? (
            <p className="text-sm text-mist-600">{t('storage.noMatch')}</p>
          ) : (
            <ul className="flex flex-col">
              {daten.entries.map((eintrag) => (
                <li
                  key={eintrag.id}
                  className="flex items-center gap-3 border-b border-ink-800 py-2 last:border-b-0"
                >
                  <div className="min-w-0 flex-1">
                    {/* Der Pfad steht nur noch in der Sprechblase. Ausgeschrieben
                        passte er in dieser schmalen Spalte nirgends hin – abgeschnitten
                        nach zwei Dritteln sagt ein Pfad nichts mehr, er sieht nur
                        nach Text aus. Weggeworfen wird er trotzdem nicht: Er
                        beantwortet die Frage, welcher von zwei gleichnamigen Titeln
                        gemeint ist, und genau dann greift man danach.
                        Ausgeschrieben steht er weiter im Hausbestand und auf der
                        Detailseite – dort ist die Breite da. */}
                    <p
                      className="line-clamp-1 text-sm font-medium"
                      title={eintrag.path || undefined}
                    >
                      {eintrag.title}
                    </p>
                    <p className="text-xs text-mist-600">
                      {eintrag.season !== null
                        ? t('storage.season', { number: eintrag.season })
                        : t(
                            eintrag.media_type === 'movie'
                              ? 'common.movie'
                              : 'common.series',
                          )}
                      {eintrag.tier === 'uhd' && (
                        <span className="ml-1.5 text-accent-500">4K</span>
                      )}
                    </p>
                  </div>
                  <span className="shrink-0 tabular-nums">
                    {formatSize(eintrag.size_bytes, i18n.language)}
                  </span>
                  <button
                    type="button"
                    onClick={() => setFrage(eintrag.id)}
                    disabled={insHaus.isPending}
                    className="shrink-0 rounded-full border border-ink-700 px-3 py-1 text-xs font-medium text-mist-400 transition-colors hover:border-accent-600 hover:text-mist-100 disabled:opacity-40"
                  >
                    {t('storage.toHouse')}
                  </button>
                </li>
              ))}
            </ul>
          )}

          {daten.matches > daten.per_page && (
            <Pagination
              seite={seite}
              seiten={Math.ceil(daten.matches / daten.per_page)}
              onSeite={setSeite}
            />
          )}
        </>
      )}

      {/* Der hauseigene Dialog, nicht der des Browsers. Der Unterschied ist
          nicht nur Optik: Hier lässt sich sagen, was wirklich passiert – und
          vor allem, was **nicht** passiert. „Ins Haus" klingt nach Wegwerfen,
          und genau das ist es nicht. */}
      <ConfirmDialog
        open={ausgewaehlt !== undefined}
        title={t('storage.toHouseTitle')}
        description={t('storage.toHouseText', { title: ausgewaehlt?.title ?? '' })}
        confirmLabel={t('storage.toHouseYes')}
        loading={insHaus.isPending}
        onCancel={() => setFrage(null)}
        onConfirm={() => {
          if (ausgewaehlt) insHaus.mutate(ausgewaehlt.id)
          setFrage(null)
        }}
      />
    </div>
  )
}
