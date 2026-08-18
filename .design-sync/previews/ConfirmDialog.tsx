import { ConfirmDialog } from 'nexview-ui'

/** Nexviews eigene Rueckfrage statt des Browser-Popups. */
export const Rueckfrage = () => (
  <ConfirmDialog
    open
    title="Tickets löschen?"
    description="3 geschlossene Tickets werden endgültig entfernt."
    warning="Der gesamte Verlauf verschwindet mit — auch für den Benutzer. Das lässt sich nicht rückgängig machen."
    confirmLabel="Endgültig löschen"
    onConfirm={() => {}}
    onCancel={() => {}}
  />
)
