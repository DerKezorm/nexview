import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Wohin der Proxy zeigt. Standard ist Port 8000; ueber NEXVIEW_API laesst er
// sich umbiegen, wenn dort schon das Backend einer anderen Sitzung laeuft -
// dann startet man Backend und Oberflaeche einfach auf eigenen Ports.
const apiZiel = process.env.NEXVIEW_API || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
