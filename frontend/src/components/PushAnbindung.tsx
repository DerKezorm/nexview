import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'

import { arbeiter, moeglich, sicherstellen } from '../lib/push'

/* Einmal je Sitzung des Browsers nachmelden, nicht bei jedem Seitenwechsel.
   Der Server legt dieselbe Adresse ohnehin kein zweites Mal an; die Zeile
   spart nur den Aufruf. */
const GEMELDET = 'nexview.push.gemeldet'

function schonGemeldet(): boolean {
  try {
    return sessionStorage.getItem(GEMELDET) === '1'
  } catch {
    return false
  }
}

/**
 * Web Push beim Start der Anwendung anbinden.
 *
 * ⚠️ **Registrieren und Fragen sind zwei Dinge.** Hier wird der Service Worker
 * registriert und ein Gerät mit längst erteilter Erlaubnis still nachgemeldet;
 * das fragt niemanden etwas und ist die Voraussetzung dafür, dass ein Gerät
 * nach einem Serverumzug oder gelöschten Browserdaten weiterläuft. Die
 * **Nachfrage** steht hinter einem Klick unter Profil → Benachrichtigungen →
 * Web Push: Eine, die beim Laden aus dem Nichts kommt, klickt man weg, und
 * danach ist die Funktion für dieses Gerät dauerhaft zu.
 *
 * Zeichnet nichts. Steht nur im Baum der angemeldeten Erwachsenen, damit die
 * Nachmeldung nie ohne Sitzung läuft.
 */
export function PushAnbindung() {
  const { i18n } = useTranslation()

  useEffect(() => {
    if (!moeglich()) return
    void arbeiter().catch(() => undefined)

    if (schonGemeldet()) return

    void sicherstellen(i18n.language)
      .then(() => {
        try {
          sessionStorage.setItem(GEMELDET, '1')
        } catch {
          /* Ohne sessionStorage wird beim nächsten Start eben noch einmal gemeldet. */
        }
      })
      .catch(() => undefined)
    // Nur beim Start: Ein Sprachwechsel meldet nicht neu, die Sprache des
    // Geräts wird beim nächsten Start mitgezogen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return null
}
