/**
 * Mein Konto → Sicherheit → Anmeldeanbieter: je aktivem Anbieter eine Zeile
 * mit Zustand und „Verknüpfen“ oder „Lösen“ (Bauplan „Anmeldung mit OIDC und
 * authentik“, 04).
 *
 * Verknüpfen ist eine **Reise des ganzen Browsers**: Die Seite fragt erst das
 * eigene Passwort noch einmal (ein Verknüpfen gibt das Konto an den, der beim
 * Anbieter angemeldet ist), holt mit der Sitzung die Absprungadresse und
 * schickt den Browser dorthin. Zurück kommt er hierher mit `?linked=<slug>`
 * oder `?error=<code>` – gelesen, angezeigt, aus der Adresse geräumt.
 *
 * Ein Konto ohne Passwort kann seine letzte Verknüpfung nicht lösen; der Knopf
 * fehlt dann, und der Satz `oidc.account.only` sagt warum.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { OidcMeinAnbieter, User } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { Fenster } from '../../components/Fenster'
import { Button, ErrorBanner, Field, Section } from '../../components/ui'
import i18n from '../../i18n'
import { oidcErrorKey } from '../../vendor/nexoidc/oidc'

/**
 * Ergebnis der Rückkehr aus der Adresse lesen – ohne sie anzufassen.
 *
 * ⚠️ Rein: React ruft einen `useState`-Anfang im Entwicklungsmodus zweimal auf
 * und behält einen der beiden. Räumte schon der erste die Adresse, sähe der
 * zweite nichts mehr. Geräumt wird deshalb danach (`adresseRaeumen`).
 */
function rueckkehrAusAdresse(): { ok: boolean; slug?: string; text?: string } | null {
  const params = new URLSearchParams(window.location.search)
  const verknuepft = params.get('linked')
  const code = params.get('error')
  if (!verknuepft && !code) return null
  if (code) {
    const schluessel = oidcErrorKey(code)
    return { ok: false, text: schluessel ? i18n.t(schluessel) : i18n.t('errors.generic') }
  }
  return { ok: true, slug: verknuepft ?? '' }
}

/** `?linked=` und `?error=` aus der Adresse nehmen: Ein Neuladen zeigt die Meldung nicht noch einmal. */
function adresseRaeumen(): void {
  const params = new URLSearchParams(window.location.search)
  if (!params.has('linked') && !params.has('error')) return
  params.delete('linked')
  params.delete('error')
  const rest = params.toString()
  window.history.replaceState(
    window.history.state,
    '',
    window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash,
  )
}

