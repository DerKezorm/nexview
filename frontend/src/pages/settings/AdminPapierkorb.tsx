import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type {
  PapierkorbInhalt as PapierkorbInhaltDaten,
  PapierkorbInstanz,
  PapierkorbStand,
} from '../../api/types'
import { Fenster } from '../../components/Fenster'
import { Button, Card, ErrorBanner, Spinner } from '../../components/ui'

/** Ein Entwurf je Instanz, geschlüsselt wie der Server sie kennt. */
function schluessel(instanz: { media_type: string; tier: string }): string {
  return `${instanz.media_type}:${instanz.tier}`
}

/**
 * Wo landen gelöschte Dateien – in jeder eingerichteten Instanz?
 *
 * **Nexview führt hier keine eigene Einstellung.** Der Zustand steht in Radarr
 * bzw. Sonarr, und nur dort; er wird bei jedem Öffnen frisch geholt. Wer den
 * Papierkorb direkt drüben einträgt, findet den Haken hier gesetzt vor – und
 * wer nächste Woche eine zweite Instanz ohne Papierkorb dazunimmt, sieht ihn
 * von selbst umspringen. Eine gespeicherte Kopie würde genau das verpassen und
 * eine Löschung für umkehrbar halten, die es nicht ist.
 */
export function AdminPapierkorb() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const abfrage = useQuery({
    queryKey: ['papierkorb'],
    queryFn: () => api.get<PapierkorbStand>('/api/settings/recyclebin'),
    // Der Zustand lebt woanders – bei jedem Öffnen neu holen.
    staleTime: 0,
  })

  // null heißt „noch nicht geladen" – erst dann steht fest, was der Haken zeigt.
  const [an, setAn] = useState<boolean | null>(null)
  const [tage, setTage] = useState<string>('')
  const [entwuerfe, setEntwuerfe] = useState<Record<string, string>>({})
  // Welche Zeile gerade einen Ordner sucht.
  const [waehlt, setWaehlt] = useState<string | null>(null)

  const daten = abfrage.data

  // Der Haken kommt vom Server, nicht aus dem Gedächtnis der Oberfläche.
  useEffect(() => {
    if (!daten || an !== null) return
    setAn(daten.enabled)
    setEntwuerfe(Object.fromEntries(daten.instances.map((i) => [schluessel(i), i.path])))
    const gesetzt = daten.instances.find((i) => i.cleanup_days)
    setTage(String(gesetzt?.cleanup_days ?? 7))
  }, [daten, an])

  const speichern = useMutation({
    mutationFn: (leeren: boolean) =>
      api.put<PapierkorbStand>('/api/settings/recyclebin', {
        instances: (daten?.instances ?? []).map((i) => ({
          media_type: i.media_type,
          tier: i.tier,
          path: leeren ? '' : (entwuerfe[schluessel(i)] ?? ''),
        })),
        cleanup_days: Number(tage) || 7,
      }),
    onSuccess: (neu) => {
      void queryClient.invalidateQueries({ queryKey: ['papierkorb'] })
      void queryClient.invalidateQueries({ queryKey: ['papierkorb-inhalt'] })
      setAn(neu.enabled)
      setWaehlt(null)
    },
  })

  if (abfrage.isLoading || an === null) {
    return (
      <Card className="flex justify-center p-6">
        <Spinner />
      </Card>
    )
  }
  if (!daten || daten.instances.length === 0) return null

  const alleGesetzt = daten.instances.every((i) => (entwuerfe[schluessel(i)] ?? '').trim())
  const tageGueltig = Number.isInteger(Number(tage)) && Number(tage) >= 1
  // Abschalten ist die gefährliche Richtung – und sie braucht keine Pfade.
  const abschalten = !an && daten.enabled

  // Ein Speichern-Knopf, der auch ohne Änderung leuchtet, lädt zum sinnlosen
  // Klicken ein – und hier schreibt jeder Klick in einen fremden Dienst.
  // Verglichen wird gegen das, was **drüben** steht, nicht gegen einen
  // vorherigen Entwurf: Nur das ist der Zustand, der zählt.
  const geaendert =
    an !== daten.enabled ||
    daten.instances.some(
      (i) => (entwuerfe[schluessel(i)] ?? '').trim() !== i.path.trim(),
    ) ||
    daten.instances.some(
      (i) => i.cleanup_days !== null && i.cleanup_days !== Number(tage),
    )

  const bereit = geaendert && (abschalten || (alleGesetzt && tageGueltig))

  return (
    <Card className="flex flex-col gap-4 p-5">
      <label className="flex cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          checked={an}
          onChange={(e) => setAn(e.target.checked)}
          className="mt-0.5 h-4 w-4 accent-accent-500"
        />
        <span>
          <span className="font-medium">{t('papierkorb.use')}</span>
          <span className="mt-0.5 block text-sm text-mist-500">
            {t('papierkorb.useHint')}
          </span>
          {/* Gehört unter den Haken, nicht ans Ende der Karte: Wer ihn setzt,
              greift damit in einen **fremden** Dienst ein. Am Fuß der Karte
              wird das übersehen – genau das ist passiert. */}
          <span className="mt-2 block text-sm leading-relaxed text-mist-500">
            {t('papierkorb.managedBy')}
          </span>
        </span>
      </label>

      {/* Eine stumme Instanz ist etwas anderes als eine ohne Papierkorb – über
          sie lässt sich nichts sagen, und der Haken oben ist dann eine Aussage
          über unvollständige Auskunft. Das gehört dazugesagt. */}
      {!daten.complete && (
        <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {t('papierkorb.incomplete')}
        </p>
      )}

      {abschalten && (
        <p className="rounded-xl border border-bad-500/40 bg-bad-500/10 px-4 py-3 text-sm text-bad-500">
          {t('papierkorb.offWarning')}
        </p>
      )}

      {an && (
        <>
          <div className="border-t border-ink-700 pt-4">
            <label className="block text-sm font-medium" htmlFor="papierkorb-tage">
              {t('papierkorb.days')}
            </label>
            <div className="mt-1.5 flex items-center gap-2">
              <input
                id="papierkorb-tage"
                type="number"
                min={1}
                max={365}
                value={tage}
                onChange={(e) => setTage(e.target.value)}
                className="w-24 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
              />
              <span className="text-sm text-mist-500">{t('papierkorb.daysUnit')}</span>
            </div>
            {/* Null ist nicht erlaubt: Ob Radarr sie als „nie aufräumen" oder
                „sofort" versteht, ist nicht dokumentiert – und bei einem
                Papierkorb ist diese Verwechslung fatal. */}
            <p className="mt-1.5 text-xs text-mist-600">{t('papierkorb.daysHint')}</p>
          </div>

          <ul className="flex flex-col border-t border-ink-700">
            {daten.instances.map((instanz) => (
              <Zeile
                key={schluessel(instanz)}
                instanz={instanz}
                pfad={entwuerfe[schluessel(instanz)] ?? ''}
                waehlt={waehlt === schluessel(instanz)}
                onWaehlen={() =>
                  setWaehlt(waehlt === schluessel(instanz) ? null : schluessel(instanz))
                }
                onPfad={(neu) => {
                  setEntwuerfe((alt) => ({ ...alt, [schluessel(instanz)]: neu }))
                  setWaehlt(null)
                }}
              />
            ))}
          </ul>
        </>
      )}

      {speichern.error ? (
        <ErrorBanner
          message={
            speichern.error instanceof ApiError
              ? speichern.error.message
              : t('errors.generic')
          }
        />
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          onClick={() => speichern.mutate(abschalten)}
          loading={speichern.isPending}
          disabled={!bereit}
        >
          {abschalten ? t('papierkorb.saveOff') : t('common.save')}
        </Button>
        {an && !alleGesetzt && (
          <span className="text-sm text-warn-500">{t('papierkorb.needAll')}</span>
        )}
        {geaendert && <span className="text-sm text-warn-500">{t('common.unsaved')}</span>}
        {!geaendert && speichern.isSuccess && !speichern.isPending && (
          <span className="text-sm text-ok-500">{t('papierkorb.saved')}</span>
        )}
      </div>
    </Card>
  )
}

function Zeile({
  instanz,
  pfad,
  waehlt,
  onWaehlen,
  onPfad,
}: {
  instanz: PapierkorbInstanz
  pfad: string
  waehlt: boolean
  onWaehlen: () => void
  onPfad: (pfad: string) => void
}) {
  const { t } = useTranslation()
  const [zeigt, setZeigt] = useState(false)

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-ink-800 py-3 last:border-b-0">
      <span className="w-24 shrink-0 text-sm font-medium">{instanz.name}</span>

      {/* Nicht erreichbar zuerst: Über eine stumme Instanz lässt sich nichts
          sagen, und „kein Papierkorb" wäre dort geraten. */}
      {!instanz.reachable ? (
        <span className="flex-1 text-sm text-warn-500">{t('papierkorb.unreachable')}</span>
      ) : pfad ? (
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-mist-400">
          {pfad}
        </span>
      ) : (
        <span className="flex-1 text-sm text-bad-500">{t('papierkorb.none')}</span>
      )}

      {/* Öffnen zeigt den **ausgewählten** Ordner, auch wenn er noch nicht
          gespeichert ist. Den Ordner gibt es ja schon – Radarr führt ihn nur
          noch nicht als Papierkorb. Erst speichern zu müssen, um hineinsehen
          zu dürfen, wäre die falsche Reihenfolge. */}
      <Button
        variant="ghost"
        onClick={() => setZeigt(true)}
        disabled={!instanz.reachable || !pfad}
        className="shrink-0 px-3 py-1 text-xs"
      >
        {t('papierkorb.open')}
      </Button>
      <Button
        variant="ghost"
        onClick={onWaehlen}
        disabled={!instanz.reachable}
        className="shrink-0 px-3 py-1 text-xs"
      >
        {t('papierkorb.edit')}
      </Button>

      <Ordnerwahl
        offen={waehlt}
        instanz={instanz}
        start={pfad}
        onWahl={onPfad}
        onSchliessen={onWaehlen}
      />
      <Inhalt
        offen={zeigt}
        instanz={instanz}
        pfad={pfad}
        onSchliessen={() => setZeigt(false)}
      />
    </li>
  )
}

