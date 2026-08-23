import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { Fenster } from './Fenster'
import { Button } from './ui'

type Neuigkeiten = {
  version: string
  offen: boolean
}

/** Der redaktionelle Text zu einer Fassung, wie er in den Sprachdateien steht. */
type WasNeuEintrag = {
  lead: string
  sections: { title: string; where: string; body: string }[]
  smallTitle: string
  small: string[]
}

/**
 * Hat der Eintrag die Form, die das Fenster erwartet?
 *
 * Die Texte kommen aus einer Datei, die vor jedem Release von Hand
 * geschrieben wird — ein Tippfehler darin darf nicht die ganze Oberfläche
 * schwarz machen. Passt die Form nicht, zeigt das Fenster den Verweis auf
 * die Release-Seite.
 */
function istEintrag(wert: unknown): wert is WasNeuEintrag {
  if (!wert || typeof wert !== 'object') return false
  const k = wert as Partial<WasNeuEintrag>
  return typeof k.lead === 'string' && Array.isArray(k.sections) && Array.isArray(k.small)
}

/**
 * „Alles, was neu ist" – der Hinweis nach einem Update.
 *
 * **Nur für Administratoren**: Sie haben das Update eingespielt, sie sollen
 * wissen, was es bringt. Benutzer sehen weder Balken noch Fenster.
 *
 * Der Balken steht oben wie der Plex-Hinweis und bleibt, bis er im Fenster
 * mit „Verstanden, nicht mehr anzeigen" quittiert wird – gespeichert wird
 * dabei die Fassung, nicht ein Haken. Nach dem nächsten Update erscheint er
 * deshalb von selbst wieder. „Schließen" lässt den Balken dagegen stehen.
 */
export function WasNeuBanner() {
  const { t } = useTranslation()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [offen, setOffen] = useState(false)

  const stand = useQuery({
    queryKey: ['neuigkeiten'],
    queryFn: () => api.get<Neuigkeiten>('/api/about/neuigkeiten'),
    enabled: user?.role === 'admin',
    staleTime: Infinity,
  })

  const quittieren = useMutation({
    mutationFn: () => api.post<Neuigkeiten>('/api/about/neuigkeiten/gesehen', {}),
    onSuccess: (daten) => {
      queryClient.setQueryData(['neuigkeiten'], daten)
      setOffen(false)
    },
  })

  if (user?.role !== 'admin' || !stand.data?.offen) return null

  /* Der Text zur laufenden Fassung. Er liegt in den Sprachdateien unter
     `whatsNew.entries`, nicht im Code: So lässt er sich vor jedem Release
     schreiben, ohne dass jemand eine Komponente anfasst.
     ⚠️ **Nicht über einen zusammengesetzten Schlüssel suchen.** Eine
     Versionsnummer enthält Punkte, und i18next trennt seine Schlüssel genau
     daran — `whatsNew.entries.0.15.0` wird zu fünf Ebenen. Je nach Fassung
     kommt dabei der Schlüsseltext selbst zurück statt `null`, und der
     nächste Zugriff auf `.sections` reißt die ganze Oberfläche mit. Deshalb
     einmal das Verzeichnis holen und in JavaScript nachschlagen. */
  const alleEintraege = t('whatsNew.entries', {
    returnObjects: true,
    defaultValue: {},
  }) as Record<string, unknown>
  const roh =
    alleEintraege && typeof alleEintraege === 'object'
      ? alleEintraege[stand.data.version]
      : undefined
  const eintrag = istEintrag(roh) ? roh : null

  return (
    <>
      <div className="relative z-10 border-b border-accent-500/40 bg-accent-500/10">
        <div className="mx-auto w-full max-w-7xl px-4 py-3 sm:px-6">
          <div className="flex flex-wrap items-center gap-3 text-sm text-accent-400">
            <span>
              <b>{t('whatsNew.title')}</b>{' '}
              {t('whatsNew.hint', { version: stand.data.version })}
            </span>
            <button
              type="button"
              onClick={() => setOffen(true)}
              className="ml-auto rounded-full border border-accent-500/50 px-3 py-1 text-xs font-semibold transition-colors hover:bg-accent-500/15"
            >
              {t('whatsNew.action')}
            </button>
          </div>
        </div>
      </div>

      <Fenster
        offen={offen}
        titel={t('whatsNew.title')}
        unterzeile={t('whatsNew.subtitle', { version: stand.data.version })}
        onSchliessen={() => setOffen(false)}
        fuss={
          <>
            <Button variant="ghost" onClick={() => setOffen(false)}>
              {t('common.close')}
            </Button>
            <Button
              onClick={() => quittieren.mutate()}
              loading={quittieren.isPending}
            >
              {t('whatsNew.dismiss')}
            </Button>
          </>
        }
      >
        {eintrag ? (
          <div className="flex flex-col gap-6">
            <p className="text-sm leading-relaxed text-mist-300">{eintrag.lead}</p>

            {/* Zuerst die Funktionen – und je Funktion der Weg dorthin. Ein
                Betreiber will nach dem Update nicht wissen, was sich im Code
                geändert hat, sondern was er jetzt anders machen kann. */}
            <ol className="flex flex-col gap-5">
              {eintrag.sections.map((abschnitt) => (
                <li key={abschnitt.title} className="border-l-2 border-accent-500/40 pl-4">
                  <h4 className="font-semibold">{abschnitt.title}</h4>
                  <p className="mt-0.5 font-mono text-xs text-accent-400">
                    {abschnitt.where}
                  </p>
                  <p className="mt-1.5 text-sm leading-relaxed text-mist-300">
                    {abschnitt.body}
                  </p>
                </li>
              ))}
            </ol>

            {/* Kleinkram klein: Es soll auffindbar sein, aber den Blick nicht
                von dem nehmen, worum es geht. */}
            {eintrag.small.length > 0 && (
              <div className="border-t border-ink-700 pt-4">
                <h4 className="text-xs font-semibold tracking-wide text-mist-500 uppercase">
                  {eintrag.smallTitle || t('whatsNew.smallFallback')}
                </h4>
                <ul className="mt-2 flex flex-col gap-1.5">
                  {eintrag.small.map((zeile) => (
                    <li key={zeile} className="flex gap-2 text-xs leading-relaxed text-mist-500">
                      <span aria-hidden="true">·</span>
                      <span>{zeile}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : (
          /* Kein Text zu dieser Fassung – dann führt der Verweis auf die
             Release-Seite, statt ein leeres Fenster zu zeigen. */
          <p className="text-sm leading-relaxed text-mist-300">
            {t('whatsNew.fallback')}{' '}
            <a
              href="https://github.com/DerKezorm/nexview/releases"
              target="_blank"
              rel="noreferrer"
              className="text-accent-400 underline underline-offset-2"
            >
              GitHub
            </a>
          </p>
        )}
      </Fenster>
    </>
  )
}
