/**
 * In der Zeile eines Kontos: die Anmelde-Anbieter, mit denen es verknüpft ist,
 * als kleine Marken – und je Marke „Verknüpfung lösen“ (Bauplan „Anmeldung mit
 * OIDC und authentik“, 01 und 04, Kontenliste).
 *
 * Lösen fragt das eigene Passwort des Betreibers noch einmal; das Konto des
 * Betreibers selbst kann niemand sonst anfassen (der Server lehnt es ab). Ein
 * Konto ohne Passwort behält seine letzte Verknüpfung (`oidc_only_account`).
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { OidcVerknuepfung, User } from '../../api/types'
import { Fenster } from '../../components/Fenster'
import { Button, ErrorBanner, Field } from '../../components/ui'

export function OidcMarken({ user }: { user: User }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [loesen, setLoesen] = useState<OidcVerknuepfung | null>(null)
  const [passwort, setPasswort] = useState('')
  const [erledigt, setErledigt] = useState(false)

  const weg = useMutation({
    mutationFn: (link: OidcVerknuepfung) =>
      api.delete(`/api/oidc/admin/accounts/${user.id}/links/${link.provider_id}`, {
        password: passwort,
      }),
    onSuccess: () => {
      setLoesen(null)
      setErledigt(true)
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      void queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })

  const links = user.oidc_links ?? []
  if (links.length === 0 && !erledigt) return null

  return (
    <span className="flex shrink-0 flex-wrap items-center gap-1.5" data-testid="oidc-marken">
      {links.map((link) => (
        <span
          key={link.provider_id}
          className="inline-flex items-center gap-1 rounded-full border border-ok-500/40 bg-ok-500/10 px-2 py-0.5 text-xs text-ok-500"
          title={link.email ?? undefined}
        >
          {link.label}
          <button
            type="button"
            className="text-ok-500/70 hover:text-mist-100"
            aria-label={t('anmeldung.unlinkFor', { name: link.label })}
            onClick={() => {
              weg.reset()
              setPasswort('')
              setLoesen(link)
            }}
          >
            ×
          </button>
        </span>
      ))}
      {erledigt && links.length === 0 && (
        <span className="text-xs text-mist-500">{t('anmeldung.unlinkForDone')}</span>
      )}
      <Fenster
        offen={loesen !== null}
        titel={
          loesen ? t('anmeldung.unlinkForTitle', { user: user.display_name ?? user.username, name: loesen.label }) : ''
        }
        onSchliessen={() => setLoesen(null)}
        fuss={
          <>
            <Button variant="ghost" onClick={() => setLoesen(null)}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" form={`loesen-${user.id}`} loading={weg.isPending}>
              {t('oidc.account.unlink')}
            </Button>
          </>
        }
      >
        <form
          id={`loesen-${user.id}`}
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (loesen) weg.mutate(loesen)
          }}
        >
          <p className="text-sm text-mist-500">
            {loesen
              ? t('anmeldung.unlinkForText', { user: user.display_name ?? user.username, name: loesen.label })
              : ''}
          </p>
          <Field
            label={t('anmeldung.password')}
            type="password"
            value={passwort}
            onChange={(event) => setPasswort(event.target.value)}
            autoComplete="current-password"
            autoFocus
            required
          />
          {weg.isError && (
            <ErrorBanner message={weg.error instanceof ApiError ? weg.error.message : t('errors.network')} />
          )}
        </form>
      </Fenster>
    </span>
  )
}
