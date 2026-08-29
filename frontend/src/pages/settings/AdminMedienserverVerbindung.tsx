/**
 * Die Rückverbindung: Radarr und Sonarr sagen dem Medienserver Bescheid.
 *
 * ⚠️ **Warum das hier steht und nicht bei den Medienservern.** Es ist keine
 * Einstellung *am* Medienserver, sondern eine *in Radarr und Sonarr* — je
 * Instanz eine, je Server eine. Bei drei Servern und vier Instanzen sind das
 * zwölf Einträge, jeder mit Adresse, Zugang und den richtigen Haken. Genau
 * diese Fleißarbeit nimmt Nexview ab.
 *
 * ⚠️ **Zwei verschiedene Zugänge, und die Verwechslung wäre teuer.** Bei Plex
 * genügt der Token, den Nexview ohnehin hat. Bei Jellyfin und Emby braucht
 * Radarr einen **API-Schlüssel** aus deren Dashboard — Nexviews eigener Zugang
 * entsteht dort aus Benutzer und Passwort und endet mit der Sitzung.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { MedienserverLuecke, VerbindungslageGesamt } from '../../api/types'
import { Button, Section, Spinner } from '../../components/ui'

/** Wie der Anbieter heißt — nicht, wie der Server sich selbst nennt. */
export const ANBIETER: Record<string, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  emby: 'Emby',
}

/**
 * Trägt dieser Servername etwas bei, oder ist es eine Maschinenkennung?
 *
 * ⚠️ Emby nennt sich hier schon mal „fed014e636a7“ — das ist die Kennung der
 * Installation, kein Name. Sie in der Oberfläche zu zeigen hilft niemandem;
 * „Emby“ sagt mehr. Plex dagegen liefert echte Namen wie „Bizzy“, und die
 * sind es wert, genannt zu werden.
 */
export function eigenname(name: string, provider: string): string {
  const sauber = (name || '').trim()
  if (!sauber) return ''
  if (sauber.toLowerCase() === provider.toLowerCase()) return ''
  if (/^[0-9a-f]{8,}$/i.test(sauber)) return ''
  return sauber
}

export function anzeigename(provider: string, name: string): string {
  const anbieter = ANBIETER[provider] ?? provider
  const eigen = eigenname(name, provider)
  return eigen ? `${anbieter} (${eigen})` : anbieter
}

/**
 * Die Pfad-Vorschau zu einer Lücke.
 *
 * ⚠️ **Warum das sichtbar sein muss.** Radarr nennt dem Medienserver einen
 * Pfad aus seiner eigenen Sicht. Stimmt der nicht mit dem überein, den der
 * Medienserver kennt, kommt der Anruf an, wird bejaht — und nichts passiert.
 * Wer hier sieht, was umgeschrieben wird, kann den Irrtum erkennen, bevor er
 * jahrelang unbemerkt bleibt.
 */
function PfadVorschau({ luecke }: { luecke: MedienserverLuecke }) {
  const { t } = useTranslation()
  const z = luecke.zuordnung
  if (!z) return null

  if (z.hindernis) {
    return (
      <p className="text-xs text-warn-500">
        {t(`mediaLink.pathIssue.${z.hindernis}`, {
          defaultValue: t('mediaLink.pathIssue.unknown'),
          name: ANBIETER[luecke.provider] ?? luecke.provider,
        })}
      </p>
    )
  }
  if (!z.von && !z.nach) {
    return (
      <p className="text-xs text-mist-600">
        {t('mediaLink.pathSame', { pfad: z.beispiel_arr })}
      </p>
    )
  }
  return (
    <p className="flex flex-wrap items-center gap-1.5 text-xs text-mist-500">
      <span>{t('mediaLink.pathMapped')}</span>
      <code className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-mist-300">
        {z.von}
      </code>
      <span aria-hidden="true">→</span>
      <code className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-mist-300">
        {z.nach}
      </code>
      <span className="text-mist-600">
        {t('mediaLink.pathExample', {
          arr: z.beispiel_arr,
          server: z.beispiel_server,
        })}
      </span>
    </p>
  )
}

