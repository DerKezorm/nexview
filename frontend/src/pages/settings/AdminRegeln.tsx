/**
 * Regeln — Attrappe. **Nichts davon wirkt.**
 *
 * Zum Anschauen und Ändern, bevor irgendetwas gebaut wird. Kein Backend, kein
 * Speichern; ein Neuladen wirft alles weg. Texte stehen hier absichtlich auf
 * Deutsch fest verdrahtet statt in den Katalogen — beim echten Bau kommen sie
 * über `useTranslation`, und bis dahin wäre jede Zeile in zwei Sprachen
 * doppelte Arbeit an etwas, das sich noch ändert.
 *
 * ⚠️ **Was hier echt gerechnet wird:** die Kollisionserkennung. Eine Regel ist
 * ein Kasten — Bewertung von–bis, Jahr von–bis, Genre aus einer Menge. Zwei
 * Regeln stoßen zusammen, wenn sich in *jeder* Dimension die Bereiche
 * überschneiden. Das ist eine Rechnung, keine Schätzung, und sie geht nur
 * deshalb auf, weil Bedingungen ausschließlich mit UND verknüpft sind.
 * Mit Klammern und ODER wäre sie nicht mehr exakt.
 */

import { useMemo, useState } from 'react'

import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Fenster } from '../../components/Fenster'
import { Umschalter } from '../../components/Umschalter'
import { AUSWAHL, Button, Card, Section } from '../../components/ui'

// ---------------------------------------------------------------------------
// Was eine Regel ist
// ---------------------------------------------------------------------------

type FeldArt = 'zahl' | 'menge'

type Feld = {
  kennung: string
  name: string
  art: FeldArt
  einheit?: string
  werte?: { wert: string; name: string }[]
  hinweis?: string
}

const FELDER: Feld[] = [
  {
    kennung: 'typ',
    name: 'Typ',
    art: 'menge',
    werte: [
      { wert: 'movie', name: 'Film' },
      { wert: 'tv', name: 'Serie' },
    ],
  },
  {
    kennung: 'genre',
    name: 'Genre',
    art: 'menge',
    werte: [
      { wert: '99', name: 'Dokumentation' },
      { wert: '28', name: 'Action' },
      { wert: '27', name: 'Horror' },
      { wert: '35', name: 'Komödie' },
      { wert: '18', name: 'Drama' },
      { wert: '878', name: 'Science Fiction' },
      { wert: '16', name: 'Animation' },
      { wert: '10751', name: 'Familie' },
    ],
  },
  {
    kennung: 'bewertung',
    name: 'Bewertung',
    art: 'zahl',
    einheit: 'von 10',
    hinweis: 'Der Durchschnitt bei TMDB.',
  },
  {
    kennung: 'stimmen',
    name: 'Anzahl Stimmen',
    art: 'zahl',
    hinweis:
      'Bitte immer mitgeben. 9,2 aus vier Stimmen ist kein guter Film, und eine ' +
      'Regel „ab 8,0 freigeben" wird ohne diese Bedingung zum Einfallstor.',
  },
  { kennung: 'jahr', name: 'Erscheinungsjahr', art: 'zahl' },
  { kennung: 'laufzeit', name: 'Laufzeit', art: 'zahl', einheit: 'Minuten' },
  {
    kennung: 'sprache',
    name: 'Originalsprache',
    art: 'menge',
    werte: [
      { wert: 'de', name: 'Deutsch' },
      { wert: 'en', name: 'Englisch' },
      { wert: 'fr', name: 'Französisch' },
      { wert: 'ja', name: 'Japanisch' },
    ],
  },
  {
    kennung: 'altersfreigabe',
    name: 'Altersfreigabe',
    art: 'zahl',
    einheit: 'Jahre',
    hinweis: 'FSK, soweit TMDB sie kennt.',
  },
  {
    kennung: 'anbieter',
    name: 'Läuft schon bei',
    art: 'menge',
    werte: [
      { wert: 'netflix', name: 'Netflix' },
      { wert: 'prime', name: 'Prime Video' },
      { wert: 'disney', name: 'Disney+' },
      { wert: 'keiner', name: 'keinem Abo im Haus' },
    ],
  },
  {
    kennung: 'qualitaet',
    name: 'Angefragte Stufe',
    art: 'menge',
    werte: [
      { wert: 'hd', name: 'HD' },
      { wert: 'uhd', name: '4K' },
    ],
  },
  {
    // ⚠️ **Diese Bedingung fragt nicht TMDB, sondern den eigenen Bestand.**
    // Heute blockiert Nexview nur dieselbe Stufe: Denselben Film in HD *und*
    // in 4K anzufragen ist ausdruecklich erlaubt, das sind zwei Dateien in
    // zwei Ordnern. Wer das nicht will, kann es hier abschalten - und zwar in
    // beide Richtungen.
    kennung: 'bestand',
    name: 'Liegt schon vor als',
    art: 'menge',
    werte: [
      { wert: 'hd', name: 'HD' },
      { wert: 'uhd', name: '4K' },
      { wert: 'nichts', name: 'gar nicht' },
    ],
    hinweis:
      'Braucht zwei Instanzen, sonst gibt es nur eine Stufe und die Bedingung ' +
      'trifft nie zu.',
  },
  {
    kennung: 'rolle',
    name: 'Anfrage kommt von',
    art: 'menge',
    werte: [
      { wert: 'user', name: 'Benutzer' },
      { wert: 'approver', name: 'Entscheider' },
      { wert: 'admin', name: 'Administrator' },
    ],
  },
]