export function OidcLinks() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { user, updateUser } = useAuth()
  const [rueckkehr] = useState(rueckkehrAusAdresse)
  useEffect(adresseRaeumen, [])
  const [meldung, setMeldung] = useState<{ ok: boolean; text: string } | null>(null)
  const [passwortFuer, setPasswortFuer] = useState<OidcMeinAnbieter | null>(null)
  const [passwort, setPasswort] = useState('')

  const anbieter = useQuery({
    queryKey: ['oidc-meine'],
    queryFn: () => api.get<OidcMeinAnbieter[]>('/api/oidc/me'),
  })
  const liste = anbieter.data ?? []

  const verknuepfen = useMutation({
    mutationFn: ({ slug, password }: { slug: string; password: string }) =>
      api.post<{ url: string }>(`/api/oidc/${encodeURIComponent(slug)}/link`, { password }),
    onSuccess: ({ url }) => {
      window.location.href = url
    },
  })

  const loesen = useMutation({
    mutationFn: (eintrag: OidcMeinAnbieter) =>
      api.delete(`/api/oidc/${encodeURIComponent(eintrag.slug)}/link`),
    onMutate: () => setMeldung(null),
    onSuccess: async (_antwort, eintrag) => {
      setMeldung({ ok: true, text: t('anmeldung.unlinkedDone', { name: eintrag.label }) })
      await queryClient.invalidateQueries({ queryKey: ['oidc-meine'] })
      updateUser(await api.get<User>('/api/auth/me'))
    },
    onError: (fehler) =>
      setMeldung({ ok: false, text: fehler instanceof ApiError ? fehler.message : t('errors.network') }),
  })

  if (!user || (liste.length === 0 && !rueckkehr)) return null

  const hatPasswort = user.has_password !== false
  const verknuepfte = liste.filter((eintrag) => eintrag.linked)
  const zurueckVon = rueckkehr?.ok ? liste.find((eintrag) => eintrag.slug === rueckkehr.slug) : undefined
  const erstMeldung =
    meldung ??
    (rueckkehr
      ? rueckkehr.ok
        ? { ok: true, text: t('anmeldung.linkedDone', { name: zurueckVon?.label ?? rueckkehr.slug }) }
        : { ok: false, text: rueckkehr.text ?? t('errors.generic') }
      : null)

  return (
    <Section title={t('oidc.account.title')} breit>
      <p className="-mt-2 text-sm leading-relaxed text-mist-500">{t('oidc.account.text')}</p>
      {erstMeldung &&
        (erstMeldung.ok ? (
          <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
            {erstMeldung.text}
          </p>
        ) : (
          <ErrorBanner message={erstMeldung.text} />
        ))}
      <ul className="divide-y divide-ink-700 rounded-xl border border-ink-700" data-testid="meine-anbieter">
        {liste.map((eintrag) => {
          // Ohne Passwort hält die letzte Verknüpfung das Konto: Sie bleibt.
          const letzte = !hatPasswort && eintrag.linked && verknuepfte.length === 1
          return (
            <li key={eintrag.slug} className="flex flex-wrap items-center gap-3 px-3 py-3 text-sm">
              <span className="min-w-0 flex-1">
                <span className="block font-medium text-mist-100">{eintrag.label}</span>
                <span className={'block text-xs ' + (eintrag.linked ? 'text-ok-500' : 'text-mist-500')}>
                  {eintrag.linked ? t('oidc.account.linked') : t('oidc.account.notLinked')}
                </span>
                {letzte && (
                  <span className="mt-1 block text-xs text-mist-500">
                    {t('oidc.account.only', { name: eintrag.label })}
                  </span>
                )}
              </span>
              {eintrag.linked ? (
                !letzte && (
                  <Button
                    variant="ghost"
                    loading={loesen.isPending && loesen.variables?.slug === eintrag.slug}
                    onClick={() => loesen.mutate(eintrag)}
                  >
                    {t('oidc.account.unlink')}
                  </Button>
                )
              ) : (
                hatPasswort && (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      verknuepfen.reset()
                      setPasswort('')
                      setPasswortFuer(eintrag)
                    }}
                  >
                    {t('oidc.account.link')}
                  </Button>
                )
              )}
            </li>
          )
        })}
      </ul>
      <Fenster
        offen={passwortFuer !== null}
        titel={passwortFuer ? t('anmeldung.passwordTitle', { name: passwortFuer.label }) : ''}
        onSchliessen={() => setPasswortFuer(null)}
        fuss={
          <>
            <Button variant="ghost" onClick={() => setPasswortFuer(null)}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" form="verknuepfen-passwort" loading={verknuepfen.isPending}>
              {t('oidc.account.link')}
            </Button>
          </>
        }
      >
        <form
          id="verknuepfen-passwort"
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (passwortFuer) verknuepfen.mutate({ slug: passwortFuer.slug, password: passwort })
          }}
        >
          <p className="text-sm text-mist-500">{t('oidc.account.text')}</p>
          <Field
            label={t('anmeldung.password')}
            type="password"
            value={passwort}
            onChange={(event) => setPasswort(event.target.value)}
            autoComplete="current-password"
            autoFocus
            required
          />
          {verknuepfen.isError && (
            <ErrorBanner
              message={verknuepfen.error instanceof ApiError ? verknuepfen.error.message : t('errors.network')}
            />
          )}
        </form>
      </Fenster>
    </Section>
  )
}
