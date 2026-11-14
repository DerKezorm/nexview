/**
 * Der Rückweg zum Code des Fernsehers: nur `/link`, nur einmal, nur zehn Minuten.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { abholen, geraeteSeite, merken } from './nachAnmeldung'

beforeEach(() => {
  window.sessionStorage.clear()
})

afterEach(() => {
  vi.useRealTimers()
  window.history.replaceState(null, '', '/')
})

describe('nachAnmeldung', () => {
  it('erkennt die Seite für den Fernseher-Code und sonst nichts', () => {
    window.history.replaceState(null, '', '/link?code=BCDF-GHJK')
    expect(geraeteSeite()).toBe('/link?code=BCDF-GHJK')
    for (const pfad of ['/', '/linkliste', '/profil?x=/link', '/titel/movie/1']) {
      window.history.replaceState(null, '', pfad)
      expect(geraeteSeite()).toBeNull()
    }
  })

  it('gibt die gemerkte Adresse genau einmal heraus', () => {
    merken('/link?code=BCDF-GHJK')
    expect(abholen()).toBe('/link?code=BCDF-GHJK')
    expect(abholen()).toBeNull()
  })

  it('vergisst sie nach zehn Minuten', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-14T18:00:00Z'))
    merken('/link?code=BCDF-GHJK')
    vi.setSystemTime(new Date('2026-11-14T18:10:01Z'))
    expect(abholen()).toBeNull()
  })

  it('führt nirgendwo anders hin, auch wenn jemand etwas anderes hineinschreibt', () => {
    for (const ziel of ['https://example.com/link', '//example.com', '/profil', '/linkx']) {
      merken(ziel)
      expect(abholen()).toBeNull()
    }
    window.sessionStorage.setItem('nexview.nachAnmeldung', 'kein json')
    expect(abholen()).toBeNull()
  })
})
