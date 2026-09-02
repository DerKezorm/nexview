/**
 * Bestehende Konten eines Medienservers nach Nexview holen.
 *
 * ⚠️ **Warum die Liste so aussieht, wie sie aussieht.** Der teuerste Fehler
 * hier ist nicht ein vergessenes Häkchen, sondern ein falsch zugeordnetes
 * Konto: Nexview kennt kein Zusammenführen zweier Konten. Ein Duplikat heißt
 * zwei Kontingente, zwei Anfragelisten, zwei Favoritenlisten, und der Weg
 * zurück ist Löschen samt allem, was daran hängt.
 *
 * Daraus folgen drei Entscheidungen, die man der Oberfläche ansehen soll:
 *
 * 1. **Nichts ist vorausgewählt.** Ein Knopf, der dreißig Konten anlegt, muss
 *    dreißig bewusste Häkchen kosten.
 * 2. **„Verknüpfen" ist nie die Vorgabe.** Die Auswahl steht immer auf „neues
 *    Konto anlegen". Eine falsche Verknüpfung gäbe einem Fremden Zugang zum
 *    Konto einer anderen Person, und das darf nicht durch Wegklicken
 *    entstehen.
 * 3. **Jedes Konto in der Auswahl sagt, woran es schon hängt** - „oma (Plex)"
 *    statt bloß „oma". Beim zweiten Import ist das der einzige Hinweis
 *    darauf, dass ein Mensch bereits ein Konto hat: Über die Anbieter hinweg
 *    gibt es kein verlässliches Merkmal, mit dem Nexview ihn selbst erkennen
 *    könnte. Plex kennt eine Mailadresse, Jellyfin und Emby haben
 *    grundsätzlich keine, und derselbe Mensch heißt auf zwei Servern häufig
 *    verschieden.
 *
 * ⚠️ **Ein Abgleich über ähnliche Namen steht hier bewusst nicht.** Er würde
 * Sicherheit vortäuschen: Wer die Zeile „kein Gegenstück" liest, hakt sie
 * durch - und legt genau dabei das zweite Konto an.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Fenster } from '../../components/Fenster'
import { AUSWAHL, Button } from '../../components/ui'
import type { Kontingentwert } from '../../api/types'

// ⚠️ **Farben aus der Haus-Palette, nicht aus der Textskala.** Hier standen
// zuerst `bg-mist-950` und `border-mist-700`: im dunklen Modus unauffällig, im
// hellen ein schwarzer Kasten auf weißem Grund. `ink-*` dreht sich mit dem
// Modus (aus `--color-ink-900` wird im hellen Weiß), `mist-*` nicht.

type Kandidat = {
  account_id: string
  username: string
  email: string | null
  schon_verknuepft: boolean
  gehoert_zu: string | null
  gesperrt: boolean
}

type Zuordenbar = {
  user_id: number
  username: string
  verknuepft_mit: string[]
}

type Vorlage = {
  provider: string
  kandidaten: Kandidat[]
  zuordenbar: Zuordenbar[]
}

type Ergebnis = {
  angelegt: number
  verknuepft: number
  aufgehoben: number
  abgelehnt: Record<string, string>
}

/** „neu" heißt: ein Konto anlegen. Sonst die Nummer des Zielkontos. */
type Wahl = 'neu' | number

/**
 * Eine Grenze hat drei Zustände, und ein leeres Zahlenfeld kann sie nicht
 * auseinanderhalten.
 *
 * ⚠️ Dieselbe Form wie in `AdminUsersSettings` (`Grenzentwurf`), und aus
 * demselben Grund: „Hausvorgabe", „unbegrenzt" und „genau diese Zahl" sind
 * drei verschiedene Aussagen. Die **0 heißt „darf nichts"** - genau diese
 * Verwechslung hat in 0.26.2 einen Fehler gekostet, bei dem sich eine gesetzte
 * 0 bei jedem Start in „unbegrenzt" zurückverwandelte.
 *
 * Die getippte Zahl bleibt beim Wechsel auf „Hausvorgabe" erhalten, sonst
 * verliert man sie beim Hin- und Herschalten.
 */
type Grenze = { modus: 'standard' | 'unlimited' | 'zahl'; zahl: string }

const GRENZE_START: Grenze = { modus: 'standard', zahl: '' }

