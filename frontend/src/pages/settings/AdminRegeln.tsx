/**
 * Regeln — sie entscheiden über Anfragen, bevor die Einstellung am Konto gilt.
 *
 * Die Liste wird **von oben nach unten** geprüft, die erste passende Regel
 * gewinnt. Das ist keine Umsetzungsfrage, sondern die Bedeutung: „eine weite
 * Regel plus eine Ausnahme" denkt man so, und Mailfilter sehen seit dreißig
 * Jahren deshalb so aus.
 *
 * ⚠️ **Was hier wirklich gerechnet wird, ist die Kollision.** Weil Bedingungen
 * ausschließlich mit UND verknüpft sind, ist jede Regel ein Kasten — Bewertung
 * von–bis, Jahr von–bis, Genre aus einer Menge. Zwei Regeln stoßen zusammen,
 * wenn sich in *jeder* Dimension die Bereiche überschneiden. Das ist eine
 * Rechnung und keine Schätzung, und sie geht nur ohne Klammern und ohne ODER
 * auf.
 *
 * ⚠️ **Der Hinweis „wird überholt" wird bei jedem Aufruf neu gerechnet**, nicht
 * beim Anlegen gezeigt. Eine Regel bleibt gleich, während sich das Haus ändert
 * — steht der Zielordner erst bei der Freigabe fest, gibt dieselbe Regel
 * plötzlich nicht mehr sofort frei. Ein Hinweis von damals wäre heute falsch.
 */

import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { AppConfig, Genre } from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Fenster } from '../../components/Fenster'
import { Umschalter } from '../../components/Umschalter'
import { AUSWAHL, Button, Card, ErrorBanner, Section, Spinner } from '../../components/ui'

// ---------------------------------------------------------------------------
// Was vom Server kommt
// ---------------------------------------------------------------------------

type Entscheidung = 'freigeben' | 'ablehnen'

const ENTSCHEIDUNGEN = ['freigeben', 'ablehnen'] as const

type Bedingung = {
  feld: string
  von?: number | null
  bis?: number | null
  werte?: string[]
}

type Regel = {
  id: number
  position: number
  name: string
  aktiv: boolean
  bedingungen: Bedingung[]
  entscheidung: Entscheidung
  hausbestand: boolean
  begruendung: string
  trotzdem_fragen: boolean
}

type FeldArt = 'zahl' | 'menge'
type ServerFeld = { kennung: string; art: FeldArt }
/** Was ein Feld in der Oberfläche heißt und welche Werte es kennt. */
type Feld = {
  kennung: string
  name: string
  art: FeldArt
  einheit?: string
  hinweis?: string
  werte?: { wert: string; name: string }[]
}

// ---------------------------------------------------------------------------

