import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { AdminAddressSettings } from './settings/AdminAddressSettings'
import { AdminApiToken } from './settings/AdminApiToken'
import { AdminChannelSettings } from './settings/AdminChannelSettings'
import { AdminHausordnung } from './settings/AdminHausordnung'
import { AdminHausordnungUebersicht } from './settings/AdminHausordnungUebersicht'
import { AdminLogsSettings } from './settings/AdminLogsSettings'
import { AdminSicherungen } from './settings/AdminSicherungen'
import { AdminMailSettings } from './settings/AdminMailSettings'
import { AdminOidcSettings } from './settings/AdminOidcSettings'
import { AdminServicesSettings } from './settings/AdminServicesSettings'
import { AdminStorageSettings } from './settings/AdminStorageSettings'
import { AdminBlocklistSettings } from './settings/AdminBlocklistSettings'
import { AdminUsersSettings } from './settings/AdminUsersSettings'
import { AdminWatchlistSettings } from './settings/AdminWatchlistSettings'
import { useSearchParams } from 'react-router-dom'

import { Reiterreihe, type Reiter } from '../components/Reiterreihe'
import { useConfig } from '../hooks/useConfig'

type Tab =
  | 'services'
  | 'address'
  | 'mail'
  | 'anmeldung'
  | 'channels'
  | 'users'
  | 'hausordnung'
  | 'watchlist'
  | 'storage'
  | 'blocklist'
  | 'logs'
  | 'sicherungen'
  | 'token'

/**
 * Was unter „System" liegt.
 *
 * ⚠️ **Eine zweite Ebene lohnt erst ab genug Inhalt.** Die Leiste war auf zehn
 * Punkte gewachsen und brach auf schmalen Bildschirmen um. Vier davon haben
 * gemeinsam, dass sie nicht den *Betrieb* betreffen, sondern die Anlage selbst
 * — Erreichbarkeit, Mailversand, Protokoll und Sicherungen. Die wandern
 * zusammen nach unten; oben bleiben die Punkte, die man im Alltag anfasst.
 *
 * Ein Untermenü mit nur einem Eintrag waere schlechter als gar keins: Es macht
 * die Navigation ungleichmaessig, ohne Platz zu sparen.
 */
const SYSTEM: Reiter<Tab>[] = [
  { value: 'address', label: 'settings.tabAddress', symbol: 'adresse' },
  { value: 'mail', label: 'settings.tabMail', symbol: 'mail' },
  // Anmeldung über fremde Anbieter (OIDC). Unter „System", weil es die
  // Anlage betrifft, nicht den Alltag - und direkt hinter der Adresse, von
  // der seine Rückkehr-Adresse abhängt.
  { value: 'anmeldung', label: 'settings.tabSignIn', symbol: 'schluessel' },
  { value: 'logs', label: 'settings.tabLogs', symbol: 'protokoll' },
  { value: 'sicherungen', label: 'settings.tabBackups', symbol: 'sicherung' },
  // Aufsicht, kein Alltag: Ein Administrator sieht hier, wer Token hat und ob
  // sie noch benutzt werden. Deshalb steht es neben dem Protokoll und nicht
  // bei den Benutzern - dort ginge es zwischen Rollen und Kontingenten unter.
  { value: 'token', label: 'settings.tabTokens', symbol: 'schluessel' },
]

const IM_SYSTEM = new Set<Tab>(SYSTEM.map((e) => e.value))

/**
 * Welcher Reiter hinter welchem Adress-Wort steckt.
 *
 * ⚠️ **Deutsche Woerter in der Adresse, englische Werte im Code.** Die Adresse
 * ist etwas, das man liest und weitergibt — `?reiter=dienste` sagt einem
 * Menschen, wo er landet, `?reiter=services` sagt es nur dem Programm. Dasselbe
 * Muster wie im Profil.
 *
 * ⚠️ **Diese Namen sind eine Zusage.** Sie stehen in Befund-Zielen, in
 * Benachrichtigungen und in Lesezeichen. Wer einen umbenennt, macht die alten
 * Links stumm — dann lieber den alten Namen als zweiten Eintrag stehen lassen,
 * wie es das Profil mit `sprache` und `sicherheit` tut.
 */
