import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import type { ChannelKind } from '../../api/types'

/**
 * Schritt-für-Schritt-Anleitung zu einem Dienst.
 *
 * Nur dort, wo die Einrichtung außerhalb von Nexview stattfindet und mehr als
 * zwei Handgriffe braucht. Bei Gotify und ntfy trägt man eine Adresse und ein
 * Token ein – dafür genügen die Hinweise am Feld. Telegram dagegen verlangt ein
 * Konto, einen Bot, ein Token und eine Chat-Kennung, und jeder dieser Schritte
 * spielt woanders.
 *
 * Sie steht **in** der rechten Spalte, nicht in einem Fenster darüber: Dort
 * liegen sonst die Postfächer, und die gibt es ohnehin erst, wenn die
 * Verbindung steht. So bleibt beim Lesen sichtbar, welches Feld links gerade
 * gemeint ist.
 */

type Verweis = { href: string; label: string }

type Schritt = {
  /** Schlüssel unter `channels.guide.<dienst>.` */
  key: string
  links?: Verweis[]
  /**
   * Bild zum Schritt. Bewusst noch nirgends gesetzt: Aufnahmen fremder
   * Oberflächen veralten schnell, und erfundene wären schlimmer als keine.
   * Sobald echte vorliegen, genügt hier ein Dateiname.
   */
  bild?: string
}

const ANLEITUNGEN: Partial<Record<ChannelKind, Schritt[]>> = {
  telegram: [
    {
      key: 'account',
      links: [
        { href: 'https://desktop.telegram.org', label: 'desktop.telegram.org' },
        { href: 'https://web.telegram.org', label: 'web.telegram.org' },
      ],
    },
    { key: 'botfather', links: [{ href: 'https://t.me/BotFather', label: '@BotFather' }] },
    { key: 'newbot' },
    { key: 'token' },
    { key: 'instance' },
    { key: 'checktoken' },
    { key: 'startchat' },
    { key: 'group' },
    { key: 'finish' },
  ],
}

export function hatAnleitung(kanal: ChannelKind): boolean {
  return Boolean(ANLEITUNGEN[kanal])
}

/** Der runde Knopf, der die Anleitung ein- und ausblendet. */
export function HilfeKnopf({ offen, onKlick }: { offen: boolean; onKlick: () => void }) {
  const { t } = useTranslation()

  return (
    <button
      type="button"
      onClick={onKlick}
      aria-label={t(offen ? 'channels.guideClose' : 'channels.guideOpen')}
      title={t(offen ? 'channels.guideClose' : 'channels.guideOpen')}
      aria-pressed={offen}
      className={
        'flex h-9 w-9 shrink-0 items-center justify-center rounded-full border ' +
        'transition-colors ' +
        (offen
          ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
          : 'border-ink-700 bg-ink-850 text-mist-500 hover:border-accent-500/50 hover:text-accent-400')
      }
    >
      <svg
        viewBox="0 0 24 24"
        className="h-4 w-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      >
        <circle cx="12" cy="12" r="9" />
        <path d="M9.6 9.3a2.5 2.5 0 1 1 3.3 2.4c-.6.2-.9.7-.9 1.3v.5" strokeLinecap="round" />
        <path d="M12 16.6h.01" strokeLinecap="round" />
      </svg>
    </button>
  )
}

/**
 * Die Schritte als senkrechte Zeitleiste: Nummern auf einer durchgehenden
 * Linie, daneben je eine Box.
 */
export function Anleitung({ kanal, symbol }: { kanal: ChannelKind; symbol?: ReactNode }) {
  const { t } = useTranslation()
  const schritte = ANLEITUNGEN[kanal]
  if (!schritte) return null

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start gap-3">
        {symbol}
        <div>
          <p className="font-semibold text-mist-100">{t(`channels.guide.${kanal}.title`)}</p>
          <p className="mt-1 text-sm leading-relaxed text-mist-500">
            {t(`channels.guide.${kanal}.intro`)}
          </p>
        </div>
      </div>

      <ol className="relative flex flex-col gap-4">
        {/* Die durchgehende Linie hinter den Nummern. */}
        <span
          aria-hidden="true"
          className="absolute bottom-4 left-4 top-4 w-px -translate-x-1/2 bg-accent-500/25"
        />

        {schritte.map((schritt, nummer) => (
          <li key={schritt.key} className="relative flex gap-4">
            <span
              aria-hidden="true"
              className={
                'relative z-10 mt-1 flex h-8 w-8 shrink-0 items-center justify-center ' +
                'rounded-full border border-accent-500/70 bg-ink-900 text-sm font-semibold ' +
                'text-accent-400'
              }
            >
              {nummer + 1}
            </span>

            <div className="flex-1 rounded-2xl border border-ink-700 bg-ink-900/70 px-4 py-3">
              <p className="text-sm font-medium text-mist-100">
                {t(`channels.guide.${kanal}.${schritt.key}.title`)}
              </p>
              <p className="mt-1 text-sm leading-relaxed text-mist-400">
                {t(`channels.guide.${kanal}.${schritt.key}.body`)}
              </p>

              {schritt.links && (
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                  {schritt.links.map((verweis) => (
                    <a
                      key={verweis.href}
                      href={verweis.href}
                      target="_blank"
                      rel="noreferrer"
                      className="text-sm text-accent-400 hover:underline"
                    >
                      {verweis.label} ↗
                    </a>
                  ))}
                </div>
              )}

              {schritt.bild && (
                <img
                  src={schritt.bild}
                  alt=""
                  className="mt-3 max-w-full rounded-xl border border-ink-700"
                />
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}