const FELD = Object.fromEntries(FELDER.map((f) => [f.kennung, f]))

/** Eine Bedingung: entweder ein Zahlenbereich oder eine Menge erlaubter Werte. */
type Bedingung =
  | { feld: string; art: 'zahl'; von: number | null; bis: number | null }
  | { feld: string; art: 'menge'; werte: string[] }

type Entscheidung = 'freigeben' | 'ablehnen'

//: Reihenfolge im Umschalter. Freigeben zuerst - der haeufigere Fall.
const ENTSCHEIDUNGEN = ['freigeben', 'ablehnen'] as const

type Regel = {
  id: number
  name: string
  an: boolean
  bedingungen: Bedingung[]
  entscheidung: Entscheidung
  hausbestand: boolean
  begruendung: string
}

// ---------------------------------------------------------------------------
// Beispiele — genau die drei aus dem Gespräch
// ---------------------------------------------------------------------------

const BEISPIELE: Regel[] = [
  {
    id: 1,
    name: 'Schwache Filme gar nicht erst',
    an: true,
    bedingungen: [
      { feld: 'typ', art: 'menge', werte: ['movie'] },
      { feld: 'bewertung', art: 'zahl', von: null, bis: 5 },
      { feld: 'stimmen', art: 'zahl', von: 50, bis: null },
    ],
    entscheidung: 'ablehnen',
    hausbestand: false,
    begruendung: 'Der Titel ist schwach bewertet. Frag mich, wenn du ihn trotzdem brauchst.',
  },
  {
    id: 2,
    name: 'Gute Dokumentationen durchwinken',
    an: true,
    bedingungen: [
      { feld: 'genre', art: 'menge', werte: ['99'] },
      { feld: 'bewertung', art: 'zahl', von: 5, bis: null },
      { feld: 'stimmen', art: 'zahl', von: 50, bis: null },
    ],
    entscheidung: 'freigeben',
    hausbestand: false,
    begruendung: '',
  },
  {
    id: 3,
    name: 'Neue Science Fiction ins Haus',
    an: true,
    bedingungen: [
      { feld: 'typ', art: 'menge', werte: ['movie'] },
      { feld: 'genre', art: 'menge', werte: ['878'] },
      { feld: 'jahr', art: 'zahl', von: 2026, bis: null },
      { feld: 'bewertung', art: 'zahl', von: 7, bis: null },
    ],
    entscheidung: 'freigeben',
    hausbestand: true,
    begruendung: '',
  },
  {
    id: 4,
    name: 'Keine Dopplung: liegt schon in 4K',
    an: true,
    bedingungen: [
      { feld: 'qualitaet', art: 'menge', werte: ['hd'] },
      { feld: 'bestand', art: 'menge', werte: ['uhd'] },
    ],
    entscheidung: 'ablehnen',
    hausbestand: false,
    begruendung: 'Den Titel gibt es schon in 4K. Eine zweite Fassung in HD lohnt nicht.',
  },
]

// ---------------------------------------------------------------------------
// Kollisionen
// ---------------------------------------------------------------------------

function bedingungFuer(regel: Regel, feld: string): Bedingung | undefined {
  return regel.bedingungen.find((b) => b.feld === feld)
}

