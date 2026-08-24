/**
 * Auswahlwerte, die mehrere Seiten teilen.
 *
 * Lagen früher in `FilterBar.tsx`. Die Filterleiste der alten Entdecken-Seite
 * ist mit dieser entfallen — die beiden Werte hier brauchen aber weiterhin
 * das Profil (Vorgabe-Region), die Diensteinstellungen und die Merkliste.
 */

/**
 * Länder, aus denen sich eine Vorgabe-Region wählen lässt.
 *
 * ⚠️ Region ist **nicht** Sprache und nicht Originalsprache. Sie bestimmt
 * Erscheinungstermine, Verfügbarkeit und Altersfreigabe — deshalb steht sie
 * im Profil und nicht in einer Filterleiste.
 */
export const REGION_OPTIONS = ['DE', 'AT', 'CH', 'GB', 'US', 'FR', 'IT', 'ES'] as const

/** Kacheln oder Liste. */
export type ViewMode = 'grid' | 'list'
