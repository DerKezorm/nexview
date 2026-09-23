import type { Beschaffung } from '../../api/types'

const SETUP_STEPS = [
  'account',
  'avatar',
  'tmdb',
  'beschaffung',
  'radarr',
  'sonarr',
  'nexcrate',
  'address',
  'mail',
  'done',
] as const
export type SetupStep = (typeof SETUP_STEPS)[number]

/**
 * Die Schritte, die dieser Weg wirklich geht (Bauplan 7.1).
 *
 * ⚠️ **Radarr und nexcrate schließen einander aus.** Es gibt keinen
 * Mischbetrieb; wer nexcrate wählt, soll die Arr-Schritte nicht einmal als
 * übersprungen sehen - sie wären eine Einladung, doch noch etwas einzutragen,
 * das danach niemand liest.
 */
export function schritteFuer(modus: Beschaffung): SetupStep[] {
  const weg = modus === 'nex' ? ['radarr', 'sonarr'] : ['nexcrate']
  return SETUP_STEPS.filter((step) => !weg.includes(step))
}