export function AdminRegeln() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [bearbeitet, setBearbeitet] = useState<Regel | null>(null)
  const [zuLoeschen, setZuLoeschen] = useState<Regel | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)

  const regelnQuery = useQuery({
    queryKey: ['regeln'],
    queryFn: () => api.get<Regel[]>('/api/admin/regeln'),
  })
  // ⚠️ **Die Felder kommen vom Server**, nicht aus einer Liste hier. Eine
  // zweite Aufzählung derselben Felder wäre beim nächsten neuen Feld falsch,
  // und der Fehler fiele erst auf, wenn eine Regel nicht greift.
  const felderQuery = useQuery({
    queryKey: ['regeln', 'felder'],
    queryFn: () => api.get<ServerFeld[]>('/api/admin/regeln/felder'),
  })
  const genresQuery = useQuery({
    queryKey: ['genres', 'alle'],
    queryFn: async () => {
      const [filme, serien] = await Promise.all([
        api.get<Genre[]>('/api/discover/genres/movie'),
        api.get<Genre[]>('/api/discover/genres/tv'),
      ])
      const zusammen = new Map<number, string>()
      for (const g of [...filme, ...serien]) zusammen.set(g.id, g.name)
      return [...zusammen]
        .map(([id, name]) => ({ id, name }))
        .sort((a, b) => a.name.localeCompare(b.name))
    },
  })
  // ⚠️ Aus ``/api/config``, nicht aus ``/api/settings``: Dort steht, was die
  // Oberfläche über das Haus wissen darf, und der Schlüssel ist derselbe, den
  // die übrigen Seiten benutzen - also kein zweiter Abruf.
  const configQuery = useQuery({
    queryKey: ['config'],
    queryFn: () => api.get<AppConfig>('/api/config'),
  })

  const regeln = useMemo(
    () => [...(regelnQuery.data ?? [])].sort((a, b) => a.position - b.position || a.id - b.id),
    [regelnQuery.data],
  )

  const felder = useMemo(
    () => bauFelder(felderQuery.data ?? [], genresQuery.data ?? []),
    [felderQuery.data, genresQuery.data],
  )
  const FELD = useMemo(() => Object.fromEntries(felder.map((f) => [f.kennung, f])), [felder])

  /**
   * Wählt der Entscheider den Zielordner? Dann übersteuert das jede
   * freigebende Regel — die Anfrage geht trotzdem an ihn.
   */
  const zielBeimFreigeben = Boolean(
    configQuery.data &&
      (configQuery.data.approver_picks_target_movie ||
        configQuery.data.approver_picks_target_tv ||
        configQuery.data.approver_picks_target_movie_uhd ||
        configQuery.data.approver_picks_target_tv_uhd),
  )

  function melde(caught: unknown) {
    setFehler(caught instanceof ApiError ? caught.message : t('errors.generic'))
  }

  const nachladen = () => {
    void queryClient.invalidateQueries({ queryKey: ['regeln'] })
    setFehler(null)
  }

  const speichern = useMutation({
    mutationFn: (regel: Regel) =>
      regel.id > 0
        ? api.put<Regel>(`/api/admin/regeln/${regel.id}`, hinaus(regel))
        : api.post<Regel>('/api/admin/regeln', hinaus(regel)),
    onSuccess: () => {
      nachladen()
      setBearbeitet(null)
    },
    onError: melde,
  })

  const loeschen = useMutation({
    mutationFn: (regel: Regel) => api.delete<void>(`/api/admin/regeln/${regel.id}`),
    onSuccess: () => {
      nachladen()
      setZuLoeschen(null)
    },
    onError: melde,
  })

  const umsortieren = useMutation({
    mutationFn: (reihenfolge: number[]) =>
      api.put<Regel[]>('/api/admin/regeln/reihenfolge', { reihenfolge }),
    onSuccess: nachladen,
    onError: melde,
  })

  const umschalten = useMutation({
    mutationFn: (regel: Regel) =>
      api.put<Regel>(`/api/admin/regeln/${regel.id}`, hinaus({ ...regel, aktiv: !regel.aktiv })),
    onSuccess: nachladen,
    onError: melde,
  })

  /** Welche Regel wird von welcher überholt? Siehe Kopf der Datei. */
  const ueberholt = useMemo(() => {
    const treffer = new Map<number, Regel>()
    for (let i = 0; i < regeln.length; i++) {
      for (let j = i + 1; j < regeln.length; j++) {
        const oben = regeln[i]
        const unten = regeln[j]
        if (!oben.aktiv || !unten.aktiv) continue
        if (oben.entscheidung === unten.entscheidung) continue
        if (treffer.has(unten.id)) continue
        if (ueberschneiden(oben, unten, felder)) treffer.set(unten.id, oben)
      }
    }
    return treffer
  }, [regeln, felder])

  function verschieben(id: number, richtung: -1 | 1) {
    const i = regeln.findIndex((r) => r.id === id)
    const j = i + richtung
    if (i < 0 || j < 0 || j >= regeln.length) return
    const neu = regeln.map((r) => r.id)
    ;[neu[i], neu[j]] = [neu[j], neu[i]]
    umsortieren.mutate(neu)
  }

  function davorSetzen(id: number, zielId: number) {
    const ohne = regeln.map((r) => r.id).filter((x) => x !== id)
    const ziel = ohne.indexOf(zielId)
    if (ziel < 0) return
    umsortieren.mutate([...ohne.slice(0, ziel), id, ...ohne.slice(ziel)])
  }

  if (regelnQuery.isLoading || felderQuery.isLoading) {
    return (
      <Card>
        <Spinner />
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      {fehler && <ErrorBanner message={fehler} />}

      <Section title={t('regeln.title')}>
        <p className="text-sm text-mist-400">{t('regeln.intro')}</p>
        <p className="text-sm text-mist-400">{t('regeln.introUnd')}</p>

        <div className="rounded-2xl border border-ink-700 bg-ink-850/60 px-4 py-3">
          <div className="text-sm font-semibold text-mist-200">{t('regeln.grenzenTitel')}</div>
          <ul className="mt-2 space-y-1.5 text-sm text-mist-400">
            <li>
              <b className="text-mist-300">{t('regeln.grenzeAlterTitel')}</b>{' '}
              {t('regeln.grenzeAlter')}
            </li>
            <li>
              <b className="text-mist-300">{t('regeln.grenzeElternTitel')}</b>{' '}
              {t('regeln.grenzeEltern')}
            </li>
            <li>
              <b className="text-mist-300">{t('regeln.grenzeKontingentTitel')}</b>{' '}
              {t('regeln.grenzeKontingent')}
            </li>
          </ul>
        </div>
      </Section>

      {regeln.length === 0 && (
        <Card>
          <p className="text-sm text-mist-400">{t('regeln.leer')}</p>
        </Card>
      )}

      <div className="space-y-2">
        {regeln.map((r, i) => (
          <Card key={r.id}>
            <div className="flex items-start gap-3">
              <div className="flex flex-col">
                <button
                  type="button"
                  className="px-1 text-mist-600 hover:text-mist-300 disabled:opacity-20"
                  disabled={i === 0 || umsortieren.isPending}
                  onClick={() => verschieben(r.id, -1)}
                  aria-label={t('regeln.nachObenAria', { name: r.name })}
                >
                  ▲
                </button>
                <button
                  type="button"
                  className="px-1 text-mist-600 hover:text-mist-300 disabled:opacity-20"
                  disabled={i === regeln.length - 1 || umsortieren.isPending}
                  onClick={() => verschieben(r.id, 1)}
                  aria-label={t('regeln.nachUntenAria', { name: r.name })}
                >
                  ▼
                </button>
              </div>

              <div className={`min-w-0 flex-1 ${r.aktiv ? '' : 'opacity-40'}`}>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="rounded bg-ink-850 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-mist-500">
                    {i + 1}
                  </span>
                  <b className="text-mist-100">{r.name}</b>
                  {!r.aktiv && <span className="text-xs text-mist-600">{t('regeln.aus')}</span>}
                  <div className="flex-1" />
                  <Folge regel={r} />
                </div>

                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {r.bedingungen.map((b, k) => (
                    <span
                      key={k}
                      className="rounded-full border border-ink-700 px-2.5 py-0.5 text-xs whitespace-nowrap text-mist-300"
                    >
                      {chipText(b, FELD)}
                    </span>
                  ))}
                </div>

                {r.entscheidung === 'ablehnen' && r.begruendung && (
                  <div className="mt-2 text-xs text-mist-600">
                    {t('regeln.liestText', { text: r.begruendung })}
                  </div>
                )}
                {r.entscheidung === 'ablehnen' && r.trotzdem_fragen && (
                  <div className="mt-1 text-xs text-mist-600">{t('regeln.darfTrotzdem')}</div>
                )}

                {zielBeimFreigeben && r.entscheidung === 'freigeben' && (
                  <div className="mt-2 text-xs text-warn-500">{t('regeln.zielHinweis')}</div>
                )}

                {ueberholt.get(r.id) && (
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-warn-500">
                    <span>{t('regeln.ueberholt', { name: ueberholt.get(r.id)!.name })}</span>
                    <Button
                      variant="ghost"
                      className="px-3 py-1 text-xs"
                      onClick={() => davorSetzen(r.id, ueberholt.get(r.id)!.id)}
                    >
                      {t('regeln.nachOben')}
                    </Button>
                  </div>
                )}
              </div>

              <div className="flex shrink-0 gap-2">
                <Button
                  variant="ghost"
                  disabled={umschalten.isPending}
                  onClick={() => umschalten.mutate(r)}
                >
                  {r.aktiv ? t('regeln.ausschalten') : t('regeln.einschalten')}
                </Button>
                <Button variant="ghost" onClick={() => setBearbeitet(r)}>
                  {t('common.edit')}
                </Button>
                <Button variant="ghost" onClick={() => setZuLoeschen(r)}>
                  {t('common.delete')}
                </Button>
              </div>
            </div>
          </Card>
        ))}
      </div>

      <Button
        onClick={() =>
          setBearbeitet({
            id: 0,
            position: regeln.length,
            name: '',
            aktiv: true,
            bedingungen: [{ feld: 'typ', werte: ['movie'] }],
            entscheidung: 'freigeben',
            hausbestand: false,
            begruendung: '',
            trotzdem_fragen: false,
          })
        }
      >
        {t('regeln.hinzufuegen')}
      </Button>

      <ConfirmDialog
        open={zuLoeschen !== null}
        title={t('regeln.loeschenTitel')}
        description={zuLoeschen ? t('regeln.loeschenText', { name: zuLoeschen.name }) : ''}
        confirmLabel={t('common.delete')}
        loading={loeschen.isPending}
        onConfirm={() => zuLoeschen && loeschen.mutate(zuLoeschen)}
        onCancel={() => setZuLoeschen(null)}
      />

      {bearbeitet && (
        <RegelFenster
          regel={bearbeitet}
          felder={felder}
          zielBeimFreigeben={zielBeimFreigeben}
          speichert={speichern.isPending}
          onSchliessen={() => setBearbeitet(null)}
          onSpeichern={(neu) => speichern.mutate(neu)}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Felder, Text, Kollision
// ---------------------------------------------------------------------------

/** Aus den Kennungen des Servers die Felder samt Beschriftung und Werten. */
function bauFelder(server: ServerFeld[], genres: Genre[]): Feld[] {
  const werte: Record<string, { wert: string; name: string }[]> = {
    typ: [
      { wert: 'movie', name: 'Film' },
      { wert: 'tv', name: 'Serie' },
    ],
    genre: genres.map((g) => ({ wert: String(g.id), name: g.name })),
    sprache: [
      { wert: 'de', name: 'Deutsch' },
      { wert: 'en', name: 'Englisch' },
      { wert: 'fr', name: 'Französisch' },
      { wert: 'es', name: 'Spanisch' },
      { wert: 'ja', name: 'Japanisch' },
      { wert: 'ko', name: 'Koreanisch' },
    ],
    qualitaet: [
      { wert: 'hd', name: 'HD' },
      { wert: 'uhd', name: '4K' },
    ],
    bestand: [
      { wert: 'hd', name: 'HD' },
      { wert: 'uhd', name: '4K' },
      { wert: 'nichts', name: 'gar nicht' },
    ],
  }
  const namen: Record<string, string> = {
    typ: 'Typ',
    genre: 'Genre',
    bewertung: 'Bewertung',
    stimmen: 'Anzahl Stimmen',
    jahr: 'Erscheinungsjahr',
    laufzeit: 'Laufzeit',
    sprache: 'Originalsprache',
    altersfreigabe: 'Altersfreigabe',
    qualitaet: 'Angefragte Stufe',
    bestand: 'Liegt schon vor als',
  }
  const einheiten: Record<string, string> = {
    bewertung: 'von 10',
    laufzeit: 'Minuten',
    altersfreigabe: 'Jahre',
  }
  const hinweise: Record<string, string> = {
    stimmen:
      'Bitte immer mitgeben. 9,2 aus vier Stimmen ist kein guter Film, und eine Regel ' +
      '„ab 8,0 freigeben" wird ohne diese Bedingung zum Einfallstor.',
    bestand:
      'Braucht zwei Instanzen. Nexview kennt nur, was über Nexview angefragt wurde — ' +
      'was vorher in der Mediathek lag, zählt hier als „gar nicht".',
    altersfreigabe: 'Soweit TMDB sie für deine Region kennt.',
  }
  // Feste Reihenfolge statt alphabetisch: erst wonach man sucht, dann wie gut
  // es ist, dann was das Haus damit macht.
  const ordnung = [
    'typ',
    'genre',
    'bewertung',
    'stimmen',
    'jahr',
    'laufzeit',
    'sprache',
    'altersfreigabe',
    'qualitaet',
    'bestand',
  ]
  return server
    .map((f) => ({
      kennung: f.kennung,
      art: f.art,
      name: namen[f.kennung] ?? f.kennung,
      einheit: einheiten[f.kennung],
      hinweis: hinweise[f.kennung],
      werte: werte[f.kennung],
    }))
    .sort((a, b) => ordnung.indexOf(a.kennung) - ordnung.indexOf(b.kennung))
}

function chipText(b: Bedingung, FELD: Record<string, Feld>): string {
  const feld = FELD[b.feld]
  if (!feld) return b.feld
  if (feld.art === 'menge') {
    const namen = (b.werte ?? []).map((w) => feld.werte?.find((v) => v.wert === w)?.name ?? w)
    if (!namen.length) return `${feld.name}: —`
    return b.feld === 'typ' || b.feld === 'genre'
      ? namen.join(' / ')
      : `${feld.name}: ${namen.join(' / ')}`
  }
  if (b.feld === 'jahr') {
    if (b.von != null && b.bis != null) return `${b.von}–${b.bis}`
    if (b.von != null) return `ab ${b.von}`
    return `vor ${b.bis}`
  }
  const kurz = b.feld === 'stimmen' ? 'Stimmen' : feld.name
  if (b.von != null && b.bis != null) return `${kurz} ${b.von}–${b.bis}`
  if (b.von != null) return `${kurz} ≥ ${b.von}`
  if (b.bis != null) return `${kurz} < ${b.bis}`
  return `${kurz}: —`
}

function Folge({ regel }: { regel: Regel }) {
  const { t } = useTranslation()
  const frei = regel.entscheidung === 'freigeben'
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span
        className={
          'inline-flex shrink-0 items-center rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ' +
          (frei
            ? 'bg-ok-500/15 text-ok-500 ring-ok-500/30'
            : 'bg-bad-500/15 text-bad-500 ring-bad-500/30')
        }
      >
        {frei ? t('regeln.sofortFreigeben') : t('regeln.ablehnen')}
      </span>
      {frei && regel.hausbestand && (
        <span className="inline-flex shrink-0 items-center rounded-full bg-accent-500/15 px-2.5 py-1 text-[11px] font-semibold text-accent-400 ring-1 ring-accent-500/30">
          {t('regeln.inDenHausbestand')}
        </span>
      )}
    </div>
  )
}

function bedingungFuer(regel: Regel, feld: string): Bedingung | undefined {
  return regel.bedingungen.find((b) => b.feld === feld)
}

/**
 * Können zwei Regeln denselben Titel treffen?
 *
 * ⚠️ **„von" schließt ein, „bis" schließt aus** — genau wie im Dienst. Mit zwei
 * einschließenden Grenzen meldete die Oberfläche „unter 5" und „ab 5" als
 * Widerspruch, den es nicht gibt.
 */
function ueberschneiden(a: Regel, b: Regel, felder: Feld[]): boolean {
  for (const feld of felder) {
    const x = bedingungFuer(a, feld.kennung)
    const y = bedingungFuer(b, feld.kennung)
    if (!x || !y) continue
    if (feld.art === 'zahl') {
      const xv = x.von ?? -Infinity
      const xb = x.bis ?? Infinity
      const yv = y.von ?? -Infinity
      const yb = y.bis ?? Infinity
      if (!(xv < yb && yv < xb)) return false
    } else if (!(x.werte ?? []).some((w) => (y.werte ?? []).includes(w))) {
      return false
    }
  }
  return true
}

/** Was der Server entgegennimmt. ``position`` setzt er selbst. */
function hinaus(r: Regel) {
  return {
    name: r.name,
    aktiv: r.aktiv,
    bedingungen: r.bedingungen,
    entscheidung: r.entscheidung,
    hausbestand: r.hausbestand,
    begruendung: r.begruendung,
    trotzdem_fragen: r.trotzdem_fragen,
  }
}

// ---------------------------------------------------------------------------
// Der Editor
// ---------------------------------------------------------------------------

function RegelFenster({
  regel,
  felder,
  zielBeimFreigeben,
  speichert,
  onSchliessen,
  onSpeichern,
}: {
  regel: Regel
  felder: Feld[]
  zielBeimFreigeben: boolean
  speichert: boolean
  onSchliessen: () => void
  onSpeichern: (r: Regel) => void
}) {
  const { t } = useTranslation()
  const [entwurf, setEntwurf] = useState<Regel>(regel)
  const FELD = Object.fromEntries(felder.map((f) => [f.kennung, f]))

  const ungenutzt = felder.filter((f) => !entwurf.bedingungen.some((b) => b.feld === f.kennung))
  // Eine Bedingung, bei der nichts angehakt oder nichts eingetragen ist, würde
  // der Server ablehnen. Das sagt der Knopf, statt es passieren zu lassen.
  const unfertig = entwurf.bedingungen.some(
    (b) =>
      (FELD[b.feld]?.art === 'menge' && !(b.werte ?? []).length) ||
      (FELD[b.feld]?.art === 'zahl' && b.von == null && b.bis == null),
  )

  return (
    <Fenster
      offen
      titel={regel.id > 0 ? t('regeln.aendernTitel') : t('regeln.neuTitel')}
      // ⚠️ `unterzeile` ist im Haus `font-mono` — dort stehen Pfade und
      // Profilnamen, keine Sätze.
      unterzeile={regel.name || undefined}
      onSchliessen={onSchliessen}
      fuss={
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onSchliessen}>
            {t('common.cancel')}
          </Button>
          <Button
            loading={speichert}
            disabled={!entwurf.name.trim() || !entwurf.bedingungen.length || unfertig}
            onClick={() => onSpeichern({ ...entwurf, name: entwurf.name.trim() })}
          >
            {t('common.save')}
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        <label className="block text-sm">
          <span className="mb-1 block text-mist-400">{t('regeln.name')}</span>
          <input
            className={`${AUSWAHL} w-full`}
            value={entwurf.name}
            placeholder={t('regeln.namePlatzhalter')}
            onChange={(e) => setEntwurf((a) => ({ ...a, name: e.target.value }))}
          />
        </label>

        <div className="space-y-2">
          <div className="text-xs tracking-wide text-mist-500 uppercase">{t('regeln.wenn')}</div>
          {entwurf.bedingungen.map((b, i) => (
            <BedingungZeile
              key={`${b.feld}-${i}`}
              bedingung={b}
              feld={FELD[b.feld]}
              erste={i === 0}
              onAendern={(neu) =>
                setEntwurf((a) => ({
                  ...a,
                  bedingungen: a.bedingungen.map((x, k) => (k === i ? neu : x)),
                }))
              }
              onWeg={() =>
                setEntwurf((a) => ({
                  ...a,
                  bedingungen: a.bedingungen.filter((_, k) => k !== i),
                }))
              }
            />
          ))}
          {ungenutzt.length > 0 && (
            <select
              className={AUSWAHL}
              value=""
              onChange={(e) => {
                const feld = FELD[e.target.value]
                if (!feld) return
                setEntwurf((a) => ({
                  ...a,
                  bedingungen: [
                    ...a.bedingungen,
                    feld.art === 'zahl'
                      ? { feld: feld.kennung, von: null, bis: null }
                      : { feld: feld.kennung, werte: [] },
                  ],
                }))
              }}
            >
              <option value="">{t('regeln.bedingungHinzufuegen')}</option>
              {ungenutzt.map((f) => (
                <option key={f.kennung} value={f.kennung}>
                  {f.name}
                </option>
              ))}
            </select>
          )}
        </div>

        <div className="space-y-2">
          <div className="text-xs tracking-wide text-mist-500 uppercase">{t('regeln.dann')}</div>
          <Umschalter
            wert={entwurf.entscheidung}
            wahl={ENTSCHEIDUNGEN}
            onChange={(neu) => setEntwurf((a) => ({ ...a, entscheidung: neu }))}
            label={(e) => (e === 'freigeben' ? t('regeln.sofortFreigeben') : t('regeln.ablehnen'))}
          />

          {zielBeimFreigeben && entwurf.entscheidung === 'freigeben' && (
            <div className="rounded-2xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-xs text-mist-300">
              <b className="text-warn-500">{t('regeln.zielKastenTitel')}</b>{' '}
              {t('regeln.zielKasten')}
            </div>
          )}

          {entwurf.entscheidung === 'freigeben' && (
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1 h-4 w-4 shrink-0 accent-accent-500"
                checked={entwurf.hausbestand}
                onChange={(e) => setEntwurf((a) => ({ ...a, hausbestand: e.target.checked }))}
              />
              <span>
                <b>{t('regeln.hausbestandTitel')}</b>
                <span className="block text-xs text-mist-500">
                  {t('regeln.hausbestandHinweis')}
                </span>
              </span>
            </label>
          )}

          {entwurf.entscheidung === 'ablehnen' && (
            <>
              <label className="block text-sm">
                <span className="mb-1 block text-mist-400">{t('regeln.begruendung')}</span>
                <input
                  className={`${AUSWAHL} w-full`}
                  value={entwurf.begruendung}
                  placeholder={t('regeln.begruendungPlatzhalter')}
                  onChange={(e) => setEntwurf((a) => ({ ...a, begruendung: e.target.value }))}
                />
              </label>
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4 shrink-0 accent-accent-500"
                  checked={entwurf.trotzdem_fragen}
                  onChange={(e) => setEntwurf((a) => ({ ...a, trotzdem_fragen: e.target.checked }))}
                />
                <span>
                  <b>{t('regeln.trotzdemTitel')}</b>
                  <span className="block text-xs text-mist-500">{t('regeln.trotzdemHinweis')}</span>
                </span>
              </label>
            </>
          )}
        </div>
      </div>
    </Fenster>
  )
}

function BedingungZeile({
  bedingung,
  feld,
  erste,
  onAendern,
  onWeg,
}: {
  bedingung: Bedingung
  feld: Feld | undefined
  erste: boolean
  onAendern: (b: Bedingung) => void
  onWeg: () => void
}) {
  const { t } = useTranslation()
  if (!feld) return null

  return (
    <div className="rounded-2xl border border-ink-700 px-4 py-3">
      <div className="flex items-center gap-2">
        <span className="w-10 shrink-0 text-xs text-mist-600">{erste ? '' : t('regeln.und')}</span>
        <b className="text-sm">{feld.name}</b>
        <div className="flex-1" />
        <button type="button" className="text-xs text-mist-500 hover:text-mist-200" onClick={onWeg}>
          {t('regeln.entfernen')}
        </button>
      </div>

      <div className="mt-2 pl-12">
        {feld.art === 'zahl' ? (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-mist-400">{t('regeln.von')}</span>
            <input
              className={`${AUSWAHL} w-24`}
              inputMode="decimal"
              value={bedingung.von ?? ''}
              placeholder={t('regeln.egal')}
              onChange={(e) =>
                onAendern({
                  ...bedingung,
                  von: e.target.value === '' ? null : Number(e.target.value.replace(',', '.')),
                })
              }
            />
            <span className="text-mist-400">{t('regeln.bis')}</span>
            <input
              className={`${AUSWAHL} w-24`}
              inputMode="decimal"
              value={bedingung.bis ?? ''}
              placeholder={t('regeln.egal')}
              onChange={(e) =>
                onAendern({
                  ...bedingung,
                  bis: e.target.value === '' ? null : Number(e.target.value.replace(',', '.')),
                })
              }
            />
            {feld.einheit && <span className="text-mist-500">{feld.einheit}</span>}
            <span className="w-full text-xs text-mist-600">{t('regeln.grenzen')}</span>
          </div>
        ) : (
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {feld.werte?.map((w) => (
              <label key={w.wert} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  className="h-4 w-4 shrink-0 accent-accent-500"
                  checked={(bedingung.werte ?? []).includes(w.wert)}
                  onChange={(e) =>
                    onAendern({
                      ...bedingung,
                      werte: e.target.checked
                        ? [...(bedingung.werte ?? []), w.wert]
                        : (bedingung.werte ?? []).filter((x) => x !== w.wert),
                    })
                  }
                />
                {w.name}
              </label>
            ))}
          </div>
        )}

        {feld.hinweis && <div className="mt-1 text-xs text-mist-600">{feld.hinweis}</div>}
      </div>
    </div>
  )
}
