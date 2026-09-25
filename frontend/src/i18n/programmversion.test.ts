/**
 * Die Programmversion heißt „Version“, nicht „Fassung“ (Rundgang 2, R2-3).
 *
 * „Fassung“ ist in Nexview ein eigener Begriff: Full-HD, 4K, 3D. Die Analyse
 * nannte nexcrates Programmversion „Fassung 0.2.0“; der Prüfer fand dasselbe
 * Wort noch in zwölf Texten über Programmversionen (Seerr, Sicherungen,
 * Update-Befund, Absturzhinweis).
 */

import { describe, expect, it } from 'vitest'

import de from './de.json'

/** Texte, die von einer Programmversion sprechen. */
const PROGRAMMVERSION: string[] = [
  de.analyse.version,
  de.restore.fromVersion,
  de.restore.tooNew,
  de.restore.unknownVersion,
  de.errors.crashHint,
  de.errors.byCode.restore_backup_newer,
  de.errors.byCode.restore_incompatible,
  de.errors.byCode.restore_unknown_version,
  de.errors.byCode.seerr_version_unknown,
  de.errors.byCode.quality_import_zu_neu,
  de.setup.seerr.saetze.fassung_unlesbar,
  de.setup.seerr.saetze.fassung_zu_alt,
  de.settings.seerr.checkOk,
  de.befund.dienst.version_alt.titel,
  de.backups.tooNew,
]

describe('Programmversion', () => {
  it('heißt überall Version', () => {
    expect(PROGRAMMVERSION.length).toBeGreaterThanOrEqual(15)
    for (const text of PROGRAMMVERSION) {
      expect(text).toMatch(/Version/)
      expect(text).not.toMatch(/Fassung/)
    }
  })
})
