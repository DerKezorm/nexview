/**
 * Persönliche Zugriffs-Schlüssel — anlegen, ansehen, widerrufen.
 *
 * ⚠️ **Der Klartext erscheint genau einmal.** Er steht nur in der Antwort aufs
 * Anlegen; danach kennt ihn niemand mehr, auch der Administrator nicht. Deshalb
 * bleibt das Fenster nach dem Anlegen offen und zeigt ihn zum Kopieren, statt
 * sich zu schließen und den Nutzer im Glauben zu lassen, er könne ihn später
 * nachschlagen.
 *
 * Warum ein Schlüssel die Rechte seines Besitzers erbt und es keine feineren
 * Abstufungen als „nur lesen" gibt, steht bei ``models.ApiKey`` im Backend.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import { Betont } from '../../components/Betont'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Fenster } from '../../components/Fenster'
import { Button, ErrorBanner, Field, Section, Spinner } from '../../components/ui'

type Schluessel = {
  id: number
  name: string
  vorschau: string
  nur_lesen: boolean
  created_at: string
  expires_at: string | null
  last_used_at: string | null
}

type Neuer = Schluessel & { schluessel: string }

export function ApiSchluessel() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [anlegenOffen, setAnlegenOffen] = useState(false)
  const [name, setName] = useState('')
  const [nurLesen, setNurLesen] = useState(false)
  const [frisch, setFrisch] = useState<Neuer | null>(null)
  const [widerrufen, setWiderrufen] = useState<Schluessel | null>(null)
  const [fehler, setFehler] = useState('')
  const [kopiert, setKopiert] = useState(false)

  const liste = useQuery({
    queryKey: ['api-schluessel'],
    queryFn: () => api.get<Schluessel[]>('/api/auth/me/schluessel'),
  })

  const anlegen = useMutation({
    mutationFn: () =>
      api.post<Neuer>('/api/auth/me/schluessel', {
        name: name.trim(),
        nur_lesen: nurLesen,
      }),
    onSuccess: (neuer) => {
      setAnlegenOffen(false)
      setName('')
      setNurLesen(false)
      setFehler('')
      setKopiert(false)
      setFrisch(neuer)
      void queryClient.invalidateQueries({ queryKey: ['api-schluessel'] })
    },
    onError: (e: unknown) => setFehler(e instanceof ApiError ? e.message : String(e)),
  })

  const entfernen = useMutation({
    mutationFn: (id: number) => api.delete(`/api/auth/me/schluessel/${id}`),
    onSuccess: () => {
      setWiderrufen(null)
      void queryClient.invalidateQueries({ queryKey: ['api-schluessel'] })
    },
    onError: (e: unknown) => setFehler(e instanceof ApiError ? e.message : String(e)),
  })

  return (
    <Section title={t('apikeys.title')} breit>
      <p className="-mt-2 text-sm leading-relaxed text-mist-500">
        <Betont text={t('apikeys.intro')} />
      </p>

      {fehler && !anlegenOffen && !widerrufen && <ErrorBanner message={fehler} />}
      {liste.isPending && <Spinner />}

      {liste.data && liste.data.length === 0 && (
        <p className="text-sm text-mist-600">{t('apikeys.empty')}</p>
      )}

      {liste.data && liste.data.length > 0 && (
        <ul className="flex flex-col gap-2">
          {liste.data.map((eintrag) => (
            <li
              key={eintrag.id}
              className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700 bg-ink-900 px-4 py-3"
            >
              <span className="flex-1">
                <span className="block text-sm font-medium text-mist-100">
                  {eintrag.name}
                  {eintrag.nur_lesen && (
                    <span className="ml-2 rounded-full bg-ink-800 px-2 py-0.5 text-xs text-mist-500">
                      {t('apikeys.readOnly')}
                    </span>
                  )}
                </span>
                <span className="mt-0.5 block font-mono text-xs text-mist-600">
                  {eintrag.vorschau}…
                </span>
              </span>

              {/* ⚠️ „Zuletzt benutzt" ist die nützlichste Spalte der Liste:
                  Ein Schlüssel, den seit Monaten niemand angefasst hat, ist
                  sichtbar tot und lässt sich guten Gewissens widerrufen. */}
              <span className="text-xs text-mist-600">
                {eintrag.last_used_at
                  ? t('apikeys.lastUsed', {
                      when: new Date(eintrag.last_used_at).toLocaleDateString(),
                    })
                  : t('apikeys.neverUsed')}
              </span>

              <Button type="button" variant="ghost" onClick={() => setWiderrufen(eintrag)}>
                {t('apikeys.revoke')}
              </Button>
            </li>
          ))}
        </ul>
      )}

      <Button
        type="button"
        variant="ghost"
        className="self-start"
        onClick={() => {
          setFehler('')
          setAnlegenOffen(true)
        }}
      >
        {t('apikeys.create')}
      </Button>

      {/* --- Anlegen -------------------------------------------------------- */}
      <Fenster
        offen={anlegenOffen}
        titel={t('apikeys.create')}
        onSchliessen={() => setAnlegenOffen(false)}
        fuss={
          <>
            <Button type="button" variant="ghost" onClick={() => setAnlegenOffen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              disabled={name.trim().length === 0}
              loading={anlegen.isPending}
              onClick={() => anlegen.mutate()}
            >
              {t('apikeys.create')}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <Field
            label={t('apikeys.nameLabel')}
            hint={t('apikeys.nameHint')}
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
          />

          <label className="flex items-start gap-2.5 text-sm">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={nurLesen}
              onChange={(e) => setNurLesen(e.target.checked)}
            />
            <span>
              <span className="block font-medium text-mist-100">{t('apikeys.readOnly')}</span>
              <span className="mt-0.5 block text-mist-500">{t('apikeys.readOnlyHint')}</span>
            </span>
          </label>

          {fehler && <ErrorBanner message={fehler} />}
        </div>
      </Fenster>

      {/* --- Der Klartext, genau einmal -------------------------------------- */}
      <Fenster
        offen={frisch !== null}
        titel={t('apikeys.createdTitle')}
        unterzeile={frisch?.name}
        onSchliessen={() => setFrisch(null)}
        fuss={
          <Button type="button" onClick={() => setFrisch(null)}>
            {t('apikeys.done')}
          </Button>
        }
      >
        <div className="flex flex-col gap-4">
          <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm leading-relaxed text-warn-500">
            <Betont text={t('apikeys.onlyOnce')} />
          </p>

          <div className="flex flex-wrap items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded-xl border border-ink-700 bg-ink-950 px-3 py-2 font-mono text-sm text-mist-100">
              {frisch?.schluessel}
            </code>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                if (frisch) void navigator.clipboard?.writeText(frisch.schluessel)
                setKopiert(true)
              }}
            >
              {kopiert ? t('apikeys.copied') : t('apikeys.copy')}
            </Button>
          </div>

          <p className="text-sm leading-relaxed text-mist-500">
            <Betont text={t('apikeys.howToUse')} />
          </p>
        </div>
      </Fenster>

      <ConfirmDialog
        open={widerrufen !== null}
        title={t('apikeys.revokeTitle')}
        description={widerrufen?.name ?? ''}
        warning={t('apikeys.revokeWarning')}
        confirmLabel={t('apikeys.revoke')}
        loading={entfernen.isPending}
        onConfirm={() => widerrufen && entfernen.mutate(widerrufen.id)}
        onCancel={() => setWiderrufen(null)}
      />
    </Section>
  )
}
