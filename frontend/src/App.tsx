import { useEffect, useRef } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'

import { useAuth } from './auth/useAuth'
import { AppShell } from './components/AppShell'
import { KidsShell } from './components/KidsShell'
import { Logo } from './components/Logo'
import { Spinner } from './components/ui'
import { AboutPage } from './pages/AboutPage'
import { BrowsePage } from './pages/BrowsePage'
import { AdminRequestsPage } from './pages/AdminRequestsPage'
import { CalendarPage } from './pages/CalendarPage'
import { StoeberPage } from './pages/StoeberPage'
import { StoeberFilterPage } from './pages/StoeberFilterPage'
import { StoeberRegalPage } from './pages/StoeberRegalPage'
import { FavoritesPage } from './pages/FavoritesPage'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'
import {
  ForgotPasswordPage,
  InvitationPage,
  SetPasswordPage,
  VerifyEmailPage,
} from './pages/OnboardingPage'
import { MyRequestsPage } from './pages/MyRequestsPage'
import { PeoplePage } from './pages/PeoplePage'
import { PersonPage } from './pages/PersonPage'
import { TicketPage } from './pages/TicketPage'
import { TicketsPage } from './pages/TicketsPage'
import { TitlePage } from './pages/TitlePage'
import { ProfilePage } from './pages/ProfilePage'
import { SearchPage } from './pages/SearchPage'
import { SettingsPage } from './pages/SettingsPage'
import { SetupPage } from './pages/SetupPage'
import { StatsPage } from './pages/StatsPage'
import { KidsHomePage } from './pages/kids/KidsHomePage'
import { KidsSearchPage } from './pages/kids/KidsSearchPage'
import { KidsTitlePage } from './pages/kids/KidsTitlePage'
import { KidsWishesPage } from './pages/kids/KidsWishesPage'

/**
 * Die Kinderansicht: drei Ziele und die Titelseite - mehr gibt es nicht.
 *
 * Der Auffangpfad führt zurück auf die Startseite; ein Kind, das einen alten
 * Link öffnet, landet also nicht auf einer Fehlerseite, die es nicht lesen
 * kann.
 */
