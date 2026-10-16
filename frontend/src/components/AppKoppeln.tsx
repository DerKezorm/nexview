/**
 * Die iPhone-App verbinden: ein QR-Code, den die App scannt.
 *
 * Beim Öffnen legt der Server eine Kopplung an (`POST pfad`) und schickt Link
 * und QR-Code mit. Das Fenster fragt alle zwei Sekunden nach, ob die App ihren
 * Schlüssel abgeholt hat, und zieht den Code beim Schließen zurück, solange
 * niemand gescannt hat - ein offener Code soll nicht zehn Minuten herumliegen,
 * nur weil das Fenster zu ist.
 *
 * Für das eigene Konto (Profil) und für ein Kinderkonto (Kinderkonten)
 * derselbe Baustein; nur Pfad und Texte unterscheiden sich. Die Regeln stehen
 * in `backend/app/services/app_kopplung.py`.
 */

import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, api } from '../api/client'
import { Fenster } from './Fenster'
import { Button, ErrorBanner, Spinner } from './ui'

type Kopplung = {
  kennung: string
  gilt_bis: string
  adresse: string
  link: string
  qr_svg: string
}

type Stand = { stand: 'wartet' | 'verbunden' | 'fehlgeschlagen' | 'abgelaufen'; geraet: string | null }

const NACHFRAGEN_MS = 2000

export function AppKoppeln({
  offen,
  pfad,
  titel,
  anleitung,
  verbundenText,
  onSchliessen,
  onVerbunden,
}: {
  offen: boolean
  /** Wo der Code entsteht: `/api/me/app-kopplung` oder `/api/children/{id}/app-kopplung`. */
  pfad: string
  titel: string
  anleitung: string
  /** Der Satz nach dem Verbinden; `{{geraet}}` wird ersetzt. */
  verbundenText: (geraet: string) => string
  onSchliessen: () => void
  onVerbunden?: () => void
}) {
  const { t } = useTranslation()
  const [kopplung, setKopplung] = useState<Kopplung | null>(null)
  const [stand, setStand] = useState<Stand['stand']>('wartet')
  const [geraet, setGeraet] = useState('')
  const [fehler, setFehler] = useState('')
  const [runde, setRunde] = useState(0)
  // Fuer das Aufraeumen beim Schliessen: der Stand von eben, nicht der beim Anlegen.
  const offeneKennung = useRef<string | null>(null)

  // Anlegen, sobald das Fenster aufgeht (und bei "Neuen Code erzeugen").
  useEffect(() => {
    if (!offen) return
    let vorbei = false
    setKopplung(null)
    setStand('wartet')
    setFehler('')
    api
      .post<Kopplung>(pfad, { adresse: window.location.origin })
      .then((neu) => {
        if (vorbei) {
          void api.delete(`/api/me/app-kopplung/${neu.kennung}`).catch(() => undefined)
          return
        }
        offeneKennung.current = neu.kennung
        setKopplung(neu)
      })
      .catch((e: unknown) => {
        if (!vorbei) setFehler(e instanceof ApiError ? e.message : t('errors.generic'))
      })
    return () => {
      vorbei = true
      // ⚠️ **Aufraeumen an einer Stelle**: beim Schliessen (``offen`` wird
      // falsch), beim Verlassen der Seite und bevor ein neuer Code entsteht.
      // Ein ungescannter Code soll nicht zehn Minuten herumliegen.
      const kennung = offeneKennung.current
      offeneKennung.current = null
      if (kennung) void api.delete(`/api/me/app-kopplung/${kennung}`).catch(() => undefined)
    }
  }, [offen, pfad, runde, t])

  // Nachfragen, bis verbunden oder abgelaufen.
  useEffect(() => {
    if (!offen || !kopplung || stand !== 'wartet') return
    const uhr = window.setInterval(() => {
      api
        .get<Stand>(`/api/me/app-kopplung/${kopplung.kennung}`)
        .then((antwort) => {
          setStand(antwort.stand)
          if (antwort.stand !== 'wartet') offeneKennung.current = null
          if (antwort.stand === 'verbunden') {
            setGeraet(antwort.geraet ?? '')
            onVerbunden?.()
          }
        })
        .catch((e: unknown) => {
          // Weg ist weg: Der Code wurde inzwischen abgeraeumt. Sonst bliebe ein
          // toter QR-Code stehen, und das Fenster fragte ewig weiter.
          if (e instanceof ApiError && e.status === 404) setStand('abgelaufen')
        })
    }, NACHFRAGEN_MS)
    return () => window.clearInterval(uhr)
  }, [offen, kopplung, stand, onVerbunden])

  // Das Zurueckziehen erledigt das Aufraeumen des ersten Effekts, sobald
  // ``offen`` falsch wird.
  const schliessen = onSchliessen

  return (
    <Fenster
      offen={offen}
      titel={titel}
      onSchliessen={schliessen}
      fuss={
        <Button type="button" onClick={schliessen}>
          {stand === 'verbunden' ? t('common.done') : t('common.close')}
        </Button>
      }
    >
      <div className="flex flex-col items-center gap-4 text-center">
        {fehler && <ErrorBanner message={fehler} />}

        {!fehler && !kopplung && <Spinner />}

        {kopplung && stand === 'wartet' && (
          <>
            <p className="text-sm leading-relaxed text-mist-300">{anleitung}</p>
            <img
              src={`data:image/svg+xml;utf8,${encodeURIComponent(kopplung.qr_svg)}`}
              alt={t('appConnect.qr')}
              className="h-56 w-56 rounded-xl"
            />
            <p className="text-xs text-mist-500">{t('appConnect.valid')}</p>
            <p className="text-xs text-mist-600">
              {t('appConnect.address', { adresse: kopplung.adresse })}
            </p>
          </>
        )}

        {stand === 'verbunden' && (
          <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
            {verbundenText(geraet)}
          </p>
        )}

        {(stand === 'abgelaufen' || stand === 'fehlgeschlagen') && (
          <>
            <p className="text-sm text-mist-500">
              {t(stand === 'abgelaufen' ? 'appConnect.expired' : 'appConnect.failed')}
            </p>
            <Button type="button" variant="ghost" onClick={() => setRunde((r) => r + 1)}>
              {t('appConnect.again')}
            </Button>
          </>
        )}
      </div>
    </Fenster>
  )
}
