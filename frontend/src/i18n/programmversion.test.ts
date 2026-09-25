/**
 * Die Programmversion heißt „Version“, nicht „Fassung“ (Rundgang 2, R2-3).
 *
 * „Fassung“ ist in Nexview ein eigener Begriff: Full-HD, 4K, 3D. Die Analyse
 * nannte nexcrates Programmversion „Fassung 0.2.0“, und die Sicherung nannte
 * so die Nexview-Version, aus der sie stammt.
 */

import { describe, expect, it } from 'vitest'

import de from './de.json'

describe('Programmversion', () => {
  it('heißt in der Analyse und bei Sicherungen Version', () => {
    expect(de.analyse.version).toBe('Version')
    expect(de.restore.fromVersion).toBe('Version')
  })
})
