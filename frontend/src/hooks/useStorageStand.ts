import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { StorageMine, StorageOverview } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { useConfig } from './useConfig'

/**
 * Der Speicher-Stand, den *diese* Person sehen will.
 *
 * **Für Administratoren ist die persönliche Zahl bedeutungslos.** Sie steht
 * per Definition auf null: Was ein Administrator holt, gehört dem Haus, und
 * eine Grenze hat er ohnehin nicht. Ihn interessiert der Gesamtbestand.
 *
 * Für alle anderen umgekehrt: Der Gesamtbestand sagt ihnen nichts über das,
 * was sie selbst dürfen.
 *
 * Bewusst an einer Stelle: Die Frage wird im Menü und bei „Meine Anfragen"
 * gestellt, und zwei Antworten darauf wären zwei Wahrheiten.
 */
export type SpeicherStand = {
  /** Was angezeigt wird - je nach Rolle die eigene oder die ganze Belegung. */
  bytes: number
  /** Nur bei persönlicher Sicht gesetzt. null heißt unbegrenzt. */
  limitBytes: number | null
  /** Für die Überschrift: „Belegter Platz" oder „Belegt insgesamt". */
  gesamtsicht: boolean
  ueberzogen: boolean
}

export function useStorageStand(aktiv = true): SpeicherStand | null {
  const { user } = useAuth()
  const { data: config } = useConfig()
  const an = aktiv && Boolean(config?.storage_enabled)
  const istAdmin = user?.role === 'admin'

  // ⚠️ `staleTime: 0` mit Absicht, entgegen der globalen Minute: Diese Zahl
  // ändert sich **auf dem Server** – ein Download wird fertig, der Abgleich
  // misst nach –, ohne dass hier irgendeine Mutation läuft, die den Cache
  // einladen könnte. Gemeldet wurde: die Profilseite zeigte den neuen Stand,
  // das Menü daneben noch den alten. Beim Öffnen wird deshalb immer frisch
  // geholt; bis die Antwort da ist, steht der letzte Wert – das flackert
  // nicht, es aktualisiert.
  const eigener = useQuery({
    queryKey: ['storage-mine'],
    queryFn: () => api.get<StorageMine>('/api/storage/me'),
    enabled: an && !istAdmin,
    staleTime: 0,
  })

  const gesamt = useQuery({
    queryKey: ['storage-overview'],
    queryFn: () => api.get<StorageOverview>('/api/storage/overview'),
    enabled: an && istAdmin,
    staleTime: 0,
  })

  if (!an) return null

  if (istAdmin) {
    if (!gesamt.data) return null
    return {
      bytes: gesamt.data.total_bytes,
      limitBytes: null,
      gesamtsicht: true,
      ueberzogen: false,
    }
  }

  if (!eigener.data) return null
  const { used_bytes: belegt, limit_bytes: grenze } = eigener.data
  return {
    bytes: belegt,
    limitBytes: grenze,
    gesamtsicht: false,
    ueberzogen: grenze !== null && belegt >= grenze,
  }
}
