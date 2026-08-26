import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Wohin der Proxy zeigt. Standard ist Port 8000; ueber NEXVIEW_API laesst er
// sich umbiegen, wenn dort schon das Backend einer anderen Sitzung laeuft -
// dann startet man Backend und Oberflaeche einfach auf eigenen Ports.
const apiZiel = process.env.NEXVIEW_API || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],

  /* Tests laufen ohne Browser und ohne Backend.
   *
   * ⚠️ **Und damit ausdrücklich nicht alles.** Was ein Test hier prüft, ist
   * das Verhalten der Oberfläche gegen eine **ersetzte** API-Schicht - also
   * das, was Nexview selbst tut. Ob der Browser die Anmeldung über ein
   * HttpOnly-Cookie wirklich über ein Neuladen hinweg hält, kann jsdom nicht
   * beweisen; dafür gibt es den einen Playwright-Test in `e2e/`.
   *
   * `globals: true` spart in jeder Testdatei drei Importzeilen und ist die
   * Voreinstellung, die die Testing Library ohnehin erwartet. */
  test: {
    environment: 'jsdom',
    globals: true,
    // Reihenfolge zählt: `globals.ts` rückt Node-Eigenheiten zurecht,
    // bevor irgendein Anwendungsmodul geladen wird.
    setupFiles: ['./src/test/globals.ts', './src/test/setup.ts'],
    css: false,
    // Die Ende-zu-Ende-Tests laufen mit Playwright, nicht hier - sonst
    // versucht Vitest sie zu starten und scheitert an fehlendem Browser.
    exclude: ['node_modules/**', 'e2e/**', 'dist/**'],
  },
  server: {
    port: 5173,
    // Im Entwicklungsmodus laeuft das Backend separat.
    // Der Proxy sorgt dafuer, dass der Browser trotzdem nur eine Adresse sieht.
    proxy: {
      '/api': {
        target: apiZiel,
        changeOrigin: true,
      },
    },
  },
})
