/**
 * Welche Dienste eine Schritt-für-Schritt-Anleitung haben, und welche Schritte.
 *
 * Nur dort, wo die Einrichtung außerhalb von Nexview stattfindet und mehr als
 * zwei Handgriffe braucht. Bei Gotify und ntfy trägt man eine Adresse und ein
 * Token ein, dafür genügen die Hinweise am Feld. Telegram dagegen verlangt ein
 * Konto, einen Bot, ein Token und eine Chat-Kennung, und jeder dieser Schritte
 * spielt woanders.
 *
 * Die Liste steht neben der Anzeige, weil die Kanal-Einstellungen nur wissen
 * müssen, *ob* es eine Anleitung gibt, und weil `ChannelHelp.tsx` damit nur
 * noch Bauteile ausliefert. Nur solche Dateien tauscht Vite im
 * Entwicklungsbetrieb im laufenden Bild aus.
 */

import type { ChannelKind } from '../../api/types'

type Verweis = { href: string; label: string }

export type Schritt = {
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

export const ANLEITUNGEN: Partial<Record<ChannelKind, Schritt[]>> = {
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
