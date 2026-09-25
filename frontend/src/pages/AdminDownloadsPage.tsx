import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, api } from '../api/client'
import type {
  DownloadAktion,
  DownloadAktionsAntwort,
  DownloadAutomatik,
  DownloadHaenger,
  DownloadKandidat,
  DownloadLaufend,
  DownloadVerlaufZeile,
  DownloadsStand,
} from '../api/types'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Fenster } from '../components/Fenster'
import { Umschalter } from '../components/Umschalter'
import { AUSWAHL, Button, ErrorBanner, Section, SeiteLaedt, Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'
import { useWegKontext } from '../hooks/useWegKontext'
import { useDownloadsTexte } from '../i18n/downloads'
import { folgenKompakt, formatDateTime } from '../lib/format'

/**
 * Die Seite Downloads: was in Radarr und Sonarr hängt, warum, und die Knöpfe dagegen.
 *
 * ⚠️ **Oben steht, was jemanden braucht, nicht was läuft.** Eine Warteschlange
 * mit dreißig Einträgen, in der zwei hängen, ist sonst eine Suchaufgabe. Wer
 * die Seite öffnet, will wissen, ob er etwas tun muss.
 *
 * ⚠️ **Der Grund in Worten, der Wortlaut darunter.** Radarr schreibt englische
 * Sätze wie „No files found are eligible for import". Wer sie versteht, soll
 * sie lesen können; vorn steht, was los ist und was hilft. Kennt Nexview einen
 * Grund nicht, sagt die Seite auch das, statt zu raten.
 *
 * ⚠️ **Was löscht, fragt vorher und sagt, was passiert.** Entfernen löscht die
 * Daten im Download-Programm; bei einem Torrent endet damit auch das Teilen.
 */

const STAND = ['admin-downloads']
const AUTOMATIK = ['admin-downloads-automatik']
const VERLAUF = ['admin-downloads-verlauf']

type T = ReturnType<typeof useTranslation>['t']
type Rueckfrage = Exclude<DownloadAktion, 'manuell_importieren'>
type Entwurf = { an: boolean; regeln: Record<string, DownloadAktion | null> }

/** „Staffel 1, Folge 2–4" je Staffel. */
function folgenText(folgen: number[][], t: T): string | null {
  if (folgen.length === 0) return null
  const jeStaffel = new Map<number, number[]>()
  for (const [staffel, folge] of folgen) {
    jeStaffel.set(staffel, [...(jeStaffel.get(staffel) ?? []), folge])
  }
  return [...jeStaffel.entries()]
    .map(([staffel, nummern]) =>
      t('downloads.stuck.episodes', { staffel, folgen: folgenKompakt(nummern) }),
    )
    .join(' · ')
}

/**
 * Größe einer Datei beim Import.
 *
 * ⚠️ Bewusst nicht `formatSize`: Das rechnet nur in GiB, und ein Sample von
 * 40 MB stünde dort als „0 GiB". Gerade an der Größe erkennt man ein Sample.
 */
function groesseText(bytes: number, sprache: string): string {
  const mib = bytes / 1024 ** 2
  if (mib < 1) return '< 1 MiB'
  if (mib < 1024) return `${Math.round(mib).toLocaleString(sprache)} MiB`
  return `${(mib / 1024).toLocaleString(sprache, { maximumFractionDigits: 1 })} GiB`
}

export function AdminDownloadsPage() {
  const { t, i18n } = useTranslation()
  // Rundgang-Befund 8: Im NEX-Betrieb nannte die Seite Radarr und Sonarr.
  const weg = useWegKontext()
  const bereit = useDownloadsTexte()
  const queryClient = useQueryClient()
  const [meldung, setMeldung] = useState<string | null>(null)

  const stand = useQuery({
    queryKey: STAND,
    queryFn: () => api.get<DownloadsStand>('/api/admin/downloads'),
    // Der Rundgang läuft alle zwei Minuten; häufiger zu fragen brächte nichts.
    refetchInterval: 60_000,
  })

  if (!bereit) return <SeiteLaedt />

  const daten = stand.data
  const stumm = daten?.instanzen.filter((instanz) => !instanz.erreichbar) ?? []

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
            {t('downloads.title')}
            <span className="text-accent-500">.</span>
          </h1>
          <p className="text-sm text-mist-500">{t('downloads.subtitle', weg)}</p>
        </div>
        <div className="flex items-center gap-3">
          {daten && (
            <span className="text-xs text-mist-600">
              {t('downloads.standAm', { zeit: formatDateTime(daten.stand_am, i18n.language) })}
            </span>
          )}
          <Button
            variant="ghost"
            loading={stand.isFetching}
            onClick={() => {
              setMeldung(null)
              void queryClient.invalidateQueries({ queryKey: STAND })
              void queryClient.invalidateQueries({ queryKey: VERLAUF })
            }}
          >
            {t('downloads.reload')}
          </Button>
        </div>
      </header>

      {stand.isLoading && (
        <div className="flex justify-center py-12">
          <Spinner className="h-6 w-6" />
        </div>
      )}
      {stand.error && <ErrorBanner message={stand.error.message} />}
      {meldung && (
        <p
          role="status"
          className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500"
        >
          {meldung}
        </p>
      )}

      {daten && daten.instanzen.length === 0 && (
        <p className="text-sm text-mist-500">{t('downloads.noInstances', weg)}</p>
      )}

      {daten && daten.instanzen.length > 0 && (
        <>
          {stumm.map((instanz) => (
            <p
              key={instanz.kennung}
              className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500"
            >
              {t('downloads.instanceDown', { name: instanz.name })}
            </p>
          ))}

          <Section title={t('downloads.stuck.title')} breit>
            {daten.haenger.length === 0 ? (
              <p className="text-sm text-mist-500">{t('downloads.stuck.none')}</p>
            ) : (
              <div className="flex flex-col gap-4">
                {daten.haenger.map((haenger) => (
                  <HaengerKarte key={haenger.id} haenger={haenger} onErledigt={setMeldung} />
                ))}
              </div>
            )}
          </Section>

          <Section title={t('downloads.running.title')} breit>
            {daten.laufend.length === 0 ? (
              // Hängt oben etwas, sind die Warteschlangen nicht leer; es lädt nur nichts weiter.
              <p className="text-sm text-mist-500">
                {daten.haenger.length > 0
                  ? t('downloads.running.noneElse')
                  : t('downloads.running.none')}
              </p>
            ) : (
              <ul className="flex flex-col divide-y divide-ink-800">
                {daten.laufend.map((zeile, index) => (
                  <LaufZeile key={`${zeile.kennung}-${index}`} zeile={zeile} />
                ))}
              </ul>
            )}
            {/* Rundgang-Befund 9: nexcrate liefert gescheiterte Downloads in
                der Warteschlange mit. Mit Fortschrittsbalken unter „Läuft“
                logen sie; was den Betreiber braucht, steht oben. */}
            {(daten.gescheitert ?? 0) > 0 && (
              <p className="mt-3 text-xs text-mist-500">
                {t('downloads.running.failed', { count: daten.gescheitert })}
              </p>
            )}
          </Section>

          <AutomatikBereich />
          <VerlaufBereich />
        </>
      )}
    </div>
  )
}

