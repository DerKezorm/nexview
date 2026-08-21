import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { AdminAddressSettings } from './settings/AdminAddressSettings'
import { AdminChannelSettings } from './settings/AdminChannelSettings'
import { AdminLogsSettings } from './settings/AdminLogsSettings'
import { AdminMailSettings } from './settings/AdminMailSettings'
import { AdminServicesSettings } from './settings/AdminServicesSettings'
import { AdminStorageSettings } from './settings/AdminStorageSettings'
import { AdminBlocklistSettings } from './settings/AdminBlocklistSettings'
import { AdminUsersSettings } from './settings/AdminUsersSettings'
import { AdminWatchlistSettings } from './settings/AdminWatchlistSettings'
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

  const tabs: { value: Tab; labelKey: string; badge?: string }[] = [
    { value: 'services', labelKey: 'settings.tabServices' },
    { value: 'address', labelKey: 'settings.tabAddress' },
    { value: 'mail', labelKey: 'settings.tabMail' },
    { value: 'channels', labelKey: 'settings.tabChannels' },
    { value: 'users', labelKey: 'settings.tabUsers' },
    ...(config?.mediaserver_configured
      ? [{ value: 'watchlist' as Tab, labelKey: 'settings.tabWatchlist' }]
      : []),
    // Noch im Bau: Gemessen und angezeigt wird schon, begrenzt noch nicht.
    // Das Abzeichen sagt das, statt es jemanden herausfinden zu lassen.
    { value: 'storage', labelKey: 'settings.tabStorage', badge: 'In Dev' },
    { value: 'blocklist', labelKey: 'settings.tabBlocklist' },
    { value: 'logs', labelKey: 'settings.tabLogs' },
  ]

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('settings.title')}
          <span className="text-accent-500">.</span>
        </h1>
      </header>

      <div className="flex flex-wrap gap-2" role="tablist">
        {tabs.map((entry) => (
          <button
            key={entry.value}
            type="button"
            role="tab"
            aria-selected={tab === entry.value}
            onClick={() => setTab(entry.value)}
            className={
              'rounded-full border px-4 py-2 text-sm font-medium transition-colors ' +
              (tab === entry.value
                ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
            }
          >
            {t(entry.labelKey)}
            {entry.badge && (
              <span className="ml-2 rounded-full border border-warn-500/50 bg-warn-500/10 px-1.5 py-0.5 text-[0.65rem] font-semibold tracking-wide text-warn-500 uppercase">
                {entry.badge}
              </span>
            )}
          </button>
        ))}
      </div>

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
          <div className="flex flex-wrap gap-2">
            <span className="rounded-full border border-accent-500/60 bg-accent-500/15 px-3.5 py-1.5 text-sm font-medium text-accent-400">
              Plex
            </span>
          </div>
          <AdminWatchlistSettings />
        </div>
      )}
      {tab === 'blocklist' && <AdminBlocklistSettings />}
      {tab === 'logs' && <AdminLogsSettings />}
    </div>
  )
}
