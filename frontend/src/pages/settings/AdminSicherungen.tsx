import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api, downloadFile } from '../../api/client'
import type { AppSettings, BackupSchedule } from '../../api/types'
import { Betont } from '../../components/Betont'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Fenster } from '../../components/Fenster'
import { SicherungEinspielen } from '../../components/SicherungEinspielen'
import { Symbol } from '../../components/Symbol'
import { Button, ErrorBanner, Field, Section, Spinner } from '../../components/ui'

const TAKTE: BackupSchedule[] = ['off', 'daily', 'weekly', 'monthly']

type Sicherung = {
  name: string
  groesse: number
  erstellt: string
  art: 'automatisch' | 'manuell'
  kommentar: string
  version: string
  einspielbar: boolean
  grund: string
}

type Liste = {
  eintraege: Sicherung[]
  version: string
  ordner: string
}

function groesse(bytes: number): string {
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

/**
 * Sicherungen: anlegen, ansehen, herunterladen.
 *
 * ⚠️ **Der Knopf, auf den es ankommt, ist „Herunterladen".** Die Kopien, die
 * Nexview von selbst anlegt, liegen im selben Verzeichnis wie die Datenbank —
 * stirbt das Volume, sind sie zusammen weg. Sie sind ein Rücksetzpunkt für ein
 * missglücktes Update, keine Sicherung. Eine Sicherung wird daraus erst, wenn
 * sie den Rechner verlässt.
 */
export function AdminSicherungen() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [anlegenOffen, setAnlegenOffen] = useState(false)
  const [kommentar, setKommentar] = useState('')
  const [holen, setHolen] = useState<Sicherung | null>(null)
  const [passwort, setPasswort] = useState('')
  const [passwortWdh, setPasswortWdh] = useState('')
  const [fehler, setFehler] = useState('')
  const [loeschen, setLoeschen] = useState<Sicherung | null>(null)
  const [einspielenOffen, setEinspielenOffen] = useState(false)

  const liste = useQuery({
    queryKey: ['sicherungen'],
    queryFn: () => api.get<Liste>('/api/admin/sicherungen'),
  })

  const einstellungen = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
  })

  const speichern = useMutation({
    mutationFn: (werte: Record<string, string | number>) =>
      api.put<AppSettings>('/api/settings', werte),
    onSuccess: (daten) => {
      queryClient.setQueryData(['settings'], daten)
      void queryClient.invalidateQueries({ queryKey: ['sicherungen'] })
    },
    onError: (e: unknown) => setFehler(e instanceof ApiError ? e.message : String(e)),
  })

  const entfernen = useMutation({
    mutationFn: (name: string) =>
      api.delete(`/api/admin/sicherungen/${encodeURIComponent(name)}`),
    onSuccess: () => {
      setLoeschen(null)
      void queryClient.invalidateQueries({ queryKey: ['sicherungen'] })
    },
    onError: (e: unknown) => setFehler(e instanceof ApiError ? e.message : String(e)),
  })

  const anlegen = useMutation({
    mutationFn: () => api.post('/api/admin/sicherungen', { kommentar: kommentar.trim() }),
    onSuccess: () => {
      setAnlegenOffen(false)
      setKommentar('')
      setFehler('')
      void queryClient.invalidateQueries({ queryKey: ['sicherungen'] })
    },
    onError: (e: unknown) => setFehler(e instanceof ApiError ? e.message : String(e)),
  })

  const herunterladen = useMutation({
    mutationFn: async () => {
      if (!holen) return
      await downloadFile(
        `/api/admin/sicherungen/${encodeURIComponent(holen.name)}/archiv`,
        holen.name.replace(/\.db$/, '.zip'),
        { passwort },
      )
    },
    onSuccess: () => {
      setHolen(null)
      setPasswort('')
      setPasswortWdh('')
      setFehler('')
    },
    onError: (e: unknown) => setFehler(e instanceof ApiError ? e.message : String(e)),
  })

  const passwortStimmt = passwort.length >= 8 && passwort === passwortWdh

  return (
    <div className="flex flex-col gap-6">
      {fehler && !anlegenOffen && !holen && !loeschen && <ErrorBanner message={fehler} />}

      {/* ⚠️ Bis 0.22 entstand eine automatische Sicherung **nur** bei einem
          Update. Zwischen zwei Fassungen koennen Monate liegen - wer am
          Dienstag versehentlich etwas loescht, hatte dann keinen Stand vom
          Montag. Genau diese Luecke schliesst der Zeitplan. */}
      {einstellungen.data && (
        <Section title={t('backups.title')} breit>
          {/* ⚠️ Überschrift und Einleitung stehen **in** der Sektion, nicht
              darüber. Auf „Adresse" und „Mail" war das schon so, auf dieser
              Seite nicht - und beim Durchklicken sprang genau das ins Auge. */}
          <p className="-mt-2 text-sm leading-relaxed text-mist-500">
            <Betont text={t('backups.intro')} />
          </p>

          <h3 className="mt-2 text-sm font-semibold text-mist-100">
            {t('backups.autoTitle')}
          </h3>

          <div className="flex flex-wrap items-end gap-6">
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-mist-100">{t('backups.scheduleLabel')}</span>
            <select
              className="rounded-xl border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-mist-100"
              value={einstellungen.data.backup_schedule}
              onChange={(e) => speichern.mutate({ backup_schedule: e.target.value })}
            >
              {TAKTE.map((takt) => (
                <option key={takt} value={takt}>
                  {t(`backups.schedule_${takt}`)}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-mist-100">{t('backups.keepLabel')}</span>
            <input
              type="number"
              min={2}
              max={50}
              className="w-24 rounded-xl border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-mist-100"
              defaultValue={einstellungen.data.backup_keep}
              onBlur={(e) => {
                const zahl = Number(e.target.value)
                if (zahl >= 2 && zahl <= 50 && zahl !== einstellungen.data?.backup_keep) {
                  speichern.mutate({ backup_keep: zahl })
                }
              }}
            />
          </label>

            <p className="max-w-md flex-1 text-xs leading-relaxed text-mist-600">
              <Betont text={t('backups.keepHint')} />
            </p>
          </div>
        </Section>
      )}

      {liste.isPending && <Spinner />}
      {liste.isError && <ErrorBanner message={t('backups.loadFailed')} />}

      {liste.data && liste.data.eintraege.length === 0 && (
        <p className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-6 text-sm text-mist-500">
          {t('backups.empty')}
        </p>
      )}

      {liste.data && liste.data.eintraege.length > 0 && (
        <Section title={t('backups.listTitle')} breit>
          <div className="-mt-1 flex justify-end">
            <Button
              type="button"
              onClick={() => {
                setFehler('')
                setAnlegenOffen(true)
              }}
            >
              {t('backups.createNow')}
            </Button>
          </div>

          <div className="overflow-x-auto rounded-xl border border-ink-700">
          <table className="w-full min-w-[46rem] text-left text-sm">
            <thead className="border-b border-ink-700 bg-ink-900 text-xs uppercase tracking-wide text-mist-600">
              <tr>
                <th className="px-4 py-3 font-medium">{t('backups.colWhen')}</th>
                <th className="px-4 py-3 font-medium">{t('backups.colKind')}</th>
                <th className="px-4 py-3 font-medium">{t('backups.colNote')}</th>
                <th className="px-4 py-3 font-medium">{t('backups.colVersion')}</th>
                <th className="px-4 py-3 text-right font-medium">{t('backups.colSize')}</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {liste.data.eintraege.map((eintrag) => (
                <tr key={eintrag.name} className="border-b border-ink-800 last:border-0">
                  <td className="whitespace-nowrap px-4 py-3 text-mist-100">
                    {new Date(eintrag.erstellt).toLocaleString()}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={
                        'rounded-full px-2.5 py-1 text-xs font-medium ' +
                        (eintrag.art === 'manuell'
                          ? 'bg-accent-500/15 text-accent-400'
                          : 'bg-ink-800 text-mist-500')
                      }
                    >
                      {t(eintrag.art === 'manuell' ? 'backups.kindManual' : 'backups.kindAuto')}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-mist-500">{eintrag.kommentar || '—'}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-mist-500">
                    {eintrag.version || '—'}
                    {/* ⚠️ Sichtbar, nicht erst beim Einspielen: Wer eine Sicherung
                        aus einer neueren Fassung aufhebt, soll das jetzt wissen
                        und nicht im Ernstfall herausfinden. */}
                    {!eintrag.einspielbar && (
                      <span className="ml-2 rounded-full bg-warn-500/15 px-2 py-0.5 text-xs text-warn-500">
                        {t(
                          eintrag.grund === 'backup_newer'
                            ? 'backups.tooNew'
                            : 'backups.unknownVersion',
                        )}
                      </span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right text-mist-500">
                    {groesse(eintrag.groesse)}
                  </td>
                  <td className="px-4 py-3">
                    {/* ⚠️ Nur Symbole - das spart in einer Tabelle, die ohnehin
                        breit ist, zwei Wortspalten. Beide Knöpfe tragen dafür
                        `aria-label` **und** `title`: Ein Symbol ohne Namen ist
                        für Vorleseprogramme stumm und für alle anderen ein
                        Ratespiel beim ersten Mal. */}
                    <div className="flex items-center justify-end gap-1">
                      <button
                        type="button"
                        aria-label={t('backups.download')}
                        title={t('backups.download')}
                        onClick={() => {
                          setFehler('')
                          setPasswort('')
                          setPasswortWdh('')
                          setHolen(eintrag)
                        }}
                        className="rounded-full border border-ink-700 p-2 text-mist-500 transition-colors hover:border-accent-600 hover:text-accent-400"
                      >
                        <Symbol name="herunterladen" className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        aria-label={t('common.delete')}
                        title={t('common.delete')}
                        onClick={() => {
                          setFehler('')
                          setLoeschen(eintrag)
                        }}
                        className="rounded-full border border-ink-700 p-2 text-mist-500 transition-colors hover:border-bad-500 hover:text-bad-500"
                      >
                        <Symbol name="loeschen" className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
            </table>
          </div>
        </Section>
      )}

      {liste.data && (
        <p className="text-xs text-mist-600">
          <Betont text={t('backups.folderHint', { ordner: liste.data.ordner })} />
        </p>
      )}

      {/* --- Wiederherstellen ----------------------------------------------
          ⚠️ **Bewusst unten und bewusst zugeklappt.** Das ist der einzige Knopf
          auf dieser Seite, der etwas wegnimmt statt anzulegen. Offen zwischen
          den anderen stehend wäre er eine Einladung zum Verklicken; die
          Warnungen stehen dann drinnen, direkt über der Entscheidung. */}
      <Section title={t('restore.title')} breit className="border-bad-500/30">
        <p className="-mt-2 text-sm leading-relaxed text-mist-500">
          <Betont text={t('restore.adminIntro')} />
        </p>

        {!einspielenOffen ? (
          <Button type="button" variant="ghost" onClick={() => setEinspielenOffen(true)}>
            {t('restore.open')}
          </Button>
        ) : (
          <>
            <SicherungEinspielen
              basis="/api/admin/sicherungen"
              // Nach dem Austausch stimmt weder die Sitzung noch irgendetwas,
              // was der Browser vorher geholt hat. Neu laden ist der einzige
              // ehrliche Abschluss - man landet auf der Anmeldung.
              onFertig={() => window.location.reload()}
            />
            <button
              type="button"
              onClick={() => setEinspielenOffen(false)}
              className="self-start text-sm text-mist-500 hover:text-mist-300"
            >
              {t('common.cancel')}
            </button>
          </>
        )}
      </Section>

      {/* --- Neue Sicherung ------------------------------------------------ */}
      <Fenster
        offen={anlegenOffen}
        titel={t('backups.createTitle')}
        unterzeile={t('backups.createSub')}
        onSchliessen={() => setAnlegenOffen(false)}
        fuss={
          <>
            <Button type="button" variant="ghost" onClick={() => setAnlegenOffen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              loading={anlegen.isPending}
              onClick={() => anlegen.mutate()}
            >
              {t('backups.createNow')}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <Field
            label={t('backups.noteLabel')}
            hint={t('backups.noteHint')}
            value={kommentar}
            maxLength={200}
            onChange={(e) => setKommentar(e.target.value)}
          />
          {fehler && <ErrorBanner message={fehler} />}
        </div>
      </Fenster>

      {/* --- Herunterladen -------------------------------------------------- */}
      <Fenster
        offen={holen !== null}
        titel={t('backups.downloadTitle')}
        unterzeile={holen?.name}
        onSchliessen={() => setHolen(null)}
        fuss={
          <>
            <Button type="button" variant="ghost" onClick={() => setHolen(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              disabled={!passwortStimmt}
              loading={herunterladen.isPending}
              onClick={() => herunterladen.mutate()}
            >
              {t('backups.download')}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <p className="text-sm leading-relaxed text-mist-500">
            <Betont text={t('backups.downloadWhat')} />
          </p>
          {/* ⚠️ Der Satz, der hier stehen muss: Ein vergessenes Passwort macht
              die Sicherung wertlos. Es gibt keinen Zweitschlüssel. */}
          <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm leading-relaxed text-warn-500">
            <Betont text={t('backups.passwordWarning')} />
          </p>
          <Field
            label={t('backups.passwordLabel')}
            type="password"
            autoComplete="new-password"
            value={passwort}
            onChange={(e) => setPasswort(e.target.value)}
          />
          <Field
            label={t('backups.passwordRepeat')}
            type="password"
            autoComplete="new-password"
            value={passwortWdh}
            onChange={(e) => setPasswortWdh(e.target.value)}
          />
          {passwort.length > 0 && passwort.length < 8 && (
            <p className="text-sm text-mist-600">{t('backups.passwordTooShort')}</p>
          )}
          {passwortWdh.length > 0 && passwort !== passwortWdh && (
            <p className="text-sm text-bad-500">{t('backups.passwordMismatch')}</p>
          )}
          {fehler && <ErrorBanner message={fehler} />}
        </div>
      </Fenster>

      {/* ⚠️ Ein Loeschen-Knopf neben Sicherungen nimmt genau das weg, was im
          Ernstfall zaehlt. Deshalb eine Rueckfrage, die den Namen nennt - und
          bei einer von Hand angelegten zusaetzlich die Notiz, an der man sie
          wiedererkennt. */}
      <ConfirmDialog
        open={loeschen !== null}
        title={t('backups.deleteTitle')}
        description={
          loeschen
            ? t('backups.deleteText', {
                wann: new Date(loeschen.erstellt).toLocaleString(),
                notiz: loeschen.kommentar || '—',
              })
            : ''
        }
        warning={t('backups.deleteWarning')}
        confirmLabel={t('common.delete')}
        loading={entfernen.isPending}
        onConfirm={() => loeschen && entfernen.mutate(loeschen.name)}
        onCancel={() => setLoeschen(null)}
      />
    </div>
  )
}
