/**
 * Heller oder dunkler Modus.
 *
 * Umgeschaltet wird ueber ein Merkmal am <html>-Element; welche Farben dahinter
 * stecken, entscheidet allein styles/index.css. Deshalb steht hier nichts
 * ueber Farben - nur, welcher Modus gilt und wo er liegt.
 *
 * Die Wahl gehoert zum **Konto**: sie wird im Profil gespeichert, damit jeder
 * seine eigene Voreinstellung hat und sie auf jedem Geraet wiederfindet. Was
 * im Browser liegt, ist nur eine Kopie davon - gebraucht wird sie fuer den
 * Moment vor dem Anmelden und fuer das Skript in index.html, das die Farben
 * setzt, bevor ueberhaupt etwas gezeichnet wird.
 */

export type Theme = 'dark' | 'light'

const SCHLUESSEL = 'nexview.theme'

export function istTheme(wert: unknown): wert is Theme {
  return wert === 'dark' || wert === 'light'
}

/** Die zuletzt angewandte Wahl aus dem Browser - Ersatz, bis das Profil da ist. */
export function gespeichertesTheme(): Theme {
  try {
    return localStorage.getItem(SCHLUESSEL) === 'light' ? 'light' : 'dark'
  } catch {
    // Privater Modus mancher Browser verbietet den Zugriff.
    return 'dark'
  }
}

/** Merkmal setzen und merken. Die Farbe der Browserleiste zieht mit. */
export function themeAnwenden(theme: Theme): void {
  const wurzel = document.documentElement
  if (theme === 'light') {
    wurzel.setAttribute('data-theme', 'light')
  } else {
    wurzel.removeAttribute('data-theme')
  }

  const leiste = document.querySelector('meta[name="theme-color"]')
  leiste?.setAttribute('content', theme === 'light' ? '#f5f5f8' : '#0b0b0f')

  try {
    localStorage.setItem(SCHLUESSEL, theme)
  } catch {
    // Dann gilt die Wahl eben nur bis zum Neuladen.
  }
}
