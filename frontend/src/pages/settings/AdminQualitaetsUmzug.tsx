/**
 * Die Profilablage mitnehmen — und drüben wiedererkennen, was zu ihr gehört.
 *
 * ⚠️ **Warum das nötig ist.** Nexview führt das Besitzbuch allein in seiner
 * eigenen Datenbank. Wer neu aufsetzt und auf dasselbe Radarr zeigt, steht vor
 * seinen eigenen Profilen wie vor fremden — und der Bestand meldet dann die
 * Muster, die ein Bauplan bewusst mit **null Punkten** mitbringt, als
 * „ungenutzt". Wer aufräumt, löscht Teile seiner eigenen Profile. Gemessen am
 * 29.08.2026 an einer frischen Installation: 17 statt 2.
 *
 * ⚠️ **Vorschau vor Zugriff.** Der Import zeigt erst, was er auf jeder Instanz
 * gefunden hat, und übernimmt erst auf Klick. Übernehmen heißt dabei
 * ausdrücklich: Nexview trägt die Nummer in sein Buch. In Radarr wird **nichts**
 * geschrieben — auch dann nicht, wenn die Kopie vom Rezept abweicht.
 */

import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { ApiError, api, downloadFile } from '../../api/client'
import type { UmzugBefund, UmzugErgebnis } from '../../api/types'
import { Button } from '../../components/ui'
import { Fenster } from '../../components/Fenster'

/** Farbe und Reihenfolge je Befund — Schwerwiegendes zuerst. */
const LAGE_STIL: Record<string, string> = {
  uebernehmen: 'border-ok-500/50 bg-ok-500/10 text-ok-500',
  weicht_ab: 'border-warn-500/50 bg-warn-500/10 text-warn-500',
  nicht_gefunden: 'border-ink-600 bg-ink-800 text-mist-500',
  unerreichbar: 'border-ink-700 bg-ink-900 text-mist-600',
}

export function AdminQualitaetsUmzug({ onFertig }: { onFertig: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const dateiFeld = useRef<HTMLInputElement>(null)

  const [fehler, setFehler] = useState<string | null>(null)
  const [meldung, setMeldung] = useState<string | null>(null)
  /** Die eingelesene Datei — sie wartet auf das Ja zur Vorschau. */
  const [datei, setDatei] = useState<unknown | null>(null)
  const [vorschau, setVorschau] = useState<UmzugErgebnis | null>(null)

  const melden = (a: unknown) => {
    setVorschau(null)
    setFehler(a instanceof ApiError ? a.message : String(a))
  }

  const ausfuhr = useMutation({
    mutationFn: () =>
      downloadFile(
        '/api/settings/qualitaetsprofile/ausfuhr',
        'nexview-qualitaetsprofile.json',
      ),
    onSuccess: () => {
      setFehler(null)
      setMeldung(t('quality.transfer.exported'))
    },
    onError: melden,
  })

  const vorschauMut = useMutation({
    mutationFn: (inhalt: unknown) =>
      api.post<UmzugErgebnis>('/api/settings/qualitaetsprofile/einfuhr/vorschau', {
        datei: inhalt,
      }),
    onSuccess: (daten) => {
      setFehler(null)
      setVorschau(daten)
    },
    onError: melden,
  })

  const einfuhr = useMutation({
    mutationFn: (inhalt: unknown) =>
      api.post<UmzugErgebnis>('/api/settings/qualitaetsprofile/einfuhr', {
        datei: inhalt,
      }),
    onSuccess: (daten) => {
      setFehler(null)
      setVorschau(null)
      setDatei(null)
      const uebernommen = daten.befunde.filter(
        (b) => b.lage === 'uebernehmen' || b.lage === 'weicht_ab',
      ).length
      setMeldung(
        t('quality.transfer.imported', {
          neu: daten.neu.length,
          uebernommen,
        }),
      )
      // ⚠️ Der Bestand hängt daran: Erst mit der Ablage weiß Nexview wieder,
      // welche Muster zu einem Bauplan gehören.
      for (const schluessel of [
        ['qualitaetsprofile'],
        ['qualitaetsprofile-abgleich'],
        ['arr-bestand'],
      ]) {
        void queryClient.invalidateQueries({ queryKey: schluessel, refetchType: 'all' })
      }
      onFertig()
    },
    onError: melden,
  })

  const dateiGewaehlt = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const gewaehlt = e.target.files?.[0]
    // Damit dieselbe Datei zweimal hintereinander gewählt werden kann.
    e.target.value = ''
    if (!gewaehlt) return
    setMeldung(null)
    try {
      const inhalt = JSON.parse(await gewaehlt.text())
      setDatei(inhalt)
      vorschauMut.mutate(inhalt)
    } catch {
      // ⚠️ Kaputtes JSON kommt gar nicht erst zum Server — er könnte dazu auch
      // nichts Besseres sagen als „das ist keine JSON-Datei".
      setVorschau(null)
      setFehler(t('quality.transfer.notJson'))
    }
  }

  const laeuft = vorschauMut.isPending || einfuhr.isPending

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          loading={ausfuhr.isPending}
          onClick={() => ausfuhr.mutate()}
        >
          {t('quality.transfer.export')}
        </Button>
        <Button
          type="button"
          variant="ghost"
          loading={laeuft}
          onClick={() => dateiFeld.current?.click()}
        >
          {t('quality.transfer.import')}
        </Button>
        <input
          ref={dateiFeld}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(e) => void dateiGewaehlt(e)}
        />
      </div>

      {fehler && (
        <p className="mt-3 rounded-xl border border-bad-500/40 bg-bad-500/10 px-4 py-3 text-sm text-bad-500">
          {fehler}
        </p>
      )}
      {meldung && (
        <p className="mt-3 rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {meldung}
        </p>
      )}

      <Fenster
        offen={vorschau !== null}
        titel={t('quality.transfer.previewTitle')}
        onSchliessen={() => setVorschau(null)}
        fuss={
          <div className="flex w-full items-center justify-between">
            <Button type="button" variant="ghost" onClick={() => setVorschau(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              disabled={!vorschau?.neu.length}
              loading={einfuhr.isPending}
              onClick={() => datei !== null && einfuhr.mutate(datei)}
            >
              {t('quality.transfer.doImport', { anzahl: vorschau?.neu.length ?? 0 })}
            </Button>
          </div>
        }
      >
        {vorschau && <Vorschau schau={vorschau} />}
      </Fenster>
    </>
  )
}

