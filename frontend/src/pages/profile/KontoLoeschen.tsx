import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Button, Card, ErrorBanner } from '../../components/ui'

/**
 * „Ich möchte mein Konto löschen" – ein **Antrag**, keine Selbstbedienung.
 *
 * Löschen kann nur der Betreiber, und der entscheidet dabei auch über die
 * hinterlassenen Titel. Der Antrag landet als Ticket bei ihm – das Ticket
 * überlebt die Löschung und bleibt der Beleg, dass sie gewollt war.
 *
 * Nur für Benutzer und Entscheider sichtbar: Ein Administrator löscht direkt
 * in der Benutzerverwaltung, ein Antrag an sich selbst wäre Theater.
 */
export function KontoLoeschen() {
  const { t } = useTranslation()
  const [frage, setFrage] = useState(false)
  const [gestellt, setGestellt] = useState(false)

  const antrag = useMutation({
    mutationFn: () => api.post('/api/tickets/kontoaufloesung', {}),
    onSuccess: () => {
      setFrage(false)
      setGestellt(true)
    },
    onError: () => setFrage(false),
  })

  return (
    <Card>
      <h2 className="text-lg font-semibold">{t('profile.deleteTitle')}</h2>
      <p className="mt-2 text-sm leading-relaxed text-mist-500">
        {t('profile.deleteText')}
      </p>

      {antrag.error && (
        <div className="mt-3">
          <ErrorBanner
            message={
              antrag.error instanceof ApiError
                ? antrag.error.message
                : t('errors.generic')
            }
          />
        </div>
      )}

      {gestellt ? (
        <p className="mt-3 rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {t('profile.deleteRequested')}
        </p>
      ) : (
        <div className="mt-4">
          <Button
            variant="ghost"
            onClick={() => setFrage(true)}
            className="border-bad-500/40 text-bad-500 hover:bg-bad-500/10 hover:text-bad-500"
          >
            {t('profile.deleteButton')}
          </Button>
        </div>
      )}

      <ConfirmDialog
        open={frage}
        title={t('profile.deleteConfirmTitle')}
        description={t('profile.deleteConfirmText')}
        confirmLabel={t('profile.deleteConfirm')}
        loading={antrag.isPending}
        onCancel={() => setFrage(false)}
        onConfirm={() => antrag.mutate()}
      />
    </Card>
  )
}
