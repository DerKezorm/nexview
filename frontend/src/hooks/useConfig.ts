import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { AppConfig } from '../api/types'

/** Allgemeine Konfiguration (Region, Demo-Modus) - für alle Angemeldeten sichtbar. */
export function useConfig() {
  return useQuery({
    queryKey: ['config'],
    queryFn: () => api.get<AppConfig>('/api/config'),
    staleTime: 5 * 60 * 1000,
  })
}
