import { useContext } from 'react'

import type { FassungAchse } from '../../api/types'
import { AuthContext } from '../../auth/context'
import { aufKachel } from '../../lib/fassungen'

/**
 * Die Abzeichen einer Kachel nach der Wahl des angemeldeten Kontos.
 *
 * Über den Kontext selbst statt `useAuth`: Eine Kachel steht auch dort, wo
 * kein Anmelde-Zustand hängt (Tests mit `rendernSchlicht`). Ohne Konto zeigt
 * sie nur die Hauptfassung, wie ab Werk.
 */
export function useKachelFassungen(fassungen: FassungAchse[] | undefined): FassungAchse[] {
  const user = useContext(AuthContext)?.user ?? null
  return aufKachel(fassungen, user)
}
