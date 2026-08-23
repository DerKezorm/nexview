import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { Child } from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import {
  Button,
  Card,
  ErrorBanner,
  Field,
  PlusKachel,
  RundKnopf,
  Spinner,
} from '../../components/ui'
import { useAuth } from '../../auth/useAuth'
import { useConfig } from '../../hooks/useConfig'
import { KinderGesperrt } from './KinderGesperrt'
import { KinderHilfe } from './KinderHilfe'
import { Kindervorschau } from './Kindervorschau'
import { Kinderwuensche } from './Kinderwuensche'

/**
 * Bis zu welchem Alter ein Kinderkonto reicht – muss zu `MAX_CHILD_AGE` im
 * Backend passen. Darüber ist es kein Kinderkonto mehr, sondern ein
 * gewöhnliches mit eigener Adresse und eigenem Kontingent.
 */
const MAX_ALTER = 16

/**
 * Kinderkonten – vom Elternteil selbst verwaltet.
 *
 * Ein Kinderkonto hat nur Benutzername, Passwort und ein Alter: keine
 * Mailadresse, keinen Media-Server, kein eigenes Kontingent. Es ist dem Konto
 * der Eltern untergeordnet.
 *
 * Aufbau wie bei den Benachrichtigungs-Zielen: ein Kachelraster mit der
 * gestrichelten Plus-Kachel am Ende, und das Formular klappt darunter auf.
 * Solange eines offen ist, bleibt nur die zugehörige Kachel stehen – das sagt
 * eindeutiger, was gerade bearbeitet wird, als jede Linie es könnte.
 */
