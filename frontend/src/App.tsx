import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useAuth } from './auth/useAuth'
import { AppShell } from './components/AppShell'
import { Logo } from './components/Logo'
import { Spinner } from './components/ui'
import { AdminRequestsPage } from './pages/AdminRequestsPage'
import { DiscoverPage } from './pages/DiscoverPage'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'
import {
  ForgotPasswordPage,
  InvitationPage,
  SetPasswordPage,
  VerifyEmailPage,
} from './pages/OnboardingPage'
import { MyRequestsPage } from './pages/MyRequestsPage'
import { ProfilePage } from './pages/ProfilePage'
import { SearchPage } from './pages/SearchPage'
import { SettingsPage } from './pages/SettingsPage'
import { SetupPage } from './pages/SetupPage'
import { StatsPage } from './pages/StatsPage'

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

export default function App() {
  const { status, user, needsSetup } = useAuth()
  // useLocation statt window.location: nur so erfährt diese Komponente von
  // einem Seitenwechsel. Mit window.location blieb der Wert vom ersten
  // Rendern stehen - der Link "Passwort vergessen" führte ins Leere.
  const { pathname } = useLocation()
  const oeffentlich = PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix))

  if (oeffentlich) return <PublicRoutes />
  if (status === 'loading') return <BootScreen />
  if (needsSetup) return <SetupPage />
  if (!user) return <LoginPage />

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
        <Route path="filme" element={<DiscoverPage mediaType="movie" />} />
        <Route path="serien" element={<DiscoverPage mediaType="tv" />} />
        <Route path="suche" element={<SearchPage />} />
        <Route path="profil" element={<ProfilePage />} />
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
