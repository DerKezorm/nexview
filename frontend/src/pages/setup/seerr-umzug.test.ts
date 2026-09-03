/**
 * Die Entscheidungsregeln des Umzugsassistenten.
 *
 * ⚠️ **Warum es diese Datei gibt.** Das Vorbild `MedienserverImport.tsx` trägt
 * drei Regeln im Kopf („nichts vorausgewählt", „verknüpfen ist nie die
 * Vorgabe", „jedes Konto sagt, woran es hängt") und keine einzige davon ist
 * von einem Test gehalten. Wer sie beim Umbau verliert, merkt es nicht - und
 * der Preis wäre ein zweites Konto für denselben Menschen, das Nexview nicht
 * mehr zusammenführen kann.
 */

import { describe, expect, it } from 'vitest'

import type { Anfragezeile, Kontozeile, Wahl } from './seerr-umzug-typen'
import { benutzernameAus, istFolgenlos, vorgabeFuer, zusammenfassen } from './seerr-umzug-typen'

function konto(teil: Partial<Kontozeile> & { seerr_id: number }): Kontozeile {
  return {
    anzeigename: 'beispiel',
    email: 'beispiel@example.com',
    herkunft: 'lokal',
    anbieter_kennung: null,
    treffer_user_id: null,
    treffer_grund: null,
    rolle_seerr: 'user',
    rolle_neu: 'user',
    rolle_verlust: null,
    kontingent_filme: null,
    kontingent_serien: null,
    kontingent_hinweise: [],
    anfragen: 0,
    bild: null,
    ...teil,
  }
}

function anfrage(teil: Partial<Anfragezeile> & { seerr_id: number }): Anfragezeile {
  return {
    titel_tmdb: 603,
    titel_tvdb: null,
    art: 'movie',
    staffel: null,
    ziel_status: 'downloaded',
    besteller_seerr_id: 1,
    uhd: false,
    uebersprungen: null,
    ...teil,
  }
}

describe('die Vorgabe je Zeile', () => {
  it('überspringt ein Konto ohne sicheres Gegenstück', () => {
    // ⚠️ Der wichtigste Test dieser Datei. Ein Assistent, der dreißig Konten
    // anlegt, muss dreißig bewusste Klicks kosten.
    expect(vorgabeFuer(konto({ seerr_id: 1 }))).toEqual({ was: 'ueberspringen' })
  })

  it('legt niemals von sich aus ein Konto an', () => {
    const faelle = [
      konto({ seerr_id: 1, herkunft: 'lokal' }),
      konto({ seerr_id: 2, herkunft: 'plex', anbieter_kennung: '4711' }),
      konto({ seerr_id: 3, rolle_seerr: 'admin' }),
      konto({ seerr_id: 4, anfragen: 500 }),
    ]
    expect(faelle.length).toBeGreaterThan(0)
    for (const fall of faelle) {
      expect(vorgabeFuer(fall).was).not.toBe('neu')
    }
  })

  it('ordnet nur bei einem sicheren Treffer zu, und dann dorthin', () => {
    // Ein Treffer entsteht ausschließlich über dieselbe Medienserver-Kennung
    // aus derselben Quelle - keine Vermutung über Namen oder Adressen.
    const wahl = vorgabeFuer(konto({ seerr_id: 1, treffer_user_id: 7 }))
    expect(wahl).toEqual({ was: 'zuordnen', zielUserId: 7 })
  })
})

