import { resolve } from 'node:path'

import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

/**
 * Baut die Bausteine als Bibliothek.
 *
 * React und der Router bleiben aussen vor (`external`): das Buendel des
 * Wandlers liefert sie selbst mit, und zwei React-Kopien in einer Seite fuehren
 * zu genau den Fehlern, die niemand sucht.
 */
export default defineConfig({
  // Ohne diese Wurzel nimmt Vite das Frontend-Verzeichnis und ueberschreibt
  // dessen dist/ - also das gebaute Anwendungs-Buendel.
  root: __dirname,
  plugins: [react(), tailwindcss()],
  build: {
    outDir: 'dist',
    lib: {
      entry: resolve(__dirname, 'src/index.ts'),
      formats: ['es'],
      fileName: () => 'index.es.js',
    },
    rollupOptions: {
      external: ['react', 'react-dom', 'react/jsx-runtime', 'react-router-dom', 'react-i18next', 'i18next'],
    },
    // Ein einziges Stylesheet statt eines je Einstiegspunkt.
    cssCodeSplit: false,
    emptyOutDir: true,
  },
})
