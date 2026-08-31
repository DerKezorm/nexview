/**
 * Wer hat über die Hausordnung entschieden – und wie.
 *
 * ⚠️ **Ablehnen ist hier eine Auskunft, keine Sperre.** Wer ablehnt, darf
 * weiter anfragen; was daraus folgt, entscheidet der Betreiber. Ein
 * Automatismus hätte den falschen Preis: Ein versehentlicher Klick sperrte
 * jemanden aus, und gemerkt hätte es niemand.
 *
 * Kinderkonten stehen nicht in der Liste. Sie bekommen die Hausordnung nie zu
 * sehen, und eine Zeile, die dauerhaft „offen" sagt, wäre kein Hinweis,
 * sondern Lärm.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import type { HausordnungUebersichtZeile, HausordnungVerwaltung } from '../../api/types'
import { Avatar } from '../../components/Avatar'
import { Reiterreihe } from '../../components/Reiterreihe'
import { Card, ErrorBanner, Spinner } from '../../components/ui'
import { formatDate } from '../../lib/format'

type Filter = 'alle' | 'offen' | 'akzeptiert' | 'abgelehnt'

/** Der Zustand einer Zeile – aus den beiden Feldern abgeleitet. */
function zustand(zeile: HausordnungUebersichtZeile): Exclude<Filter, 'alle'> {
  if (zeile.akzeptiert === null) return 'offen'
  return zeile.akzeptiert ? 'akzeptiert' : 'abgelehnt'
}

export function AdminHausordnungUebersicht() {
  const { t, i18n } = useTranslation()
  const [filter, setzeFilter] = useState<Filter>('alle')

  const stand = useQuery({
    queryKey: ['hausordnung-verwaltung'],
    queryFn: () => api.get<HausordnungVerwaltung>('/api/hausordnung/verwaltung'),
  })
  const liste = useQuery({
    queryKey: ['hausordnung-uebersicht'],
    queryFn: () => api.get<HausordnungUebersichtZeile[]>('/api/hausordnung/uebersicht'),
  })

  if (liste.isPending || stand.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }
  if (liste.isError || !liste.data) {
    return <ErrorBanner message={t('hausordnungAdmin.nichtGeladen')} />
  }

  // Ohne veröffentlichte Hausordnung hat niemand etwas zu entscheiden - dann
  // wäre eine Liste voller „offen" eine Anklage ohne Grund.
  if (!stand.data?.veroeffentlicht) {
    return (
      <Card className="p-5">
        <p className="text-sm text-mist-500">{t('hausordnungAdmin.uebersichtLeer')}</p>
      </Card>
    )
  }

  const zeilen = liste.data
  const zaehler: Record<Filter, number> = {
    alle: zeilen.length,
    offen: zeilen.filter((z) => zustand(z) === 'offen').length,
    akzeptiert: zeilen.filter((z) => zustand(z) === 'akzeptiert').length,
    abgelehnt: zeilen.filter((z) => zustand(z) === 'abgelehnt').length,
  }
  const gezeigt = filter === 'alle' ? zeilen : zeilen.filter((z) => zustand(z) === filter)

  const FARBE: Record<Exclude<Filter, 'alle'>, string> = {
    offen: 'text-mist-500',
    akzeptiert: 'text-ok-500',
    abgelehnt: 'text-warn-500',
  }

  return (
    <div className="flex flex-col gap-5">
      <Reiterreihe
        unter
        eintraege={(['alle', 'offen', 'akzeptiert', 'abgelehnt'] as Filter[]).map((wert) => ({
          value: wert,
          label: `${t(`hausordnungAdmin.filter.${wert}`)} (${zaehler[wert]})`,
        }))}
        aktiv={filter}
        onWechsel={setzeFilter}
      />

      <Card className="flex flex-col gap-1 p-2">
        {gezeigt.length === 0 ? (
          <p className="px-3 py-4 text-sm text-mist-600">{t('hausordnungAdmin.keineTreffer')}</p>
        ) : (
          gezeigt.map((zeile) => {
            const wie = zustand(zeile)
            return (
              <div
                key={zeile.user_id}
                className="flex flex-wrap items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-ink-800/60"
              >
                <Avatar
                  url={zeile.avatar_url}
                  name={zeile.display_name ?? zeile.username}
                  className="h-8 w-8"
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">
                    {zeile.display_name ?? zeile.username}
                    <span className="ml-2 text-xs font-normal text-mist-600">
                      @{zeile.username}
                    </span>
                  </p>
                  <p className="text-xs text-mist-600">{t(`adminUsers.role${zeile.role}`)}</p>
                </div>
                <div className="text-right">
                  <p className={`text-sm ${FARBE[wie]}`}>
                    {t(`hausordnungAdmin.zustand.${wie}`)}
                  </p>
                  {zeile.entschieden_am && (
                    <p className="text-xs text-mist-600">
                      {formatDate(zeile.entschieden_am.slice(0, 10), i18n.language)}
                    </p>
                  )}
                </div>
              </div>
            )
          })
        )}
      </Card>

      {/* Ehrlich dazusagen, was „offen" bei einer neuen Fassung bedeutet. */}
      {stand.data.fassung > 1 && (
        <p className="text-xs text-mist-600">
          {t('hausordnungAdmin.fassungHinweis', { fassung: stand.data.fassung })}
        </p>
      )}
    </div>
  )
}
