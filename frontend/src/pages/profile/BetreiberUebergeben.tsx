import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { BetreiberStand, User } from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Button, Card, ErrorBanner } from '../../components/ui'

/**
 * „Betreiber übergeben" – der einzige Weg, auf dem der Haken wandert.
 *
 * ⚠️ **Nur der Träger sieht diesen Bereich.** Es gibt keinen Weg, sich den
 * Haken zu holen; es gibt nur einen, ihn zu geben. Wer ihn abgegeben hat, kann
 * ihn sich nicht zurückholen – deshalb ist die Warnung vor der Bestätigung
 * kein Formsatz, sondern der Kern dieser Seite.
 *
 * Der Haken selbst gibt kein Recht. Er sagt ausschließlich, was **andere**
 * Administratoren mit diesem Konto nicht tun dürfen. Nach der Übergabe gilt
 * das für den bisherigen Träger nicht mehr – auch das steht in der Warnung.
 */
export function BetreiberUebergeben({ me }: { me: User }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [ziel, setZiel] = useState('')
  const [frage, setFrage] = useState(false)

  const stand = useQuery({
    queryKey: ['betreiber'],
    queryFn: () => api.get<BetreiberStand>('/api/users/betreiber'),
  })

  /**
   * Die möglichen Nachfolger: aktive Administratoren außer mir selbst.
   *
   * ⚠️ Die Liste ist Bequemlichkeit, keine Sperre. Die Regeln stehen im
   * Backend (`services/betreiber.uebergeben`) und gelten auch für den, der die
   * Anfrage von Hand stellt.
   */
  const kandidaten = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get<User[]>('/api/users'),
    select: (alle) =>
      alle.filter((u) => u.role === 'admin' && u.is_active && u.id !== me.id),
  })

  const uebergeben = useMutation({
    mutationFn: (user_id: number) =>
      api.post<BetreiberStand>('/api/users/betreiber/uebergeben', { user_id }),
    onSuccess: () => {
      setFrage(false)
      setZiel('')
      // Alles, was den Haken zeigt, ist jetzt veraltet: die eigene Sitzung
      // (die Knöpfe in der Benutzerliste), der Stand und die Liste selbst.
      void queryClient.invalidateQueries()
    },
    onError: () => setFrage(false),
  })

  // Nur der Träger. Solange der Stand noch lädt, lieber nichts zeigen als
  // etwas, das gleich wieder verschwindet.
  if (!me.is_betreiber) return null

  const ausUmgebung = stand.data?.aus_umgebung ?? false
  const gewaehlt = kandidaten.data?.find((u) => String(u.id) === ziel)

  return (
    <Card>
      <h2 className="text-lg font-semibold">{t('betreiber.title')}</h2>
      <p className="mt-2 text-sm leading-relaxed text-mist-500">
        {t('betreiber.text')}
      </p>

      {/* ⚠️ Der Fall, den man erklären muss, statt ihn wortlos zu sperren:
          Steht der Betreiber in der Umgebung, würde der nächste Neustart jede
          Übergabe still zurückdrehen. Nexview nimmt sie deshalb gar nicht erst
          an – und sagt, was zu tun ist. */}
      {ausUmgebung ? (
        <p className="mt-4 rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {t('betreiber.fromEnvironment')}
        </p>
      ) : (
        <>
          {uebergeben.error && (
            <div className="mt-3">
              <ErrorBanner
                message={
                  uebergeben.error instanceof ApiError
                    ? uebergeben.error.message
                    : t('errors.generic')
                }
              />
            </div>
          )}

          {kandidaten.data?.length === 0 ? (
            <p className="mt-4 text-sm text-mist-600">
              {t('betreiber.noCandidates')}
            </p>
          ) : (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <select
                value={ziel}
                onChange={(event) => setZiel(event.target.value)}
                aria-label={t('betreiber.pick')}
                className="min-w-56 rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-100"
              >
                <option value="">{t('betreiber.pick')}</option>
                {(kandidaten.data ?? []).map((u) => (
                  <option key={u.id} value={String(u.id)}>
                    {u.display_name ?? u.username} (@{u.username})
                  </option>
                ))}
              </select>
              <Button
                variant="ghost"
                disabled={!gewaehlt}
                onClick={() => setFrage(true)}
                className="border-bad-500/40 text-bad-500 hover:bg-bad-500/10 hover:text-bad-500"
              >
                {t('betreiber.handOver')}
              </Button>
            </div>
          )}
        </>
      )}

      <ConfirmDialog
        open={frage}
        title={t('betreiber.confirmTitle')}
        description={t('betreiber.confirmText', {
          name: gewaehlt?.display_name ?? gewaehlt?.username ?? '',
        })}
        warning={t('betreiber.confirmWarning')}
        confirmLabel={t('betreiber.handOver')}
        loading={uebergeben.isPending}
        onCancel={() => setFrage(false)}
        onConfirm={() => gewaehlt && uebergeben.mutate(gewaehlt.id)}
      />
    </Card>
  )
}
