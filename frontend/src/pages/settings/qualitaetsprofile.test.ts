/**
 * Die reine Logik der Qualitätsprofil-Oberfläche.
 *
 * ⚠️ **Warum gerade diese Stellen.** Beides sind Funktionen, die im Betrieb
 * aufgefallen sind: Der Anzeigename zeigte Maschinenkennungen wie
 * „fed014e636a7“ statt „Emby“, und der Fingerabdruck entscheidet, ob jemand
 * versehentlich fünfmal dasselbe Profil anlegt. Beide sind rein — sie lassen
 * sich prüfen, ohne eine Komponente zu bauen.
 */

import { describe, expect, it } from 'vitest'

import { anzeigename, eigenname } from './AdminMedienserverVerbindung'
import { LEERE_ANTWORTEN, fingerabdruck, kurzfassung } from './qualitaetsprofile-typen'
import type { Antworten } from './qualitaetsprofile-typen'

describe('Anzeigename eines Medienservers', () => {
  it('nennt den Anbieter, wenn der Server keinen eigenen Namen hat', () => {
    expect(anzeigename('jellyfin', '')).toBe('Jellyfin')
    expect(anzeigename('emby', 'Emby')).toBe('Emby')
  })

  it('verwirft Maschinenkennungen', () => {
    // ⚠️ Genau das stand in der Oberfläche und half niemandem.
    expect(eigenname('fed014e636a7', 'emby')).toBe('')
    expect(anzeigename('emby', 'fed014e636a7')).toBe('Emby')
  })

  it('behält echte Namen bei', () => {
    expect(anzeigename('plex', 'Bizzy')).toBe('Plex (Bizzy)')
    expect(anzeigename('jellyfin', 'Wohnzimmer')).toBe('Jellyfin (Wohnzimmer)')
  })

  it('kommt mit einem unbekannten Anbieter zurecht', () => {
    expect(anzeigename('kodi', '')).toBe('kodi')
  })

  it('lässt einen Namen stehen, der zufällig kurz und hexadezimal aussieht', () => {
    // "beef" ist vier Zeichen - zu kurz, um eine Maschinenkennung zu sein.
    expect(eigenname('beef', 'emby')).toBe('beef')
  })
})

describe('Fingerabdruck eines Rezepts', () => {
  const grund = (): Antworten => ({
    ...LEERE_ANTWORTEN,
    typ: 'radarr',
    aufloesung: '1080p',
    quelle: 'remux',
    sprachen: ['de', 'en'],
    sprachRollen: { de: 'pflicht', en: 'bevorzugt' },
  })

  it('ignoriert den Namen', () => {
    // ⚠️ Zwei Profile mit gleichen Antworten sind dasselbe Profil - sonst
    // legte jemand fünfmal dasselbe an und nennte es nur anders.
    const a = { ...grund(), name: 'Wohnzimmer' }
    const b = { ...grund(), name: 'Schlafzimmer' }
    expect(fingerabdruck(a)).toBe(fingerabdruck(b))
  })

  it('ignoriert die Reihenfolge der Sprachen', () => {
    const a = { ...grund(), sprachen: ['de', 'en'] }
    const b = { ...grund(), sprachen: ['en', 'de'] }
    expect(fingerabdruck(a)).toBe(fingerabdruck(b))
  })

  it('unterscheidet verschiedene Rollen derselben Sprachen', () => {
    const a = grund()
    const b = { ...grund(), sprachRollen: { de: 'bevorzugt', en: 'pflicht' } as const }
    expect(fingerabdruck(a)).not.toBe(fingerabdruck(b))
  })

  it('unterscheidet Auflösung und Quelle', () => {
    expect(fingerabdruck(grund())).not.toBe(
      fingerabdruck({ ...grund(), aufloesung: '2160p' }),
    )
    expect(fingerabdruck(grund())).not.toBe(
      fingerabdruck({ ...grund(), quelle: 'web' }),
    )
  })

  it('beachtet die Mehrsprachen-Regel nur, wenn es mehrere Pflichtsprachen gibt', () => {
    // ⚠️ Bei einer einzigen Pflichtsprache ist die Frage gegenstandslos -
    // sie darf dann keinen Unterschied machen.
    const eine = { ...grund(), sprachRollen: { de: 'pflicht', en: 'bevorzugt' } as const }
    expect(fingerabdruck({ ...eine, mehrerePflicht: 'alle' })).toBe(
      fingerabdruck({ ...eine, mehrerePflicht: 'eine' }),
    )
    const zwei = { ...grund(), sprachRollen: { de: 'pflicht', en: 'pflicht' } as const }
    expect(fingerabdruck({ ...zwei, mehrerePflicht: 'alle' })).not.toBe(
      fingerabdruck({ ...zwei, mehrerePflicht: 'eine' }),
    )
  })
})

describe('Kurzfassung eines Profils', () => {
  const t = (s: string) => s.split('.').pop() ?? s

  it('nennt Auflösung, Quelle und Pflichtsprache', () => {
    const text = kurzfassung(
      {
        ...LEERE_ANTWORTEN,
        aufloesung: '2160p',
        quelle: 'remux',
        sprachen: ['de'],
        sprachRollen: { de: 'pflicht' },
        sofortNehmen: true,
      },
      t,
    )
    expect(text).toContain('4K')
    expect(text).toContain('srcRemux')
    expect(text).toContain('shortRequired')
    expect(text).toContain('shortUpgrade')
  })

  it('nennt bevorzugte Sprachen, wenn keine Pflicht ist', () => {
    const text = kurzfassung(
      {
        ...LEERE_ANTWORTEN,
        sprachen: ['de'],
        sprachRollen: { de: 'bevorzugt' },
        sofortNehmen: false,
      },
      t,
    )
    expect(text).toContain('shortPreferred')
    expect(text).not.toContain('shortUpgrade')
  })

  it('kommt ohne Sprachwahl aus', () => {
    // Auflösung ausdrücklich setzen statt die Voreinstellung anzunehmen:
    // Sonst prüft der Test die Voreinstellung mit, und die darf sich ändern.
    const text = kurzfassung(
      { ...LEERE_ANTWORTEN, aufloesung: '1080p', sprachen: [] },
      t,
    )
    expect(text).toContain('1080p')
    expect(text).not.toContain('shortRequired')
    expect(text).not.toContain('shortPreferred')
  })
})
