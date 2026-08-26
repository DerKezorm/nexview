import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { AdminAddressSettings } from './settings/AdminAddressSettings'
import { AdminChannelSettings } from './settings/AdminChannelSettings'
import { AdminLogsSettings } from './settings/AdminLogsSettings'
import { AdminSicherungen } from './settings/AdminSicherungen'
import { AdminMailSettings } from './settings/AdminMailSettings'
import { AdminServicesSettings } from './settings/AdminServicesSettings'
import { AdminStorageSettings } from './settings/AdminStorageSettings'
import { AdminBlocklistSettings } from './settings/AdminBlocklistSettings'
import { AdminUsersSettings } from './settings/AdminUsersSettings'
import { AdminWatchlistSettings } from './settings/AdminWatchlistSettings'
import { Reiterreihe, type Reiter } from '../components/Reiterreihe'
import { useConfig } from '../hooks/useConfig'

type Tab =
  | 'services'
  | 'address'
  | 'mail'
  | 'channels'
  | 'users'
  | 'watchlist'
  | 'storage'
  | 'blocklist'
  | 'logs'
  | 'sicherungen'

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
  { value: 'logs', label: 'settings.tabLogs', symbol: 'protokoll' },
  { value: 'sicherungen', label: 'settings.tabBackups', symbol: 'sicherung' },
]

const IM_SYSTEM = new Set<Tab>(SYSTEM.map((e) => e.value))

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
  const [tab, setTab] = useState<Tab>('services')
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
        onWechsel={setTab}
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
          onWechsel={setTab}
        />
      )}

      {tab === 'services' && <AdminServicesSettings />}
      {tab === 'address' && <AdminAddressSettings />}
      {tab === 'mail' && <AdminMailSettings />}

      {/* Serverseitige Kanäle - vom Administrator eingerichtet, ein Ziel für
          die ganze Installation. Die persönlichen Wege stellt jeder im
          eigenen Profil ein. */}
      {tab === 'channels' && <AdminChannelSettings />}
      {tab === 'users' && <AdminUsersSettings />}
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
    </div>
  )
}
