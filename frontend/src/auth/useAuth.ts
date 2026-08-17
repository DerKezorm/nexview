import { useContext } from 'react'

import { AuthContext } from './context'
import type { AuthState } from './context'

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (context === null) {
    throw new Error('useAuth muss innerhalb von <AuthProvider> verwendet werden.')
  }
  return context
}
