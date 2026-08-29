/**
 * Was auf den Instanzen liegt — und was davon weg kann.
 *
 * ⚠️ **Warum hier auch Fremdes steht.** Überall sonst zeigt Nexview nur, was
 * es selbst angelegt hat. Das ist ehrlich, aber unbrauchbar, sobald es reibt:
 * Ein Muster, das den Namen belegt, den Nexview braucht, war unsichtbar; ein
 * Profil, das ein Löschen blockiert, ebenso. Der Betreiber sah einen Fehler
 * und musste in Radarr selbst suchen.
 *
 * ⚠️ **Die eigentliche Leistung ist die Auskunft.** Radarr lehnt das Löschen
 * eines benutzten Profils ab, ohne zu sagen *wer* es benutzt — die Ursache war
 * einmal eine Sammlung, an die niemand gedacht hatte. Hier stehen alle drei
 * Quellen: Medien, Importlisten, Sammlungen.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type {
  AufraeumErgebnis,
  InstanzBestand,
  UmhaengErgebnis,
} from '../../api/types'
import { Button, Section, Spinner } from '../../components/ui'

export function AdminArrBestand() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [fehler, setFehler] = useState<string | null>(null)
  const [meldung, setMeldung] = useState<string | null>(null)
  /** Welche Instanz aufgeklappt ist — alle gleichzeitig wäre eine Bleiwüste. */
  const [offen, setOffen] = useState<string | null>(null)
  /** Ausgewählt zum Löschen, je Instanz getrennt. */
  const [wahlProfile, setWahlProfile] = useState<Set<number>>(new Set())
  const [wahlMuster, setWahlMuster] = useState<Set<number>>(new Set())

  const bestand = useQuery({
    queryKey: ['arr-bestand'],
    queryFn: () => api.get<InstanzBestand[]>('/api/settings/qualitaetsprofile/bestand'),
  })

  /**
   * Alles neu holen, was sich durch einen Eingriff geaendert haben kann.
   *
   * ⚠️ **``refetchType: 'all'`` statt der Voreinstellung.** Standardmaessig
   * laedt React Query nur *aktive* Abfragen nach. Die Bestandsliste ist zwar
   * aktiv, die Profilablage und die Benennungsseite aber nicht — sie stehen
   * auf einer anderen Station des Seitenmenüs. Ohne dies zeigte ein Wechsel
   * dorthin den Stand von vorher, und der Betreiber hätte gute Gründe, an der
   * Anzeige zu zweifeln.
   *
   * Bewusst ``await``: Erst wenn die Zahlen wirklich da sind, ist der Vorgang
   * abgeschlossen — sonst blinkt kurz der alte Stand auf.
   */
  const allesNeuLaden = async () => {
    await Promise.all(
      [['arr-bestand'], ['benennung'], ['qualitaetsprofile'], ['qualitaetsprofile-abgleich']].map(
        (schluessel) =>
          queryClient.invalidateQueries({ queryKey: schluessel, refetchType: 'all' }),
      ),
    )
  }

  const leeren = () => {
    setWahlProfile(new Set())
    setWahlMuster(new Set())
  }

  /**
   * Wie weit das Aufräumen ist — `null` heißt: läuft gerade nicht.
   *
   * ⚠️ **Warum in Häppchen und nicht in einem Aufruf.** Gemessen dauert ein
   * Löschvorgang rund 0,4 s; 131 Muster wären also gut eine Minute in einer
   * offenen Anfrage. Ein Reverse Proxy bricht die standardmäßig nach 60
   * Sekunden ab — und dann weiß niemand, wie viel schon weg war. In Häppchen
   * bleibt jede Anfrage kurz, und der Balken sagt die Wahrheit.
   */
  const [lauf, setLauf] = useState<{ erledigt: number; gesamt: number } | null>(null)

  const HAEPPCHEN = 20

  /**
   * Welches Profil gerade umgehängt wird — `null` heißt: keins.
   *
   * ⚠️ **Ohne diesen Weg endet das Aufräumen, bevor es anfängt.** Ein Profil
   * lässt sich nicht löschen, solange Medien darauf liegen — und in einer
   * gewachsenen Anlage liegt fast alles auf Profilen, die es vor Nexview schon
   * gab. Wer nur löschen kann, was ohnehin leer ist, räumt nichts auf.
   */
  const [umhaengen, setUmhaengen] = useState<{
    kennung: string
    von: number
    name: string
    medien: number
  } | null>(null)
  const [ziel, setZiel] = useState<number | null>(null)

  const umhaengenMut = useMutation({
    mutationFn: (p: { kennung: string; von: number; nach: number }) =>
      api.post<UmhaengErgebnis>(
        `/api/settings/qualitaetsprofile/bestand/${p.kennung}/umhaengen`,
        { von: p.von, nach: p.nach },
      ),
    onSuccess: (daten) => {
      setFehler(null)
      setMeldung(t('inventory.moved', { anzahl: daten.umgehaengt }))
      setUmhaengen(null)
      setZiel(null)
      void allesNeuLaden()
    },
    onError: (a) => setFehler(a instanceof ApiError ? a.message : String(a)),
  })

  const aufraeumen = useMutation({
    mutationFn: async (kennung: string) => {
      const pfad = `/api/settings/qualitaetsprofile/bestand/${kennung}/aufraeumen`
      const profile = [...wahlProfile]
      const muster = [...wahlMuster]
      const gesamt = profile.length + muster.length
      setLauf({ erledigt: 0, gesamt })

      const gesammelt: AufraeumErgebnis = {
        geloescht_profile: [],
        geloescht_muster: [],
        abgelehnt: {},
      }
      const uebernehmen = (teil: AufraeumErgebnis) => {
        gesammelt.geloescht_profile.push(...teil.geloescht_profile)
        gesammelt.geloescht_muster.push(...teil.geloescht_muster)
        Object.assign(gesammelt.abgelehnt, teil.abgelehnt)
      }

      // ⚠️ **Profile zuerst, alle auf einmal.** Ein Muster gilt als benutzt,
      // solange ein Profil ihm Punkte gibt — erst wenn die Profile weg sind,
      // sind die Muster frei, die nur an ihnen hingen.
      if (profile.length) {
        uebernehmen(
          await api.post<AufraeumErgebnis>(pfad, {
            profil_ids: profile,
            muster_ids: [],
          }),
        )
        setLauf({ erledigt: profile.length, gesamt })
      }
      for (let start = 0; start < muster.length; start += HAEPPCHEN) {
        const teil = muster.slice(start, start + HAEPPCHEN)
        uebernehmen(
          await api.post<AufraeumErgebnis>(pfad, {
            profil_ids: [],
            muster_ids: teil,
          }),
        )
        setLauf({ erledigt: profile.length + start + teil.length, gesamt })
      }

      // ⚠️ **Nachsehen, was jetzt frei geworden ist.**
      //
      // Ein Muster gilt als benutzt, solange ein Profil ihm Punkte gibt. Wird
      // ein Profil gelöscht, werden **seine** Muster frei — aber erst danach,
      // also nach der Auswahl. Sie standen nicht auf der Liste und bleiben
      // liegen. Das ist richtig (nur Ausgewähltes wird gelöscht), sieht aber
      // wie ein Fehler aus, wenn es niemand sagt: „Ich habe doch alles
      // angehakt, und es bleibt immer etwas übrig."
      const frisch = await api.get<InstanzBestand[]>(
        '/api/settings/qualitaetsprofile/bestand',
      )
      const danach = frisch.find((i) => i.kennung === kennung)
      const nochFrei = danach
        ? danach.profile.filter((p) => p.loeschbar).length +
          danach.muster.filter((m) => m.loeschbar).length
        : 0
      return { ...gesammelt, nochFrei }
    },
    onSettled: () => setLauf(null),
    onSuccess: (daten) => {
      setFehler(null)
      const weg = daten.geloescht_profile.length + daten.geloescht_muster.length
      const abgelehnt = Object.entries(daten.abgelehnt)
      const grund = abgelehnt.length
        ? t('inventory.partly', {
            anzahl: weg,
            gruende: abgelehnt.map(([n, g]) => `${n} (${g})`).join(' · '),
          })
        : t('inventory.cleaned', { anzahl: weg })
      // Der Nachsatz erklärt, warum trotz „alles ausgewählt" etwas übrig ist —
      // ohne ihn wirkt es wie ein Fehler.
      setMeldung(
        daten.nochFrei > 0
          ? `${grund} ${t('inventory.freedUp', { anzahl: daten.nochFrei })}`
          : grund,
      )
      leeren()
      void allesNeuLaden()
    },
    onError: (a) => setFehler(a instanceof ApiError ? a.message : String(a)),
  })

  if (bestand.isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner className="h-5 w-5" />
      </div>
    )
  }

  const umschalten = (
    menge: Set<number>,
    setzen: (m: Set<number>) => void,
    id: number,
  ) => {
    const neu = new Set(menge)
    if (neu.has(id)) neu.delete(id)
    else neu.add(id)
    setzen(neu)
  }

  return (
    <Section title={t('inventory.title')} breit>
      <p className="max-w-3xl text-sm text-mist-600">{t('inventory.intro')}</p>
      {fehler && (
        <p className="rounded-xl border border-bad-500/40 bg-bad-500/10 px-4 py-3 text-sm text-bad-500">
          {fehler}
        </p>
      )}
      {meldung && (
        <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {meldung}
        </p>
      )}

      {/* ⚠️ Die Folge steht **vor** dem Klick: Das neue Profil bewertet anders,
          und Titel darunter merkt die Instanz zur Aufwertung vor. Wer das erst
          hinterher erfährt, hat die Downloads schon laufen. */}
      {umhaengen && (
        <div className="flex flex-col gap-3 rounded-xl border border-warn-500/50 bg-warn-500/10 px-4 py-3.5">
          <p className="text-sm font-medium text-warn-500">
            {t('inventory.moveTitle', {
              name: umhaengen.name,
              anzahl: umhaengen.medien,
            })}
          </p>
          <p className="text-xs leading-relaxed text-mist-400">
            {t('inventory.moveConsequence')}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={ziel ?? ''}
              onChange={(e) => setZiel(e.target.value ? Number(e.target.value) : null)}
              className="min-w-56 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
            >
              <option value="">{t('inventory.moveChoose')}</option>
              {(bestand.data ?? [])
                .find((i) => i.kennung === umhaengen.kennung)
                ?.profile.filter((p) => p.id !== umhaengen.von)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.unser ? ` — ${t('inventory.ours')}` : ''}
                  </option>
                ))}
            </select>
            <Button
              type="button"
              disabled={ziel === null}
              loading={umhaengenMut.isPending}
              onClick={() =>
                ziel !== null &&
                umhaengenMut.mutate({
                  kennung: umhaengen.kennung,
                  von: umhaengen.von,
                  nach: ziel,
                })
              }
            >
              {t('inventory.moveDo', { anzahl: umhaengen.medien })}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setUmhaengen(null)
                setZiel(null)
              }}
            >
              {t('common.cancel')}
            </Button>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-3">
        {(bestand.data ?? []).map((i) => {
          const auf = offen === i.kennung
          const freieProfile = i.profile.filter((p) => p.loeschbar)
          const freieMuster = i.muster.filter((m) => m.loeschbar)
          const gewaehlt = wahlProfile.size + wahlMuster.size

          return (
            <div
              key={i.kennung}
              className="rounded-xl border border-ink-700 bg-ink-900/50 px-4 py-3.5"
            >
              <div className="flex flex-wrap items-center gap-3">
                <span className="flex-1 font-medium text-mist-100">{i.name}</span>
                {!i.erreichbar ? (
                  <span className="text-xs text-mist-600">
                    {t('inventory.unreachable')}
                  </span>
                ) : (
                  <>
                    <span className="text-xs text-mist-500">
                      {t('inventory.summary', {
                        profile: i.profile.length,
                        muster: i.muster.length,
                      })}
                    </span>
                    {freieProfile.length + freieMuster.length > 0 && (
                      <span className="rounded-full border border-warn-500/50 bg-warn-500/10 px-2.5 py-0.5 text-xs text-warn-500">
                        {t('inventory.unused', {
                          anzahl: freieProfile.length + freieMuster.length,
                        })}
                      </span>
                    )}
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={lauf !== null}
                      onClick={() => {
                        setOffen(auf ? null : i.kennung)
                        leeren()
                      }}
                    >
                      {auf ? t('inventory.hide') : t('inventory.show')}
                    </Button>
                  </>
                )}
              </div>

              {auf && i.erreichbar && (
                <div className="mt-4 flex flex-col gap-5 border-t border-ink-800 pt-4">
                  <Liste
                    titel={t('inventory.profiles')}
                    eintraege={i.profile.map((p) => ({
                      id: p.id,
                      name: p.name,
                      loeschbar: p.loeschbar,
                      // ⚠️ Der Grund gehört an die Zeile: „is in use“ allein
                      // schickt den Betreiber auf die Suche.
                      hinweis: p.loeschbar
                        ? ''
                        : t('inventory.boundBy', {
                            medien: p.medien,
                            listen: p.importlisten,
                            sammlungen: p.sammlungen,
                          }),
                      marke: p.unser ? t('inventory.ours') : t('inventory.foreign'),
                      eigen: p.unser,
                      // Gebundene Profile bekommen einen Ausweg: Erst die
                      // Medien umhängen, dann ist das Profil frei.
                      aktion:
                        !p.loeschbar && p.medien > 0
                          ? {
                              text: t('inventory.moveMedia'),
                              tun: () => {
                                setUmhaengen({
                                  kennung: i.kennung,
                                  von: p.id,
                                  name: p.name,
                                  medien: p.medien,
                                })
                                setZiel(null)
                              },
                            }
                          : undefined,
                    }))}
                    gewaehlt={wahlProfile}
                    gesperrt={lauf !== null}
                    onWahl={(id) => umschalten(wahlProfile, setWahlProfile, id)}
                  />
                  <Liste
                    titel={t('inventory.formats')}
                    eintraege={i.muster.map((m) => ({
                      id: m.id,
                      name: m.name,
                      loeschbar: m.loeschbar,
                      // ⚠️ Zwei verschiedene Gründe, gebunden zu sein — und der
                      // zweite ist der überraschende: Das Muster gehört zu
                      // einem Bauplan, trägt dort aber null Punkte.
                      hinweis: m.loeschbar
                        ? ''
                        : m.benutzt_von.length
                          ? t('inventory.scoredBy', {
                              profile: m.benutzt_von.slice(0, 3).join(', '),
                              weitere: Math.max(0, m.benutzt_von.length - 3),
                            })
                          : t('inventory.partOfPlan'),
                      marke: m.alter_vorsatz ? t('inventory.oldPrefix') : '',
                      eigen: m.alter_vorsatz,
                    }))}
                    gewaehlt={wahlMuster}
                    gesperrt={lauf !== null}
                    onWahl={(id) => umschalten(wahlMuster, setWahlMuster, id)}
                  />

                  {/* Waehrend des Laufs veraendert sich der Bestand unter der
                      Auswahl - deshalb ist sie so lange gesperrt. */}
                  {lauf && (
                    <div className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900 px-4 py-3">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="text-sm font-medium text-mist-100">
                          {t('inventory.running')}
                        </span>
                        <span className="font-mono text-xs tabular-nums text-mist-500">
                          {lauf.erledigt} / {lauf.gesamt}
                        </span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-ink-800">
                        <div
                          className="h-full rounded-full bg-bad-500 transition-[width] duration-300"
                          style={{
                            width: `${lauf.gesamt ? Math.round((lauf.erledigt / lauf.gesamt) * 100) : 0}%`,
                          }}
                        />
                      </div>
                    </div>
                  )}

                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="text-xs text-mist-600">{t('inventory.checkedFirst')}</p>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="ghost"
                        disabled={
                          lauf !== null ||
                          freieProfile.length + freieMuster.length === 0
                        }
                        onClick={() => {
                          setWahlProfile(new Set(freieProfile.map((p) => p.id)))
                          setWahlMuster(new Set(freieMuster.map((m) => m.id)))
                        }}
                      >
                        {t('inventory.selectUnused')}
                      </Button>
                      <Button
                        type="button"
                        disabled={gewaehlt === 0}
                        loading={aufraeumen.isPending}
                        onClick={() => aufraeumen.mutate(i.kennung)}
                      >
                        {t('inventory.delete', { anzahl: gewaehlt })}
                      </Button>
                    </div>
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

type Eintrag = {
  id: number
  name: string
  loeschbar: boolean
  hinweis: string
  marke: string
  eigen: boolean
  /** Ein Ausweg für gebundene Einträge — etwa „Medien umhängen". */
  aktion?: { text: string; tun: () => void }
}

/**
 * Eine Liste aus Profilen oder Mustern.
 *
 * ⚠️ Gebundene Einträge lassen sich nicht anhaken — der Grund steht daneben.
 * Ein Haken, der beim Absenden abgelehnt wird, wäre eine Einladung zum
 * Missverständnis.
 */
function Liste({
  titel,
  eintraege,
  gewaehlt,
  gesperrt = false,
  onWahl,
}: {
  titel: string
  eintraege: Eintrag[]
  gewaehlt: Set<number>
  gesperrt?: boolean
  onWahl: (id: number) => void
}) {
  const { t } = useTranslation()
  const frei = eintraege.filter((e) => e.loeschbar).length
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-medium text-mist-200">{titel}</span>
        <span className="text-xs text-mist-600">
          {t('inventory.ofWhichFree', { frei, gesamt: eintraege.length })}
        </span>
      </div>
      <div className="max-h-72 overflow-y-auto rounded-xl border border-ink-700 bg-ink-900">
        {eintraege.map((e) => (
          <label
            key={e.id}
            className={
              'flex items-center gap-3 border-b border-ink-800 px-3 py-2 last:border-b-0 ' +
              (e.loeschbar && !gesperrt
                ? 'cursor-pointer hover:bg-ink-800/60'
                : 'opacity-60')
            }
          >
            <input
              type="checkbox"
              disabled={!e.loeschbar || gesperrt}
              checked={gewaehlt.has(e.id)}
              onChange={() => onWahl(e.id)}
              className="h-4 w-4 shrink-0 accent-bad-500 disabled:opacity-30"
            />
            <span className="min-w-0 flex-1 truncate text-xs text-mist-200">
              {e.name}
            </span>
            {e.marke && (
              <span
                className={
                  'shrink-0 rounded-full border px-2 py-0.5 text-[0.65rem] ' +
                  (e.eigen
                    ? 'border-accent-500/50 bg-accent-500/10 text-accent-500'
                    : 'border-ink-600 bg-ink-800 text-mist-500')
                }
              >
                {e.marke}
              </span>
            )}
            {e.hinweis && (
              <span className="shrink-0 text-[0.65rem] text-mist-500">{e.hinweis}</span>
            )}
            {e.aktion && !gesperrt && (
              <button
                type="button"
                onClick={(ereignis) => {
                  // Das Label umschließt eine Auswahlbox — ohne das hier
                  // würde der Klick den Haken umschalten.
                  ereignis.preventDefault()
                  ereignis.stopPropagation()
                  e.aktion?.tun()
                }}
                className="shrink-0 rounded-lg border border-ink-600 bg-ink-800 px-2 py-1 text-[0.65rem] text-mist-300 hover:border-accent-500/60 hover:text-accent-500"
              >
                {e.aktion.text}
              </button>
            )}
          </label>
        ))}
      </div>
    </div>
  )
}
