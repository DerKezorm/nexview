/**
 * Einen Fernseher verbinden: den Code eingeben, der auf dem Bildschirm steht.
 *
 * Erreichbar unter `/link`. Der QR-Code des Fernsehers öffnet die Seite mit
 * `?code=…`, dann ist der Code schon geprüft und man sieht gleich Gerät und
 * App. Bestätigt man, holt sich das Gerät beim nächsten Nachfragen einen
 * Schlüssel mit den Rechten dieses Kontos (oder, als Elternteil, des gewählten
 * Kindes); er steht danach unter API-Token beziehungsweise bei den Geräten
 * des Kindes und lässt sich dort widerrufen.
 *
 * Die Regeln stehen in `backend/app/services/geraete_code.py`.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { ApiError, api } from '../api/client'
import { AUSWAHL, Button, Card, ErrorBanner, Field } from '../components/ui'

type Gefunden = {
  code: string
  geraet: string
  app: string
  gilt_bis: string
  kinder: { id: number; name: string }[]
}

type Ergebnis = { art: 'verbunden'; geraet: string; konto: string } | { art: 'abgelehnt' }

export function GeraetVerbindenPage() {
  const { t } = useTranslation()
  const [suche] = useSearchParams()
  const [eingabe, setEingabe] = useState(suche.get('code') ?? '')
  const [gefunden, setGefunden] = useState<Gefunden | null>(null)
  const [fuer, setFuer] = useState('')
  const [name, setName] = useState('')
  const [fehler, setFehler] = useState('')
  const [laeuft, setLaeuft] = useState(false)
  const [ergebnis, setErgebnis] = useState<Ergebnis | null>(null)

  const meldung = useCallback(
    (e: unknown) => setFehler(e instanceof ApiError ? e.message : t('errors.generic')),
    [t],
  )

  const pruefen = useCallback(
    (code: string) => {
      setFehler('')
      setLaeuft(true)
      api
        .post<Gefunden>('/api/geraete-code/pruefen', { code })
        .then((antwort) => {
          setGefunden(antwort)
          setName(antwort.geraet)
          setFuer('')
        })
        .catch(meldung)
        .finally(() => setLaeuft(false))
    },
    [meldung],
  )

  // Aus dem QR-Code: einmal von selbst prüfen, nicht bei jedem Rendern.
  const schonGeprueft = useRef(false)
  useEffect(() => {
    const ausDemLink = suche.get('code')
    if (ausDemLink && !schonGeprueft.current) {
      schonGeprueft.current = true
      pruefen(ausDemLink)
    }
  }, [suche, pruefen])

  function absenden(e: FormEvent) {
    e.preventDefault()
    if (eingabe.trim()) pruefen(eingabe)
  }

  function bestaetigen() {
    if (!gefunden) return
    setFehler('')
    setLaeuft(true)
    api
      .post<{ geraet: string; konto: string }>('/api/geraete-code/bestaetigen', {
        code: gefunden.code,
        child_id: fuer ? Number(fuer) : null,
        name: name.trim() || null,
      })
      .then((antwort) => setErgebnis({ art: 'verbunden', ...antwort }))
      .catch(meldung)
      .finally(() => setLaeuft(false))
  }

  function ablehnen() {
    if (!gefunden) return
    setFehler('')
    setLaeuft(true)
    api
      .post('/api/geraete-code/ablehnen', { code: gefunden.code })
      .then(() => setErgebnis({ art: 'abgelehnt' }))
      .catch(meldung)
      .finally(() => setLaeuft(false))
  }

  function nochEiner() {
    setErgebnis(null)
    setGefunden(null)
    setEingabe('')
    setFehler('')
  }

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('deviceLink.title')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-mist-500">{t('deviceLink.intro')}</p>
      </header>

      <Card className="flex max-w-md flex-col gap-4">
        {fehler && <ErrorBanner message={fehler} />}

        {ergebnis?.art === 'verbunden' && (
          <>
            <p className="text-mist-100">
              {t('deviceLink.connected', { geraet: ergebnis.geraet, konto: ergebnis.konto })}
            </p>
            <Button type="button" variant="ghost" className="self-start" onClick={nochEiner}>
              {t('deviceLink.another')}
            </Button>
          </>
        )}

        {ergebnis?.art === 'abgelehnt' && (
          <>
            <p className="text-mist-100">{t('deviceLink.denied')}</p>
            <Button type="button" variant="ghost" className="self-start" onClick={nochEiner}>
              {t('deviceLink.another')}
            </Button>
          </>
        )}

        {!ergebnis && !gefunden && (
          <form className="flex flex-col gap-4" onSubmit={absenden}>
            <Field
              label={t('deviceLink.code')}
              value={eingabe}
              onChange={(e) => setEingabe(e.target.value.toUpperCase())}
              placeholder="BCDF-GHJK"
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              maxLength={20}
              className="font-mono text-lg tracking-widest"
              autoFocus
            />
            <Button type="submit" className="self-start" loading={laeuft} disabled={!eingabe.trim()}>
              {t('deviceLink.next')}
            </Button>
          </form>
        )}

        {!ergebnis && gefunden && (
          <>
            <dl className="flex flex-col gap-1 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-mist-500">{t('deviceLink.code')}</dt>
                <dd className="font-mono tracking-widest text-mist-100">{gefunden.code}</dd>
              </div>
              {gefunden.app && (
                <div className="flex justify-between gap-4">
                  <dt className="text-mist-500">{t('deviceLink.app')}</dt>
                  <dd className="text-mist-100">{gefunden.app}</dd>
                </div>
              )}
            </dl>
            <Field
              label={t('deviceLink.name')}
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={80}
            />
            {gefunden.kinder.length > 0 && (
              <label className="flex flex-col gap-1.5 text-sm font-medium text-mist-300">
                {t('deviceLink.for')}
                <select className={AUSWAHL} value={fuer} onChange={(e) => setFuer(e.target.value)}>
                  <option value="">{t('deviceLink.forMe')}</option>
                  {gefunden.kinder.map((kind) => (
                    <option key={kind.id} value={kind.id}>
                      {kind.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <p className="text-xs leading-relaxed text-mist-500">
              {fuer ? t('deviceLink.childHint') : t('deviceLink.hint')}
            </p>
            <div className="flex flex-wrap gap-3">
              <Button type="button" loading={laeuft} onClick={bestaetigen}>
                {t('deviceLink.confirm')}
              </Button>
              <Button type="button" variant="ghost" disabled={laeuft} onClick={ablehnen}>
                {t('deviceLink.deny')}
              </Button>
            </div>
          </>
        )}
      </Card>
    </div>
  )
}
