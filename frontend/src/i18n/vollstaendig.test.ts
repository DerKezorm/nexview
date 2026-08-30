/**
 * Beide Sprachen müssen dieselben Einträge kennen — jeden einzelnen.
 *
 * ⚠️ **Dieser Test hält ein Sicherheitsnetz, das es nicht mehr gibt.** Solange
 * beide Sprachen im Grundpaket lagen, konnte i18next bei einem fehlenden
 * englischen Text den deutschen einsetzen; niemand hat es je gemerkt. Seit
 * jede Sprache einzeln nachkommt, geht das nicht mehr — die andere ist ja gar
 * nicht da. Fehlt ein Eintrag, steht sein Schlüssel auf dem Bildschirm:
 * `settings.storage.title` statt „Speicherplatz".
 *
 * Der bestehende Test im Hintergrundteil prüft nur die Fehlermeldungen
 * (`errors.byCode`). Hier geht es um alles andere — die 2.600 Sätze, aus denen
 * die Oberfläche besteht.
 */

import { describe, expect, it } from 'vitest'

import de from './de.json'
import en from './en.json'

/** Alle Blattpfade eines verschachtelten Objekts, z. B. `settings.mail.title`. */
function pfade(wert: unknown, praefix = ''): string[] {
  if (typeof wert !== 'object' || wert === null || Array.isArray(wert)) {
    return praefix ? [praefix] : []
  }
  return Object.entries(wert as Record<string, unknown>).flatMap(([k, v]) =>
    pfade(v, praefix ? `${praefix}.${k}` : k),
  )
}

const deutsch = new Set(pfade(de))
const englisch = new Set(pfade(en))

describe('Sprachdateien', () => {
  it('kennen beide dieselben Einträge', () => {
    const nurDeutsch = [...deutsch].filter((p) => !englisch.has(p)).sort()
    const nurEnglisch = [...englisch].filter((p) => !deutsch.has(p)).sort()

    expect(nurDeutsch, 'Nur in de.json — auf Englisch erschiene der Schlüssel').toEqual([])
    expect(nurEnglisch, 'Nur in en.json — auf Deutsch erschiene der Schlüssel').toEqual([])
  })

  it('sind nicht versehentlich leer', () => {
    // ⚠️ Ohne das wäre der Test oben auch dann grün, wenn beide Dateien
    // kaputt geladen würden - zwei leere Mengen sind schließlich gleich.
    expect(deutsch.size).toBeGreaterThan(2000)
  })

  it('haben keine leeren Texte', () => {
    // Ein leerer Text ist so unbrauchbar wie ein fehlender, fällt aber dem
    // Vergleich oben nicht auf.
    const leer = (daten: unknown, sprache: string) =>
      pfade(daten)
        .filter((pfad) => {
          const wert = pfad
            .split('.')
            .reduce<unknown>((o, k) => (o as Record<string, unknown>)?.[k], daten)
          return typeof wert === 'string' && wert.trim() === ''
        })
        .map((pfad) => `${sprache}: ${pfad}`)

    expect([...leer(de, 'de'), ...leer(en, 'en')]).toEqual([])
  })
})