/**
 * Können zwei Regeln denselben Titel treffen?
 *
 * Wo eine Regel ein Feld nicht nennt, lässt sie alles zu — dann kann es die
 * Überschneidung nicht verhindern. Genannt wird sie nur, wenn *jedes* Feld
 * durchlässt; ein einziges sich ausschließendes Paar genügt zum Ausschluss.
 */
function ueberschneiden(a: Regel, b: Regel): boolean {
  for (const feld of FELDER) {
    const x = bedingungFuer(a, feld.kennung)
    const y = bedingungFuer(b, feld.kennung)
    if (!x || !y) continue
    if (x.art === 'zahl' && y.art === 'zahl') {
      // ⚠️ **„von" schliesst ein, „bis" schliesst aus.** Genau so steht es im
      // Satz: „ab 5" und „unter 5". Beide Grenzen einschliessend zu rechnen
      // hat die Attrappe beim ersten Blick einen Widerspruch melden lassen,
      // den es nicht gibt - die beiden Regeln beruehren sich nur bei 5.
      const xv = x.von ?? -Infinity
      const xb = x.bis ?? Infinity
      const yv = y.von ?? -Infinity
      const yb = y.bis ?? Infinity
      if (!(xv < yb && yv < xb)) return false
    } else if (x.art === 'menge' && y.art === 'menge') {
      if (!x.werte.some((w) => y.werte.includes(w))) return false
    }
  }
  return true
}

// ---------------------------------------------------------------------------
// Probe: was passiert mit einem bestimmten Titel?
// ---------------------------------------------------------------------------

/** Ein gedachter Titel. Leeres Feld heißt „dazu sage ich nichts". */
type Probe = Record<string, string>

/**
 * Trifft die Regel auf diesen Titel zu?
 *
 * ⚠️ **Ein Feld, zu dem der Titel nichts sagt, lässt die Regel scheitern.**
 * Anders herum wäre es gefährlich: Eine Regel „Bewertung ab 8 → freigeben"
 * würde sonst bei jedem Titel greifen, dessen Bewertung Nexview gerade nicht
 * kennt. Im Zweifel passiert nichts, und die Anfrage nimmt den normalen Weg.
 */
function passt(regel: Regel, probe: Probe): boolean {
  for (const b of regel.bedingungen) {
    const roh = (probe[b.feld] ?? '').trim()
    if (roh === '') return false
    if (b.art === 'menge') {
      if (!b.werte.includes(roh)) return false
    } else {
      const zahl = Number(roh.replace(',', '.'))
      if (Number.isNaN(zahl)) return false
      if (b.von !== null && zahl < b.von) return false
      if (b.bis !== null && zahl >= b.bis) return false
    }
  }
  return regel.bedingungen.length > 0
}

// ---------------------------------------------------------------------------
// Regel als Satz
// ---------------------------------------------------------------------------

/**
 * Dieselbe Bedingung, kurz genug für ein Abzeichen.
 *
 * ⚠️ **Der ausgeschriebene Satz war das Problem.** „…, dann sofort freigeben
 * und auf den Hausbestand buchen" stand am Ende einer Zeile, die mit „Wenn Typ
 * ist Film und Genre ist…" anfing — und wurde deshalb übersehen. Was eine
 * Regel *tut*, muss man auf einen Blick sehen, ohne zu lesen.
 */
function chipText(b: Bedingung): string {
  const feld = FELD[b.feld]
  if (!feld) return ''
  if (b.art === 'menge') {
    const namen = b.werte.map((w) => feld.werte?.find((v) => v.wert === w)?.name ?? w)
    if (!namen.length) return `${feld.name}: nichts gewählt`
    // Typ und Genre sprechen für sich — „Film" statt „Typ: Film".
    return b.feld === 'typ' || b.feld === 'genre' ? namen.join(' / ') : `${feld.name}: ${namen.join(' / ')}`
  }
  const kurz = b.feld === 'stimmen' ? 'Stimmen' : b.feld === 'erscheinungsjahr' ? '' : feld.name
  if (b.feld === 'jahr') {
    if (b.von !== null && b.bis !== null) return `${b.von}–${b.bis}`
    if (b.von !== null) return `ab ${b.von}`
    return `vor ${b.bis}`
  }
  if (b.von !== null && b.bis !== null) return `${kurz} ${b.von}–${b.bis}`
  if (b.von !== null) return `${kurz} ≥ ${b.von}`
  if (b.bis !== null) return `${kurz} < ${b.bis}`
  return `${kurz}: egal`
}

