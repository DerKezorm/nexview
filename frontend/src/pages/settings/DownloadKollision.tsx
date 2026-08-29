/**
 * Zwei Instanzen, eine Kategorie — der Fehler, der wie ein Netzproblem aussieht.
 *
 * ⚠️ **Warum das hier steht und nicht in Radarr.** Radarr sieht im
 * Download-Programm nur, was in *seiner* Kategorie liegt. Teilen sich zwei
 * Instanzen eine, greift jede nach den Downloads der anderen: Anfragen hängen,
 * Dateien landen falsch — und nirgends steht ein Fehler. Radarr kann nicht
 * warnen, weil es die zweite Instanz gar nicht kennt. Nexview kennt beide.
 *
 * ⚠️ **Oben auf der Seite, nicht an der Kachel.** An einer Kachel stünde die
 * halbe Wahrheit: Beteiligt sind immer mindestens zwei, und welche das sind,
 * ist die eigentliche Auskunft. Hier stehen alle Namen in einem Satz.
 */

import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { DownloadKollisionStand } from '../../api/types'
import { Button } from '../../components/ui'

export function DownloadKollision() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const stand = useQuery({
    queryKey: ['download-kollision'],
    queryFn: () =>
      api.get<DownloadKollisionStand>('/api/settings/instanzen/downloadkollision'),
    // Die Einstellung ändert sich drüben, nicht hier - also nicht bei jedem
    // Fokuswechsel neu fragen, aber auch nicht ewig auf einem Stand sitzen.
    staleTime: 60_000,
  })

  const wegklicken = useMutation({
    mutationFn: (schluessel: string) =>
      api.post('/api/settings/instanzen/downloadkollision/ignorieren', { schluessel }),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['download-kollision'] }),
  })

  const treffer = stand.data?.kollisionen ?? []
  if (treffer.length === 0) return null

  return (
    <div className="flex flex-col gap-3">
      {treffer.map((k) => (
        <div
          key={k.schluessel}
          className="flex flex-col gap-2 rounded-xl border border-bad-500/50 bg-bad-500/10 px-4 py-3.5"
        >
          <p className="text-sm font-medium text-bad-500">
            {k.ohne_kategorie
              ? t('downloadCollision.titleEmpty', {
                  instanzen: k.instanzen.join(' · '),
                  programm: k.programm,
                })
              : t('downloadCollision.title', {
                  instanzen: k.instanzen.join(' · '),
                  kategorie: k.kategorie,
                  programm: k.programm,
                })}
          </p>

          {/* ⚠️ Die Symptome gehören dazu — sie sind der Grund, warum niemand
              den Fehler findet. Wer sie liest, erkennt seine eigene Suche
              wieder. */}
          <p className="text-xs leading-relaxed text-mist-300">
            {t('downloadCollision.symptoms')}
          </p>
          <p className="text-xs leading-relaxed text-mist-400">
            {k.ohne_kategorie
              ? t('downloadCollision.fixEmpty')
              : t('downloadCollision.fix')}
          </p>

          <div className="flex flex-wrap items-center gap-2">
            {/* ⚠️ Wegklicken, weil es Aufbauten gibt, in denen das Absicht ist.
                Es gilt für **diese** Beteiligten: Kommt eine dritte Instanz auf
                dieselbe Kategorie, wird wieder gewarnt — das ist ein neuer
                Fehler, kein weggeklickter alter. */}
            <Button
              type="button"
              variant="ghost"
              loading={wegklicken.isPending && wegklicken.variables === k.schluessel}
              onClick={() => wegklicken.mutate(k.schluessel)}
            >
              {t('downloadCollision.dismiss')}
            </Button>
            <span className="text-xs text-mist-500">
              {t('downloadCollision.dismissHint')}
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}
