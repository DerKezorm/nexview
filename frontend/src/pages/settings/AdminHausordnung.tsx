/**
 * Die Hausordnung schreiben.
 *
 * Links das Textfeld mit einer Werkzeugleiste, rechts in Echtzeit das
 * Ergebnis – derselbe Anzeiger, den später alle sehen. Kein WYSIWYG im engen
 * Sinn: Du siehst die Auszeichnung im Text und daneben, was daraus wird.
 *
 * ⚠️ **Warum kein Editor-Paket.** Ein WYSIWYG liefert HTML, und wer HTML
 * anzeigen will, braucht `dangerouslySetInnerHTML` – genau die Linie, auf der
 * `services/csp.py` seine ganze Begründung aufbaut. Die Alternative wäre
 * gewesen, ProseMirror-JSON zu speichern und einen eigenen JSON→React-Anzeiger
 * zu bauen; dann hätte man 250 kB Paket **und** den Anzeiger. Siehe
 * `lib/auszeichnung.ts`.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import type { HausordnungBild, HausordnungVerwaltung } from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Hausordnungstext } from '../../components/Hausordnungstext'
import { bildAdresse } from '../../lib/hausordnungBild'
import { Button, Card, ErrorBanner, Spinner } from '../../components/ui'
import { auszeichnung } from '../../lib/auszeichnung'

/** Welche Bildnamen kommen im Text vor? */
function verwendeteBilder(text: string): Set<string> {
  const namen = new Set<string>()
  for (const block of auszeichnung(text)) {
    if (block.art === 'bild') namen.add(block.name)
  }
  return namen
}

type Werkzeug = {
  schluessel: string
  /** Was links und rechts um die Auswahl gelegt wird. */
  vor: string
  nach?: string
  /** Steht am Zeilenanfang statt um die Auswahl herum. */
  zeilenanfang?: boolean
}

const WERKZEUGE: Werkzeug[] = [
  { schluessel: 'fett', vor: '**', nach: '**' },
  { schluessel: 'kursiv', vor: '*', nach: '*' },
  { schluessel: 'code', vor: '`', nach: '`' },
  { schluessel: 'ueberschrift', vor: '## ', zeilenanfang: true },
  { schluessel: 'liste', vor: '- ', zeilenanfang: true },
  { schluessel: 'nummeriert', vor: '1. ', zeilenanfang: true },
  { schluessel: 'zitat', vor: '> ', zeilenanfang: true },
  { schluessel: 'verweis', vor: '[', nach: '](https://)' },
  { schluessel: 'trennlinie', vor: '\n---\n', zeilenanfang: true },
]

/** Ein Haken mit Beschriftung und einem Satz darunter. */
function Haken({
  gewaehlt,
  onWahl,
  titel,
  hinweis,
}: {
  gewaehlt: boolean
  onWahl: (wert: boolean) => void
  titel: string
  hinweis: string
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2.5">
      <input
        type="checkbox"
        checked={gewaehlt}
        onChange={(e) => onWahl(e.target.checked)}
        className="mt-1 h-4 w-4 shrink-0 accent-accent-500"
      />
      <span className="text-sm">
        <span className="font-medium text-mist-200">{titel}</span>
        <span className="mt-0.5 block text-mist-500">{hinweis}</span>
      </span>
    </label>
  )
}