function alsWert(grenze: Grenze): Kontingentwert {
  if (grenze.modus !== 'zahl') return grenze.modus
  const zahl = Number(grenze.zahl)
  // Unbrauchbare Eingabe fällt auf die Hausvorgabe zurück statt auf 0 - eine
  // stillschweigende 0 hieße „darf nichts" und wäre die härtere Aussage.
  if (grenze.zahl.trim() === '' || !Number.isInteger(zahl) || zahl < 0) return 'standard'
  return zahl
}

const ANBIETER: Record<string, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  emby: 'Emby',
}

function beschriftung(eintrag: Zuordenbar): string {
  if (eintrag.verknuepft_mit.length === 0) return eintrag.username
  const wo = eintrag.verknuepft_mit.map((p) => ANBIETER[p] ?? p).join(', ')
  return `${eintrag.username} (${wo})`
}

function Grenzfeld({
  titel,
  einheit,
  wert,
  setzen,
}: {
  titel: string
  einheit: string
  wert: Grenze
  setzen: (g: Grenze) => void
}) {
  const { t } = useTranslation()
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-mist-600">{titel}</span>
      <div className="flex items-center gap-2">
        <select
          aria-label={titel}
          className={AUSWAHL}
          value={wert.modus}
          onChange={(e) => setzen({ ...wert, modus: e.target.value as Grenze['modus'] })}
        >
          <option value="standard">{t('mediaserverImport.limitStandard')}</option>
          <option value="unlimited">{t('mediaserverImport.limitUnlimited')}</option>
          <option value="zahl">{t('mediaserverImport.limitNumber')}</option>
        </select>
        {wert.modus === 'zahl' && (
          <>
            <input
              type="number"
              min={0}
              aria-label={t('mediaserverImport.limitNumberFor', { titel })}
              className={`${AUSWAHL} w-24`}
              value={wert.zahl}
              onChange={(e) => setzen({ ...wert, zahl: e.target.value })}
            />
            <span className="text-xs text-mist-600">{einheit}</span>
          </>
        )}
      </div>
    </label>
  )
}

