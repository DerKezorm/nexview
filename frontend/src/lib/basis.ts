/**
 * Der Unterpfad, unter dem Nexview läuft (`NEXVIEW_URL_BASE`).
 *
 * Läuft Nexview hinter einem Reverse Proxy unter `https://domain.tld/nexview/`,
 * schreibt das Backend beim Start ein Inline-Skript in die ausgelieferte
 * `index.html`, das `window.__NEXVIEW_BASIS__` setzt. Zur **Laufzeit** statt
 * beim Bauen, weil es genau ein Docker-Abbild für alle gibt - ein fest
 * eingebauter Pfad wäre für jeden falsch, der einen anderen (oder keinen)
 * benutzt. Im Dev-Server und ohne gesetzte Variable ist die Basis leer und
 * alles verhält sich exakt wie bisher.
 *
 * Alle Stellen, die eine absolute Adresse an den Browser geben, holen sich
 * den Vorbau hier - und nur hier. Die vielen `/api/...`-Literale in den
 * Seiten bleiben unangetastet; `api/client.ts` setzt den Vorbau zentral davor.
 */

declare global {
  interface Window {
    __NEXVIEW_BASIS__?: string
  }
}

const roh = typeof window !== 'undefined' ? window.__NEXVIEW_BASIS__ : undefined

/** Der Vorbau, z. B. `/nexview` - oder `''`, wenn Nexview an der Wurzel wohnt. */
export const BASIS: string = typeof roh === 'string' ? roh : ''

/** Einen wurzel-absoluten Pfad (`/api/...`, `/profil`) um den Vorbau ergänzen. */
export function mitBasis(pfad: string): string {
  return `${BASIS}${pfad}`
}

/**
 * Fehlt einer eingegebenen öffentlichen Adresse der Vorbau?
 *
 * Gebraucht für den Warnhinweis an der Adress-Einstellung: `public_url` steckt
 * in jedem verschickten Link, und eine Adresse ohne den Vorbau führt hinter
 * einem Proxy ins Leere - ohne dass es jemand sofort merkt. Eine Adresse, die
 * gar keine gültige URL ist, meldet hier nichts; dafür ist der Verbindungstest
 * zuständig.
 */
export function adresseOhneBasis(adresse: string): boolean {
  if (!BASIS) return false
  try {
    const pfad = new URL(adresse.trim()).pathname
    return !(pfad === BASIS || pfad.startsWith(`${BASIS}/`))
  } catch {
    return false
  }
}

/**
 * Direktzugriff ohne Vorbau auf die Adresse mit Vorbau umlenken.
 *
 * Wer bei gesetzter Basis `http://server:8000/` direkt öffnet (am Proxy
 * vorbei), bekäme eine Seite, deren Adresse nicht zum Router passt. Die
 * Umlenkung passiert bewusst **im Browser** statt auf dem Server: Sie prüft
 * die Adresse, die der Browser wirklich zeigt - und die trägt den Vorbau bei
 * beiden Proxy-Arten (durchreichen wie abschneiden) bereits. Ein
 * Server-Redirect könnte das nicht unterscheiden und würde hinter einem
 * abschneidenden Proxy einen Umleitungs-Kreisverkehr bauen.
 */
export function basisPruefen(): void {
  if (!BASIS) return
  const { pathname, search, hash } = window.location
  if (pathname === BASIS || pathname.startsWith(`${BASIS}/`)) return
  window.location.replace(BASIS + pathname + search + hash)
}
