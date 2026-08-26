import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import { Avatar } from '../../components/Avatar'
import { Betont } from '../../components/Betont'
import { ErrorBanner, Section, Spinner } from '../../components/ui'

type Zeile = {
  id: number
  user_id: number
  username: string
  name: string
  vorschau: string
  nur_lesen: boolean
  created_at: string
  expires_at: string | null
  last_used_at: string | null
}

/**
 * Wer in dieser Installation API-Token hat — die Aufsicht des Administrators.
 *
 * ⚠️ **Nur ansehen, nicht widerrufen.** Das ist keine Lücke, sondern eine
 * Entscheidung: Solange es kein geschütztes Betreiberkonto gibt, könnte ein
 * ernannter Administrator die Token dessen abschalten, der die Anwendung
 * betreibt — und ihn damit aus seiner eigenen Automatisierung aussperren.
 * Widerrufen kommt zusammen mit diesem Schutz.
 *
 * Als grobe Notbremse bleibt heute, das ganze Konto stillzulegen; das sperrt
 * dessen Token automatisch mit. Grob deshalb, weil es **alle** Token dieses
 * Kontos trifft und die Person gleich mit aussperrt.
 *
 * Den Token selbst zeigt diese Seite nicht — den gibt es nur einmal, beim
 * Anlegen, und niemand kann ihn nachschlagen. Was hier steht, ist der
 * Anfang zum Wiedererkennen, damit ein Gespräch überhaupt möglich ist
 * („der, der mit nxv_K0sZ… anfängt").
 */
export function AdminApiToken() {
  const { t } = useTranslation()

  const liste = useQuery({
    queryKey: ['admin-api-schluessel'],
    queryFn: () => api.get<Zeile[]>('/api/users/api-schluessel'),
  })

  const datum = (wert: string | null, nie: string) =>
    wert ? new Date(wert).toLocaleDateString() : nie

  return (
    <Section title={t('adminTokens.title')} breit>
      <p className="-mt-2 text-sm leading-relaxed text-mist-500">
        <Betont text={t('adminTokens.intro')} />
      </p>

      {liste.isPending && <Spinner />}
      {liste.isError && <ErrorBanner message={String(liste.error)} />}

      {liste.data && liste.data.length === 0 && (
        <p className="text-sm text-mist-600">{t('adminTokens.empty')}</p>
      )}

      {liste.data && liste.data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[44rem] border-collapse text-sm">
            <thead>
              <tr className="border-b border-ink-700 text-left text-xs uppercase tracking-wide text-mist-600">
                <th className="py-2 pr-4 font-medium">{t('adminTokens.owner')}</th>
                <th className="py-2 pr-4 font-medium">{t('adminTokens.name')}</th>
                <th className="py-2 pr-4 font-medium">{t('adminTokens.created')}</th>
                <th className="py-2 pr-4 font-medium">{t('adminTokens.lastUsed')}</th>
              </tr>
            </thead>
            <tbody>
              {liste.data.map((zeile) => (
                <tr key={zeile.id} className="border-b border-ink-800 last:border-0">
                  <td className="py-2.5 pr-4">
                    <span className="inline-flex items-center gap-2">
                      <Avatar name={zeile.username} className="h-6 w-6" />
                      <span className="text-mist-100">{zeile.username}</span>
                    </span>
                  </td>
                  <td className="py-2.5 pr-4">
                    <span className="block text-mist-100">
                      {zeile.name}
                      {zeile.nur_lesen && (
                        <span className="ml-2 rounded-full bg-ink-800 px-2 py-0.5 text-xs text-mist-500">
                          {t('apikeys.readOnly')}
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block font-mono text-xs text-mist-600">
                      {zeile.vorschau}…
                    </span>
                  </td>
                  <td className="py-2.5 pr-4 text-mist-500">
                    {new Date(zeile.created_at).toLocaleDateString()}
                    {/* Ein Ablaufdatum ist die Ausnahme - nur zeigen, wenn es
                        eines gibt, statt „unbegrenzt" in jede Zeile zu setzen. */}
                    {zeile.expires_at && (
                      <span className="mt-0.5 block text-xs text-warn-500">
                        {t('adminTokens.expires', {
                          when: new Date(zeile.expires_at).toLocaleDateString(),
                        })}
                      </span>
                    )}
                  </td>
                  {/* ⚠️ Die nützlichste Spalte: Ein Token, den seit Monaten
                      niemand angefasst hat, ist sichtbar tot - und ein Grund,
                      seinen Besitzer zu fragen, ob er noch gebraucht wird. */}
                  <td className="py-2.5 pr-4 text-mist-500">
                    {datum(zeile.last_used_at, t('apikeys.neverUsed'))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}