export function AdminHausordnung() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const feld = useRef<HTMLTextAreaElement>(null)

  const [entwurf, setzeEntwurf] = useState<HausordnungVerwaltung | null>(null)
  const [erneutLesen, setzeErneutLesen] = useState(false)
  const [fehler, setzeFehler] = useState<string | null>(null)
  const [gespeichert, setzeGespeichert] = useState(false)
  const [loeschenOffen, setzeLoeschenOffen] = useState(false)
  // Auf schmalen Geräten steht beides untereinander – ein Umschalter ist dort
  // ehrlicher als zwei halb sichtbare Spalten.
  const [ansicht, setzeAnsicht] = useState<'schreiben' | 'ansehen'>('schreiben')

  const stand = useQuery({
    queryKey: ['hausordnung-verwaltung'],
    queryFn: () => api.get<HausordnungVerwaltung>('/api/hausordnung/verwaltung'),
  })
  const bilder = useQuery({
    queryKey: ['hausordnung-bilder'],
    queryFn: () => api.get<HausordnungBild[]>('/api/hausordnung/bilder'),
  })

  // Der Entwurf lebt in der Seite, nicht in der Abfrage: Sonst überschriebe
  // jedes Auffrischen im Hintergrund, was gerade getippt wird.
  const werte = entwurf ?? stand.data ?? null
  const setzen = (teil: Partial<HausordnungVerwaltung>) => {
    if (!werte) return
    setzeGespeichert(false)
    setzeEntwurf({ ...werte, ...teil })
  }

  const speichern = useMutation({
    mutationFn: () =>
      api.put<HausordnungVerwaltung>('/api/hausordnung/verwaltung', {
        titel: werte?.titel ?? '',
        inhalt: werte?.inhalt ?? '',
        quittierbar: werte?.quittierbar ?? true,
        veroeffentlicht: werte?.veroeffentlicht ?? false,
        erneut_lesen: erneutLesen,
      }),
    onSuccess: (neu) => {
      setzeFehler(null)
      setzeEntwurf(null)
      setzeErneutLesen(false)
      setzeGespeichert(true)
      queryClient.setQueryData(['hausordnung-verwaltung'], neu)
      // Der Knopf und der Fußzeilen-Verweis hängen an der Konfiguration.
      void queryClient.invalidateQueries({ queryKey: ['config'] })
    },
    onError: (f: unknown) => setzeFehler(f instanceof Error ? f.message : String(f)),
  })

  const hochladen = useMutation({
    mutationFn: (datei: File) => {
      const daten = new FormData()
      daten.append('datei', datei)
      return api.upload<HausordnungBild>('/api/hausordnung/bilder', daten)
    },
    onSuccess: () => {
      setzeFehler(null)
      void queryClient.invalidateQueries({ queryKey: ['hausordnung-bilder'] })
    },
    onError: (f: unknown) => setzeFehler(f instanceof Error ? f.message : String(f)),
  })

  const bildLoeschen = useMutation({
    mutationFn: (name: string) => api.delete(`/api/hausordnung/bilder/${name}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['hausordnung-bilder'] }),
  })

  const alleLoeschen = useMutation({
    mutationFn: () => api.delete('/api/hausordnung/verwaltung'),
    onSuccess: () => {
      setzeEntwurf(null)
      setzeLoeschenOffen(false)
      void queryClient.invalidateQueries({ queryKey: ['hausordnung-verwaltung'] })
      void queryClient.invalidateQueries({ queryKey: ['hausordnung-bilder'] })
      void queryClient.invalidateQueries({ queryKey: ['config'] })
    },
  })

  /** Auszeichnung um die Auswahl legen – oder an den Zeilenanfang setzen. */
  const einfuegen = (werkzeug: Werkzeug) => {
    const el = feld.current
    if (!el || !werte) return

    const { selectionStart: von, selectionEnd: bis } = el
    const text = werte.inhalt

    if (werkzeug.zeilenanfang) {
      const anfang = text.lastIndexOf('\n', von - 1) + 1
      const neu = text.slice(0, anfang) + werkzeug.vor + text.slice(anfang)
      setzen({ inhalt: neu })
      queueMicrotask(() => {
        el.focus()
        el.setSelectionRange(von + werkzeug.vor.length, bis + werkzeug.vor.length)
      })
      return
    }

    const ausgewaehlt = text.slice(von, bis)
    const neu =
      text.slice(0, von) + werkzeug.vor + ausgewaehlt + (werkzeug.nach ?? '') + text.slice(bis)
    setzen({ inhalt: neu })
    queueMicrotask(() => {
      el.focus()
      // Ohne Auswahl landet die Schreibmarke zwischen den Zeichen – sonst
      // müsste man nach jedem Klick erst zurücktippen.
      el.setSelectionRange(von + werkzeug.vor.length, von + werkzeug.vor.length + ausgewaehlt.length)
    })
  }

  const bildEinfuegen = (name: string) => {
    const el = feld.current
    if (!el || !werte) return
    const marke = `\n![](bild:${name})\n`
    const von = el.selectionStart
    setzen({ inhalt: werte.inhalt.slice(0, von) + marke + werte.inhalt.slice(von) })
    queueMicrotask(() => {
      el.focus()
      // Die Schreibmarke landet in den eckigen Klammern: Dort gehört der
      // Bildtext hin, und ohne ihn steht bei fehlendem Bild später nur ein
      // nackter Platzhalter.
      el.setSelectionRange(von + 3, von + 3)
    })
  }

  const vorlageEinfuegen = () => {
    setzen({
      titel: werte?.titel || t('hausordnungAdmin.vorlageTitel'),
      inhalt: t('hausordnungAdmin.vorlageText'),
    })
  }

  if (stand.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }
  if (!werte) return <ErrorBanner message={t('hausordnungAdmin.nichtGeladen')} />

  const verwendet = verwendeteBilder(werte.inhalt)
  const vorhanden = new Set((bilder.data ?? []).map((b) => b.name))
  const fehlende = [...verwendet].filter((name) => !vorhanden.has(name))
  const ungenutzt = (bilder.data ?? []).filter((b) => !verwendet.has(b.name))
  // ⚠️ **Der Haken zählt mit.** Er steckt nicht im Entwurf, sondern daneben -
  // er beschreibt ja keine Eigenschaft des Textes, sondern eine einmalige
  // Handlung. Ohne ihn hier wäre „alle müssen erneut lesen" allein nicht
  // speicherbar: Wer nichts am Text ändert, sondern nur alle zurückholen
  // will, stünde vor einem ausgegrauten Knopf.
  const geaendert = entwurf !== null || erneutLesen

  return (
    <div className="flex flex-col gap-5">
      {fehler && <ErrorBanner message={fehler} />}

      <Card className="flex flex-col gap-4 p-5">
        <div>
          <h2 className="text-lg font-semibold">{t('hausordnungAdmin.title')}</h2>
          <p className="mt-1 text-sm text-mist-500">{t('hausordnungAdmin.intro')}</p>
        </div>

        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium">{t('hausordnungAdmin.titelLabel')}</span>
          <input
            value={werte.titel}
            onChange={(e) => setzen({ titel: e.target.value })}
            placeholder={t('hausordnungAdmin.titelPlatzhalter')}
            maxLength={120}
            className="rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm outline-none focus:border-accent-500"
          />
        </label>

        {/* Auf schmalen Geräten untereinander, sonst nebeneinander. */}
        <div className="flex gap-2 lg:hidden">
          <Button
            variant={ansicht === 'schreiben' ? 'primary' : 'ghost'}
            onClick={() => setzeAnsicht('schreiben')}
          >
            {t('hausordnungAdmin.schreiben')}
          </Button>
          <Button
            variant={ansicht === 'ansehen' ? 'primary' : 'ghost'}
            onClick={() => setzeAnsicht('ansehen')}
          >
            {t('hausordnungAdmin.ansehen')}
          </Button>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div
            className={`flex flex-col gap-2 ${ansicht === 'ansehen' ? 'hidden lg:flex' : ''}`}
          >
            <div className="flex flex-wrap gap-1">
              {WERKZEUGE.map((werkzeug) => (
                <button
                  key={werkzeug.schluessel}
                  type="button"
                  onClick={() => einfuegen(werkzeug)}
                  title={t(`hausordnungAdmin.werkzeug.${werkzeug.schluessel}`)}
                  className="rounded-md border border-ink-700 bg-ink-900 px-2.5 py-1 text-xs text-mist-300 transition-colors hover:border-accent-500 hover:text-mist-100"
                >
                  {t(`hausordnungAdmin.werkzeug.${werkzeug.schluessel}`)}
                </button>
              ))}
            </div>
            <textarea
              ref={feld}
              value={werte.inhalt}
              onChange={(e) => setzen({ inhalt: e.target.value })}
              rows={20}
              maxLength={50000}
              placeholder={t('hausordnungAdmin.textPlatzhalter')}
              className="w-full rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-sm leading-relaxed outline-none focus:border-accent-500"
            />
            <div className="flex items-center justify-between text-xs text-mist-600">
              <span>{t('hausordnungAdmin.zeichen', { count: werte.inhalt.length })}</span>
              <button
                type="button"
                onClick={vorlageEinfuegen}
                className="underline underline-offset-2 hover:text-mist-300"
              >
                {t('hausordnungAdmin.vorlage')}
              </button>
            </div>
          </div>

          <div className={`flex flex-col gap-2 ${ansicht === 'schreiben' ? 'hidden lg:flex' : ''}`}>
            <p className="text-xs font-medium uppercase tracking-wide text-mist-600">
              {t('hausordnungAdmin.vorschau')}
            </p>
            <div className="min-h-[20rem] rounded-lg border border-ink-700 bg-ink-950/60 p-4">
              {werte.inhalt.trim() ? (
                <Hausordnungstext text={werte.inhalt} />
              ) : (
                <p className="text-sm text-mist-600">{t('hausordnungAdmin.nochLeer')}</p>
              )}
            </div>
          </div>
        </div>
      </Card>

      {/* --- Bilder ------------------------------------------------------ */}
      <Card className="flex flex-col gap-3 p-5">
        <div>
          <h3 className="font-semibold">{t('hausordnungAdmin.bilderTitel')}</h3>
          <p className="mt-1 text-sm text-mist-500">{t('hausordnungAdmin.bilderHinweis')}</p>
        </div>

        <label className="w-fit cursor-pointer rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm transition-colors hover:border-accent-500">
          {hochladen.isPending ? t('common.loading') : t('hausordnungAdmin.bildWaehlen')}
          <input
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            className="hidden"
            onChange={(e) => {
              const datei = e.target.files?.[0]
              if (datei) hochladen.mutate(datei)
              e.target.value = ''
            }}
          />
        </label>

        {fehlende.length > 0 && (
          // ⚠️ Der Fall nach einer Wiederherstellung: Der Text verweist auf
          // Bilder, die nicht mitgekommen sind. Ohne diesen Hinweis merkt es
          // niemand – der Betreiber ruft seine eigene Hausordnung selten auf.
          <p className="rounded-lg border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-sm text-warn-500">
            {t('hausordnungAdmin.bilderFehlen', { count: fehlende.length })}
          </p>
        )}

        {(bilder.data ?? []).length === 0 ? (
          <p className="text-sm text-mist-600">{t('hausordnungAdmin.keineBilder')}</p>
        ) : (
          <ul className="flex flex-wrap gap-3">
            {(bilder.data ?? []).map((bild) => (
              <li
                key={bild.name}
                className="flex w-36 flex-col gap-1.5 rounded-lg border border-ink-700 p-2"
              >
                <img
                  src={bildAdresse(bild.name)}
                  alt=""
                  className="h-20 w-full rounded object-cover"
                />
                <div className="flex items-center justify-between gap-1">
                  <button
                    type="button"
                    onClick={() => bildEinfuegen(bild.name)}
                    className="text-xs text-accent-400 underline underline-offset-2"
                  >
                    {t('hausordnungAdmin.einfuegen')}
                  </button>
                  <button
                    type="button"
                    onClick={() => bildLoeschen.mutate(bild.name)}
                    className="text-xs text-mist-600 underline underline-offset-2 hover:text-bad-500"
                  >
                    {t('common.delete')}
                  </button>
                </div>
                {!verwendet.has(bild.name) && (
                  // Nicht automatisch aufräumen: Wer einen Absatz kurz
                  // herausnimmt, hätte sonst sein Bild verloren.
                  <span className="text-[11px] text-mist-600">
                    {t('hausordnungAdmin.ungenutzt')}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        {ungenutzt.length > 0 && (
          <p className="text-xs text-mist-600">
            {t('hausordnungAdmin.ungenutztHinweis', { count: ungenutzt.length })}
          </p>
        )}
      </Card>

      {/* --- Schalter und Speichern -------------------------------------- */}
      <Card className="flex flex-col gap-4 p-5">
        <Haken
          gewaehlt={werte.quittierbar}
          onWahl={(wert) => setzen({ quittierbar: wert })}
          titel={t('hausordnungAdmin.quittierbar')}
          hinweis={t('hausordnungAdmin.quittierbarHinweis')}
        />
        <Haken
          gewaehlt={werte.veroeffentlicht}
          onWahl={(wert) => setzen({ veroeffentlicht: wert })}
          titel={t('hausordnungAdmin.veroeffentlicht')}
          hinweis={t('hausordnungAdmin.veroeffentlichtHinweis')}
        />

        <label className="flex cursor-pointer items-start gap-2.5">
          <input
            type="checkbox"
            checked={erneutLesen}
            onChange={(e) => setzeErneutLesen(e.target.checked)}
            className="mt-1 h-4 w-4 shrink-0 accent-accent-500"
          />
          <span className="text-sm">
            <span className="font-medium text-mist-200">{t('hausordnungAdmin.erneutLesen')}</span>
            {/* Was der Haken auslöst, in Zahlen – nicht in Worten. */}
            <span className="mt-0.5 block text-mist-500">
              {t('hausordnungAdmin.erneutLesenHinweis', { count: werte.betroffene_konten })}
            </span>
          </span>
        </label>

        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={() => speichern.mutate()}
            disabled={speichern.isPending || !geaendert}
          >
            {speichern.isPending ? t('common.loading') : t('common.save')}
          </Button>
          {gespeichert && !geaendert && (
            <span className="text-sm text-ok-500">{t('hausordnungAdmin.gespeichert')}</span>
          )}
          {/* ⚠️ **Der Fehler gehört an den Knopf, nicht an den Seitenanfang.**
              Oben stand er auch schon – nur ist bis dorthin drei Bildschirme
              weit gescrollt, wenn man unten auf „Speichern" drückt. Es sah
              deshalb so aus, als passiere einfach nichts. Genau so gemeldet. */}
          {speichern.isError && (
            <span className="text-sm text-bad-500">
              {speichern.error instanceof Error
                ? speichern.error.message
                : t('hausordnungAdmin.nichtGespeichert')}
            </span>
          )}
          <button
            type="button"
            onClick={() => setzeLoeschenOffen(true)}
            className="ml-auto text-sm text-mist-600 underline underline-offset-2 hover:text-bad-500"
          >
            {t('hausordnungAdmin.loeschen')}
          </button>
        </div>
      </Card>

      <ConfirmDialog
        open={loeschenOffen}
        title={t('hausordnungAdmin.loeschenTitel')}
        description={t('hausordnungAdmin.loeschenText')}
        confirmLabel={t('hausordnungAdmin.loeschen')}
        onConfirm={() => alleLoeschen.mutate()}
        onCancel={() => setzeLoeschenOffen(false)}
      />
    </div>
  )
}
