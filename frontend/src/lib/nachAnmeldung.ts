/**
 * Zurück zum Code des Fernsehers, wenn man sich erst anmelden musste.
 *
 * Der QR-Code eines Fernsehers öffnet `/link?code=…`. Wer im Browser des
 * Telefons noch nicht angemeldet ist, sieht zuerst die Anmeldeseite, und nach
 * der Anmeldung führt nexview sonst immer auf die Startseite. Bei der
 * Anmeldung über einen Anbieter ist die Adresse ohnehin weg: Der Rückweg vom
 * Anbieter endet auf `/`.
 *
 * Deshalb merkt sich die Anmeldeseite die Adresse im `sessionStorage` (nur
 * dieser Tab, höchstens zehn Minuten, so lange wie ein Code gilt), und nach
 * der Anmeldung geht es einmal dorthin zurück.
 *
 * ⚠️ **Nur `/link`.** Ein allgemeines „zurück, wo du warst" ist bewusst nicht
 * gebaut (siehe `LoginPage`), und eine gemerkte Adresse ist nie ein Weg nach
 * draußen: Was nicht mit `/link` beginnt, wird verworfen.
 */

const SCHLUESSEL = 'nexview.nachAnmeldung'
const GILT_MS = 10 * 60 * 1000
const SEITE = '/link'

function istGeraeteSeite(ziel: string): boolean {
  return ziel === SEITE || ziel.startsWith(`${SEITE}?`)
}

/** Die Adresse, wenn gerade die Seite für den Fernseher-Code offen ist. */
export function geraeteSeite(): string | null {
  const ziel = window.location.pathname + window.location.search
  return istGeraeteSeite(ziel) ? ziel : null
}

export function merken(ziel: string): void {
  try {
    window.sessionStorage.setItem(SCHLUESSEL, JSON.stringify({ ziel, bis: Date.now() + GILT_MS }))
  } catch {
    // Ohne Speicher (privates Fenster) bleibt nur der Weg über die Startseite.
  }
}

/** Die gemerkte Adresse, genau einmal - danach ist sie vergessen. */
export function abholen(): string | null {
  try {
    const roh = window.sessionStorage.getItem(SCHLUESSEL)
    window.sessionStorage.removeItem(SCHLUESSEL)
    if (!roh) return null
    const { ziel, bis } = JSON.parse(roh) as { ziel?: unknown; bis?: unknown }
    if (typeof ziel !== 'string' || typeof bis !== 'number' || bis < Date.now()) return null
    return istGeraeteSeite(ziel) ? ziel : null
  } catch {
    return null
  }
}