const REITER_AUS_ADRESSE: Record<string, Tab> = {
  dienste: 'services',
  adresse: 'address',
  mail: 'mail',
  anmeldung: 'anmeldung',
  kanaele: 'channels',
  benutzer: 'users',
  hausordnung: 'hausordnung',
  merkliste: 'watchlist',
  kontingente: 'storage',
  sperrliste: 'blocklist',
  protokoll: 'logs',
  sicherungen: 'sicherungen',
  token: 'token',
}

/** Der Punkt, auf dem „System" aufgeht - der erste seiner Reihe. */
const SYSTEM_START: Tab = SYSTEM[0].value

/**
 * Einstellungen des Administrators - Dienste und Benutzer auf einer Seite.
 *
 * Vorher waren das zwei getrennte Menüpunkte; zusammengefasst bleibt das
 * Hauptmenü übersichtlich.
 */
export function SettingsPage() {
  const { t } = useTranslation()
  const [suchparameter, setSuchparameter] = useSearchParams()
  // ⚠️ Der erste Reiter der Reihe ist auch der Startreiter - alles andere
  // markiert beim Öffnen einen Punkt, auf den niemand geklickt hat. Steht
  // aber ein Reiter in der Adresse, gilt der: Dann kommt jemand aus einem
  // Befund oder einer Meldung und weiß genau, wohin er will.
  const [tab, setTab] = useState<Tab>(
    REITER_AUS_ADRESSE[suchparameter.get('reiter') ?? ''] ?? SYSTEM_START,
  )
  // Die zweite Ebene der Dienste-Seite. Nur der Startwert kommt von hier —
  // danach verwaltet sie ihn selbst.
  const [startUnter] = useState(suchparameter.get('unter') ?? undefined)
  // Welche der beiden Hausordnungs-Seiten offen ist. Startwert aus der
  // Adresse, damit ein Verweis direkt in der Übersicht landen kann.
  const [hausordnungUnter, setzeHausordnungUnter] = useState<'schreiben' | 'uebersicht'>(
    suchparameter.get('unter') === 'uebersicht' ? 'uebersicht' : 'schreiben',
  )

  /**
   * Beim Wechseln von Hand fliegt der Parameter aus der Adresse.
   *
   * Ohne das springt ein Neuladen auf den Reiter aus der Adresse zurück — man
   * hat also weitergeklickt, lädt neu und ist wieder am Anfang, ohne zu
   * verstehen warum. Dasselbe Verhalten wie im Profil.
   */
  const wechseln = (wert: Tab) => {
    setTab(wert)
    if (suchparameter.has('reiter') || suchparameter.has('unter')) {
      const rest = new URLSearchParams(suchparameter)
      rest.delete('reiter')
      rest.delete('unter')
      setSuchparameter(rest, { replace: true })
    }
  }
  // Merklisten gibt es nur mit verbundenem Media-Server - ohne ihn wäre der
  // Reiter eine Einstellung ohne Gegenstand.
  const { data: config } = useConfig()

  const systemOffen = IM_SYSTEM.has(tab)

  // ⚠️ „System" steht **vorn**, nicht hinten: Es ist der einzige Punkt mit
  // einer zweiten Ebene, und die klappt darunter auf. Am Anfang der Reihe
  // liegt diese Ebene direkt unter ihrem Auslöser; am Ende der Reihe stünde
  // sie weit links unter fremden Reitern.
  const tabs: Reiter<Tab>[] = [
    { value: SYSTEM_START, label: t('settings.tabSystem'), symbol: 'system' },
    { value: 'services', label: t('settings.tabServices'), symbol: 'dienste' },
    { value: 'channels', label: t('settings.tabChannels'), symbol: 'glocke' },
    { value: 'users', label: t('settings.tabUsers'), symbol: 'benutzer' },
    // Direkt hinter den Benutzern: Die Hausordnung richtet sich an genau die,
    // die dort stehen.
    { value: 'hausordnung', label: t('settings.tabHausordnung'), symbol: 'hausordnung' },
    ...(config?.mediaserver_configured
      ? [{ value: 'watchlist' as Tab, label: t('settings.tabWatchlist'), symbol: 'merkliste' as const }]
      : []),
    { value: 'storage', label: t('settings.tabStorage'), symbol: 'kontingent' },
    { value: 'blocklist', label: t('settings.tabBlocklist'), symbol: 'sperre' },
  ]

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('settings.title')}
          <span className="text-accent-500">.</span>
        </h1>
      </header>

      <Reiterreihe
        eintraege={tabs}
        // „System" gilt als gewählt, solange irgendetwas darunter offen ist -
        // sonst sähe die obere Reihe leer aus, während unten etwas steht.
        aktiv={systemOffen ? SYSTEM_START : tab}
        onWechsel={wechseln}
      />

      {/* Die zweite Reihe erscheint nur, wenn sie gebraucht wird. Eine dauerhaft
          sichtbare Unterreihe wäre wieder genau die volle Leiste, die wir
          loswerden wollten. */}
      {systemOffen && (
        <Reiterreihe
          unter
          label={t('settings.tabSystem')}
          eintraege={SYSTEM.map((e) => ({ ...e, label: t(e.label) }))}
          aktiv={tab}
          onWechsel={wechseln}
        />
      )}

      {tab === 'services' && <AdminServicesSettings startUnter={startUnter} />}
      {tab === 'address' && <AdminAddressSettings />}
      {tab === 'mail' && <AdminMailSettings />}
      {tab === 'anmeldung' && <AdminOidcSettings />}

      {/* Serverseitige Kanäle - vom Administrator eingerichtet, ein Ziel für
          die ganze Installation. Die persönlichen Wege stellt jeder im
          eigenen Profil ein. */}
      {tab === 'channels' && <AdminChannelSettings />}
      {tab === 'users' && <AdminUsersSettings />}
      {tab === 'hausordnung' && (
        <div className="flex flex-col gap-6">
          {/* Zwei Seiten unter einem Reiter: hier wird geschrieben, daneben
              steht, wer entschieden hat. */}
          <Reiterreihe
            unter
            eintraege={[
              {
                value: 'schreiben' as const,
                label: t('settings.tabHausordnung'),
                symbol: 'hausordnung',
              },
              {
                value: 'uebersicht' as const,
                label: t('hausordnungAdmin.uebersicht'),
                symbol: 'benutzer',
              },
            ]}
            aktiv={hausordnungUnter}
            onWechsel={setzeHausordnungUnter}
          />
          {hausordnungUnter === 'schreiben' ? (
            <AdminHausordnung />
          ) : (
            <AdminHausordnungUebersicht />
          )}
        </div>
      )}
      {tab === 'storage' && <AdminStorageSettings />}

      {/* Untermenü mit genau einem Eintrag - Plex ist der einzige Anbieter
          mit Merkliste. Die Reihe steht trotzdem da, damit ein zweiter
          Anbieter später kein Umbau der Navigation wird. */}
      {tab === 'watchlist' && (
        <div className="flex flex-col gap-6">
          <Reiterreihe
            unter
            eintraege={[{ value: 'plex' as const, label: 'Plex', symbol: 'medienserver' }]}
            aktiv="plex"
            onWechsel={() => {}}
          />
          <AdminWatchlistSettings />
        </div>
      )}
      {tab === 'blocklist' && <AdminBlocklistSettings />}
      {tab === 'logs' && <AdminLogsSettings />}
      {tab === 'sicherungen' && <AdminSicherungen />}
      {tab === 'token' && <AdminApiToken />}
    </div>
  )
}
