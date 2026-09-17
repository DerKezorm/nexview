import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, api } from '../../api/client'
import type { VergleichZeile } from '../../api/types'
import { Button, Spinner } from '../../components/ui'
import { providerName } from '../../lib/mediaserver'

/**
 * „Neu zuordnen" - direkt am Eintrag der Vergleichstabelle.
 *
 * ⚠️ **Schreibt in die Medienserver.** Es ist der einzige Weg, auf dem Nexview
 * dort Metadaten ändert, und er läuft nur auf diesen Klick.
 *
 * Zur Wahl stehen nur die Nummern, die schon in der Zeile vorkommen - bei drei
 * Servern also höchstens drei, dazu die Nummer aus dem Dateinamen. Keine
 * freie Suche: Wer „8" eintippt, bekommt zwanzig Filme und nimmt den falschen,
 * genau so ist die falsche Zuordnung meist entstanden.
 */

type Option = {
  nummer: number
  titel: string | null
  jahr: number | null
  server: string[]
  imPfad: boolean
}

type Ergebnis = { anbieter: string; text: string; ok: boolean }

const PFAD_NUMMER = /[[{](tmdb|tvdb)(?:id)?[-=](\d+)[\]}]/gi

export function NeuZuordnen({
  zeile,
  server,
  onSchliessen,
}: {
  zeile: VergleichZeile
  server: string[]
  onSchliessen: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  // Serien erkennt Nexview über TVDB, Filme über TMDB - also wird auch darüber
  // zugeordnet, sofern die Zeile die Nummer überhaupt kennt.
  const schluessel: 'tmdb' | 'tvdb' =
    zeile.art === 'tv' && server.some((a) => (zeile.zellen[a]?.tvdb ?? []).length > 0)
      ? 'tvdb'
      : 'tmdb'

  const optionen: Option[] = []
  const merken = (nummer: number, eintrag: Partial<Option>) => {
    const vorhanden = optionen.find((o) => o.nummer === nummer)
    if (vorhanden) {
      vorhanden.server.push(...(eintrag.server ?? []))
      vorhanden.imPfad ||= !!eintrag.imPfad
      vorhanden.titel ??= eintrag.titel ?? null
      vorhanden.jahr ??= eintrag.jahr ?? null
    } else {
      optionen.push({
        nummer,
        titel: eintrag.titel ?? null,
        jahr: eintrag.jahr ?? null,
        server: eintrag.server ?? [],
        imPfad: !!eintrag.imPfad,
      })
    }
  }
  for (const anbieter of server) {
    const zelle = zeile.zellen[anbieter]
    if (!zelle || zelle.zustand === 'fehlt') continue
    for (const nummer of zelle[schluessel]) {
      merken(nummer, { titel: zelle.titel, jahr: zelle.jahr, server: [anbieter] })
    }
    for (const pfad of zelle.pfade) {
      for (const [, quelle, wert] of pfad.matchAll(PFAD_NUMMER)) {
        if (quelle.toLowerCase() === schluessel) merken(Number(wert), { imPfad: true })
      }
    }
  }
  // Was im Dateinamen steht, zuerst - Radarr und Sonarr haben die Nummer
  // beim Herunterladen vergeben, sie ist meist die richtige.
  optionen.sort((a, b) => Number(b.imPfad) - Number(a.imPfad) || b.server.length - a.server.length)

  const [gewaehlt, setGewaehlt] = useState<number | null>(optionen[0]?.nummer ?? null)
  const [laeuft, setLaeuft] = useState<string | null>(null)
  const [ergebnisse, setErgebnisse] = useState<Ergebnis[]>([])

  const ziele = server.filter((anbieter) => {
    const zelle = zeile.zellen[anbieter]
    return (
      !!zelle &&
      zelle.zustand !== 'fehlt' &&
      !!zelle.schluessel &&
      gewaehlt !== null &&
      !zelle[schluessel].includes(gewaehlt)
    )
  })
  const namen = (liste: string[]) => liste.map(providerName).join(', ')

  async function korrigieren() {
    if (gewaehlt === null) return
    setErgebnisse([])
    const neu: Ergebnis[] = []
    for (const anbieter of ziele) {
      const zelle = zeile.zellen[anbieter]
      setLaeuft(anbieter)
      try {
        const antwort = await api.post<{ ergebnis: string; titel: string | null }>(
          '/api/admin/analyse/server-vergleich/zuordnen',
          {
            anbieter,
            schluessel: zelle.schluessel,
            art: zeile.art,
            [schluessel]: gewaehlt,
          },
        )
        neu.push({
          anbieter,
          ok: antwort.ergebnis === 'korrigiert',
          text: t(`analyse.matrix.rematch.result.${antwort.ergebnis}`, {
            server: providerName(anbieter),
            titel: antwort.titel ?? '',
          }),
        })
      } catch (caught) {
        neu.push({
          anbieter,
          ok: false,
          text: `${providerName(anbieter)}: ${
            caught instanceof ApiError ? caught.message : t('errors.generic')
          }`,
        })
      }
      setErgebnisse([...neu])
    }
    setLaeuft(null)
    void queryClient.invalidateQueries({ queryKey: ['server-vergleich'] })
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm font-semibold text-mist-200">{t('analyse.matrix.rematch.title')}</p>
      <div className="flex flex-col gap-1.5" role="radiogroup">
        {optionen.map((option) => (
          <label
            key={option.nummer}
            className={
              'flex cursor-pointer flex-wrap items-center gap-x-2 gap-y-0.5 rounded-xl border px-3 py-2 text-sm ' +
              (gewaehlt === option.nummer
                ? 'border-accent-500 bg-accent-500/10'
                : 'border-ink-700 bg-ink-900 hover:border-ink-600')
            }
          >
            <input
              type="radio"
              name={`zuordnen-${zeile.kennung}`}
              checked={gewaehlt === option.nummer}
              onChange={() => setGewaehlt(option.nummer)}
              disabled={laeuft !== null}
              className="accent-accent-500"
            />
            <span className="text-mist-100">
              {option.titel ?? t('analyse.matrix.rematch.noTitle')}
              {option.jahr ? ` (${option.jahr})` : ''}
            </span>
            <span className="font-mono text-xs text-mist-500">
              {schluessel.toUpperCase()} {option.nummer}
            </span>
            {option.server.length > 0 && (
              <span className="text-xs text-mist-500">
                {t('analyse.matrix.rematch.onServers', { servers: namen(option.server) })}
              </span>
            )}
            {option.imPfad && (
              <span className="rounded-full bg-ok-500/10 px-2 py-0.5 text-xs text-ok-500">
                {t('analyse.matrix.rematch.fromPath')}
              </span>
            )}
          </label>
        ))}
      </div>

      <p className="text-xs text-mist-600">{t('analyse.matrix.rematch.hint')}</p>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          onClick={() => void korrigieren()}
          disabled={ziele.length === 0 || laeuft !== null}
          loading={laeuft !== null}
        >
          {ziele.length
            ? t('analyse.matrix.rematch.apply', { servers: namen(ziele) })
            : t('analyse.matrix.rematch.nothingToDo')}
        </Button>
        <button
          type="button"
          onClick={onSchliessen}
          disabled={laeuft !== null}
          className="text-sm text-mist-500 hover:text-mist-300 disabled:opacity-50"
        >
          {t('common.close')}
        </button>
      </div>

      {laeuft && (
        <p className="flex items-center gap-2 text-xs text-mist-500">
          <Spinner className="h-3 w-3" />
          {t('analyse.matrix.rematch.running', { server: providerName(laeuft) })}
        </p>
      )}
      {ergebnisse.map((ergebnis) => (
        <p
          key={ergebnis.anbieter}
          className={'text-sm ' + (ergebnis.ok ? 'text-ok-500' : 'text-warn-500')}
        >
          {ergebnis.text}
        </p>
      ))}
    </div>
  )
}