export function AdminMedienserverVerbindung() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [fehler, setFehler] = useState<string | null>(null)
  const [meldung, setMeldung] = useState<string | null>(null)
  /** Eingetippte Schlüssel, bis sie gespeichert sind. */
  const [entwurf, setEntwurf] = useState<Record<number, string>>({})
  /**
   * Welche Server gerade bearbeitet werden.
   *
   * ⚠️ **Ohne das gäbe es keinen Weg zurück.** Ein hinterlegter Schlüssel
   * zeigte vorher nur noch ein Abzeichen — wer sich vertippt oder den falschen
   * Wert eingefügt hatte, kam nicht mehr heran. Genau das ist passiert. Der
   * Schlüssel selbst wird nie zurückgeliefert, also beginnt das Feld leer:
   * Ändern heißt hier immer „neu eingeben“.
   */
  const [bearbeite, setBearbeite] = useState<Set<number>>(new Set())

  const umschalten = (id: number, an: boolean) =>
    setBearbeite((alt) => {
      const neu = new Set(alt)
      if (an) neu.add(id)
      else neu.delete(id)
      return neu
    })

  const lage = useQuery({
    queryKey: ['medienserver-lage'],
    queryFn: () =>
      api.get<VerbindungslageGesamt>('/api/settings/qualitaetsprofile/medienserver'),
  })

  const melden = (a: unknown) =>
    setFehler(a instanceof ApiError ? a.message : String(a))
  const frisch = () =>
    void queryClient.invalidateQueries({ queryKey: ['medienserver-lage'] })

  const schluesselMut = useMutation({
    mutationFn: (p: { server_id: number; schluessel: string }) =>
      api.put('/api/settings/qualitaetsprofile/medienserver/schluessel', p),
    onSuccess: (_d, p) => {
      setFehler(null)
      setEntwurf((a) => ({ ...a, [p.server_id]: '' }))
      umschalten(p.server_id, false)
      frisch()
    },
    onError: melden,
  })

  const verbindenMut = useMutation({
    mutationFn: (kennungen: string[]) =>
      api.post<{ hergestellt: number; gescheitert: string[] }>(
        '/api/settings/qualitaetsprofile/medienserver/verbinden',
        { kennungen },
      ),
    onSuccess: (daten) => {
      setFehler(null)
      setMeldung(
        daten.gescheitert.length
          ? t('mediaLink.partly', {
              anzahl: daten.hergestellt,
              gruende: daten.gescheitert.join(' · '),
            })
          : t('mediaLink.done', { anzahl: daten.hergestellt }),
      )
      frisch()
    },
    onError: melden,
  })

  if (lage.isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner className="h-5 w-5" />
      </div>
    )
  }

  const server = lage.data?.server ?? []
  const instanzen = lage.data?.instanzen ?? []
  const warnungen = lage.data?.warnungen ?? []
  const offen = instanzen.reduce((summe, i) => summe + i.fehlend.length, 0)
  // Verbinden lohnt nur, wo ein Zugang **und** eine geklärte Zuordnung
  // vorliegen — ohne beides entstünde ein Eintrag, der nichts bewirkt.
  const bereit = instanzen.some((i) =>
    i.fehlend.some((l) => l.selbst_moeglich && !l.zuordnung?.hindernis),
  )

  return (
    <Section title={t('mediaLink.title')} breit>
      <p className="max-w-3xl text-sm text-mist-600">{t('mediaLink.intro')}</p>

      {/* ⚠️ Ganz oben und nicht zu übersehen: Eine Verbindung, die
          stillschweigend aufgehört hat zu wirken, fällt sonst nie auf. */}
      {warnungen.length > 0 && (
        <div className="rounded-xl border border-warn-500/50 bg-warn-500/10 px-4 py-3">
          <p className="text-sm font-medium text-warn-500">
            {/* ⚠️ i18next bildet die Mehrzahl über ``count`` — ein anders
                benannter Wert lässt die Rohkennung stehen. */}
            {t('mediaLink.brokenTitle', { count: warnungen.length })}
          </p>
          <ul className="mt-1.5 flex flex-col gap-1">
            {warnungen.map((w) => (
              <li key={w.instanz + w.provider} className="text-xs text-mist-400">
                {t(`mediaLink.brokenReason.${w.grund}`, {
                  defaultValue: t('mediaLink.brokenReason.unknown'),
                  instanz: w.instanz,
                  name: ANBIETER[w.provider] ?? w.provider,
                })}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-mist-500">{t('mediaLink.brokenHint')}</p>
        </div>
      )}
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

      {/* Die Zugänge zuerst: Ohne sie lässt sich unten nichts einrichten. */}
      <div className="flex flex-col gap-2">
        {server.map((s) => (
          <div
            key={s.id}
            className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/50 px-4 py-3"
          >
            <div className="min-w-0 flex-1">
              <div className="font-medium text-mist-100">
                {anzeigename(s.provider, s.name)}
              </div>
              <div className="font-mono text-xs break-all text-mist-600">{s.url}</div>
            </div>
            {!s.braucht_schluessel ? (
              <span className="rounded-full border border-ok-500/50 bg-ok-500/10 px-2.5 py-0.5 text-xs text-ok-500">
                {t('mediaLink.tokenFromNexview')}
              </span>
            ) : s.schluessel_da && !bearbeite.has(s.id) ? (
              // Aktion links, Zustand rechts: Die Marke schließt die Zeile ab
              // und steht damit in einer Spalte mit den Marken darüber und
              // darunter. Ein Knopf dahinter bräche diese Linie.
              <div className="flex items-center gap-2">
                {/* Hinterlegt heißt nicht richtig: Es muss möglich bleiben,
                    einen falschen Schlüssel zu ersetzen. */}
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => umschalten(s.id, true)}
                >
                  {t('mediaLink.changeKey')}
                </Button>
                <span className="rounded-full border border-ok-500/50 bg-ok-500/10 px-2.5 py-0.5 text-xs text-ok-500">
                  {t('mediaLink.keyStored')}
                </span>
              </div>
            ) : (
              <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
                <input
                  type="password"
                  value={entwurf[s.id] ?? ''}
                  onChange={(e) =>
                    setEntwurf((a) => ({ ...a, [s.id]: e.target.value }))
                  }
                  placeholder={t('mediaLink.keyPlaceholder')}
                  className="min-w-56 flex-1 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
                  autoComplete="off"
                />
                <Button
                  type="button"
                  variant="ghost"
                  disabled={!(entwurf[s.id] ?? '').trim()}
                  loading={
                    schluesselMut.isPending &&
                    schluesselMut.variables?.server_id === s.id
                  }
                  onClick={() =>
                    schluesselMut.mutate({
                      server_id: s.id,
                      schluessel: entwurf[s.id] ?? '',
                    })
                  }
                >
                  {t('mediaLink.saveKey')}
                </Button>
                {s.schluessel_da && (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => {
                      setEntwurf((a) => ({ ...a, [s.id]: '' }))
                      umschalten(s.id, false)
                    }}
                  >
                    {t('common.cancel')}
                  </Button>
                )}
              </div>
            )}
          </div>
        ))}
        {server.some(
          (s) => s.braucht_schluessel && (!s.schluessel_da || bearbeite.has(s.id)),
        ) && (
          <p className="rounded-r-xl border-l-2 border-accent-500/60 bg-ink-900/70 px-4 py-3 text-xs leading-relaxed text-mist-400">
            {t('mediaLink.whereKey')}
          </p>
        )}
      </div>

      {/* Und darunter, was fehlt. */}
      <div className="flex flex-col gap-2">
        {instanzen.map((i) => (
          <div
            key={i.kennung}
            className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900/50 px-4 py-3"
          >
            <div className="flex flex-wrap items-center gap-3">
              <span className="flex-1 text-sm font-medium text-mist-100">{i.name}</span>
              {!i.erreichbar ? (
                <span className="text-xs text-mist-600">
                  {t('mediaLink.unreachable')}
                </span>
              ) : i.fehlend.length === 0 ? (
                <span className="rounded-full border border-ok-500/50 bg-ok-500/10 px-2.5 py-0.5 text-xs text-ok-500">
                  {t('mediaLink.allLinked')}
                </span>
              ) : (
                <span className="flex flex-wrap gap-1.5">
                  {/* Erst das Bestehende: Ohne diese Marken sieht eine halb
                      verbundene Instanz aus, als wäre nichts eingerichtet. */}
                  {(i.verbunden ?? []).map((p) => (
                    <span
                      key={'ok-' + p}
                      className="rounded-full border border-ok-500/50 bg-ok-500/10 px-2.5 py-0.5 text-xs text-ok-500"
                    >
                      {t('mediaLink.linked', { name: ANBIETER[p] ?? p })}
                    </span>
                  ))}
                  {i.fehlend.map((l) => (
                    <span
                      key={l.provider + l.url}
                      className={
                        'rounded-full border px-2.5 py-0.5 text-xs ' +
                        (l.selbst_moeglich && !l.zuordnung?.hindernis
                          ? 'border-warn-500/50 bg-warn-500/10 text-warn-500'
                          : 'border-ink-600 bg-ink-800 text-mist-500')
                      }
                      title={l.hindernis}
                    >
                      {/* Nur der Anbieter: In einer Reihe kurzer Marken zählt,
                          *welcher* Dienst fehlt, nicht wie er sich selbst nennt. */}
                      {t('mediaLink.missing', {
                        name: ANBIETER[l.provider] ?? l.provider,
                      })}
                    </span>
                  ))}
                </span>
              )}
            </div>
            {/* Die Vorschau: was beim Verbinden an den Pfaden geschieht. */}
            {i.erreichbar && i.fehlend.length > 0 && (
              <div className="flex flex-col gap-1 border-l-2 border-ink-700 pl-3">
                {i.fehlend.map((l) => (
                  <div key={'pfad-' + l.provider + l.url} className="flex flex-wrap items-baseline gap-2">
                    <span className="w-16 shrink-0 text-xs text-mist-600">
                      {ANBIETER[l.provider] ?? l.provider}
                    </span>
                    <PfadVorschau luecke={l} />
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {offen > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-mist-600">{t('mediaLink.testedFirst')}</p>
          <Button
            type="button"
            disabled={!bereit}
            loading={verbindenMut.isPending}
            onClick={() => verbindenMut.mutate([])}
          >
            {t('mediaLink.connectAll')}
          </Button>
        </div>
      )}
    </Section>
  )
}
