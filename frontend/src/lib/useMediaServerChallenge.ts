/**
 * Der Anmeldeweg über den Media-Server - an drei Stellen derselbe Ablauf.
 *
 * Anmelden, das eigene Konto verknüpfen und den Server einrichten
 * unterscheiden sich nur in den Adressen und darin, was am Ende passiert. Der
 * Ablauf dazwischen ist immer gleich: Vorgang starten, ein Fenster beim
 * Anbieter öffnen, nachfragen bis bestätigt wurde.
 *
 * Zwei Dinge, die leicht untergehen:
 *
 * * **Popup-Blocker.** Das Fenster wird oft blockiert, am Handy fast immer.
 *   Deshalb gibt der Hook Code und Adresse immer auch heraus - die Oberfläche
 *   zeigt sie an, und man kommt auch ohne Popup weiter.
 * * **Kein Ende in Sicht.** Wer das Fenster schließt, ohne zuzustimmen, würde
 *   sonst ewig abgefragt. Nach einer Weile bricht der Hook von selbst ab.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, api } from '../api/client'

const ABFRAGE_ABSTAND = 2000
const HOECHSTDAUER = 5 * 60 * 1000

export type ChallengeStart = {
  poll_token: string
  code: string
  auth_url: string
}

type Optionen<T> = {
  startPfad: string
  abfragePfad: string
  /** Beim Anmelden gibt es noch keine Sitzung, die mitgeschickt werden könnte. */
  auth?: boolean
  onFertig: (ergebnis: T) => void | Promise<void>
}

export function useMediaServerChallenge<T extends { status: string }>({
  startPfad,
  abfragePfad,
  auth = true,
  onFertig,
}: Optionen<T>) {
  const { t } = useTranslation()
  const [laeuft, setLaeuft] = useState(false)
  const [start, setStart] = useState<ChallengeStart | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)

  // Läuft der Vorgang noch? Wird beim Abbrechen und beim Verlassen der Seite
  // umgelegt, damit keine Abfrage mehr auf eine verschwundene Ansicht trifft.
  const aktiv = useRef(false)
  useEffect(() => () => {
    aktiv.current = false
  }, [])

  const abbrechen = useCallback(() => {
    aktiv.current = false
    setLaeuft(false)
    setStart(null)
  }, [])

  const starten = useCallback(async () => {
    if (aktiv.current) return
    setFehler(null)
    setLaeuft(true)
    aktiv.current = true

    let begonnen: ChallengeStart
    try {
      begonnen = await api.post<ChallengeStart>(startPfad, {}, { auth })
    } catch (caught) {
      aktiv.current = false
      setLaeuft(false)
      setFehler(caught instanceof ApiError ? caught.message : t('errors.network'))
      return
    }

    setStart(begonnen)
    // Blockiert der Browser das Fenster, ist das kein Abbruch: Adresse und
    // Code stehen in der Oberfläche, der Weg bleibt offen.
    window.open(begonnen.auth_url, 'nexview-mediaserver', 'width=800,height=720')

    const ende = Date.now() + HOECHSTDAUER
    while (aktiv.current) {
      await new Promise((weiter) => setTimeout(weiter, ABFRAGE_ABSTAND))
      if (!aktiv.current) return

      if (Date.now() > ende) {
        aktiv.current = false
        setLaeuft(false)
        setFehler(t('mediaserver.timeout'))
        return
      }

      try {
        const antwort = await api.post<T>(
          abfragePfad,
          { poll_token: begonnen.poll_token },
          { auth },
        )
        if (antwort.status !== 'ready') continue

        aktiv.current = false
        setLaeuft(false)
        await onFertig(antwort)
        return
      } catch (caught) {
        aktiv.current = false
        setLaeuft(false)
        setFehler(caught instanceof ApiError ? caught.message : t('errors.network'))
        return
      }
    }
  }, [abfragePfad, auth, onFertig, startPfad, t])

  return { starten, abbrechen, laeuft, start, fehler, setFehler }
}