export default function MedienserverImport({ provider }: { provider: string }) {
  const { t } = useTranslation()
  const [offen, setOffen] = useState(false)
  const [angehakt, setAngehakt] = useState<Record<string, boolean>>({})
  const [wahl, setWahl] = useState<Record<string, Wahl>>({})
  const [ergebnis, setErgebnis] = useState<Ergebnis | null>(null)
  const [filme, setFilme] = useState<Grenze>(GRENZE_START)
  const [serien, setSerien] = useState<Grenze>(GRENZE_START)
  const [speicher, setSpeicher] = useState<Grenze>(GRENZE_START)
  const [aktiv, setAktiv] = useState(true)
  // Die Rückfrage vor dem Übernehmen, wenn Gesperrte dabei sind.
  const [rueckfrage, setRueckfrage] = useState<string[] | null>(null)
  const queryClient = useQueryClient()

  const { data, isLoading, error } = useQuery<Vorlage>({
    queryKey: ['import-kandidaten', provider],
    queryFn: () => api.get<Vorlage>(`/api/admin/mediaserver/${provider}/import-kandidaten`),
    // Erst holen, wenn jemand hinsieht: Der Aufruf geht nach draußen und kann
    // dauern. Ihn beim Öffnen der Seite zu stellen, ließe sie hängen.
    enabled: offen,
    // Nicht aus dem Zwischenspeicher: Wer die Liste öffnet, will den Stand von
    // jetzt, nicht den von vor zehn Minuten.
    staleTime: 0,
  })

  const uebernehmen = useMutation({
    mutationFn: (wuensche: unknown[]) =>
      api.post<Ergebnis>(`/api/admin/mediaserver/${provider}/import`, {
        wuensche,
        filme: alsWert(filme),
        serien: alsWert(serien),
        speicher_gb: alsWert(speicher),
        aktiv,
      }),
    onSuccess: (antwort) => {
      setErgebnis(antwort)
      setAngehakt({})
      // Die Liste neu holen: Was gerade übernommen wurde, steht danach unter
      // „schon da" - und die zuordenbaren Konten sind andere geworden.
      void queryClient.invalidateQueries({ queryKey: ['import-kandidaten', provider] })
      // Und die Benutzerverwaltung: Dort sind jetzt Konten, die es vorher
      // nicht gab. Ohne das steht sie bis zum Neuladen auf dem alten Stand.
      void queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })

  function starten() {
    const gewaehlt = offeneZeilen.filter((z) => angehakt[z.account_id])
    uebernehmen.mutate(
      gewaehlt.map((z) => ({
        account_id: z.account_id,
        username: z.username,
        email: z.email,
        user_id:
          wahl[z.account_id] === undefined || wahl[z.account_id] === 'neu'
            ? null
            : wahl[z.account_id],
        // Wer hier ankommt, hat die Rückfrage bestätigt - sonst wäre sie gar
        // nicht gestartet worden.
        trotz_sperre: z.gesperrt,
      })),
    )
  }

  const name = ANBIETER[provider] ?? provider
  const offeneZeilen = (data?.kandidaten ?? []).filter((k) => !k.schon_verknuepft)
  const anzahlGewaehlt = Object.values(angehakt).filter(Boolean).length
  const bekannteZeilen = (data?.kandidaten ?? []).filter((k) => k.schon_verknuepft)

  return (
    <>
      <button
        type="button"
        onClick={() => setOffen(true)}
        className="self-start rounded-lg border border-ink-700 px-3 py-2 text-sm text-mist-200 hover:border-accent-500"
      >
        {t('mediaserverImport.button', { name })}
      </button>

      {/* ⚠️ **Ohne Fußzeile, deshalb genau ein Ausgang.** ``Fenster`` blendet
          seinen eigenen Schließen-Knopf aus, sobald unten Knöpfe stehen -
          sonst gäbe es zwei Ausgänge mit derselben Wirkung. Sobald das
          Übernehmen dazukommt, gehört es in ``fuss`` und der Knopf oben
          verschwindet von selbst. */}
      <Fenster
        offen={offen}
        titel={t('mediaserverImport.title', { name })}
        unterzeile={
          data
            ? t('mediaserverImport.subtitle', { count: data.kandidaten.length })
            : undefined
        }
        onSchliessen={() => setOffen(false)}
        fuss={
          <>
            <Button variant="ghost" onClick={() => setOffen(false)}>
              {t('mediaserverImport.close')}
            </Button>
            <Button
              onClick={() => {
                const gewaehlt = offeneZeilen.filter((z) => angehakt[z.account_id])
                const gesperrte = gewaehlt.filter((z) => z.gesperrt)
                // ⚠️ Erst fragen, dann tun. Eine Sperre steht dort, weil jemand
                // dieses Konto gelöscht hat; sie aufzuheben kann richtig sein,
                // darf aber keine Nebenwirkung eines Häkchens sein.
                if (gesperrte.length > 0) {
                  setRueckfrage(gesperrte.map((z) => z.username))
                  return
                }
                starten()
              }}
              disabled={anzahlGewaehlt === 0}
              loading={uebernehmen.isPending}
            >
              {anzahlGewaehlt === 0
                ? t('mediaserverImport.submit')
                : t('mediaserverImport.submitCount', { count: anzahlGewaehlt })}
            </Button>
          </>
        }
      >

        <div className="flex flex-col gap-4">
      {isLoading && (
        <p className="text-sm text-mist-500">{t('mediaserverImport.loading', { name })}</p>
      )}

      {/* ⚠️ Ein Ausfall darf nicht wie eine leere Liste aussehen. „Kann ich
          gerade nicht" und „da ist niemand" sind verschiedene Auskünfte, und
          wer sie verwechselt, sucht den Fehler bei sich. */}
      {error && (
        <p className="rounded-lg border border-rose-800/60 bg-rose-950/30 p-3 text-sm text-rose-200">
          {t('mediaserverImport.loadFailed', { grund: (error as Error).message })}
        </p>
      )}

      {data && data.kandidaten.length === 0 && (
        <p className="text-sm text-mist-500">
          {t('mediaserverImport.empty', { name })}
        </p>
      )}

      {offeneZeilen.length > 0 && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-4">
            <p className="text-xs uppercase tracking-wide text-mist-600">
              {t('mediaserverImport.groupOpen')}
            </p>
            {/* Einmal über der Spalte statt vor jedem Feld - vor jeder Zeile
                stand dasselbe Wort und verdrängte den Namen daneben. */}
            <p className="text-xs uppercase tracking-wide text-mist-600">
              {t('mediaserverImport.groupColumn')}
            </p>
          </div>
          {offeneZeilen.map((zeile) => (
            <div
              key={zeile.account_id}
              className="flex flex-wrap items-center gap-3 border-b border-ink-800 py-2 last:border-b-0"
            >
              {/* Das ganze Namensfeld ist die Schaltfläche, nicht nur das
                  Kästchen - bei dreißig Zeilen ist ein 16 Pixel großes Ziel
                  die falsche Zumutung. */}
              <label className="flex flex-1 cursor-pointer items-center gap-3">
                <input
                  type="checkbox"
                  checked={!!angehakt[zeile.account_id]}
                  onChange={(e) =>
                    setAngehakt((v) => ({ ...v, [zeile.account_id]: e.target.checked }))
                  }
                  className="h-4 w-4 shrink-0 accent-accent-500"
                />
                <span className="min-w-0 truncate text-sm text-mist-100">
                  {zeile.username}
                </span>
                {zeile.email && (
                  <span className="min-w-0 truncate text-xs text-mist-600">{zeile.email}</span>
                )}
                {/* ⚠️ Nur ein Abzeichen, keine zweite Schaltfläche in der
                    Zeile. Ein Kästchen dort brach die Zeile um und schob die
                    Zuordnung darunter; gefragt wird stattdessen einmal beim
                    Übernehmen, wo die Entscheidung ohnehin fällt. */}
                {zeile.gesperrt && (
                  <span className="shrink-0 rounded-full border border-amber-700/60 bg-amber-950/30 px-2 py-0.5 text-[11px] text-amber-200">
                    {t('mediaserverImport.blocked')}
                  </span>
                )}
              </label>
              {/* ⚠️ Beschriftung über dem Feld statt in jeder Option: Sonst
                  stand „verknüpfen mit:" in jeder Zeile noch einmal und
                  verdrängte den Namen, auf den es ankommt. */}
              <select
                aria-label={t('mediaserverImport.assignFor', { name: zeile.username })}
                // Ohne Haken gibt es nichts zu entscheiden - ein Feld, das
                // dann trotzdem bedienbar wäre, lädt zum Einstellen von etwas
                // ein, das nicht passiert.
                disabled={!angehakt[zeile.account_id]}
                value={String(wahl[zeile.account_id] ?? 'neu')}
                onChange={(e) =>
                  setWahl((v) => ({
                    ...v,
                    [zeile.account_id]:
                      e.target.value === 'neu' ? 'neu' : Number(e.target.value),
                  }))
                }
                className={AUSWAHL}
              >
                {/* Immer zuerst und immer die Vorgabe - siehe Kopf der Datei. */}
                <option value="neu">{t('mediaserverImport.createNew')}</option>
                {(data?.zuordenbar ?? []).map((konto) => (
                  <option key={konto.user_id} value={konto.user_id}>
                    {beschriftung(konto)}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}

      {bekannteZeilen.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-xs uppercase tracking-wide text-mist-600">
            {t('mediaserverImport.groupKnown')}
          </p>
          {bekannteZeilen.map((zeile) => (
            <div
              key={zeile.account_id}
              className="flex items-center gap-3 border-b border-ink-800 py-2 opacity-60 last:border-b-0"
            >
              <span className="flex-1 text-sm text-mist-200">{zeile.username}</span>
              <span className="text-sm text-mist-500">
                {t('mediaserverImport.linkedWith', { name: zeile.gehoert_zu })}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Noch kein Ausführen: Diese Stufe zeigt die Entscheidung, sie führt sie
          nicht aus. Der Knopf kommt, wenn die Liste steht. */}
      {/* ⚠️ **Diese Werte gelten nur für neu angelegte Konten**, und der Satz
          darüber sagt es, weil man es der Zeile sonst nicht ansieht. Ein
          verknüpftes Konto behält seine Grenzen: Wer sie einmal gesetzt hat,
          hat das bewusst getan, und ein Import, der sie nebenbei
          überschreibt, wäre ein Datenverlust, den niemand angefordert hat. */}
      {offeneZeilen.length > 0 && (
        <div className="flex flex-col gap-3 border-t border-ink-700 pt-4">
          <p className="text-xs uppercase tracking-wide text-mist-600">
            {t('mediaserverImport.defaultsTitle')}
          </p>
          <div className="flex flex-wrap gap-4">
            <Grenzfeld
              titel={t('mediaserverImport.quotaMovies')}
              einheit={t('mediaserverImport.unitPieces')}
              wert={filme}
              setzen={setFilme}
            />
            <Grenzfeld
              titel={t('mediaserverImport.quotaSeries')}
              einheit={t('mediaserverImport.unitPieces')}
              wert={serien}
              setzen={setSerien}
            />
            <Grenzfeld
              titel={t('mediaserverImport.quotaStorage')}
              einheit={t('mediaserverImport.unitGb')}
              wert={speicher}
              setzen={setSpeicher}
            />
          </div>
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              checked={aktiv}
              onChange={(e) => setAktiv(e.target.checked)}
              className="h-4 w-4 shrink-0 accent-accent-500"
            />
            <span className="text-sm text-mist-200">
              {t('mediaserverImport.activeLabel')}
              <span className="ml-2 text-xs text-mist-600">
                {t('mediaserverImport.activeHint')}
              </span>
            </span>
          </label>
          <p className="text-xs text-mist-600">
            {t('mediaserverImport.fixedNote')}
          </p>
          {/* ⚠️ Gemeldet, weil genau das gefehlt hat: Ohne den Hinweis setzt man
              dem neuen Konto ein Passwort - und läuft damit in die
              Adressbestätigung, die ein Konto ohne Adresse nicht bestehen kann. */}
          <p className="text-xs text-mist-600">
            <Trans i18nKey="mediaserverImport.noPasswordNote" values={{ name }}>
              <b /><b />
            </Trans>
          </p>
        </div>
      )}

      {/* ⚠️ **Die Zusammenfassung ist kein Beiwerk.** Der erste Import ist
          harmlos, jeder weitere ist der gefährliche: Dort entstehen Duplikate,
          wenn jemand „neues Konto anlegen" stehen lässt, obwohl die Person
          schon eines hat. Zwei Zahlen nebeneinander lassen das auffallen -
          „4 angelegt" wo man 1 erwartet hat, ist der einzige Hinweis, den es
          gibt. */}
      {ergebnis && (
        <div className="rounded-lg border border-ink-700 p-3 text-sm">
          <p className="text-mist-100">
            {ergebnis.aufgehoben > 0
              ? t('mediaserverImport.resultCreatedWithBlocks', ergebnis)
              : t('mediaserverImport.resultCreated', ergebnis)}
          </p>
          {Object.keys(ergebnis.abgelehnt).length > 0 && (
            <ul className="mt-2 flex flex-col gap-1 text-xs text-mist-500">
              {Object.entries(ergebnis.abgelehnt).map(([kennung, grund]) => (
                <li key={kennung}>
                  {kennung}: {grund}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {uebernehmen.isError && (
        <p className="rounded-lg border border-rose-800/60 bg-rose-950/30 p-3 text-sm text-rose-200">
          {t('mediaserverImport.importFailed', {
            grund: (uebernehmen.error as Error).message,
          })}
        </p>
      )}
        </div>
      </Fenster>

      {/* ⚠️ **Eine Rückfrage statt eines Häkchens je Zeile.** Das Häkchen brach
          die Zeile um und schob die Zuordnung darunter; und die Frage gehört
          ohnehin dorthin, wo die Entscheidung fällt. Was hier bestätigt wird,
          ist nicht bloß "übernehmen", sondern das Aufheben einer Sperre - und
          die steht dort, weil jemand dieses Konto gelöscht hat. */}
      <ConfirmDialog
        open={rueckfrage !== null}
        title={t('mediaserverImport.confirmTitle')}
        description={
          <>
            <p>
              <Trans
                i18nKey="mediaserverImport.confirmIntro"
                values={{ name, namen: (rueckfrage ?? []).join(', ') }}
              >
                <b />
              </Trans>
            </p>
            <p className="mt-2">{t('mediaserverImport.confirmWhy')}</p>
          </>
        }
        warning={t('mediaserverImport.confirmWarning')}
        confirmLabel={t('mediaserverImport.confirmOk')}
        onConfirm={() => {
          setRueckfrage(null)
          starten()
        }}
        onCancel={() => setRueckfrage(null)}
      />
    </>
  )
}
