/**
 * Die Kinderansicht hat ihre **eigene Farbwelt**.
 *
 * Nexview ist dunkelgrau mit einem roten Akzent – gut für einen Katalog, den
 * Erwachsene abends durchsehen. Für ein Kind ist das nichts: Rot heißt in
 * jeder anderen App „Achtung", und auf Schwarz sind bunte Poster das Einzige,
 * was leuchtet. Hier ist es deshalb hell, warm und farbig, mit einer eigenen
 * Farbe je Rubrik.
 *
 * Bewusst als Konstanten und nicht als Tailwind-Theme: Diese Farben gelten
 * **nur** in der Kinderansicht und sollen nirgendwo sonst auftauchen.
 */
export const KIDS = {
  /** Seitengrund – ein warmer Verlauf statt einer Fläche. */
  seite: 'linear-gradient(170deg, #fff4e6 0%, #ffeaf2 45%, #e8f0ff 100%)',
  flaeche: '#ffffff',
  /** Leicht getönt, für Bereiche, die sich vom Weiß abheben sollen. */
  flaecheSanft: '#f4f1ff',
  text: '#2b2350',
  textLeise: '#7b7699',
  rand: '#e6e1f5',
  /** Die Hauptfarbe der Kinderansicht: freundliches Violett. */
  primaer: '#6c5ce7',
  /** Der Wunsch-Knopf – warm und einladend, nicht warnend. */
  wunsch: '#ff7a45',
  /** „Das kannst du schon schauen." */
  fertig: '#16a34a',
} as const

/**
 * Farbe und Symbol je Rubrik.
 *
 * Ein Kind unterscheidet die Bereiche an der Farbe, lange bevor es die
 * Überschrift liest. Die Schlüssel müssen zu `RUBRIKEN` im Backend passen.
 */
export const RUBRIK_FARBEN: Record<string, string> = {
  animation: '#7c5cff',
  family: '#ff9f1c',
  kids: '#00b8a9',
  adventure: '#2d9cdb',
  fantasy: '#e05fd6',
  comedy: '#f2c14e',
  music: '#ff5d8f',
  documentary: '#3ec97b',
}

/** Ein schlichtes Symbol je Rubrik – Pfade für ein 24er-Raster. */
export const RUBRIK_SYMBOLE: Record<string, string> = {
  animation: 'M12 3a9 9 0 1 0 0 18 2 2 0 0 0 0-4 2 2 0 0 1 0-4h3a5 5 0 0 0 5-5c0-3-3-5-8-5z',
  family:
    'M4 20v-2a4 4 0 0 1 4-4h1M20 20v-2a3 3 0 0 0-3-3h-1M9 7a3 3 0 1 0 6 0 3 3 0 0 0-6 0M17 10a2 2 0 1 0 0-4',
  kids: 'M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM9 10h.01M15 10h.01M9 15c1.5 1.5 4.5 1.5 6 0',
  adventure: 'M3 20l7-16 4 9 3-5 4 12z',
  fantasy: 'M12 3l2 6 6 .5-4.5 4 1.4 6-4.9-3.2L7.1 19.5 8.5 13.5 4 9.5 10 9z',
  comedy: 'M4 8s1.5 12 8 12 8-12 8-12M8 5h.01M16 5h.01',
  music: 'M9 18V6l10-2v12M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0zM19 16a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z',
  documentary: 'M4 6h10v12H4zM14 10l6-3v10l-6-3z',
}

export function rubrikFarbe(rubrik: string): string {
  return RUBRIK_FARBEN[rubrik] ?? KIDS.primaer
}
