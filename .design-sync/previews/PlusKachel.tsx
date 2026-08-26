import { PlusKachel } from 'nexview-ui'

/**
 * Steht dort, wo das naechste Ding hinkaeme - damit ist ohne Erklaerung klar,
 * was sie anlegt.
 */
export const RuhendUndAktiv = () => (
  <div className="bg-ink-950 p-6 grid grid-cols-2 gap-4 max-w-md">
    <PlusKachel beschriftung="Ziel hinzufügen" aktiv={false} onClick={() => {}} />
    <PlusKachel beschriftung="Kinderkonto anlegen" aktiv onClick={() => {}} />
  </div>
)
