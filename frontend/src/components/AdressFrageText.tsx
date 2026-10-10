import { useTranslation } from 'react-i18next'

import type { AdressFrage } from '../lib/adressFrage'

/** Die Sätze der Rückfrage: authentik kennt noch die Adresse, und/oder Anbieter von Hand. */
export function AdressFrageText({ frage }: { frage: AdressFrage }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-2" data-testid="adress-frage">
      {frage.count > 0 && <p>{t('oidc.admin.addressChangeConfirm', { address: frage.address })}</p>}
      {frage.hand > 0 && <p>{t('oidc.admin.addressChangeHand', { count: frage.hand })}</p>}
    </div>
  )
}