/** Was die Regel tut, als Abzeichen. Grün gibt frei, rot lehnt ab. */
function Folge({ regel }: { regel: Regel }) {
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
        {frei ? 'sofort freigeben' : 'ablehnen'}
      </span>
      {frei && regel.hausbestand && (
        <span className="inline-flex shrink-0 items-center rounded-full bg-accent-500/15 px-2.5 py-1 text-[11px] font-semibold text-accent-400 ring-1 ring-accent-500/30">
          in den Hausbestand
        </span>
      )}
    </div>
  )
}

function folgeText(r: Regel): string {
  const teile = [r.entscheidung === 'freigeben' ? 'sofort freigeben' : 'ablehnen']
  if (r.entscheidung === 'freigeben' && r.hausbestand) {
    teile.push('auf den Hausbestand buchen (zählt bei niemandem)')
  }
  return teile.join(' und ')
}

// ---------------------------------------------------------------------------

export function AdminRegeln() {
  const [regeln, setRegeln] = useState<Regel[]>(BEISPIELE)
  const [bearbeitet, setBearbeitet] = useState<Regel | null>(null)
  // ⚠️ Loeschen ohne Rueckfrage hat beim ersten Ausprobieren sofort eine
  // Regel gekostet. In einer Liste, in der Reihenfolge zaehlt, sitzt der
  // Knopf zwangslaeufig neben denen, die man staendig braucht.
  const [zuLoeschen, setZuLoeschen] = useState<Regel | null>(null)
  // ⚠️ **Nur in der Attrappe ein Schalter.** Im echten Bau kommt der Wert aus
  // den Einstellungen. Er steht hier, weil er den Kern zeigt: Die Regel bleibt
  // gleich, das Haus aendert sich - und dann muss die Liste es sagen. Ein
  // Hinweis nur beim Anlegen waere am Tag danach schon falsch.
  const [zielBeimFreigeben, setZielBeimFreigeben] = useState(false)

  /**
   * Welche Regel wird von welcher überholt?
   *
   * ⚠️ **Die erste Fassung war ein eigener Kasten über der Liste**, der jeden
   * Zusammenstoß in Prosa erklärte, samt einem zusammengesetzten Beispieltitel
   * („Dokumentation, Bewertung 5, Anzahl Stimmen 50, HD, 4K"). Das las sich wie
   * Maschinenausgabe und war zu viel für etwas, das man in fünf Wörtern sagen
   * kann. Jetzt hängt der Hinweis an der Regel, um die es geht - dort, wo man
   * ohnehin hinsieht.
   */
  const ueberholt = useMemo(() => {
    const treffer = new Map<number, Regel>()
    for (let i = 0; i < regeln.length; i++) {
      for (let j = i + 1; j < regeln.length; j++) {
        const oben = regeln[i]
        const unten = regeln[j]
        if (!oben.an || !unten.an) continue
        if (oben.entscheidung === unten.entscheidung) continue
        if (treffer.has(unten.id)) continue
        if (ueberschneiden(oben, unten)) treffer.set(unten.id, oben)
      }
    }
    return treffer
  }, [regeln])

  /** Eine Regel direkt vor eine andere setzen - fuer den Hinweis unten. */
  function davorSetzen(id: number, zielId: number) {
    setRegeln((alt) => {
      const regel = alt.find((r) => r.id === id)
      if (!regel) return alt
      const ohne = alt.filter((r) => r.id !== id)
      const ziel = ohne.findIndex((r) => r.id === zielId)
      if (ziel < 0) return alt
      return [...ohne.slice(0, ziel), regel, ...ohne.slice(ziel)]
    })
  }

  function verschieben(id: number, richtung: -1 | 1) {
    setRegeln((alt) => {
      const i = alt.findIndex((r) => r.id === id)
      const j = i + richtung
      if (i < 0 || j < 0 || j >= alt.length) return alt
      const neu = [...alt]
      ;[neu[i], neu[j]] = [neu[j], neu[i]]
      return neu
    })
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-mist-300">
        <b className="text-warn-500">Attrappe.</b> Nichts davon wirkt, nichts wird
        gespeichert, ein Neuladen wirft alles weg. Zum Anschauen und Ändern, bevor gebaut
        wird.
        <label className="mt-2 flex items-start gap-2 text-mist-400">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 shrink-0 accent-warn-500"
            checked={zielBeimFreigeben}
            onChange={(e) => setZielBeimFreigeben(e.target.checked)}
          />
          <span>
            Haus-Einstellung nachstellen: <b>der Entscheider wählt den Zielordner</b>.
            Häkchen setzen und die Liste ansehen — die Regeln bleiben unverändert,
            aber zwei von ihnen geben nicht mehr sofort frei.
          </span>
        </label>
      </div>

      <Section title="Regeln">
        <p className="text-sm text-mist-400">
          Regeln entscheiden über Anfragen, bevor die persönliche Freigabeeinstellung
          greift. Nexview geht die Liste <b className="text-mist-200">von oben nach unten</b>{' '}
          durch und nimmt die erste Regel, die passt. Passt keine, gilt alles wie bisher.
        </p>
        <p className="text-sm text-mist-400">
          Innerhalb einer Regel müssen <b className="text-mist-200">alle</b> Bedingungen
          zutreffen. Wer ein „oder" braucht, legt eine zweite Regel an — das hält die Liste
          lesbar.
        </p>

        <div className="rounded-2xl border border-ink-700 bg-ink-850/60 px-4 py-3">
          <div className="text-sm font-semibold text-mist-200">
            Drei Dinge kann keine Regel übergehen
          </div>
          <ul className="mt-2 space-y-1.5 text-sm text-mist-400">
            <li>
              <b className="text-mist-300">Den Altersfilter.</b> Eine Regel „ab 2026 alles
              freigeben" gibt einem Kind keinen Titel, den es nicht sehen darf.
            </li>
            <li>
              <b className="text-mist-300">Die Entscheidung der Eltern.</b> Ein Kinderwunsch
              geht immer an sie, auch wenn eine Regel passen würde. Das ist der Sinn der
              Kinderkonten und hat mit Altersfreigaben nichts zu tun.
            </li>
            <li>
              <b className="text-mist-300">Das Kontingent des Anfragenden.</b> Wer nichts
              mehr anfragen darf, kommt auch über eine Regel nicht weiter — selbst dann
              nicht, wenn der Titel auf den Hausbestand ginge. Sonst hinge es vom Zufall
              ab, ob dieselbe Anfrage durchgeht.
            </li>
          </ul>
        </div>
      </Section>

      <div className="space-y-2">
        {regeln.map((r, i) => (
          <Card key={r.id}>
            <div className="flex items-start gap-3">
              <div className="flex flex-col">
                <button
                  type="button"
                  className="px-1 opacity-60 hover:opacity-100 disabled:opacity-20"
                  disabled={i === 0}
                  onClick={() => verschieben(r.id, -1)}
                  aria-label={`${r.name} nach oben`}
                >
                  ▲
                </button>
                <button
                  type="button"
                  className="px-1 opacity-60 hover:opacity-100 disabled:opacity-20"
                  disabled={i === regeln.length - 1}
                  onClick={() => verschieben(r.id, 1)}
                  aria-label={`${r.name} nach unten`}
                >
                  ▼
                </button>
              </div>

              <div className={`min-w-0 flex-1 ${r.an ? '' : 'opacity-40'}`}>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="rounded bg-ink-850 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-mist-500">
                    {i + 1}
                  </span>
                  <b className="text-mist-100">{r.name}</b>
                  {!r.an && <span className="text-xs text-mist-600">aus</span>}
                  <div className="flex-1" />
                  <Folge regel={r} />
                </div>

                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {r.bedingungen.map((b, k) => (
                    <span
                      key={k}
                      className="rounded-full border border-ink-700 px-2.5 py-0.5 text-xs whitespace-nowrap text-mist-300"
                    >
                      {chipText(b)}
                    </span>
                  ))}
                </div>

                {r.entscheidung === 'ablehnen' && r.begruendung && (
                  <div className="mt-2 text-xs text-mist-600">
                    Der Anfragende liest: „{r.begruendung}"
                  </div>
                )}

                {zielBeimFreigeben && r.entscheidung === 'freigeben' && (
                  <div className="mt-2 text-xs text-warn-500">
                    Gibt hier nicht sofort frei: Der Entscheider wählt den Zielordner,
                    also geht die Anfrage trotzdem an ihn. Für Entscheider und
                    Administratoren greift sie weiterhin.
                  </div>
                )}

                {ueberholt.get(r.id) && (
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-warn-500">
                    <span>
                      Bei manchen Titeln greift <b>{ueberholt.get(r.id)!.name}</b> vorher.
                    </span>
                    <Button
                      variant="ghost"
                      className="px-3 py-1 text-xs"
                      onClick={() => davorSetzen(r.id, ueberholt.get(r.id)!.id)}
                    >
                      Nach oben
                    </Button>
                  </div>
                )}
              </div>

              <div className="flex shrink-0 gap-2">
                <Button
                  variant="ghost"
                  onClick={() =>
                    setRegeln((alt) =>
                      alt.map((x) => (x.id === r.id ? { ...x, an: !x.an } : x)),
                    )
                  }
                >
                  {r.an ? 'Aus' : 'An'}
                </Button>
                <Button variant="ghost" onClick={() => setBearbeitet(r)}>
                  Ändern
                </Button>
                <Button variant="ghost" onClick={() => setZuLoeschen(r)}>
                  Löschen
                </Button>
              </div>
            </div>
          </Card>
        ))}
      </div>

      <Button
        onClick={() =>
          setBearbeitet({
            id: Math.max(0, ...regeln.map((r) => r.id)) + 1,
            name: '',
            an: true,
            bedingungen: [{ feld: 'typ', art: 'menge', werte: ['movie'] }],
            entscheidung: 'freigeben',
            hausbestand: false,
            begruendung: '',
          })
        }
      >
        Regel hinzufügen
      </Button>

      <ProbeKarte regeln={regeln} />

      <ConfirmDialog
        open={zuLoeschen !== null}
        title="Regel löschen?"
        description={
          zuLoeschen
            ? `„${zuLoeschen.name}" wird aus der Liste genommen. Anfragen, die sie ` +
              'bisher abgefangen hat, nehmen danach den normalen Weg.'
            : ''
        }
        confirmLabel="Löschen"
        onConfirm={() => {
          setRegeln((alt) => alt.filter((x) => x.id !== zuLoeschen?.id))
          setZuLoeschen(null)
        }}
        onCancel={() => setZuLoeschen(null)}
      />

      {bearbeitet && (
        <RegelFenster
          regel={bearbeitet}
          zielBeimFreigeben={zielBeimFreigeben}
          onSchliessen={() => setBearbeitet(null)}
          onSpeichern={(neu) => {
            setRegeln((alt) =>
              alt.some((r) => r.id === neu.id)
                ? alt.map((r) => (r.id === neu.id ? neu : r))
                : [...alt, neu],
            )
            setBearbeitet(null)
          }}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------

/** Drei gedachte Titel, an denen man die Liste sofort begreift. */
const PROBETITEL: { name: string; probe: Probe }[] = [
  {
    name: 'Ein mäßiger Actionfilm',
    probe: { typ: 'movie', genre: '28', bewertung: '4.2', stimmen: '1300', jahr: '2024' },
  },
  {
    name: 'Eine gefeierte Dokumentation',
    probe: { typ: 'movie', genre: '99', bewertung: '8.1', stimmen: '640', jahr: '2023' },
  },
  {
    name: 'Neue Science Fiction',
    probe: { typ: 'movie', genre: '878', bewertung: '7.6', stimmen: '210', jahr: '2026' },
  },
]

const PROBEFELDER = ['typ', 'genre', 'bewertung', 'stimmen', 'jahr', 'qualitaet', 'bestand']

function ProbeKarte({ regeln }: { regeln: Regel[] }) {
  const [probe, setProbe] = useState<Probe>(PROBETITEL[0].probe)

  const treffer = regeln.find((r) => r.an && passt(r, probe))

  return (
    <Section title="Probe" breit>
      <div className="space-y-3">
        <p className="max-w-3xl text-sm text-mist-400">
          Denk dir einen Titel aus und sieh nach, was passieren würde. Ändert nichts.
        </p>

        <div className="flex flex-wrap gap-2">
          {PROBETITEL.map((p) => (
            <Button key={p.name} variant="ghost" onClick={() => setProbe(p.probe)}>
              {p.name}
            </Button>
          ))}
        </div>

        <div className="flex flex-wrap gap-3">
          {PROBEFELDER.map((kennung) => {
            const feld = FELD[kennung]
            if (!feld) return null
            return (
              <label key={kennung} className="text-sm">
                <span className="mb-1 block opacity-70">{feld.name}</span>
                {feld.art === 'menge' ? (
                  <select
                    className={AUSWAHL}
                    value={probe[kennung] ?? ''}
                    onChange={(e) => setProbe((a) => ({ ...a, [kennung]: e.target.value }))}
                  >
                    <option value="">unbekannt</option>
                    {feld.werte?.map((w) => (
                      <option key={w.wert} value={w.wert}>
                        {w.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className={`${AUSWAHL} w-28`}
                    inputMode="decimal"
                    value={probe[kennung] ?? ''}
                    placeholder="unbekannt"
                    onChange={(e) => setProbe((a) => ({ ...a, [kennung]: e.target.value }))}
                  />
                )}
              </label>
            )
          })}
        </div>

        <div className="max-w-3xl rounded-2xl border border-ink-700 bg-ink-850/60 px-4 py-3 text-sm text-mist-300">
          {treffer ? (
            <>
              Es greift <b>{treffer.name}</b> — {folgeText(treffer)}.
              <div className="mt-1 text-xs opacity-60">
                Regel {regeln.indexOf(treffer) + 1} von {regeln.length}. Weiter unten wird
                nicht mehr gesucht.
              </div>
            </>
          ) : (
            <>
              <b>Keine Regel passt.</b>
              <div className="mt-1 text-xs opacity-60">
                Dann gilt alles wie bisher: die Freigabeeinstellung des Anfragenden, sein
                Kontingent, und die Entscheidung des Administrators.
              </div>
            </>
          )}
        </div>
      </div>
    </Section>
  )
}

// ---------------------------------------------------------------------------

function RegelFenster({
  regel,
  zielBeimFreigeben,
  onSchliessen,
  onSpeichern,
}: {
  regel: Regel
  /** Waehlt der Entscheider den Zielordner? Dann uebersteuert Sprosse 10. */
  zielBeimFreigeben: boolean
  onSchliessen: () => void
  onSpeichern: (r: Regel) => void
}) {
  const [entwurf, setEntwurf] = useState<Regel>(regel)

  function setzeBedingung(i: number, b: Bedingung) {
    setEntwurf((a) => ({ ...a, bedingungen: a.bedingungen.map((x, k) => (k === i ? b : x)) }))
  }

  const ungenutzt = FELDER.filter((f) => !entwurf.bedingungen.some((b) => b.feld === f.kennung))

  return (
    <Fenster
      offen
      titel={regel.name ? 'Regel ändern' : 'Neue Regel'}
      // ⚠️ `unterzeile` ist im Haus `font-mono` - dort stehen Pfade und
      // Profilnamen, keine Saetze. Ein Satz sah dort aus wie ein Fehler.
      unterzeile={regel.name || undefined}
      onSchliessen={onSchliessen}
      fuss={
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onSchliessen}>
            Abbrechen
          </Button>
          <Button
            onClick={() =>
              onSpeichern({ ...entwurf, name: entwurf.name.trim() || 'Namenlose Regel' })
            }
          >
            Übernehmen
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        <label className="block text-sm">
          <span className="mb-1 block opacity-70">Name</span>
          <input
            className={`${AUSWAHL} w-full`}
            value={entwurf.name}
            placeholder="Wofür ist die Regel da?"
            onChange={(e) => setEntwurf((a) => ({ ...a, name: e.target.value }))}
          />
        </label>

        <div className="space-y-2">
          <div className="text-xs uppercase tracking-wide opacity-60">Wenn</div>
          {entwurf.bedingungen.map((b, i) => (
            <BedingungZeile
              key={`${b.feld}-${i}`}
              bedingung={b}
              erste={i === 0}
              onAendern={(neu) => setzeBedingung(i, neu)}
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
                      ? { feld: feld.kennung, art: 'zahl', von: null, bis: null }
                      : { feld: feld.kennung, art: 'menge', werte: [] },
                  ],
                }))
              }}
            >
              <option value="">Bedingung hinzufügen …</option>
              {ungenutzt.map((f) => (
                <option key={f.kennung} value={f.kennung}>
                  {f.name}
                </option>
              ))}
            </select>
          )}
        </div>

        <div className="space-y-2">
          <div className="text-xs uppercase tracking-wide opacity-60">Dann</div>
          <Umschalter
            wert={entwurf.entscheidung}
            wahl={ENTSCHEIDUNGEN}
            onChange={(neu) => setEntwurf((a) => ({ ...a, entscheidung: neu }))}
            label={(e) => (e === 'freigeben' ? 'sofort freigeben' : 'ablehnen')}
          />

          {/* ⚠️ **Sprosse 10 schlägt Sprosse 13.** Wählt der Entscheider den
              Zielordner, wird „sofort freigeben" übersteuert - die Anfrage geht
              trotzdem an ihn, sonst käme sie an genau der Wahl vorbei, die er
              treffen soll. Das gilt heute schon für die Auto-Freigabe am Konto,
              fällt aber bei einer selbst geschriebenen Regel stärker auf.
              Im echten Bau erscheint dieser Kasten nur, wenn
              `approver_picks_target` für diese Art und Stufe eingeschaltet
              ist - sonst wäre er Rauschen für alle anderen Häuser. */}
          {zielBeimFreigeben && entwurf.entscheidung === 'freigeben' && (
            <div className="rounded-2xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-xs text-mist-300">
              <b className="text-warn-500">Greift bei euch nicht überall.</b> Weil in
              diesem Haus der Entscheider den Zielordner wählt, geht die Anfrage
              trotzdem an ihn — sonst käme sie an genau der Wahl vorbei, die er
              treffen soll. Ausgenommen sind Entscheider und Administratoren: Sie
              wählen gleich beim Anfragen, für sie gibt die Regel sofort frei.
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
                Auf den <b>Hausbestand</b> buchen
                <span className="block text-xs opacity-60">
                  Der Titel zählt dann bei niemandem — weder gegen die Stückzahl noch gegen
                  das Speicherkontingent.
                </span>
              </span>
            </label>
          )}

          {entwurf.entscheidung === 'ablehnen' && (
            <label className="block text-sm">
              <span className="mb-1 block opacity-70">Das liest der Anfragende</span>
              <input
                className={`${AUSWAHL} w-full`}
                value={entwurf.begruendung}
                placeholder={'Ohne Text steht dort nur „abgelehnt".'}
                onChange={(e) => setEntwurf((a) => ({ ...a, begruendung: e.target.value }))}
              />
            </label>
          )}
        </div>
      </div>
    </Fenster>
  )
}

function BedingungZeile({
  bedingung,
  erste,
  onAendern,
  onWeg,
}: {
  bedingung: Bedingung
  erste: boolean
  onAendern: (b: Bedingung) => void
  onWeg: () => void
}) {
  const feld = FELD[bedingung.feld]
  if (!feld) return null

  return (
    <div className="rounded-2xl border border-ink-700 px-4 py-3">
      <div className="flex items-center gap-2">
        <span className="w-10 shrink-0 text-xs opacity-50">{erste ? '' : 'und'}</span>
        <b className="text-sm">{feld.name}</b>
        <div className="flex-1" />
        <button type="button" className="text-xs opacity-60 hover:opacity-100" onClick={onWeg}>
          entfernen
        </button>
      </div>

      <div className="mt-2 pl-12">
        {bedingung.art === 'zahl' ? (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="opacity-70">von</span>
            <input
              className={`${AUSWAHL} w-24`}
              inputMode="decimal"
              value={bedingung.von ?? ''}
              placeholder="egal"
              onChange={(e) =>
                onAendern({
                  ...bedingung,
                  von: e.target.value === '' ? null : Number(e.target.value),
                })
              }
            />
            <span className="opacity-70">bis</span>
            <input
              className={`${AUSWAHL} w-24`}
              inputMode="decimal"
              value={bedingung.bis ?? ''}
              placeholder="egal"
              onChange={(e) =>
                onAendern({
                  ...bedingung,
                  bis: e.target.value === '' ? null : Number(e.target.value),
                })
              }
            />
            {feld.einheit && <span className="opacity-60">{feld.einheit}</span>}
          </div>
        ) : (
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {feld.werte?.map((w) => (
              <label key={w.wert} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  className="h-4 w-4 shrink-0 accent-accent-500"
                  checked={bedingung.werte.includes(w.wert)}
                  onChange={(e) =>
                    onAendern({
                      ...bedingung,
                      werte: e.target.checked
                        ? [...bedingung.werte, w.wert]
                        : bedingung.werte.filter((x) => x !== w.wert),
                    })
                  }
                />
                {w.name}
              </label>
            ))}
          </div>
        )}

        {feld.hinweis && <div className="mt-1 text-xs opacity-60">{feld.hinweis}</div>}
      </div>
    </div>
  )
}
