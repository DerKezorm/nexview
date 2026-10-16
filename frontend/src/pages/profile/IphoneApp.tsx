/**
 * „iPhone-App verbinden" im Profil: ein QR-Code statt eines abgetippten Schlüssels.
 *
 * Die App bekommt dabei einen gewöhnlichen Schlüssel mit den Rechten dieses
 * Kontos; er steht danach in der Liste darunter und lässt sich dort widerrufen.
 */

import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'

import { AppKoppeln } from '../../components/AppKoppeln'
import { Button, Section } from '../../components/ui'

export function IphoneApp() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [offen, setOffen] = useState(false)

  const verbunden = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['api-schluessel'] })
  }, [queryClient])

  return (
    <Section title={t('appConnect.title')} breit>
      <p className="-mt-2 text-sm leading-relaxed text-mist-500">{t('appConnect.intro')}</p>
      <Button type="button" variant="ghost" className="self-start" onClick={() => setOffen(true)}>
        {t('appConnect.button')}
      </Button>
      <AppKoppeln
        offen={offen}
        pfad="/api/me/app-kopplung"
        titel={t('appConnect.button')}
        anleitung={t('appConnect.scan')}
        verbundenText={(geraet) => t('appConnect.connected', { geraet })}
        onSchliessen={() => setOffen(false)}
        onVerbunden={verbunden}
      />
    </Section>
  )
}
