/**
 * Das Konto, mit dem der Ende-zu-Ende-Test arbeitet.
 *
 * Steht in einer eigenen Datei, weil die Konfiguration denselben Python-Pfad
 * braucht wie der Test - und zwei Fassungen davon liefen beim ersten
 * Feinschliff auseinander.
 */

import path from 'node:path'
import { fileURLToPath } from 'node:url'

const hier = path.dirname(fileURLToPath(import.meta.url))
export const WURZEL = path.resolve(hier, '..', '..')

/**
 * In der CI liegt Python schlicht im Pfad; hier daneben in der venv. Über
 * `NEXVIEW_E2E_PYTHON` lässt sich beides überstimmen.
 */
export const PYTHON =
  process.env.NEXVIEW_E2E_PYTHON ||
  (process.platform === 'win32'
    ? path.join(WURZEL, 'backend', '.venv', 'Scripts', 'python.exe')
    : 'python')

export const KONTO = {
  username: 'e2e-admin',
  password: 'Ein-langes-Passwort-1234',
  email: 'e2e@beispiel.test',
  // Englisch, weil das die Sprache ist, die die meisten sehen. Der Test hängt
  // damit an denselben Texten wie eine frische Installation irgendwo auf der
  // Welt.
  language: 'en',
}
