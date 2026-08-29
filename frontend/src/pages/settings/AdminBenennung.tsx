/**
 * Das empfohlene Benennungsschema übernehmen.
 *
 * ⚠️ **Warum Datei und Ordner getrennt angeboten werden.** Ihre Folgen sind
 * verschieden: Ein neuer *Dateiname* ist harmlos — der Medienserver erkennt
 * denselben Titel weiter. Ein neuer *Ordnername* dagegen lässt Plex, Emby oder
 * Jellyfin den Eintrag leicht als etwas Neues ansehen, und dann ist der
 * Gesehen-Status weg. Beides in einen Schalter zu packen hieße, die schwerere
 * Entscheidung hinter der leichteren zu verstecken.
 *
 * ⚠️ **Nichts wird umbenannt, was schon da ist.** Das Schema gilt nur für das,
 * was Radarr oder Sonarr ab jetzt selbst schreibt. Der Satz steht so auch in
 * der Oberfläche — er ist der häufigste Irrtum bei diesem Thema.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type {
  AltnamenAufgeraeumt,
  BenennungStand,
  UmbenennenFortschritt,
} from '../../api/types'
import { Button, Section, Spinner } from '../../components/ui'

export function AdminBenennung() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [fehler, setFehler] = useState<string | null>(null)
  /** Welche Instanz gerade aufgeklappt ist. */
  const [offen, setOffen] = useState<string | null>(null)
  const [wahl, setWahl] = useState<{ datei: boolean; ordner: boolean; bestand: boolean }>(
    { datei: true, ordner: false, bestand: false },
  )
  /** Für welche Instanz gerade ein Bestandslauf beobachtet wird. */
  const [laufend, setLaufend] = useState<string | null>(null)

  /**
   * Der Fortschritt des Bestandslaufs.
   *
   * ⚠️ Der Lauf hängt **nicht** an der Anfrage, die ihn angestoßen hat: Bei
   * mehreren tausend Titeln dauert er Minuten, und eine so lange offene
   * Verbindung stirbt unterwegs. Deshalb wird nachgefragt statt gewartet.
   */
  const fortschritt = useQuery({
    queryKey: ['benennung-fortschritt', laufend],
    queryFn: () =>
      api.get<UmbenennenFortschritt>(
        `/api/settings/qualitaetsprofile/benennung/${laufend}/fortschritt`,
      ),
    enabled: laufend !== null,
    refetchInterval: 1500,
    gcTime: 0,
  })

  const stand = useQuery({
    queryKey: ['benennung'],
    queryFn: () => api.get<BenennungStand[]>('/api/settings/qualitaetsprofile/benennung'),
  })

  /**
   * Einen laufenden Bestandslauf auch dann finden, wenn diese Seite ihn nicht
   * angestoßen hat.
   *
   * ⚠️ **Ohne das ist die Absicherung im Hintergrund wertlos.** Wer die Seite
   * neu lädt oder von einem anderen Gerät hereinschaut, sah bisher gar nichts —
   * während im Hintergrund tausende Dateien umbenannt wurden. Der Server weiß
   * es, also soll die Oberfläche danach fragen, statt sich nur auf die eigene
   * Erinnerung zu verlassen.
   */
  useEffect(() => {
    if (laufend !== null) return
    const offen = (stand.data ?? []).find((i) => i.lauf_offen)
    if (offen) setLaufend(offen.kennung)
  }, [stand.data, laufend])

  const setzenMut = useMutation({
    mutationFn: (payload: {
      kennung: string
      datei: boolean
      ordner: boolean
      bestand: boolean
    }) => api.put<BenennungStand>('/api/settings/qualitaetsprofile/benennung', payload),
    onSuccess: (_daten, payload) => {
      setFehler(null)
      setOffen(null)
      // Nur wenn der Bestand mit dranhängt, gibt es einen Lauf zu beobachten.
      setLaufend(payload.bestand ? payload.kennung : null)
      void queryClient.invalidateQueries({ queryKey: ['benennung'] })
    },
    onError: (ausnahme) =>
      setFehler(ausnahme instanceof ApiError ? ausnahme.message : String(ausnahme)),
  })

  /**
   * Die alten Musternamen zurückdrehen.
   *
   * ⚠️ Bewusst ein eigener Knopf statt einer stillen Vorarbeit im Lauf: Es
   * ändert Namen in Radarr, die auch in fremden Profilmasken auftauchen. Wer
   * das anstößt, soll es gewollt haben.
   */
  const aufraeumenMut = useMutation({
    mutationFn: (kennung: string) =>
      api.post<AltnamenAufgeraeumt>(
        `/api/settings/qualitaetsprofile/benennung/${kennung}/altnamen`,
        {},
      ),
    onSuccess: () => {
      setFehler(null)
      void queryClient.invalidateQueries({ queryKey: ['benennung'] })
      void queryClient.invalidateQueries({ queryKey: ['qualitaetsprofile-abgleich'] })
    },
    onError: (ausnahme) =>
      setFehler(ausnahme instanceof ApiError ? ausnahme.message : String(ausnahme)),
  })

  if (stand.isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner className="h-5 w-5" />
      </div>
    )
  }

  return (
    <Section title={t('naming.title')} breit>
      <p className="max-w-3xl text-sm text-mist-600">{t('naming.intro')}</p>
      {fehler && (
        <p className="rounded-xl border border-bad-500/40 bg-bad-500/10 px-4 py-3 text-sm text-bad-500">
          {fehler}
        </p>
      )}

      <div className="flex flex-col gap-3">
        {(stand.data ?? []).map((i) => {
          const dateiGleich = i.datei_ist === i.datei_soll
          const ordnerGleich = i.ordner_ist === i.ordner_soll
          const allesGleich = dateiGleich && ordnerGleich
          const auf = offen === i.kennung
          const altnamen = i.altnamen ?? {
            gesamt: 0,
            im_dateinamen: 0,
            blockiert: 0,
            beispiele: [],
          }
          // Solange alte Musternamen in Dateinamen fließen, ist ein
          // Bestandslauf keine Aufräumarbeit, sondern eine Verschlimmerung.
          const bestandGesperrt = wahl.bestand && altnamen.im_dateinamen > 0
          return (
            <div
              key={i.kennung}
              className="rounded-xl border border-ink-700 bg-ink-900/50 px-4 py-3.5"
            >
              {laufend === i.kennung && fortschritt.data?.laeuft && (
                <Bestandslauf stand={fortschritt.data} />
              )}
              {laufend === i.kennung &&
                fortschritt.data &&
                !fortschritt.data.laeuft &&
                fortschritt.data.schritt === 'fertig' && (
                  <p className="mb-3 rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
                    {t('naming.renameDone', { anzahl: fortschritt.data.betroffen })}
                  </p>
                )}

              <div className="flex flex-wrap items-center gap-3">
                <span className="flex-1 font-medium text-mist-100">{i.name}</span>
                {!i.erreichbar ? (
                  <span className="text-xs text-mist-600">{t('naming.unreachable')}</span>
                ) : allesGleich ? (
                  <span className="rounded-full border border-ok-500/50 bg-ok-500/10 px-2.5 py-0.5 text-xs text-ok-500">
                    {t('naming.matches')}
                  </span>
                ) : (
                  <>
                    {/* ⚠️ **Sagen, WAS abweicht.** Dateiname und Ordnername
                        haben verschieden schwere Folgen: Ein neuer Dateiname
                        ist harmlos, ein neuer Ordnername kann den
                        Gesehen-Status im Medienserver kosten. Eine Marke, die
                        beides zu „Weicht ab“ verrührt, nimmt genau die
                        Information weg, die für die Entscheidung zählt. */}
                    <span className="rounded-full border border-warn-500/50 bg-warn-500/10 px-2.5 py-0.5 text-xs text-warn-500">
                      {!dateiGleich && !ordnerGleich
                        ? t('naming.differsBoth')
                        : !dateiGleich
                          ? t('naming.differsFile')
                          : t('naming.differsFolder')}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() => {
                        setOffen(auf ? null : i.kennung)
                        setWahl({ datei: !dateiGleich, ordner: false, bestand: false })
                      }}
                    >
                      {auf ? t('naming.hide') : t('naming.compare')}
                    </Button>
                  </>
                )}
              </div>

              {auf && i.erreichbar && (
                <div className="mt-4 flex flex-col gap-4 border-t border-ink-800 pt-4">
                  <Vergleich
                    titel={t('naming.file')}
                    ist={i.datei_ist}
                    soll={i.datei_soll}
                    gleich={dateiGleich}
                    gewaehlt={wahl.datei}
                    onWahl={(v) => setWahl((a) => ({ ...a, datei: v }))}
                    folge={t('naming.fileConsequence')}
                  />
                  <Vergleich
                    titel={t('naming.folder')}
                    ist={i.ordner_ist}
                    soll={i.ordner_soll}
                    gleich={ordnerGleich}
                    gewaehlt={wahl.ordner}
                    onWahl={(v) => setWahl((a) => ({ ...a, ordner: v }))}
                    folge={t('naming.folderConsequence')}
                    reichweite={t('naming.folderScope')}
                    schwer
                  />

                  {/* Der häufigste Irrtum, deshalb steht er direkt über dem Knopf. */}
                  <p className="rounded-r-xl border-l-2 border-accent-500/60 bg-ink-900/70 px-4 py-3 text-xs leading-relaxed text-mist-400">
                    {t('naming.existingFilesHint')}
                  </p>

                  {/* ⚠️ Der einzige Haken, der Dateien auf der Platte anfasst -
                      deshalb steht er unten, für sich, und mit allem, was
                      danach passiert. */}
                  <div className="rounded-xl border border-bad-500/40 bg-bad-500/5 px-4 py-3">
                    <label className="flex cursor-pointer items-start gap-3">
                      <input
                        type="checkbox"
                        checked={wahl.bestand}
                        onChange={(e) =>
                          setWahl((a) => ({ ...a, bestand: e.target.checked }))
                        }
                        className="mt-0.5 h-4 w-4 shrink-0 accent-bad-500"
                      />
                      <span>
                        <span className="text-sm font-medium text-mist-100">
                          {t('naming.alsoExisting')}
                        </span>
                        <span className="mt-1 block text-xs leading-relaxed text-mist-500">
                          {t('naming.alsoExistingHint')}
                        </span>
                      </span>
                    </label>

                    {wahl.bestand && (
                      <div className="mt-3 flex flex-col gap-2">
                        {/* ⚠️ **Muss vor allem anderen stehen.** Tragen die
                            Erkennungsmuster noch den alten Vorsatz, schreibt
                            der Lauf ihn in jeden Dateinamen — über die ganze
                            Bibliothek. Das ist kein Schönheitsfehler, sondern
                            ein zweiter kompletter Lauf, um ihn loszuwerden. */}
                        {altnamen.im_dateinamen > 0 && (
                          <div className="rounded-xl border border-bad-500/50 bg-bad-500/15 px-3 py-2.5">
                            <p className="text-xs font-medium leading-relaxed text-bad-500">
                              {t('naming.oldPrefixWarn', {
                                anzahl: altnamen.im_dateinamen,
                                beispiel: altnamen.beispiele[0] ?? 'NXV - German DL',
                              })}
                            </p>
                            <div className="mt-2 flex flex-wrap items-center gap-2">
                              {/* ⚠️ **Keinen Knopf anbieten, der nichts bewirkt.**
                                  Sind alle Muster blockiert, räumt er null davon
                                  auf — und der Lauf bliebe für immer gesperrt,
                                  ohne dass irgendwo stünde, wie man hier
                                  herauskommt. Dann lieber sagen, was zu tun
                                  ist. */}
                              {altnamen.blockiert < altnamen.im_dateinamen && (
                                <Button
                                  type="button"
                                  variant="ghost"
                                  loading={
                                    aufraeumenMut.isPending &&
                                    aufraeumenMut.variables === i.kennung
                                  }
                                  onClick={() => aufraeumenMut.mutate(i.kennung)}
                                >
                                  {t('naming.cleanPrefix')}
                                </Button>
                              )}
                              {altnamen.blockiert > 0 && (
                                <span className="text-xs text-mist-500">
                                  {t(
                                    altnamen.blockiert >= altnamen.im_dateinamen
                                      ? 'naming.prefixAllBlocked'
                                      : 'naming.prefixBlocked',
                                    {
                                      anzahl: altnamen.blockiert,
                                      namen: (altnamen.blockierte_namen ?? [])
                                        .slice(0, 3)
                                        .map((n) => n.replace(/^NXV - /, ''))
                                        .join(', '),
                                      dienst:
                                        i.dienst === 'sonarr' ? 'Sonarr' : 'Radarr',
                                    },
                                  )}
                                </span>
                              )}
                            </div>
                          </div>
                        )}
                        <p className="rounded-xl border border-bad-500/40 bg-bad-500/10 px-3 py-2.5 text-xs leading-relaxed text-bad-500">
                          {t('naming.renameConsequence')}
                        </p>
                        {/* Ohne diese Verbindung merkt der Medienserver vom
                            Umbenennen erst beim nächsten eigenen Durchlauf etwas. */}
                        {!i.meldet_medienserver && (
                          <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-3 py-2.5 text-xs leading-relaxed text-warn-500">
                            {t('naming.noMediaServerLink')}
                          </p>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="flex justify-end">
                    <Button
                      type="button"
                      disabled={
                        (!wahl.datei && !wahl.ordner && !wahl.bestand) ||
                        bestandGesperrt
                      }
                      loading={
                        setzenMut.isPending && setzenMut.variables?.kennung === i.kennung
                      }
                      onClick={() =>
                        setzenMut.mutate({
                          kennung: i.kennung,
                          datei: wahl.datei,
                          ordner: wahl.ordner,
                          bestand: wahl.bestand,
                        })
                      }
                    >
                      {wahl.bestand ? t('naming.applyAndRename') : t('naming.apply')}
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </Section>
  )
}

/**
 * Wie weit das Angleichen des Bestands ist.
 *
 * ⚠️ **Zwei Abschnitte, kein einzelner Balken.** Erst wird jeder Titel gefragt,
 * was sich ändern würde — das ist reines Lesen und jederzeit gefahrlos. Erst
 * danach werden Dateien angefasst. Ein gemeinsamer Balken verwischte genau den
 * Punkt, an dem es ernst wird.
 */
function Bestandslauf({ stand }: { stand: UmbenennenFortschritt }) {
  const { t } = useTranslation()
  const anteil = stand.gesamt > 0 ? Math.round((stand.erledigt / stand.gesamt) * 100) : 0
  const pruefen = stand.schritt === 'pruefen'
  return (
    <div className="mb-4 flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900 px-4 py-3">
      {/* Ein Lauf, der nach einem Neustart von selbst weiterläuft, wirkt ohne
          diesen Hinweis wie ein Fehler statt wie die Rettung, die er ist. */}
      {stand.fortgesetzt && (
        <p className="rounded-lg border border-accent-500/40 bg-accent-500/10 px-3 py-2 text-xs text-accent-500">
          {t('naming.renameResumed')}
        </p>
      )}
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-mist-100">
          {pruefen ? t('naming.phaseChecking') : t('naming.phaseRenaming')}
        </span>
        <span className="font-mono text-xs tabular-nums text-mist-500">
          {stand.erledigt} / {stand.gesamt}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-ink-800">
        <div
          className={
            'h-full rounded-full transition-[width] duration-500 ' +
            (pruefen ? 'bg-accent-500' : 'bg-warn-500')
          }
          style={{ width: `${anteil}%` }}
        />
      </div>
      <p className="text-xs text-mist-600">
        {pruefen
          ? t('naming.phaseCheckingHint', { anzahl: stand.betroffen })
          : t('naming.phaseRenamingHint')}
      </p>
    </div>
  )
}

/**
 * Eine Gegenüberstellung: was gilt, was empfohlen wird.
 *
 * ``schwer`` markiert die Wahl, deren Folgen weiter reichen — sie bekommt einen
 * anderen Ton, damit sie sich nicht wie die harmlose danebenstehende liest.
 */
function Vergleich({
  titel,
  ist,
  soll,
  gleich,
  gewaehlt,
  onWahl,
  folge,
  reichweite,
  schwer = false,
}: {
  titel: string
  ist: string
  soll: string
  gleich: boolean
  gewaehlt: boolean
  onWahl: (wert: boolean) => void
  folge: string
  schwer?: boolean
  reichweite?: string
}) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-2">
      <label className="flex cursor-pointer items-center gap-3">
        <input
          type="checkbox"
          checked={gewaehlt}
          disabled={gleich}
          onChange={(e) => onWahl(e.target.checked)}
          className="h-4 w-4 shrink-0 accent-accent-500 disabled:opacity-40"
        />
        <span className="text-sm font-medium text-mist-200">{titel}</span>
        {/* ⚠️ **Die Reichweite gehört an den Schalter, nicht darunter.**
            Wer den Ordner-Haken setzt, erwartet leicht, dass vorhandene Ordner
            mitwandern. Das kann Radarr auf diesem Weg gar nicht — und wer es
            erst im Fließtext darunter liest, hat schon geklickt. */}
        {reichweite && (
          <span className="text-xs text-mist-500">{reichweite}</span>
        )}
        {gleich && <span className="text-xs text-ok-500">{t('naming.alreadySet')}</span>}
      </label>

      <div className="overflow-x-auto rounded-xl border border-ink-700 bg-ink-900">
        <table className="w-full min-w-[30rem] border-collapse text-xs">
          <tbody>
            <tr className="border-b border-ink-800">
              <td className="w-28 px-3 py-2 text-mist-600">{t('naming.current')}</td>
              <td className="px-3 py-2 font-mono break-all text-mist-400">
                {ist || '—'}
              </td>
            </tr>
            <tr>
              <td className="px-3 py-2 text-mist-600">{t('naming.recommended')}</td>
              <td className="px-3 py-2 font-mono break-all text-ok-500">{soll || '—'}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p
        className={
          'rounded-xl border px-4 py-2.5 text-xs leading-relaxed ' +
          (schwer
            ? 'border-warn-500/40 bg-warn-500/10 text-warn-500'
            : 'border-ink-700 bg-ink-900/60 text-mist-500')
        }
      >
        {folge}
      </p>
    </div>
  )
}