describe('die Zusammenfassung', () => {
  const konten = [
    konto({ seerr_id: 1, treffer_user_id: 7 }),
    konto({ seerr_id: 2 }),
    konto({ seerr_id: 3 }),
  ]

  it('zählt ohne Zutun nur die sicheren Treffer', () => {
    const stand = zusammenfassen(konten, [], {})
    expect(stand.zugeordnet).toBe(1)
    expect(stand.neu).toBe(0)
    expect(stand.uebersprungen).toBe(2)
  })

  it('erlaubt zwei Seerr-Konten auf demselben Nexview-Konto', () => {
    // ⚠️ Der Fall, der diesen Assistenten von der Medienserver-Übernahme
    // trennt. Dort würde eine zweite Verknüpfung die erste überschreiben und
    // wird abgelehnt. Hier wird nichts verknüpft, hier werden Anfragen
    // zugerechnet - und derselbe Mensch hatte drüben oft zwei Zugänge.
    const wahlen: Record<number, Wahl> = {
      2: { was: 'zuordnen', zielUserId: 7 },
    }
    const stand = zusammenfassen(konten, [], wahlen)
    expect(stand.zugeordnet).toBe(2)
    expect(stand.mehrfachZiele).toEqual([7])
  })

  it('meldet Anfragen, die mit einem übersprungenen Konto wegfallen', () => {
    const anfragen = [
      anfrage({ seerr_id: 10, besteller_seerr_id: 1 }),
      anfrage({ seerr_id: 11, besteller_seerr_id: 2 }),
      anfrage({ seerr_id: 12, besteller_seerr_id: 2 }),
    ]
    const stand = zusammenfassen(konten, anfragen, {})
    expect(stand.anfragen).toBe(1)
    expect(stand.anfragenOhneKonto).toBe(2)
  })

  it('zählt Anfragen nicht mit, die ohnehin übersprungen werden', () => {
    const anfragen = [
      anfrage({ seerr_id: 10, besteller_seerr_id: 1 }),
      anfrage({ seerr_id: 11, besteller_seerr_id: 1, uebersprungen: 'keine TVDB-Nummer' }),
    ]
    const stand = zusammenfassen(konten, anfragen, {})
    expect(stand.anfragen).toBe(1)
    expect(stand.anfragenOhneKonto).toBe(0)
  })

  it('nimmt eine ausdrückliche Wahl vor der Vorgabe', () => {
    const wahlen: Record<number, Wahl> = {
      1: { was: 'ueberspringen' },
      2: { was: 'neu' },
    }
    const stand = zusammenfassen(konten, [], wahlen)
    expect(stand.zugeordnet).toBe(0)
    expect(stand.neu).toBe(1)
    expect(stand.uebersprungen).toBe(2)
  })
})


describe('folgenlose Zeilen', () => {
  it('erkennt einen sicheren Treffer ohne Anfragen als folgenlos', () => {
    // Konto ist da, Verknüpfung ist da, Rolle und Kontingente bleiben - zu
    // übertragen wäre einzig die Historie, und die ist leer.
    expect(istFolgenlos(konto({ seerr_id: 1, treffer_user_id: 7, anfragen: 0 }))).toBe(true)
  })

  it('lässt einen Treffer MIT Anfragen zur Entscheidung stehen', () => {
    // Hier gäbe es etwas zu übertragen, also darf der Betreiber es abwählen.
    expect(istFolgenlos(konto({ seerr_id: 1, treffer_user_id: 7, anfragen: 81 }))).toBe(false)
  })

  it('lässt ein Konto ohne Treffer immer zur Entscheidung stehen', () => {
    // Ohne Treffer geht es um die Frage, ob ein Konto entsteht - nie folgenlos.
    expect(istFolgenlos(konto({ seerr_id: 1, anfragen: 0 }))).toBe(false)
  })
})

describe('benutzernameAus', () => {
  it('macht aus einem Seerr-Namen einen, den Nexview annimmt', () => {
    // Nexview: 3-32 Zeichen, nur A-Za-z0-9._-
    expect(benutzernameAus('Dilara Uygun')).toBe('Dilara.Uygun')
    expect(benutzernameAus('Jörg Müller')).toBe('Jorg.Muller')
    expect(benutzernameAus('Straße')).toBe('Strasse')
  })

  it('gibt lieber nichts zurück als etwas Abgelehntes', () => {
    // ⚠️ Ein Vorschlag, der beim Anlegen scheitert, ist schlimmer als ein
    // leeres Feld: Er scheitert am Ende des Assistenten, wenn schon alles
    // andere geschrieben ist.
    expect(benutzernameAus('🎬')).toBe('')
    expect(benutzernameAus('ab')).toBe('')
    expect(benutzernameAus('   ')).toBe('')
  })

  it('lässt keine Trenner an den Rändern und keine Doppelpunkte stehen', () => {
    expect(benutzernameAus('  Robin  ')).toBe('Robin')
    expect(benutzernameAus('a..b..c')).toBe('a.b.c')
    expect(benutzernameAus('_robin_')).toBe('robin')
  })

  it('hält die Obergrenze ein', () => {
    expect(benutzernameAus('x'.repeat(50))).toHaveLength(32)
  })
})
