/**
 * Qualität und Benennung — ein Thema, vier Stationen.
 *
 * ⚠️ **Warum ein Seitenmenü und nicht vier Abschnitte untereinander.**
 * Untereinander standen hier zuletzt die Ablage, der Bestand der Instanzen,
 * das Benennungsschema und die Rückverbindung — zwei davon mit Listen über
 * hundert Einträge. Die Seite war nicht mehr zu überblicken, und wer etwas
 * Bestimmtes suchte, scrollte daran vorbei.
 *
 * ⚠️ **Warum trotzdem ein einziger Reiter oben.** Das alles kommt aus
 * derselben Quelle und gehört zusammen: Profile, Erkennungsmuster,
 * Benennungsschema. Zwei gleichrangige Reiter neben „Radarr“ und „Sonarr“
 * hätten ein Thema in zwei Hälften zerlegt, die einzeln unvollständig sind.
 * Die Gliederung gehört **nach innen**.
 *
 * ⚠️ **Warum die Rückverbindung hier steht und nicht bei den Medienservern.**
 * Sie ist eine Einstellung *in Radarr und Sonarr*, keine am Medienserver — und
 * sie ist die Voraussetzung dafür, dass eine Umbenennung nicht wehtut. Wer
 * Dateien anfasst, muss beides sehen.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Symbol, type SymbolName } from '../../components/Symbol'
import { AdminArrBestand } from './AdminArrBestand'
import { AdminBenennung } from './AdminBenennung'
import { AdminMedienserverVerbindung } from './AdminMedienserverVerbindung'
import { AdminQualitaetsprofile } from './AdminQualitaetsprofile'

type Station = 'profile' | 'bestand' | 'benennung' | 'medienserver'

const STATIONEN: {
  value: Station
  labelKey: string
  hinweisKey: string
  symbol: SymbolName
}[] = [
  {
    value: 'profile',
    labelKey: 'qualityArea.profiles',
    hinweisKey: 'qualityArea.profilesHint',
    symbol: 'qualitaet',
  },
  {
    value: 'bestand',
    labelKey: 'qualityArea.inventory',
    hinweisKey: 'qualityArea.inventoryHint',
    symbol: 'radarr',
  },
  {
    value: 'benennung',
    labelKey: 'qualityArea.naming',
    hinweisKey: 'qualityArea.namingHint',
    symbol: 'allgemein',
  },
  {
    value: 'medienserver',
    labelKey: 'qualityArea.link',
    hinweisKey: 'qualityArea.linkHint',
    symbol: 'medienserver',
  },
]

export function AdminQualitaetsBereich() {
  const { t } = useTranslation()
  const [station, setStation] = useState<Station>('profile')

  return (
    // Ab `lg` nebeneinander, darunter untereinander: Ein Seitenmenü, das auf
    // dem Telefon eine schmale Spalte bekäme, wäre schlechter als eine Reihe.
    <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
      <nav
        aria-label={t('qualityArea.nav')}
        // ⚠️ ``lg:mt-6`` gleicht den Abstand aus, den die Inhaltskarte selbst
        // mitbringt (gemessen: 24 px). Ohne ihn beginnt das Menü eine Kante
        // höher als der Kasten daneben, und die beiden Spalten fluchten nicht.
        // Nur ab `lg`: Darunter steht das Menü über dem Inhalt, dort wäre der
        // Abstand nur zusätzliche Luft.
        className="flex shrink-0 gap-2 overflow-x-auto lg:mt-6 lg:w-56 lg:flex-col lg:overflow-visible"
      >
        {STATIONEN.map((eintrag) => {
          const aktiv = station === eintrag.value
          return (
            <button
              key={eintrag.value}
              type="button"
              aria-current={aktiv ? 'page' : undefined}
              onClick={() => setStation(eintrag.value)}
              className={
                'flex shrink-0 items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors lg:w-full ' +
                (aktiv
                  ? 'border-accent-500/50 bg-accent-500/10 text-accent-500'
                  : 'border-ink-700 bg-ink-900/50 text-mist-300 hover:border-ink-600 hover:text-mist-100')
              }
            >
              <Symbol name={eintrag.symbol} className="h-4 w-4 shrink-0" />
              <span className="flex min-w-0 flex-col">
                <span className="truncate text-sm font-medium">
                  {t(eintrag.labelKey)}
                </span>
                {/* Der Halbsatz sagt, wofür man hier ist — auf kleinen
                    Bildschirmen ist dafür kein Platz. */}
                <span className="hidden truncate text-xs text-mist-600 lg:block">
                  {t(eintrag.hinweisKey)}
                </span>
              </span>
            </button>
          )
        })}
      </nav>

      <div className="min-w-0 flex-1">
        {station === 'profile' && <AdminQualitaetsprofile />}
        {station === 'bestand' && <AdminArrBestand />}
        {station === 'benennung' && <AdminBenennung />}
        {station === 'medienserver' && <AdminMedienserverVerbindung />}
      </div>
    </div>
  )
}
