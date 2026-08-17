import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { AdminAddressSettings } from './settings/AdminAddressSettings'
import { AdminLogsSettings } from './settings/AdminLogsSettings'
import { AdminMailSettings } from './settings/AdminMailSettings'
import { AdminServicesSettings } from './settings/AdminServicesSettings'
import { AdminBlocklistSettings } from './settings/AdminBlocklistSettings'
import { AdminUsersSettings } from './settings/AdminUsersSettings'

type Tab = 'services' | 'address' | 'mail' | 'users' | 'blocklist' | 'logs'

/**
 * Einstellungen des Administrators - Dienste und Benutzer auf einer Seite.
 *
 * Vorher waren das zwei getrennte Menüpunkte; zusammengefasst bleibt das
 * Hauptmenü übersichtlich.
 */
export function SettingsPage() {
  const { t } = useTranslation()
  const [tab, setTab] = useState<Tab>('services')

  const tabs: { value: Tab; labelKey: string }[] = [
    { value: 'services', labelKey: 'settings.tabServices' },
    { value: 'address', labelKey: 'settings.tabAddress' },
    { value: 'mail', labelKey: 'settings.tabMail' },
    { value: 'users', labelKey: 'settings.tabUsers' },
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
          </button>
        ))}
      </div>

      {tab === 'services' && <AdminServicesSettings />}
      {tab === 'address' && <AdminAddressSettings />}
      {tab === 'mail' && <AdminMailSettings />}
      {tab === 'users' && <AdminUsersSettings />}
      {tab === 'blocklist' && <AdminBlocklistSettings />}
      {tab === 'logs' && <AdminLogsSettings />}
    </div>
  )
}
