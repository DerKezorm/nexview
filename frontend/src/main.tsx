import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import App from './App'
import { AuthProvider } from './auth/AuthProvider'
import { BASIS, basisPruefen } from './lib/basis'
import './i18n'
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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* Mit Unterpfad verwaltet der Router Adressen wie /nexview/profil -
          basename hält die Routen-Definitionen davon frei. */}
      <BrowserRouter basename={BASIS || undefined}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