function HaengerKarte({
  haenger,
  onErledigt,
}: {
  haenger: DownloadHaenger
  onErledigt: (text: string) => void
}) {
  const { t, i18n } = useTranslation()
  // #job-17: Rückfragen und Meldungen nennen im NEX-Betrieb nexcrate.
  const weg = useWegKontext()
  const queryClient = useQueryClient()
  const [frage, setFrage] = useState<Rueckfrage | null>(null)
  const [importOffen, setImportOffen] = useState(false)
  const [mehr, setMehr] = useState(false)

  function veraltet() {
    void queryClient.invalidateQueries({ queryKey: STAND })
    void queryClient.invalidateQueries({ queryKey: VERLAUF })
  }

  const aktion = useMutation({
    mutationFn: (was: Rueckfrage) =>
      was === 'erneut_pruefen'
        ? api.post<DownloadAktionsAntwort>(`/api/admin/downloads/${haenger.id}/erneut`)
        : api.post<DownloadAktionsAntwort>(`/api/admin/downloads/${haenger.id}/entfernen`, {
            neu_suchen: was === 'entfernen_neu_suchen',
          }),
    onSuccess: (antwort, was) => {
      setFrage(null)
      if (was === 'entfernen_neu_suchen') {
        onErledigt(
          antwort.gesucht
            ? t('downloads.done.entfernen_neu_suchen')
            : t('downloads.done.entfernen_ohne_suche', weg),
        )
      } else if (was === 'entfernen') {
        onErledigt(t('downloads.done.entfernen'))
      } else {
        onErledigt(t('downloads.done.erneut_pruefen'))
      }
      veraltet()
    },
    onError: (fehler) => {
      // Weg oder nie da: Die Karte ist veraltet. Der Satz dazu steht oben,
      // denn mit dem Neuladen verschwindet sie samt Rückfrage.
      if (
        fehler instanceof ApiError &&
        (fehler.code === 'download_gone' || fehler.code === 'download_not_found')
      ) {
        setFrage(null)
        onErledigt(fehler.message)
        veraltet()
      }
    },
  })

  function knopf(was: DownloadAktion, haupt: boolean) {
    return (
      <Button
        key={was}
        variant={haupt ? 'primary' : 'ghost'}
        onClick={() => {
          aktion.reset()
          if (was === 'manuell_importieren') setImportOffen(true)
          else setFrage(was)
        }}
      >
        {t(`downloads.aktion.${was}`)}
      </Button>
    )
  }

  const folgen = folgenText(haenger.folgen, t)

  return (
    <article className="flex gap-4 rounded-2xl border border-warn-500/40 bg-warn-500/5 p-4">
      {haenger.poster && (
        <img
          src={haenger.poster}
          alt=""
          className="hidden h-24 w-16 shrink-0 rounded-lg object-cover sm:block"
        />
      )}
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          {/* Ohne Titel steht hier der Release-Name, ein Wort ohne Leerzeichen.
              Er trug die Seite am Prüfstand auf 413 statt 390 px Breite. */}
          <h3 className="min-w-0 text-base font-semibold [overflow-wrap:anywhere] text-mist-100">
            {haenger.titel || haenger.release}
            {haenger.jahr ? <span className="ml-1.5 text-mist-500">({haenger.jahr})</span> : null}
          </h3>
          <span className="text-xs text-mist-500">
            {haenger.instanz} ·{' '}
            {t('downloads.stuck.since', {
              zeit: formatDateTime(haenger.haengt_seit, i18n.language),
            })}
          </span>
        </div>
        {folgen && <p className="text-xs text-mist-400">{folgen}</p>}

        <p className="text-sm font-medium text-warn-500">
          {t(`downloads.grund.${haenger.grund}.titel`, {
            defaultValue: haenger.grund,
          })}
        </p>
        <p className="text-sm leading-relaxed text-mist-300">
          {/* Ein Grund, den Nexview nicht kennt, steht als Kennung da - das ist
              ehrlich. nexcrate darf neue Codes bekommen, ohne dass hier ein
              Schlüsselname erscheint. */}
          {t(`downloads.grund.${haenger.grund}.hilfe`, { defaultValue: "" })}
        </p>

        {haenger.wortlaut.length > 0 && (
          <details className="text-xs text-mist-500">
            <summary className="cursor-pointer select-none hover:text-mist-300">
              {t('downloads.stuck.original', { instanz: haenger.instanz })}
            </summary>
            <ul className="mt-1.5 flex flex-col gap-0.5 font-mono break-words">
              {haenger.wortlaut.map((zeile) => (
                <li key={zeile}>{zeile}</li>
              ))}
            </ul>
          </details>
        )}

        <p className="truncate font-mono text-xs text-mist-600" title={haenger.release}>
          {haenger.release}
          {haenger.programm ? ` · ${haenger.programm}` : ''}
        </p>
        {haenger.besteller.length > 0 && (
          <p className="text-xs text-mist-500">
            {t('downloads.stuck.requestedBy', {
              namen: haenger.besteller.map((b) => b.name).join(', '),
            })}
          </p>
        )}

        <div className="mt-1 flex flex-wrap items-center gap-2">
          {haenger.empfohlen.map((was, index) => knopf(was, index === 0))}
          {mehr && haenger.weitere.map((was) => knopf(was, false))}
          {haenger.weitere.length > 0 && (
            <button
              type="button"
              aria-expanded={mehr}
              onClick={() => setMehr((offen) => !offen)}
              className="px-2 text-xs text-mist-500 underline hover:text-mist-300"
            >
              {mehr ? t('downloads.stuck.fewer') : t('downloads.stuck.more')}
            </button>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={frage !== null}
        title={frage ? t(`downloads.confirm.${frage}.title`) : ''}
        description={frage ? t(`downloads.confirm.${frage}.text`, { release: haenger.release, ...weg }) : ''}
        warning={frage && frage !== 'erneut_pruefen' ? t(`downloads.confirm.${frage}.warning`) : undefined}
        fehler={aktion.error ? aktion.error.message : undefined}
        confirmLabel={frage ? t(`downloads.aktion.${frage}`) : ''}
        loading={aktion.isPending}
        onConfirm={() => {
          if (frage) aktion.mutate(frage)
        }}
        onCancel={() => {
          setFrage(null)
          aktion.reset()
        }}
      />

      {importOffen && (
        <ImportFenster
          haenger={haenger}
          onSchliessen={() => setImportOffen(false)}
          onErledigt={(text) => {
            setImportOffen(false)
            onErledigt(text)
            veraltet()
          }}
        />
      )}
    </article>
  )
}

function ImportFenster({
  haenger,
  onSchliessen,
  onErledigt,
}: {
  haenger: DownloadHaenger
  onSchliessen: () => void
  onErledigt: (text: string) => void
}) {
  const { t, i18n } = useTranslation()
  // #job-17: Rückfragen und Meldungen nennen im NEX-Betrieb nexcrate.
  const weg = useWegKontext()
  const kandidaten = useQuery({
    queryKey: ['admin-downloads-dateien', haenger.id],
    queryFn: () => api.get<DownloadKandidat[]>(`/api/admin/downloads/${haenger.id}/dateien`),
    // Die Dateien ändern sich drüben; ein alter Stand hier wäre eine falsche Auswahl.
    gcTime: 0,
  })
  // `null` heißt: noch nichts angefasst, es gilt der Vorschlag.
  const [auswahl, setAuswahl] = useState<Set<string> | null>(null)
  const [trotzdem, setTrotzdem] = useState(false)

  const liste = kandidaten.data ?? []
  const einwand = (k: DownloadKandidat) => k.ablehnungen.some((a) => a.dauerhaft)
  // ⚠️ Vorgewählt ist nur, was zugeordnet ist und keinen Einwand hat. Eine
  // Datei, gegen die Radarr etwas hat, wählt man bewusst aus oder gar nicht.
  const vorschlag = new Set(liste.filter((k) => k.zuordenbar && !einwand(k)).map((k) => k.pfad))
  const gewaehlt = auswahl ?? vorschlag
  const mitEinwand = liste.some((k) => gewaehlt.has(k.pfad) && einwand(k))

  const importieren = useMutation({
    mutationFn: () =>
      api.post<DownloadAktionsAntwort>(`/api/admin/downloads/${haenger.id}/importieren`, {
        pfade: [...gewaehlt],
        trotzdem: mitEinwand && trotzdem,
      }),
    onSuccess: (antwort) => {
      if (antwort.befehl === 'completed') onErledigt(t('downloads.done.import_completed'))
      else if (antwort.befehl === 'failed') onErledigt(t('downloads.done.import_failed', weg))
      else onErledigt(t('downloads.done.import_running'))
    },
  })

  function umschalten(pfad: string) {
    const neu = new Set(gewaehlt)
    if (neu.has(pfad)) neu.delete(pfad)
    else neu.add(pfad)
    setAuswahl(neu)
  }

  return (
    <Fenster
      offen
      breit
      titel={t('downloads.import.title')}
      unterzeile={haenger.release}
      onSchliessen={onSchliessen}
      fuss={
        <>
          <Button variant="ghost" onClick={onSchliessen} disabled={importieren.isPending}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => importieren.mutate()}
            loading={importieren.isPending}
            disabled={gewaehlt.size === 0 || (mitEinwand && !trotzdem)}
          >
            {t('downloads.import.submit')}
          </Button>
        </>
      }
    >
      {kandidaten.isLoading && (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner />
          {t('downloads.import.loading')}
        </p>
      )}
      {kandidaten.error && <ErrorBanner message={kandidaten.error.message} />}
      {kandidaten.data && liste.length === 0 && (
        <p className="text-sm text-mist-500">{t('downloads.import.empty', weg)}</p>
      )}

      {liste.length > 0 && (
        <div className="flex flex-col gap-3">
          <ul className="flex flex-col gap-2">
            {liste.map((kandidat) => (
              <li
                key={kandidat.pfad}
                className="rounded-xl border border-ink-700 bg-ink-900/60 px-3 py-2.5"
              >
                <label className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
                    checked={gewaehlt.has(kandidat.pfad)}
                    disabled={!kandidat.zuordenbar}
                    onChange={() => umschalten(kandidat.pfad)}
                  />
                  <span className="flex min-w-0 flex-col gap-1">
                    <span className="font-mono text-xs break-all text-mist-200">
                      {kandidat.name}
                    </span>
                    <span className="text-xs text-mist-500">
                      {[
                        kandidat.zuordenbar
                          ? [kandidat.zuordnung, folgenText(kandidat.folgen, t)]
                              .filter(Boolean)
                              .join(' · ')
                          : t('downloads.import.unmapped'),
                        kandidat.qualitaet,
                        kandidat.sprachen.join(', '),
                        groesseText(kandidat.groesse, i18n.language),
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                    {kandidat.ablehnungen.length > 0 && (
                      <ul className="flex flex-col gap-0.5 text-xs text-warn-500">
                        {kandidat.ablehnungen.map((ablehnung) => (
                          <li key={ablehnung.text}>
                            {ablehnung.text}
                            {ablehnung.dauerhaft ? '' : ` (${t('downloads.import.temporary')})`}
                          </li>
                        ))}
                      </ul>
                    )}
                  </span>
                </label>
              </li>
            ))}
          </ul>

          <p className="text-xs text-mist-500">
            {t('downloads.import.selected', { count: gewaehlt.size })}
          </p>

          {mitEinwand && (
            <label className="flex items-start gap-3 rounded-xl border border-warn-500/40 bg-warn-500/10 px-3 py-2.5 text-sm text-warn-500">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
                checked={trotzdem}
                onChange={(event) => setTrotzdem(event.target.checked)}
              />
              <span className="flex flex-col gap-1">
                <span className="font-medium">
                  {t('downloads.import.anyway', { instanz: haenger.instanz })}
                </span>
                <span className="text-xs">
                  {t('downloads.import.anywayHint', { instanz: haenger.instanz })}
                </span>
              </span>
            </label>
          )}

          <p className="text-xs text-mist-600">
            {t('downloads.import.mapping', { instanz: haenger.instanz })}
          </p>
          {importieren.error && <ErrorBanner message={importieren.error.message} />}
        </div>
      )}
    </Fenster>
  )
}

function LaufZeile({ zeile }: { zeile: DownloadLaufend }) {
  const { t } = useTranslation()
  const folgen = folgenText(zeile.folgen, t)
  const name = zeile.titel || zeile.release

  return (
    <li className="flex flex-col gap-1.5 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="min-w-0 text-sm font-medium [overflow-wrap:anywhere] text-mist-200">
          {name}
          {zeile.jahr ? ` (${zeile.jahr})` : ''}
          {folgen ? ` · ${folgen}` : ''}
        </span>
        <span className="text-xs text-mist-500 tabular-nums">
          {[
            zeile.instanz,
            zeile.programm,
            zeile.fortschritt !== null ? `${zeile.fortschritt} %` : null,
            zeile.restzeit ? t('downloads.running.remaining', { zeit: zeile.restzeit }) : null,
          ]
            .filter(Boolean)
            .join(' · ')}
        </span>
      </div>
      {zeile.fortschritt !== null && (
        <div
          role="progressbar"
          aria-label={name}
          aria-valuenow={zeile.fortschritt}
          aria-valuemin={0}
          aria-valuemax={100}
          className="h-1.5 overflow-hidden rounded-full bg-ink-800"
        >
          <div
            className="h-full rounded-full bg-accent-500"
            style={{ width: `${zeile.fortschritt}%` }}
          />
        </div>
      )}
      <p className="truncate font-mono text-xs text-mist-600" title={zeile.release}>
        {zeile.release}
      </p>
      {zeile.beobachtet && (
        <p className="text-xs text-warn-500">
          {t('downloads.running.watching', {
            grund: t(`downloads.grund.${zeile.beobachtet}.titel`),
          })}
        </p>
      )}
    </li>
  )
}

function AutomatikBereich() {
  const { t } = useTranslation()
  const weg = useWegKontext()
  const { data: config } = useConfig()
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: AUTOMATIK,
    queryFn: () => api.get<DownloadAutomatik>('/api/admin/downloads/automatik'),
  })
  const [entwurf, setEntwurf] = useState<Entwurf | null>(null)
  const [gespeichert, setGespeichert] = useState(false)

  const speichern = useMutation({
    mutationFn: (wert: Entwurf) => api.put<DownloadAutomatik>('/api/admin/downloads/automatik', wert),
    onSuccess: (neu) => {
      queryClient.setQueryData(AUTOMATIK, neu)
      void queryClient.invalidateQueries({ queryKey: STAND })
      setEntwurf(null)
      setGespeichert(true)
    },
  })

  const daten = query.data
  if (!daten) return query.error ? <ErrorBanner message={query.error.message} /> : null

  // #58: Im NEX-Betrieb entscheidet nexcrate selbst, was bei hängenden
  // Downloads automatisch geschieht (Entscheidung des Betreibers,
  // 25.09.2026: keine zwei Automatiken nebeneinander). Schalter und Regeln
  // gäbe es hier nur zum Schein; gemeldet wird trotzdem, wenn derselbe
  // Download wiederholt hängt.
  if (daten.beim_weg) {
    const sprung = config?.beschaffung_sprung?.probleme
    return (
      <Section title={t('downloads.automation.title')}>
        <p className="text-sm text-mist-400">{t('downloads.automation.beimWeg', weg)}</p>
        <p className="text-xs leading-relaxed text-mist-500">
          {t('downloads.automation.beimWegMeldet', {
            ab: daten.wiederholt_ab,
            tage: daten.wiederholt_tage,
          })}
        </p>
        {sprung && (
          <a
            className="text-sm text-accent-400 hover:underline"
            href={sprung}
            target="_blank"
            rel="noreferrer"
          >
            {t('beschaffung.openThere', weg)}
          </a>
        )}
      </Section>
    )
  }

  const aktuell: Entwurf = entwurf ?? {
    an: daten.an,
    regeln: Object.fromEntries(daten.regeln.map((regel) => [regel.grund, regel.aktion])),
  }

  function aendern(neu: Entwurf) {
    setGespeichert(false)
    setEntwurf(neu)
  }

  return (
    <Section title={t('downloads.automation.title')}>
      <p className="text-sm text-mist-400">{t('downloads.automation.intro')}</p>
      <Umschalter
        wert={aktuell.an ? 'an' : 'aus'}
        wahl={['aus', 'an'] as const}
        onChange={(wert) => aendern({ ...aktuell, an: wert === 'an' })}
        label={(wert) => (wert === 'an' ? t('downloads.automation.on') : t('downloads.automation.off'))}
      />
      <ul className="flex flex-col divide-y divide-ink-800">
        {daten.regeln.map((regel) => (
          // Auf dem Handy untereinander: Nebeneinander blieb für den Grund nur ein
          // Wort je Zeile, weil die Auswahl ihre volle Breite behält.
          <li
            key={regel.grund}
            className="flex flex-col gap-2 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-3"
          >
            <span className="min-w-0 text-sm text-mist-300 sm:flex-1">
              {t(`downloads.grund.${regel.grund}.titel`)}
            </span>
            <select
              className={`${AUSWAHL} w-full sm:w-60`}
              aria-label={t(`downloads.grund.${regel.grund}.titel`)}
              value={aktuell.regeln[regel.grund] ?? ''}
              onChange={(event) =>
                aendern({
                  ...aktuell,
                  regeln: {
                    ...aktuell.regeln,
                    [regel.grund]: (event.target.value || null) as DownloadAktion | null,
                  },
                })
              }
            >
              <option value="">{t('downloads.automation.nothing')}</option>
              {regel.erlaubt.map((erlaubt) => (
                <option key={erlaubt} value={erlaubt}>
                  {t(`downloads.aktion.${erlaubt}`)}
                </option>
              ))}
            </select>
          </li>
        ))}
      </ul>
      <p className="text-xs leading-relaxed text-mist-500">
        {t('downloads.automation.limits', {
          obergrenze: daten.obergrenze,
          stunden: daten.fenster_stunden,
          ab: daten.wiederholt_ab,
          tage: daten.wiederholt_tage,
        })}
      </p>
      <p className="text-xs text-mist-500">{t('downloads.automation.never')}</p>
      {speichern.error && <ErrorBanner message={speichern.error.message} />}
      <div className="flex items-center gap-3">
        <Button
          onClick={() => speichern.mutate(aktuell)}
          disabled={entwurf === null}
          loading={speichern.isPending}
        >
          {t('downloads.automation.save')}
        </Button>
        {gespeichert && (
          <span role="status" className="text-sm text-ok-500">
            {t('downloads.automation.saved')}
          </span>
        )}
      </div>
    </Section>
  )
}

function VerlaufBereich() {
  const { t, i18n } = useTranslation()
  const query = useQuery({
    queryKey: VERLAUF,
    queryFn: () => api.get<DownloadVerlaufZeile[]>('/api/admin/downloads/verlauf?limit=30'),
  })
  const zeilen = query.data

  function ergebnis(code: string): string {
    const schluessel = `downloads.history.ergebnis.${code}`
    return i18n.exists(schluessel) ? t(schluessel) : code
  }

  return (
    <Section title={t('downloads.history.title')} breit>
      {query.error && <ErrorBanner message={query.error.message} />}
      {zeilen && zeilen.length === 0 && (
        <p className="text-sm text-mist-500">{t('downloads.history.none')}</p>
      )}
      {zeilen && zeilen.length > 0 && (
        <ul className="flex flex-col divide-y divide-ink-800">
          {zeilen.map((zeile) => {
            // Erkannt und gemeldet hat niemand "getan" - dort gibt es keinen Handelnden.
            const handelnd =
              zeile.was === 'erkannt' || zeile.was === 'gemeldet'
                ? null
                : zeile.automatisch
                  ? t('downloads.history.automatic')
                  : zeile.wer
            return (
              <li
                key={zeile.id}
                className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2 text-sm"
              >
                <span className="min-w-0 [overflow-wrap:anywhere] text-mist-200">
                  <span className="font-medium">{zeile.titel || zeile.release}</span>
                  <span className="text-mist-500"> · {t(`downloads.history.was.${zeile.was}`)}</span>
                  {zeile.grund && (
                    <span className="text-mist-600"> · {t(`downloads.grund.${zeile.grund}.titel`, { defaultValue: zeile.grund })}</span>
                  )}
                  {zeile.ergebnis && (
                    <span className="text-bad-500">
                      {' '}
                      · {t('downloads.history.failed', { grund: ergebnis(zeile.ergebnis) })}
                    </span>
                  )}
                </span>
                <span className="text-xs text-mist-600 tabular-nums">
                  {[formatDateTime(zeile.am, i18n.language), zeile.instanz, handelnd]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </Section>
  )
}
