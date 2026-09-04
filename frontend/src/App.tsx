import { Suspense, lazy, useEffect, useRef } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'

import { useAuth } from './auth/useAuth'
import { PushAnbindung } from './components/PushAnbindung'
import { AppShell } from './components/AppShell'
import { KidsShell } from './components/KidsShell'
import { Logo } from './components/Logo'
import { Spinner } from './components/ui'

/* ---------------------------------------------------------------------------
 * Die Alltagsseiten - sie kommen mit dem ersten Laden.
 *
 * ⚠️ **Was hier steht, trägt jeder Besucher mit herein.** Diese Seiten sind
 * genau die, die jemand im normalen Gebrauch ohnehin binnen Sekunden öffnet:
 * die Startseite, der Katalog, ein Titel, die Suche. Sie nachzuliefern hieße,
 * einen kleineren ersten Eindruck gegen eine kurze Wartezeit bei **jedem**
 * Klick zu tauschen - ein schlechter Tausch.
 * ------------------------------------------------------------------------ */
import { BrowsePage } from './pages/BrowsePage'
import { CalendarPage } from './pages/CalendarPage'
import { StoeberPage } from './pages/StoeberPage'
import { StoeberFilterPage } from './pages/StoeberFilterPage'
import { StoeberRegalPage } from './pages/StoeberRegalPage'
import { FavoritesPage } from './pages/FavoritesPage'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'
import { MyRequestsPage } from './pages/MyRequestsPage'
import { PeoplePage } from './pages/PeoplePage'
import { PersonPage } from './pages/PersonPage'
import { TicketPage } from './pages/TicketPage'
import { TicketsPage } from './pages/TicketsPage'
import { TitlePage } from './pages/TitlePage'
import { SearchPage } from './pages/SearchPage'

/* ---------------------------------------------------------------------------
 * Der Nachschub - erst holen, wenn jemand hingeht.
 *
 * ⚠️ **Hier lag der ganze Ballast.** Die Oberfläche war ein einziges Stück
 * von 1.392 kB, und jeder Besucher trug es komplett herein: dreißig
 * Einstellungsseiten, das Betreiber-Dashboard, sechs Analysereiter, die
 * Kinderansicht, den Einrichtungsassistenten. Wer nur einen Film wünschen
 * wollte, schleppte das gesamte Werkzeug des Betreibers mit.
 *
 * ⚠️ **Der `import(...)` muss wörtlich dastehen.** Über eine Variable
 * erkennt der Bau nicht, dass daraus eine eigene Datei werden soll, und packt
 * vorsichtshalber wieder alles zusammen. Aufgefallen wäre das nur an der
 * Waage im automatischen Bau.
 *
 * ⚠️ **Und die Adressen dieser Dateien stehen nicht fest.** Läuft Nexview
 * unter `/nexview`, muss der Nachschub von dort kommen und nicht von der
 * Wurzel der Domain. Wie das zusammenhängt, steht in `vite.config.ts`.
 * ------------------------------------------------------------------------ */
const AboutPage = lazy(() => import('./pages/AboutPage').then((m) => ({ default: m.AboutPage })))
const AdminRequestsPage = lazy(() =>
  import('./pages/AdminRequestsPage').then((m) => ({ default: m.AdminRequestsPage })),
)
const AdminDashboardPage = lazy(() =>
  import('./pages/AdminDashboardPage').then((m) => ({ default: m.AdminDashboardPage })),
)
const ProfilePage = lazy(() =>
  import('./pages/ProfilePage').then((m) => ({ default: m.ProfilePage })),
)
const SettingsPage = lazy(() =>
  import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })),
)
const SetupPage = lazy(() => import('./pages/SetupPage').then((m) => ({ default: m.SetupPage })))
const StatsPage = lazy(() => import('./pages/StatsPage').then((m) => ({ default: m.StatsPage })))

// Die Seiten aus einer E-Mail: Einladung, Passwort, Adressbestätigung. Sie
// stehen alle in **einer** Datei, werden also auch als eine geholt - wer einen
// solchen Link öffnet, bekommt genau sie und sonst nichts.
const ForgotPasswordPage = lazy(() =>
  import('./pages/OnboardingPage').then((m) => ({ default: m.ForgotPasswordPage })),
)
const InvitationPage = lazy(() =>
  import('./pages/OnboardingPage').then((m) => ({ default: m.InvitationPage })),
)
const SetPasswordPage = lazy(() =>
  import('./pages/OnboardingPage').then((m) => ({ default: m.SetPasswordPage })),
)
const VerifyEmailPage = lazy(() =>
  import('./pages/OnboardingPage').then((m) => ({ default: m.VerifyEmailPage })),
)

// Die Kinderansicht ist ein eigener Seitenbaum - und für die allermeisten
// Konten toter Ballast.
const KidsHomePage = lazy(() =>
  import('./pages/kids/KidsHomePage').then((m) => ({ default: m.KidsHomePage })),
)
const KidsSearchPage = lazy(() =>
  import('./pages/kids/KidsSearchPage').then((m) => ({ default: m.KidsSearchPage })),
)
const KidsTitlePage = lazy(() =>
  import('./pages/kids/KidsTitlePage').then((m) => ({ default: m.KidsTitlePage })),
)
const KidsWishesPage = lazy(() =>
  import('./pages/kids/KidsWishesPage').then((m) => ({ default: m.KidsWishesPage })),
)

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
    // Hier gibt es keinen Rahmen, in dem ein kleines Ladezeichen sitzen
    // könnte - also dasselbe Bild wie beim Start der Anwendung.
    <Suspense fallback={<BootScreen />}>
      <Routes>
        <Route path="/einladung/:token" element={<InvitationPage />} />
        <Route path="/passwort/:token" element={<SetPasswordPage />} />
        <Route path="/bestaetigen/:token" element={<VerifyEmailPage />} />
        <Route path="/passwort-vergessen" element={<ForgotPasswordPage />} />
      </Routes>
    </Suspense>
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
  if (needsSetup)
    return (
      <Suspense fallback={<BootScreen />}>
        <SetupPage />
      </Suspense>
    )
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
      {/* Die Push-Anbindung hängt am Rahmen der Erwachsenen: Sie meldet ein
          Gerät nur nach, solange jemand angemeldet ist, und fragt nie. */}
      <Route
        element={
          <>
            <PushAnbindung />
            <AppShell />
          </>
        }
      >
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
        {/* Das Betreiber-Dashboard: was ist kaputt, was wartet. Bewusst eine
            eigene Adresse und kein Aufsatz auf der Startseite - die bleibt
            unveraendert erreichbar, und der Platz laesst sich spaeter ohne
            Umbau aendern. */}
        <Route path="admin/dashboard" element={adminOnly(<AdminDashboardPage />)} />
        {/* Seit 0.25 admin-only: Auf der Seite stehen jetzt Betriebsdaten -
            Instanz-Zustand, Plattenfuellstand, Sicherungen. */}
        <Route path="admin/stats" element={adminOnly(<StatsPage />)} />
        {/* Benutzerverwaltung ist jetzt ein Reiter der Einstellungen. */}
        <Route path="admin/users" element={<Navigate to="/admin/settings" replace />} />
        <Route path="admin/settings" element={adminOnly(<SettingsPage />)} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
