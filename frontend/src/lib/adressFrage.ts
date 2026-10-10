/**
 * Die Rückfrage, bevor sich die öffentliche Adresse ändert (Bauplan „Anmeldung
 * mit OIDC und authentik“, 04, Karte 1; Anmeldemodul 1.0.10).
 *
 * Führt die neue Adresse bei einem Eintrag des authentik-Knopfs von der
 * Rückleitadresse weg, die authentik kennt, oder gibt es aktive Anbieter von
 * Hand, antwortet der Server mit 409 `public_url_change` und speichert nichts.
 * Die Seite fragt dann und schickt dasselbe noch einmal mit Bestätigung.
 */

import { ApiError } from '../api/client'

/** Einträge des Knopfs, deren Adresse sich ändert (`count`, die erste als `address`), und aktive von Hand (`hand`). */
export type AdressFrage = { count: number; address: string; hand: number }

export function adressFrageAus(fehler: unknown): AdressFrage | null {
  if (!(fehler instanceof ApiError) || fehler.code !== 'public_url_change') return null
  const daten = fehler.data ?? {}
  return {
    count: Number(daten.count ?? 0),
    address: String(daten.address ?? ''),
    hand: Number(daten.hand ?? 0),
  }
}
