import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Wohin der Proxy zeigt. Standard ist Port 8000; ueber NEXVIEW_API laesst er
// sich umbiegen, wenn dort schon das Backend einer anderen Sitzung laeuft -
// dann startet man Backend und Oberflaeche einfach auf eigenen Ports.
const apiZiel = process.env.NEXVIEW_API || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],

  /* Woher die Oberfläche ihre nachgelieferten Teile holt.
   *
   * ⚠️ **Das ist die Stelle, an der der Unterpfad kaputtgehen würde.**
   * Nexview kann unter `https://domain.tld/nexview/` laufen. Die
   * `index.html` schreibt das Backend beim Start um, damit ihre Verweise
   * dorthin zeigen (`main._index_mit_basis`). Die nachgelieferten Teile holt
   * sich die Oberfläche aber **selbst**, später, mit Adressen, die hier beim
   * Bauen entstehen - und die zeigten sonst auf die Wurzel der Domain. Dort
   * horcht der Pförtner gar nicht: weiße Seite.
   *
   * ⚠️ **Und es fällt niemandem auf, der keinen Unterpfad benutzt.** Genau
   * deshalb wird die Adresse hier nicht eingebaut, sondern erst im Browser
   * berechnet - aus demselben Wert, den das Backend in die Seite schreibt.
   * Der Unterpfad ist eine Einstellung des Betreibers; ein eingebauter Pfad
   * wäre für jeden falsch, der einen anderen (oder keinen) hat.
   *
   * Nur `hostType: 'js'`: In der `index.html` müssen die Verweise absolut
   * bleiben (`/assets/…`), denn genau die schreibt das Backend um. Und sie
   * dürfen dort nicht relativ werden - dieselbe Seite wird ja auch für tiefe
   * Adressen wie `/nexview/admin/settings` ausgeliefert, von wo aus ein
   * relativer Verweis ins Leere zeigte.
   *
   * Bewiesen wird das von `e2e/unterpfad.spec.ts`: Der dortige Pförtner
   * beantwortet alles außerhalb von `/nexview` mit 404.
   */
  build: {
    /* Das Manifest ist die Zutatenliste des Baus: Welches Stück hängt fest am
     * Einstieg, welches wird nur bei Bedarf geholt. `tools/gewicht-pruefen.mjs`
     * rechnet daraus aus, was ein Besucher beim Öffnen wirklich herunterlädt -
     * ohne raten zu müssen, welche Datei wozu gehört. Es landet in
     * `dist/.vite/` und wird nie ausgeliefert.
     */
    manifest: true,
    rollupOptions: {
      output: {
        /* React, Router, Abfragen, i18next in eine eigene Datei.
         *
         * Das macht den ersten Besuch nicht leichter - dieselben Bytes, nur
         * anders verpackt. Es hilft beim **zweiten**: Dieses Grundgerüst
         * ändert sich nur, wenn eine Fremdbibliothek erneuert wird, also
         * selten. Nach einem Nexview-Update holt der Browser deshalb nur den
         * Anwendungsteil neu und behält die 318 kB Gerüst aus seinem
         * Zwischenspeicher.
         *
         * Nebenbei rutscht der Anwendungsteil damit unter die 500-kB-Marke,
         * ab der der Bau von sich aus warnt. Das ist Wirkung, nicht Absicht -
         * die eigentliche Grenze steht in der Waage im automatischen Bau.
         */
        manualChunks(id) {
          if (id.includes('node_modules')) return 'grundgeruest'
          return undefined
        },
      },
    },
  },

  experimental: {
    renderBuiltUrl(dateiname, { hostType }) {
      if (hostType !== 'js') return undefined
      return {
        runtime: `((window.__NEXVIEW_BASIS__ || '') + '/' + ${JSON.stringify(dateiname)})`,
      }
    },
  },

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