/** Was der Import vorhat — je Profil, je Instanz. */
function Vorschau({ schau }: { schau: UmzugErgebnis }) {
  const { t } = useTranslation()
  const jeProfil = new Map<string, UmzugBefund[]>()
  for (const b of schau.befunde) {
    jeProfil.set(b.name, [...(jeProfil.get(b.name) ?? []), b])
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm leading-relaxed text-mist-400">
        {t('quality.transfer.previewIntro')}
      </p>

      {schau.neu.length === 0 && (
        <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {t('quality.transfer.nothingNew')}
        </p>
      )}

      {schau.schon_da.length > 0 && (
        <div className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-3">
          <p className="text-sm font-medium text-mist-200">
            {t('quality.transfer.alreadyHere', { anzahl: schau.schon_da.length })}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-mist-500">
            {t('quality.transfer.alreadyHereWhy')}
          </p>
          <p className="mt-2 text-xs text-mist-400">{schau.schon_da.join(' · ')}</p>
        </div>
      )}

      {schau.neu.map((name) => (
        <div key={name} className="rounded-xl border border-ink-700 bg-ink-900/50 px-4 py-3">
          <p className="text-sm font-medium text-mist-100">{name}</p>
          <div className="mt-2 flex flex-col gap-1.5">
            {(jeProfil.get(name) ?? []).length === 0 ? (
              <span className="text-xs text-mist-600">
                {t('quality.transfer.noInstance')}
              </span>
            ) : (
              (jeProfil.get(name) ?? []).map((b) => (
                <div
                  key={b.kennung}
                  className="flex flex-wrap items-center gap-2 text-xs"
                >
                  <span className="min-w-32 text-mist-400">{b.instanz}</span>
                  <span
                    className={
                      'rounded-full border px-2 py-0.5 text-[0.65rem] ' +
                      (LAGE_STIL[b.lage] ?? LAGE_STIL.unerreichbar)
                    }
                  >
                    {t(`quality.transfer.state.${b.lage}`)}
                  </span>
                  {b.lage === 'weicht_ab' && (
                    <span className="text-mist-500">
                      {t('quality.transfer.differs', { anzahl: b.unterschiede })}
                    </span>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      ))}

      {/* ⚠️ Der Satz, der die ganze Sorge nimmt — er gehört unter die Liste,
          nicht ins Kleingedruckte. */}
      <p className="rounded-r-xl border-l-2 border-accent-500/60 bg-ink-900/70 px-4 py-3 text-xs leading-relaxed text-mist-400">
        {t('quality.transfer.nothingWritten')}
      </p>
    </div>
  )
}
