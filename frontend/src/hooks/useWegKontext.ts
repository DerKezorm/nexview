import { wegKontext } from '../lib/weg'
import { useConfig } from './useConfig'

/**
 * Der i18next-Kontext des eingestellten Beschaffungswegs – für Bauteile, die
 * die Konfiguration sonst nicht brauchen. Siehe `lib/weg.ts`.
 */
export function useWegKontext(): { context?: 'nex' } {
  const { data: config } = useConfig()
  return wegKontext(config)
}