/**
 * Ordner aussuchen – gefragt wird **diese** Instanz.
 *
 * Nicht einmal für alle: Sonarr kann völlig anders eingebunden sein als
 * Radarr. Ein geratener Pfad führt dazu, dass die Instanz später an eine
 * Stelle löscht, die es bei ihr gar nicht gibt.
 */
function Ordnerwahl({
  offen,
  instanz,
  start,
  onWahl,
  onSchliessen,
}: {
  offen: boolean
  instanz: PapierkorbInstanz
  start: string
  onWahl: (pfad: string) => void
  onSchliessen: () => void
}) {
  const { t } = useTranslation()
  const [pfad, setPfad] = useState(start || '/')

  const abfrage = useQuery({
    queryKey: ['papierkorb-ordner', instanz.media_type, instanz.tier, pfad],
    queryFn: () =>
      api.get<{ path: string; directories: string[] }>(
        `/api/settings/recyclebin/folders?media_type=${instanz.media_type}` +
          `&tier=${instanz.tier}&path=${encodeURIComponent(pfad)}`,
      ),
    enabled: offen,
  })

  const ohneEnde = pfad.replace(/\/+$/, '')
  const hoch = ohneEnde.split('/').slice(0, -1).join('/') || '/'

  return (
    <Fenster
      offen={offen}
      titel={t('papierkorb.chooseFor', { name: instanz.name })}
      unterzeile={pfad}
      onSchliessen={onSchliessen}
      fuss={
        <>
          <Button variant="ghost" onClick={onSchliessen}>
            {t('common.cancel')}
          </Button>
          <Button onClick={() => onWahl(pfad)}>{t('papierkorb.apply')}</Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {/* Navigation gehört in den Inhalt, Entscheidungen in den Fuß – sonst
            steht „eine Ebene höher" neben „Übernehmen", als wären es zwei
            gleichrangige Ausgänge. */}
        <Button
          variant="ghost"
          onClick={() => setPfad(hoch)}
          disabled={ohneEnde === ''}
          className="self-start px-3 py-1 text-xs"
        >
          {t('papierkorb.up')}
        </Button>

        {abfrage.isLoading ? (
          <div className="flex justify-center py-6">
            <Spinner />
          </div>
        ) : (abfrage.data?.directories.length ?? 0) === 0 ? (
          <p className="py-2 text-sm text-mist-600">{t('papierkorb.noSubfolders')}</p>
        ) : (
          <ul className="flex flex-col">
            {abfrage.data?.directories.map((eintrag) => (
              <li key={eintrag}>
                <button
                  type="button"
                  onClick={() => setPfad(eintrag)}
                  className="w-full truncate rounded-lg px-3 py-2 text-left text-sm text-mist-300 hover:bg-ink-800 hover:text-mist-100"
                >
                  {eintrag.replace(/\/+$/, '').split('/').slice(-1)[0] || eintrag}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Fenster>
  )
}

/**
 * Was liegt gerade im Papierkorb dieser Instanz?
 *
 * **Nur die Ordnernamen.** Kein Plakat – das ginge nur über die TMDB-Nummer im
 * Ordnernamen, und die steht dort nicht von Natur aus, sondern nur wenn jemand
 * sein Benennungsschema so eingerichtet hat. Wer das anders hält, sähe überall
 * eine leere Fläche.
 *
 * **Nur ansehen.** Zurückholen kann Nexview nicht – dafür müssten Dateien
 * verschoben werden, und Nexview sieht das Dateisystem gar nicht.
 */
function Inhalt({
  offen,
  instanz,
  pfad,
  onSchliessen,
}: {
  offen: boolean
  instanz: PapierkorbInstanz
  pfad: string
  onSchliessen: () => void
}) {
  const { t } = useTranslation()

  const abfrage = useQuery({
    queryKey: ['papierkorb-inhalt', instanz.media_type, instanz.tier, pfad],
    queryFn: () =>
      api.get<PapierkorbInhaltDaten>(
        `/api/settings/recyclebin/contents?media_type=${instanz.media_type}` +
          `&tier=${instanz.tier}&path=${encodeURIComponent(pfad)}`,
      ),
    enabled: offen,
  })

  const eintraege = abfrage.data?.instances[0]?.entries ?? []
  const gekuerzt = abfrage.data?.instances[0]?.truncated ?? false

  return (
    <Fenster
      offen={offen}
      titel={t('papierkorb.contentOf', { name: instanz.name })}
      unterzeile={pfad}
      onSchliessen={onSchliessen}
    >
      {abfrage.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : eintraege.length === 0 ? (
        <p className="py-2 text-sm text-mist-500">{t('papierkorb.contentEmpty')}</p>
      ) : (
        <>
          <p className="mb-2 text-xs text-mist-600">
            {t('papierkorb.contentCount', { count: eintraege.length })}
          </p>
          <ul className="flex flex-col">
            {eintraege.map((name) => (
              <li
                key={name}
                className="border-b border-ink-800 py-1.5 font-mono text-xs break-all text-mist-300 last:border-b-0"
              >
                {name}
              </li>
            ))}
          </ul>
          {gekuerzt && (
            <p className="mt-4 text-xs text-warn-500">{t('papierkorb.contentMore')}</p>
          )}
        </>
      )}

      <p className="mt-5 border-t border-ink-800 pt-4 text-xs leading-relaxed text-mist-600">
        {t('papierkorb.contentHint')}
      </p>
    </Fenster>
  )
}