export function Kinder() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const [hilfe, setHilfe] = useState(false)

  /** `null` = nichts offen, `'neu'` = anlegen, sonst die Kennung des Kindes. */
  const [offen, setOffen] = useState<number | 'neu' | null>(null)
  const [loeschKandidat, setLoeschKandidat] = useState<Child | null>(null)
  const [vorschau, setVorschau] = useState<Child | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)

  const darfVerwalten = user?.role === 'admin' || Boolean(user?.can_manage_children)

  const kinderQuery = useQuery({
    queryKey: ['children'],
    queryFn: () => api.get<Child[]>('/api/children'),
    // Ohne Freigabe gibt es nichts zu holen - und die Antwort wäre ohnehin leer.
    enabled: darfVerwalten,
  })

  const auffrischen = () => void queryClient.invalidateQueries({ queryKey: ['children'] })

  const umschalten = useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) =>
      api.patch<Child>(`/api/children/${id}`, { is_active }),
    onSuccess: auffrischen,
    onError: (error) => setFehler(error instanceof ApiError ? error.message : String(error)),
  })

  const loeschen = useMutation({
    mutationFn: (id: number) => api.delete(`/api/children/${id}`),
    onSuccess: () => {
      setLoeschKandidat(null)
      setOffen(null)
      auffrischen()
    },
    onError: (error) => setFehler(error instanceof ApiError ? error.message : String(error)),
  })

  if (!darfVerwalten) return <KinderGesperrt />

  if (kinderQuery.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }

  const kinder = kinderQuery.data ?? []
  const bearbeitet = typeof offen === 'number' ? kinder.find((k) => k.id === offen) : undefined
  const feldOffen = offen !== null

  // Die Vorschau nimmt die ganze Fläche ein: Sie soll sich anfühlen wie ein
  // Blick in die App des Kindes, nicht wie ein Kästchen daneben.
  if (vorschau) {
    const aktuell = kinder.find((k) => k.id === vorschau.id) ?? vorschau
    return <Kindervorschau kind={aktuell} onZurueck={() => setVorschau(null)} />
  }

  return (
    <div className="flex flex-col gap-6">
      <Kinderwuensche />

      <div className="border-t border-ink-700 pt-6">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          {t('children.title')}
          {/* Was die Funktion kann - und vor allem, was sie nicht kann.
              Als Fragezeichen und nicht als Textwand: Wer es schon weiß,
              soll nicht jedes Mal darüber hinweglesen müssen. */}
          <button
            type="button"
            onClick={() => setHilfe(true)}
            aria-label={t('children.helpTitle')}
            title={t('children.helpTitle')}
            className="flex h-6 w-6 items-center justify-center rounded-full border border-ink-600 text-xs font-bold text-mist-500 transition-colors hover:border-accent-500/60 hover:text-accent-400"
          >
            ?
          </button>
        </h2>
        <p className="mt-1 text-sm text-mist-500">{t('children.intro')}</p>
      </div>

      <KinderHilfe offen={hilfe} onSchliessen={() => setHilfe(false)} />

      {fehler && <ErrorBanner message={fehler} />}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {kinder
          .filter((kind) => !feldOffen || offen === kind.id)
          .map((kind) => (
            <Kachel
              key={kind.id}
              kind={kind}
              aktiv={offen === kind.id}
              onBearbeiten={() => {
                setFehler(null)
                setOffen(offen === kind.id ? null : kind.id)
              }}
              onUmschalten={() => umschalten.mutate({ id: kind.id, is_active: !kind.is_active })}
              onLoeschen={() => setLoeschKandidat(kind)}
              onVorschau={() => setVorschau(kind)}
            />
          ))}

        {(!feldOffen || offen === 'neu') && (
          <PlusKachel
            beschriftung={t('children.add')}
            aktiv={offen === 'neu'}
            onClick={() => {
              setFehler(null)
              setOffen(offen === 'neu' ? null : 'neu')
            }}
          />
        )}
      </div>

      {kinder.length === 0 && offen === null && (
        <p className="text-sm text-mist-500">{t('children.emptyHint')}</p>
      )}

      {offen === 'neu' && (
        <KindFeld
          kind={null}
          onFertig={() => {
            setOffen(null)
            auffrischen()
          }}
          onAbbrechen={() => setOffen(null)}
        />
      )}

      {bearbeitet && (
        <KindFeld
          key={bearbeitet.id}
          kind={bearbeitet}
          onFertig={auffrischen}
          onAbbrechen={() => setOffen(null)}
          onVorschau={() => setVorschau(bearbeitet)}
        />
      )}

      {/* Was diese Sperre ist – und was nicht. Ein Kinderkonto verspricht
          sonst mehr, als Nexview halten kann. */}
      <p className="rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3 text-sm text-mist-500">
        {t('children.scopeHint')}
      </p>

      <ConfirmDialog
        open={loeschKandidat !== null}
        title={t('children.deleteTitle')}
        description={t('children.deleteText', {
          name: loeschKandidat?.display_name ?? loeschKandidat?.username ?? '',
        })}
        confirmLabel={t('common.delete')}
        loading={loeschen.isPending}
        onConfirm={() => loeschKandidat && loeschen.mutate(loeschKandidat.id)}
        onCancel={() => setLoeschKandidat(null)}
      />
    </div>
  )
}

function Kachel({
  kind,
  aktiv,
  onBearbeiten,
  onUmschalten,
  onLoeschen,
  onVorschau,
}: {
  kind: Child
  aktiv: boolean
  onBearbeiten: () => void
  onUmschalten: () => void
  onLoeschen: () => void
  onVorschau: () => void
}) {
  const { t } = useTranslation()

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onBearbeiten}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onBearbeiten()
        }
      }}
      className={
        'relative flex min-h-28 cursor-pointer flex-col justify-between overflow-hidden ' +
        'rounded-2xl border px-4 py-3 transition-colors ' +
        (aktiv
          ? 'border-accent-500/60 bg-accent-500/10'
          : 'border-ink-700 bg-ink-900 hover:border-ink-600') +
        // Stillgelegt: sichtbar, aber erkennbar außer Betrieb.
        (kind.is_active ? '' : ' opacity-50')
      }
    >
      <div className="relative">
        <p className="text-lg font-semibold text-mist-100">
          {kind.display_name ?? kind.username}
        </p>
        <p className="mt-0.5 text-xs text-mist-600">
          {kind.age === null ? kind.username : t('children.ageLine', { age: kind.age })}
        </p>
      </div>

      <div className="relative mt-3 flex items-center justify-between gap-2">
        <span className="text-xs text-mist-600">
          {kind.is_active ? '' : t('children.disabled')}
        </span>

        <div className="flex gap-2">
          {/* „Was würde mein Kind sehen?" – das Auge steht bewusst zuerst:
              Nachsehen ist die Handlung, die man hier am häufigsten will. */}
          <RundKnopf label={t('children.preview')} onClick={onVorschau}>
            <path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6z" />
            <circle cx="12" cy="12" r="2.6" />
          </RundKnopf>
          <RundKnopf
            label={t(kind.is_active ? 'children.disable' : 'children.enable')}
            onClick={onUmschalten}
            an={kind.is_active}
          >
            <path d="M12 4v8M7.8 6.3a7 7 0 1 0 8.4 0" strokeLinecap="round" />
          </RundKnopf>
          <RundKnopf label={t('common.edit')} onClick={onBearbeiten}>
            <path d="M4 20h4L18.5 9.5a2.1 2.1 0 0 0-3-3L5 17v3Z" strokeLinejoin="round" />
          </RundKnopf>
          <RundKnopf label={t('common.delete')} onClick={onLoeschen} gefahr>
            <path
              d="M5 7h14M10 7V5h4v2M6 7l1 13h10l1-13"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </RundKnopf>
        </div>
      </div>
    </div>
  )
}

