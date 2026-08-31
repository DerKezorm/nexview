import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import App from './App'
import { Auffangnetz } from './components/Auffangnetz'
import { AuthProvider } from './auth/AuthProvider'
import { BASIS, basisPruefen } from './lib/basis'
import { i18nStarten } from './i18n'
import { startFehlgeschlagen } from './lib/startFehlgeschlagen'
import './styles/index.css'

// Wer die Seite bei gesetztem Unterpfad ohne Vorbau geöffnet hat, wird sofort
// umgelenkt - vor dem ersten Rendern, sonst passt die Adresse nicht zum Router.
basisPruefen()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 60_000,
    },
  },
})

function anwendungStarten(): void {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      {/* ⚠️ **Ganz außen, damit wirklich alles darunter liegt.** Ein
          Auffangnetz fängt nur, was *unter* ihm gezeichnet wird - stünde es
          innerhalb des Routers, bliebe ein Fehler im Router selbst wieder eine
          weiße Seite. */}
      <Auffangnetz>
        <QueryClientProvider client={queryClient}>
          {/* Mit Unterpfad verwaltet der Router Adressen wie /nexview/profil -
              basename hält die Routen-Definitionen davon frei. */}
          <BrowserRouter basename={BASIS || undefined}>
            <AuthProvider>
              <App />
            </AuthProvider>
          </BrowserRouter>
        </QueryClientProvider>
      </Auffangnetz>
    </StrictMode>,
  )
}

/* ⚠️ **Erst die Texte, dann die Oberfläche.** Seit jede Sprache einzeln
 * nachkommt, sind sie beim Start noch nicht da. Ohne dieses Warten stünde für
 * einen Wimpernschlag die rohe Schlüsselliste auf dem Bildschirm - ein
 * Anblick, den jeder als kaputt liest.
 *
 * ⚠️ **Und deshalb der zweite Zweig.** Was übers Netz kommt, kann ausbleiben;
 * fest eingebaute Texte konnten das nicht. Ohne ihn bliebe der Bildschirm dann
 * für immer leer - kein Bild, keine Meldung, nichts zum Anklicken. Genau die
 * Sorte Fehler, bei der ein Besucher die ganze Anwendung für kaputt hält.
 *
 * Zwei Zweige an **einem** `then`, nicht `.catch(...).then(...)`: So läuft
 * immer genau einer von beiden. Hintereinandergehängt liefe nach dem
 * Auffangen auch noch der Erfolgszweig.
 */
i18nStarten().then(anwendungStarten, startFehlgeschlagen)
