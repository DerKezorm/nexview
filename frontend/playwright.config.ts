/**
 * Der eine Test, den jsdom nicht ersetzen kann.
 *
 * Die 37 Vitest-Tests prüfen, was Nexview selbst tut - gegen eine ersetzte
 * API-Schicht, in einer nachgebauten Umgebung. Was sie **nicht** prüfen können,
 * ist die Zusammenarbeit mit einem echten Browser und einem echten Server: ob
 * das Anmelde-Cookie so gesetzt wird, dass der Browser es über ein Neuladen
 * hinweg zurückschickt, und ob das Abmelden es wirklich wegräumt. Das ist der
 * Kern von allem, was hinter der Anmeldung liegt - und es hängt an Dingen, die
 * nur ein Browser hat: `HttpOnly`, `SameSite`, `Path`.
 *
 * Deshalb startet diese Konfiguration beides selbst: ein Backend auf einer
 * **frischen, leeren Datenbank** und die Oberfläche davor. Eigene Ports, damit
 * ein nebenher laufendes Nexview nicht gestört wird, und ein eigenes
 * Datenverzeichnis, das vor jedem Lauf geleert wird.
 */

import { defineConfig, devices } from '@playwright/test'
import { rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { PYTHON, WURZEL as wurzel } from './e2e/konto'
import {
  UNTERPFAD_BACKEND_PORT,
  UNTERPFAD_DATEN,
  PROXY_ABSCHNEIDEN_PORT,
  PROXY_DURCHREICHEN_PORT,
} from './e2e/unterpfad-ports'

const hier = path.dirname(fileURLToPath(import.meta.url))

/** Eigene Ports: 8000/5173 gehören dem, der nebenher entwickelt. */
const BACKEND_PORT = 8799
const OBERFLAECHE_PORT = 5599

/**
 * ⚠️ **Frisch, nicht wiederverwendet.** Der Test läuft durch den
 * Einrichtungsassistenten, und der zeigt sich nur, solange es noch kein Konto
 * gibt. Ein Verzeichnis vom letzten Lauf hieße: beim zweiten Mal grün, weil
 * nichts mehr geprüft wird.
 */
const DATEN = path.join(hier, '.e2e-data')

/**
 * ⚠️ **Nur im Hauptprozess.** Playwright lädt diese Datei auch in jedem
 * Arbeitsprozess noch einmal - und der räumte dann die Datenbank weg, die der
 * längst laufende Server gerade offen hat. Unter Windows endet das in
 * „EPERM", unter Linux stiller und schlimmer: mit einem Server ohne Daten.
 * `TEST_WORKER_INDEX` setzt Playwright ausschließlich in den Arbeitsprozessen.
 */
if (process.env.TEST_WORKER_INDEX === undefined) {
  rmSync(DATEN, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 })
  rmSync(UNTERPFAD_DATEN, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 })
}

export default defineConfig({
  testDir: './e2e',
  // Ein Server, eine Datenbank - parallele Läufe würden sich gegenseitig die
  // Einrichtung wegnehmen.
  workers: 1,
  fullyParallel: false,
  // In der CI kein stilles Übergehen: ein `test.only`, das jemand vergessen
  // hat, soll dort auffallen und nicht die halbe Reihe abschalten.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],
  use: {
    baseURL: `http://127.0.0.1:${OBERFLAECHE_PORT}`,
    // Nur beim Fehlschlag, und nur beim zweiten Anlauf - sonst kostet jeder
    // grüne Lauf Zeit und Platz für nichts.
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],

  webServer: [
    {
      command: `"${PYTHON}" -m uvicorn app.main:app --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: path.join(wurzel, 'backend'),
      // ⚠️ Ohne eigenes Datenverzeichnis liefe der Test auf der Datenbank der
      // Entwicklungsinstanz - und legte dort ein Konto an.
      env: { NEXVIEW_DATA_DIR: DATEN },
      url: `http://127.0.0.1:${BACKEND_PORT}/api/setup/status`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
    {
      // ⚠️ **`--host` gehört dazu.** Ohne die Angabe hört Vite nur auf
      // `localhost` - und das ist auf manchen Rechnern ausschließlich die
      // IPv6-Adresse. Playwright fragt `127.0.0.1` und wartet dann bis zur
      // Zeitgrenze auf einen Server, der längst läuft.
      command: `npm run dev -- --host 127.0.0.1 --port ${OBERFLAECHE_PORT} --strictPort`,
      cwd: hier,
      env: { NEXVIEW_API: `http://127.0.0.1:${BACKEND_PORT}` },
      url: `http://127.0.0.1:${OBERFLAECHE_PORT}`,
      reuseExistingServer: false,
      timeout: 120_000,
    },

    // ── Der Unterpfad-Aufbau (unterpfad.spec.ts) ──────────────────────────
    //
    // Nexview unter https://…/nexview/ gibt es in keiner Entwicklungs-
    // Installation - deshalb wird die Umgebung hier nachgebaut: ein zweites
    // Backend mit NEXVIEW_URL_BASE und dem **gebauten** Frontend (der Spec
    // überspringt lokal mit Handgriff-Hinweis, wenn dist fehlt), davor je ein
    // Pförtner für die beiden Proxy-Betriebsarten.
    {
      command: `"${PYTHON}" -m uvicorn app.main:app --host 127.0.0.1 --port ${UNTERPFAD_BACKEND_PORT}`,
      cwd: path.join(wurzel, 'backend'),
      env: {
        NEXVIEW_DATA_DIR: UNTERPFAD_DATEN,
        NEXVIEW_URL_BASE: '/nexview',
        NEXVIEW_STATIC_DIR: path.join(hier, 'dist'),
      },
      // Die Wurzel bleibt bewusst bedienbar - darüber meldet sich der Server.
      url: `http://127.0.0.1:${UNTERPFAD_BACKEND_PORT}/api/setup/status`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
    {
      command: `node e2e/unterpfad-proxy.mjs`,
      cwd: hier,
      env: {
        PROXY_PORT: String(PROXY_DURCHREICHEN_PORT),
        PROXY_ZIEL: String(UNTERPFAD_BACKEND_PORT),
        PROXY_MODUS: 'durchreichen',
      },
      url: `http://127.0.0.1:${PROXY_DURCHREICHEN_PORT}/nexview/api/setup/status`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `node e2e/unterpfad-proxy.mjs`,
      cwd: hier,
      env: {
        PROXY_PORT: String(PROXY_ABSCHNEIDEN_PORT),
        PROXY_ZIEL: String(UNTERPFAD_BACKEND_PORT),
        PROXY_MODUS: 'abschneiden',
      },
      url: `http://127.0.0.1:${PROXY_ABSCHNEIDEN_PORT}/nexview/api/setup/status`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
})