/**
 * Anlegen (`kind === null`) oder Bearbeiten.
 *
 * Beim Bearbeiten steht der Benutzername fest: Er ist die Anmeldung, und ein
 * stiller Wechsel wäre für das Kind nicht nachvollziehbar. Das Passwort lässt
 * sich dagegen jederzeit neu setzen – für Kinder gibt es kein Zurücksetzen per
 * Mail, weil es keine Adresse gibt, an die etwas gehen könnte.
 */
function KindFeld({
  kind,
  onFertig,
  onAbbrechen,
  onVorschau,
}: {
  kind: Child | null
  onFertig: () => void
  onAbbrechen: () => void
  /** Nur beim Bearbeiten - ein Konto, das es noch nicht gibt, hat keine Sicht. */
  onVorschau?: () => void
}) {
  const { t } = useTranslation()
  const { data: config } = useConfig()
  const minPasswort = config?.min_password_length ?? 4

  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState(kind?.display_name ?? '')
  const [age, setAge] = useState(kind?.age != null ? String(kind.age) : '')
  const [passwort, setPasswort] = useState('')
  const [passwortWdh, setPasswortWdh] = useState('')
  const [sprache, setSprache] = useState(kind?.language ?? 'de')
  const [trailer, setTrailer] = useState(kind?.child_trailers ?? true)
  const [fehler, setFehler] = useState<string | null>(null)
  const [meldung, setMeldung] = useState<string | null>(null)

  // Die Liste kommt vom Server, statt sie hier aufzuzählen: sonst gäbe es zwei
  // Listen, die auseinanderlaufen können.
  const rubrikenQuery = useQuery({
    queryKey: ['children', 'genres'],
    queryFn: () => api.get<string[]>('/api/children/genres'),
    staleTime: Infinity,
  })
  const alleRubriken = rubrikenQuery.data ?? []

  // Leer gespeichert heißt „alle" – dann sind auch alle Häkchen gesetzt.
  const [rubriken, setRubriken] = useState<string[] | null>(
    kind && kind.genres.length > 0 ? kind.genres : null,
  )
  const gewaehlt = rubriken ?? alleRubriken

  function umschalten(name: string) {
    const neu = gewaehlt.includes(name)
      ? gewaehlt.filter((eintrag) => eintrag !== name)
      : [...gewaehlt, name]
    setRubriken(neu)
  }

  const speichern = useMutation({
    mutationFn: async () => {
      if (kind === null) {
        return api.post<Child>('/api/children', {
          username: username.trim(),
          password: passwort,
          age: Number(age),
          display_name: displayName.trim() || null,
          genres: gewaehlt,
          child_trailers: trailer,
          language: sprache,
        })
      }
      const geaendert = await api.patch<Child>(`/api/children/${kind.id}`, {
        display_name: displayName.trim() || null,
        age: Number(age),
        genres: gewaehlt,
        child_trailers: trailer,
        language: sprache,
      })
      // Das Passwort geht über einen eigenen Aufruf – und nur, wenn wirklich
      // eines eingetippt wurde. Ein leeres Feld heißt "unverändert lassen".
      if (passwort) {
        await api.post<Child>(`/api/children/${kind.id}/password`, { password: passwort })
      }
      return geaendert
    },
    onSuccess: () => {
      setFehler(null)
      setPasswort('')
      setPasswortWdh('')
      setMeldung(kind === null ? null : t('children.saved'))
      onFertig()
    },
    onError: (error) => {
      setMeldung(null)
      setFehler(error instanceof ApiError ? error.message : String(error))
    },
  })

  /**
   * Gibt es überhaupt etwas zu speichern?
   *
   * Ein Knopf, der immer klickbar ist, sagt nichts – und ein Klick darauf
   * schickt eine Änderung, die keine ist. Beim Anlegen ist er dagegen immer
   * aktiv: Dort ist alles neu.
   *
   * Die Rubriken brauchen einen Sonderfall: Leer gespeichert heißt „alle", die
   * Häkchen stehen dann aber alle einzeln. Verglichen wird deshalb gegen genau
   * das, was auch angezeigt wird.
   */
  const rubrikenVorher =
    kind && kind.genres.length > 0 ? kind.genres : alleRubriken
  const geaendert =
    kind === null ||
    (displayName.trim() || null) !== (kind.display_name ?? null) ||
    Number(age) !== kind.age ||
    sprache !== kind.language ||
    trailer !== kind.child_trailers ||
    passwort.length > 0 ||
    gewaehlt.length !== rubrikenVorher.length ||
    gewaehlt.some((name) => !rubrikenVorher.includes(name))

  function absenden(event: FormEvent) {
    event.preventDefault()
    setFehler(null)
    setMeldung(null)

    const alter = Number(age)
    if (!Number.isInteger(alter) || alter < 0 || alter > MAX_ALTER) {
      setFehler(t('children.ageInvalid'))
      return
    }
    // Ohne eine einzige Rubrik sähe das Kind gar nichts – das ist nie
    // gemeint, also lieber hier abfangen als eine leere App ausliefern.
    if (gewaehlt.length === 0) {
      setFehler(t('children.genresEmpty'))
      return
    }
    // Beim Anlegen ist das Passwort Pflicht, beim Bearbeiten heißt leer
    // "unverändert" - zu kurz ist es in beiden Fällen ein Fehler.
    if ((kind === null || passwort) && passwort.length < minPasswort) {
      setFehler(t('children.passwordShort', { min: minPasswort }))
      return
    }
    // Das Passwort vergibt das Elternteil für jemand anderen - ein Vertipper
    // fiele erst auf, wenn das Kind sich nicht anmelden kann und niemand weiß,
    // woran es liegt. Deshalb zweimal eingeben.
    if (passwort && passwort !== passwortWdh) {
      setFehler(t('children.passwordMismatch'))
      return
    }
    speichern.mutate()
  }

  return (
    <Card className="flex flex-col gap-4">
      <h3 className="text-base font-semibold">
        {kind === null
          ? t('children.formNew')
          : t('children.formEdit', { name: kind.display_name ?? kind.username })}
      </h3>

      {fehler && <ErrorBanner message={fehler} />}
      {meldung && !fehler && (
        <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {meldung}
        </p>
      )}

      <form className="flex flex-col gap-4" onSubmit={absenden}>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {kind === null ? (
            <Field
              label={t('children.username')}
              hint={t('children.usernameHint')}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="off"
              required
            />
          ) : (
            <Field
              label={t('children.username')}
              hint={t('children.usernameFixed')}
              value={kind.username}
              readOnly
              disabled
            />
          )}

          <Field
            label={t('children.displayName')}
            hint={t('children.displayNameHint')}
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            autoComplete="off"
          />

          <Field
            label={t('children.age')}
            hint={t('children.ageHint')}
            type="number"
            min={0}
            max={MAX_ALTER}
            value={age}
            onChange={(event) => setAge(event.target.value)}
            required
          />

          {/* Ein Kind stellt seine Sprache nicht selbst um - in der
              Kinderansicht gibt es dafür bewusst keinen Schalter. Also muss
              sie hier stimmen. */}
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-mist-300">
              {t('children.language')}
            </span>
            <select
              value={sprache}
              onChange={(event) => setSprache(event.target.value)}
              className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
            >
              <option value="de">Deutsch</option>
              <option value="en">English</option>
            </select>
            <span className="text-xs text-mist-600">{t('children.languageHint')}</span>
          </label>
        </div>

        {/* Eigene Zeile: Nebeneinander gehören sie zusammen, und im Raster
            oben wäre eines von beiden in die nächste Zeile gerutscht. */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field
            label={kind === null ? t('children.password') : t('children.newPassword')}
            hint={
              kind === null
                ? t('children.passwordHint', { min: minPasswort })
                : t('children.newPasswordHint')
            }
            type="password"
            value={passwort}
            onChange={(event) => setPasswort(event.target.value)}
            autoComplete="new-password"
            required={kind === null}
          />

          <Field
            label={t('children.passwordRepeat')}
            hint={t('children.passwordRepeatHint')}
            type="password"
            value={passwortWdh}
            onChange={(event) => setPasswortWdh(event.target.value)}
            autoComplete="new-password"
            required={kind === null}
          />

        </div>

        <div className="border-t border-ink-700 pt-4">
          <label className="flex cursor-pointer items-start gap-3 text-sm text-mist-300">
            <input
              type="checkbox"
              checked={trailer}
              onChange={(event) => setTrailer(event.target.checked)}
              className="mt-0.5 h-4 w-4 accent-accent-500"
            />
            <span>
              {t('children.trailers')}
              <span className="mt-0.5 block text-xs text-mist-600">
                {t('children.trailersHint')}
              </span>
            </span>
          </label>
        </div>

        <fieldset className="flex flex-col gap-2">
          <legend className="text-sm font-medium text-mist-300">
            {t('children.genres')}
          </legend>
          <p className="text-xs text-mist-600">{t('children.genresHint')}</p>
          <div className="flex flex-wrap gap-x-6 gap-y-2 pt-1">
            {alleRubriken.map((name) => (
              <label
                key={name}
                className="flex cursor-pointer items-center gap-2 text-sm text-mist-300"
              >
                <input
                  type="checkbox"
                  checked={gewaehlt.includes(name)}
                  onChange={() => umschalten(name)}
                  className="h-4 w-4 accent-accent-500"
                />
                {t(`children.genre.${name}`)}
              </label>
            ))}
          </div>
        </fieldset>

        <div className="flex flex-wrap gap-2">
          <Button type="submit" loading={speichern.isPending} disabled={!geaendert}>
            {kind === null ? t('children.create') : t('common.save')}
          </Button>
          <Button type="button" variant="ghost" onClick={onAbbrechen}>
            {t('common.cancel')}
          </Button>
          {/* Sonst sieht ein grauer Knopf aus wie ein Fehler. */}
          {kind !== null && (
            <p className="self-center text-xs text-mist-600">
              {geaendert ? t('children.unsaved') : t('children.nothingToSave')}
            </p>
          )}

          {/* Nachsehen gehört direkt neben das Einstellen: Wer gerade Alter
              oder Rubriken geändert hat, will als Nächstes wissen, was dabei
              herauskommt. Gelb, weil es nichts speichert und nichts verwirft -
              es zeigt nur. */}
          {kind !== null && onVorschau && (
            <button
              type="button"
              onClick={onVorschau}
              className="ml-auto flex items-center gap-2 rounded-full bg-warn-500 px-4 py-2 text-sm font-semibold text-ink-950 transition-transform hover:brightness-110 active:scale-95"
            >
              <svg
                viewBox="0 0 24 24"
                className="h-4 w-4"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6z" />
                <circle cx="12" cy="12" r="2.6" />
              </svg>
              {t('children.previewFor', {
                name: kind.display_name ?? kind.username,
              })}
            </button>
          )}
        </div>
      </form>
    </Card>
  )
}
