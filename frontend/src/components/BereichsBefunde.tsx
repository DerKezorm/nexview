import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { api } from '../api/client'
import type { Befund, BefundBereich } from '../api/types'
import { Befundliste } from './Befundliste'
import { Card } from './ui'

/**
 * Die Befunde eines Bereichs — der Kasten, mit dem jeder Analyse-Reiter beginnt.
 *
 * ⚠️ **Befunde führen, Zahlen belegen.** Das war die Grundentscheidung für
 * diese Seite: Wer sie aufmacht, will zuerst wissen, ob hier etwas nicht
 * stimmt, und erst danach, wie die Zahlen dazu aussehen. Stünden die Tabellen
 * oben, müsste er sie selbst deuten — und genau das kann er nicht, sonst hätte
 * er nicht nachgesehen.
 *
 * ⚠️ **Ist nichts zu melden, verschwindet der Kasten ganz.** Ein grüner
 * „alles in Ordnung"-Balken über jeder Seite ist nach dem dritten Mal
 * unsichtbar — und nimmt der Meldung, wenn sie einmal kommt, die Wirkung. Auf
 * dem Dashboard ist das anders: Dort ist die Abwesenheit von Befunden die
 * eigentliche Nachricht.
 */
export function BereichsBefunde({
  /**
   * Ein oder mehrere Bereiche.
   *
   * ⚠️ **Mehrere, weil Reiter und Bereiche nicht deckungsgleich sind.** Der
   * Reiter „Bibliothek" beantwortet eine Frage — was liegt da und stimmt die
   * Buchführung —, und dazu gehören drei Bereiche: der Bestand selbst, der
   * Platz darunter und der Abgleich der Quellen. Sie in drei Reiter zu
   * zerlegen hieße, dieselbe Frage dreimal zu stellen.
   */
  bereiche,
}: {
  bereiche: BefundBereich[]
}) {
  const { t } = useTranslation()

  const query = useQuery({
    queryKey: ['befunde', ...bereiche],
    queryFn: async () => {
      const listen = await Promise.all(
        bereiche.map((b) => api.get<Befund[]>(`/api/admin/befunde?bereich=${b}`)),
      )
      return listen.flat()
    },
  })

  const befunde = query.data ?? []
  if (befunde.length === 0) return null

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold">{t('dashboard.findings')}</h2>
      <Befundliste befunde={befunde} />
    </Card>
  )
}
