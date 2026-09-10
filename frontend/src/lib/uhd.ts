import type { MediaType, User } from '../api/types'

type UhdRecht = Pick<User, 'can_approve' | 'can_request_uhd_movies' | 'can_request_uhd_series'>

/**
 * Darf dieses Konto diese Medienart in 4K anfragen?
 *
 * Das Gegenstück zu ``User.may_request_uhd`` im Backend, mit derselben Regel:
 * Wer freigeben darf – Administratoren **und** Entscheider –, immer; alle
 * anderen nur mit dem Häkchen je Medienart.
 *
 * ⚠️ **Über ``can_approve``, nicht über die Rolle.** Im Formular stand einmal
 * ``role === 'admin'``. Ein Entscheider ohne Häkchen sah dann auf der
 * Titelseite „4K nicht angefragt" samt Anfrageknopf – der Server liefert
 * ``status_uhd`` auch an ihn –, aber im Formular fehlte der Umschalter. Lag
 * die Standard-Fassung schon vor, ließ sich dort gar nichts abschicken.
 *
 * Ob es überhaupt eine 4K-Instanz gibt, steht bewusst nicht hier: Das Recht
 * hängt am Konto, die Instanz am Haus.
 */
export function darfUhdAnfragen(user: UhdRecht | null | undefined, mediaType: MediaType): boolean {
  if (!user) return false
  if (user.can_approve) return true
  return mediaType === 'movie' ? user.can_request_uhd_movies : user.can_request_uhd_series
}