function KidsRoutes() {
  return (
    <Routes>
      <Route element={<KidsShell />}>
        <Route index element={<KidsHomePage />} />
        <Route path="suchen" element={<KidsSearchPage />} />
        <Route path="wuensche" element={<KidsWishesPage />} />
        <Route path="titel/:mediaType/:tmdbId" element={<KidsTitlePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

function BootScreen() {
  const { t } = useTranslation()
  return (
    <div className="nv-glow flex min-h-dvh flex-col items-center justify-center gap-4">
      <Logo className="h-12 w-12" />
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner />
        {t('common.loading')}
      </p>
    </div>
  )
}

/**
 * Seiten, die man ohne Anmeldung erreichen muss.
 *
 * Sie stehen bewusst **vor** der Anmeldeprüfung: wer einen Link aus einer
 * E-Mail öffnet, hat ja noch kein Passwort - und würde sonst auf der
 * Anmeldeseite landen, wo er nichts ausrichten kann.
 */
function PublicRoutes() {
  return (
    <Routes>
      <Route path="/einladung/:token" element={<InvitationPage />} />
      <Route path="/passwort/:token" element={<SetPasswordPage />} />
      <Route path="/bestaetigen/:token" element={<VerifyEmailPage />} />
      <Route path="/passwort-vergessen" element={<ForgotPasswordPage />} />
    </Routes>
  )
}

const PUBLIC_PREFIXES = ['/einladung/', '/passwort/', '/bestaetigen/', '/passwort-vergessen']

/**
 * Beim Sprachwechsel alles neu holen.
 *
 * Titel und Handlungen kommen von TMDB in der eingestellten Sprache. Die
 * Abfragen im Frontend merken sich ihr Ergebnis aber unter einem Schlüssel
 * ohne Sprache - nach dem Umschalten blieb deshalb der alte Text stehen, bis
 * man die Seite neu lud. Statt die Sprache in jeden einzelnen Schlüssel zu
 * schreiben (und sie bei der nächsten neuen Seite zu vergessen), wird hier
 * einmal zentral alles für ungültig erklärt.
 */
function useNeuLadenBeiSprachwechsel() {
  const { i18n } = useTranslation()
  const queryClient = useQueryClient()
  const zuletzt = useRef(i18n.language)

  useEffect(() => {
    if (zuletzt.current === i18n.language) return
    zuletzt.current = i18n.language
    void queryClient.invalidateQueries()
  }, [i18n.language, queryClient])
}

export default function App() {
  const { status, user, needsSetup } = useAuth()
  useNeuLadenBeiSprachwechsel()
  // useLocation statt window.location: nur so erfährt diese Komponente von
  // einem Seitenwechsel. Mit window.location blieb der Wert vom ersten
  // Rendern stehen - der Link "Passwort vergessen" führte ins Leere.
  const { pathname } = useLocation()
  const oeffentlich = PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix))

  if (oeffentlich) return <PublicRoutes />
  if (status === 'loading') return <BootScreen />
  if (needsSetup) return <SetupPage />
  if (!user) return <LoginPage />
  // Ein Kinderkonto bekommt einen **eigenen Seitenbaum**, nicht denselben mit
  // ausgeblendeten Punkten. Was hier nicht steht, existiert für ein Kind nicht
  // - auch nicht über die Adresszeile. Die Sperre selbst sitzt im Backend
  // (`require_adult` an allen Erwachsenen-Routern); das hier ist die Ansicht
  // dazu, nicht der Schutz.
  if (user.role === 'child') return <KidsRoutes />

  const isAdmin = user.role === 'admin'
  /** Geschützte Bereiche sind zusätzlich im Backend abgesichert; hier wird nur umgeleitet. */
  const adminOnly = (element: React.ReactElement) =>
    isAdmin ? element : <Navigate to="/" replace />
  /** Freigaben dürfen auch Entscheider sehen. */
  const approverOnly = (element: React.ReactElement) =>
    user.can_approve ? element : <Navigate to="/" replace />

  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
        {/* Die alten Entdecken-Seiten sind entfernt. Die Adressen bleiben
            als Umleitung stehen: Ein Lesezeichen soll im Katalog landen und
            nicht auf der Startseite, wo man erst wieder suchen muss. */}
        <Route path="filme" element={<Navigate to="/stoebern" replace />} />
        <Route path="serien" element={<Navigate to="/stoebern/serien" replace />} />
        {/* Alte Adresse aus der Zeit, als der Assistent eine eigene Seite
            war. Er ist jetzt ein Fenster über der aktuellen Seite (siehe
            AppShell), deshalb führt der Weg auf den Katalog. */}
        <Route path="filmabend" element={<Navigate to="/stoebern?filmabend=movie" replace />} />
        {/* Stoebern liegt bewusst NEBEN "Entdecken", nicht darin: Die eine
            Seite fragt "was ist neu?", die andere "was schauen wir heute
            Abend?". Beide bleiben vorerst bestehen, damit sich vergleichen
            laesst, welche traegt. */}
        <Route path="stoebern" element={<StoeberPage mediaType="movie" />} />
        <Route path="stoebern/serien" element={<StoeberPage mediaType="tv" />} />
        <Route path="stoebern/regal/:mediaType/:kennung" element={<StoeberRegalPage />} />
        <Route path="stoebern/filter/:mediaType" element={<StoeberFilterPage />} />
        <Route path="kalender" element={<CalendarPage />} />
        <Route path="suche" element={<SearchPage />} />
        <Route path="personen" element={<PeoplePage />} />
        <Route path="profil" element={<ProfilePage />} />
        <Route path="mag-ich" element={<FavoritesPage />} />
        <Route path="tickets" element={<TicketsPage />} />
        <Route path="tickets/:ticketId" element={<TicketPage />} />
        <Route path="ueber" element={<AboutPage />} />
        {/* Vollbildseite je Titel und je Person - der Klick auf eine
            Kachel landet hier, nicht mehr im Popup. */}
        <Route path="titel/:mediaType/:tmdbId" element={<TitlePage />} />
        <Route path="person/:personId" element={<PersonPage />} />
        {/* Ergebnisliste zu einem Schlagwort bzw. Studio. */}
        <Route path="liste/:mediaType/:art/:id" element={<BrowsePage />} />
        <Route path="requests" element={<MyRequestsPage />} />
        <Route path="admin/requests" element={approverOnly(<AdminRequestsPage />)} />
        <Route path="admin/stats" element={approverOnly(<StatsPage />)} />
        {/* Benutzerverwaltung ist jetzt ein Reiter der Einstellungen. */}
        <Route path="admin/users" element={<Navigate to="/admin/settings" replace />} />
        <Route path="admin/settings" element={adminOnly(<SettingsPage />)} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
