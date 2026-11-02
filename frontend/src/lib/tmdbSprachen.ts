/**
 * Sprachen, in denen TMDB Titel, Beschreibungen und Suchtreffer liefert.
 *
 * Der Name steht in der Sprache selbst („Français“, „日本語“): Wer seine
 * Sprache sucht, erkennt sie so, egal in welcher Sprache die Oberfläche läuft.
 * Darum stehen die Namen hier und nicht in den Sprachdateien.
 *
 * Kürzel nach TMDB: ISO 639-1, wo es auf das Land ankommt mit ISO 3166-1
 * (`pt-BR`, `zh-TW`). Ohne Land hängt das Backend die Region an.
 */
export const TMDB_SPRACHEN: ReadonlyArray<{ code: string; name: string }> = [
  { code: 'de', name: 'Deutsch' },
  { code: 'en', name: 'English' },
  { code: 'fr', name: 'Français' },
  { code: 'es', name: 'Español' },
  { code: 'it', name: 'Italiano' },
  { code: 'nl', name: 'Nederlands' },
  { code: 'pt-PT', name: 'Português (Portugal)' },
  { code: 'pt-BR', name: 'Português (Brasil)' },
  { code: 'pl', name: 'Polski' },
  { code: 'sv', name: 'Svenska' },
  { code: 'da', name: 'Dansk' },
  { code: 'no', name: 'Norsk' },
  { code: 'fi', name: 'Suomi' },
  { code: 'cs', name: 'Čeština' },
  { code: 'hu', name: 'Magyar' },
  { code: 'ro', name: 'Română' },
  { code: 'el', name: 'Ελληνικά' },
  { code: 'tr', name: 'Türkçe' },
  { code: 'uk', name: 'Українська' },
  { code: 'ru', name: 'Русский' },
  { code: 'ja', name: '日本語' },
  { code: 'ko', name: '한국어' },
  { code: 'zh-CN', name: '简体中文' },
  { code: 'zh-TW', name: '繁體中文' },
]

/**
 * Die Auswahl für das Feld, samt einem gespeicherten Wert, der nicht in der
 * Liste steht.
 *
 * ⚠️ Ein `<select>` ohne passende Option zeigt still die erste an, und das
 * nächste Speichern schriebe sie zurück: Ein `de-AT` würde so zu `de`, ohne
 * dass jemand es gewählt hätte. Deshalb bleibt ein unbekannter Wert als
 * eigene Option stehen, beschriftet mit seinem Kürzel.
 */
export function sprachOptionen(
  gespeichert: string,
): ReadonlyArray<{ code: string; name: string }> {
  if (!gespeichert || TMDB_SPRACHEN.some((s) => s.code === gespeichert)) {
    return TMDB_SPRACHEN
  }
  return [{ code: gespeichert, name: gespeichert }, ...TMDB_SPRACHEN]
}
