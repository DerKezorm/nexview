/**
 * Von einer laufenden Seerr-Installation übernehmen - während der Einrichtung.
 *
 * ⚠️ **Warum hier und nicht in den Einstellungen.** In einer laufenden
 * Installation bringt ein Seerr-Umzug wenig: Konten holt Nexview längst aus dem
 * Medienserver, die Anfragehistorie ist ein Archiv (die Verfügbarkeit sagt die
 * Bibliothek, das Kontingent zählt nur den laufenden Zeitraum), und der
 * Speicher bleibt ohnehin Hausbestand. Was wirklich Arbeit spart, spart sie
 * genau hier: Seerrs Einstellungen bringen Radarr, Sonarr, den Mailserver
 * samt Passwort und Region und Sprache mit.
 *
 * ⚠️ **Jeder Schritt lässt sich überspringen.** Wer Radarr bewusst anders
 * einstellen will, überspringt den Schritt - und bekommt am Ende nur das, was
 * er ausgewählt hat. Ein Umzug, der alles oder nichts kann, wird zu „nichts".
 *
 * ⚠️ **Zwei Dinge, die Seerr nicht hergibt:** das Plex-Token (es hängt dort am
 * Konto und ist in der Schnittstelle ausgeblendet) und einen TMDB-Schlüssel
 * (den hat Seerr gar nicht einstellbar). Beide fragt dieser Assistent selbst.
 *
 * ⚠️ **Der Schlüssel wird nicht gespeichert.** Seerrs API-Schlüssel handelt
 * als Administrator und kann sich zusätzlich als jedes Konto ausgeben. Er lebt
 * für die Dauer dieses Assistenten.
 */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import { Symbol } from '../../components/Symbol'
import { Button, Field } from '../../components/ui'
import type { Vorschau, Wahl } from './seerr-umzug-typen'
import { benutzernameAus, vorgabeFuer } from './seerr-umzug-typen'

type Pruefung = {
  version: string
  geprueft: boolean
  hinweis: string | null
  commit: string | null
}

/**
 * Die Schritte in ihrer Reihenfolge.
 *
 * ⚠️ **Der Medienserver steht vor den Benutzern, und das ist keine Kosmetik.**
 * Eine Plex-Verknüpfung am Konto nützt nichts, solange Nexview den Server
 * nicht kennt: Beim Anmelden fragt der Server nach Anbieter und Kennung, und
 * ohne Verbindung kommt der Weg gar nicht zustande. Wer die Konten zuerst
 * holt, baut Verknüpfungen ins Leere.
 */
const SCHRITTE = [
  'verbinden',
  'medienserver',
  'dienste',
  'tmdb',
  'mail',
  'kanaele',
  'adresse',
  'allgemein',
  'sperrliste',
  'benutzer',
  'fertig',
] as const
type Schritt = (typeof SCHRITTE)[number]

/** Welcher Schritt welchen Bereich der Übernahme anhakt. */
const BEREICH_JE_SCHRITT: Partial<Record<Schritt, string>> = {
  medienserver: 'medienserver',
  dienste: 'dienste',
  mail: 'mail',
  kanaele: 'kanaele',
  allgemein: 'allgemein',
  sperrliste: 'sperrliste',
}

function meldung(fehler: unknown, rueckfall: string): string {
  const roh = (fehler as { message?: string })?.message
  return roh && roh.trim() !== '' ? roh : rueckfall
}

export function SeerrStep({ onZurueck }: { onZurueck: () => void }) {
  const { t } = useTranslation()
  const [schritt, setSchritt] = useState<Schritt>('verbinden')
  const [url, setUrl] = useState('')
  const [schluessel, setSchluessel] = useState('')
  const [tmdb, setTmdb] = useState('')
  const [eigeneAdresse, setEigeneAdresse] = useState('')
  const [vorschau, setVorschau] = useState<Vorschau | null>(null)
  const [wahlen, setWahlen] = useState<Record<number, Wahl>>({})
  const [besitzer, setBesitzer] = useState<number | null>(null)
  // ⚠️ **Der Besitzer braucht ein Kennwort, auch wenn drüben alles über Plex
  // lief.** Nexviews erstes Konto entsteht über `/api/setup/admin`, und das
  // verlangt Benutzername, Adresse und Kennwort - eine Anmeldung über den
  // Medienserver gibt es erst, wenn eine Verbindung steht, und die kann in
  // diesem Assistenten noch nicht entstehen. Wer hier kein Kennwort vergibt,
  // hätte am Ende eine Installation, in die niemand hineinkommt.
  const [zugang, setZugang] = useState({
    benutzername: '',
    email: '',
    kennwort: '',
    kennwort2: '',
  })
  // ⚠️ Nichts ist vorausgewählt. Jeder Bereich kostet einen bewussten Klick.
  const [bereiche, setBereiche] = useState<Set<string>>(new Set())

  // Die Mindestlänge sagt der Server. `/api/config` setzt eine Anmeldung
  // voraus, die es hier noch nicht gibt - der Setup-Status kommt ohne aus.
  const statusQuery = useQuery({
    queryKey: ['setup-status'],
    queryFn: () => api.get<{ min_password_length: number }>('/api/setup/status', { auth: false }),
  })
  const minKennwort = statusQuery.data?.min_password_length ?? 4

  const pruefen = useMutation<Pruefung>({
    mutationFn: () =>
      api.post<Pruefung>('/api/setup/seerr/pruefen', { url, api_key: schluessel }),
  })

  const holen = useMutation<Vorschau>({
    mutationFn: () =>
      api.post<Vorschau>('/api/setup/seerr/vorschau', { url, api_key: schluessel }),
    onSuccess: (daten) => {
      setVorschau(daten)
      setSchritt('medienserver')
    },
  })

  const index = SCHRITTE.indexOf(schritt)
  const bereit = url.trim() !== '' && schluessel.trim() !== ''

  function weiter() {
    if (schritt === 'verbinden') {
      holen.mutate()
      return
    }
    const naechster = SCHRITTE[index + 1]
    if (naechster) setSchritt(naechster)
  }

  function zurueck() {
    if (index === 0) return onZurueck()
    setSchritt(SCHRITTE[index - 1])
  }

  /**
   * Besitzer wählen heißt: Vorschläge in die Felder, nicht Werte festnageln.
   *
   * ⚠️ Der Anzeigename aus Seerr taugt nicht als Benutzername - dort stehen
   * Leerzeichen und Umlaute, Nexview lässt sie nicht zu. `benutzernameAus`
   * macht einen Vorschlag daraus und gibt lieber nichts zurück als etwas, das
   * der Server am Ende ablehnt.
   */
  function besitzerWaehlen(seerrId: number | null) {
    setBesitzer(seerrId)
    const zeile = vorschau?.konten.find((k) => k.seerr_id === seerrId)
    setZugang((vorher) => ({
      ...vorher,
      benutzername: benutzernameAus(zeile?.anzeigename ?? ''),
      email: zeile?.email ?? '',
    }))
  }

  const zugangBereit =
    zugang.benutzername.trim().length >= 3 &&
    zugang.email.trim() !== '' &&
    zugang.kennwort.length >= minKennwort &&
    zugang.kennwort === zugang.kennwort2

  function schalten(kennung: string, an: boolean) {
    const neu = new Set(bereiche)
    if (an) neu.add(kennung)
    else neu.delete(kennung)
    setBereiche(neu)
  }

  const bereich = BEREICH_JE_SCHRITT[schritt]
  const daten = vorschau?.bereiche?.find((b) => b.kennung === bereich)

  return (
    <div className="flex flex-col gap-5">
      {/* ⚠️ Die Balken stehen ganz oben, vor der Überschrift: Sie beantworten
          die Frage „wie lange noch", und die stellt man, bevor man liest. */}
      <Balken aktuell={index} />

      <div>
        <h1 className="text-2xl font-bold tracking-tight">
          {t(`setup.seerr.step.${schritt}.title`)}
        </h1>
        <p className="mt-1.5 text-sm leading-relaxed text-mist-500">
          {t(`setup.seerr.step.${schritt}.text`)}
        </p>
      </div>

      {schritt === 'verbinden' && (
        <Verbinden
          url={url}
          setUrl={setUrl}
          schluessel={schluessel}
          setSchluessel={setSchluessel}
          bereit={bereit}
          pruefen={pruefen}
          holenFehler={
            holen.isError ? meldung(holen.error, t('settings.seerr.previewFailed')) : null
          }
        />
      )}

      {bereich && daten && (
        <BereichsSchritt
          kennung={bereich}
          anbieter={daten.anbieter}
          zeilen={daten.zeilen}
          posten={daten.posten}
          luecken={daten.luecken}
          leer={daten.leer}
          gewaehlt={bereiche}
          schalten={schalten}
        />
      )}

      {schritt === 'adresse' && (
        <EigeneAdresse wert={eigeneAdresse} setzen={setEigeneAdresse} />
      )}

      {schritt === 'tmdb' && (
        <Tmdb wert={tmdb} setzen={setTmdb} />
      )}

      {schritt === 'benutzer' && vorschau && (
        <Benutzer
          vorschau={vorschau}
          wahlen={wahlen}
          setWahlen={setWahlen}
          besitzer={besitzer}
          setBesitzer={besitzerWaehlen}
          zugang={zugang}
          setZugang={setZugang}
          minKennwort={minKennwort}
        />
      )}

      {schritt === 'fertig' && vorschau && (
        <Fertig
          vorschau={vorschau}
          bereiche={bereiche}
          tmdb={tmdb}
          eigeneAdresse={eigeneAdresse}
        />
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="ghost"
          onClick={zurueck}
        >
          ← {index === 0 ? t('common.cancel') : t('common.back')}
        </Button>
        {schritt !== 'fertig' && (
          <Button
            onClick={weiter}
            disabled={
              (schritt === 'verbinden' && !bereit) ||
              // ⚠️ **Ohne Besitzer geht es nicht weiter.** Einer muss die
              // Verantwortung tragen; eine Installation ohne Besitzer wäre
              // eine, die niemandem gehört.
              // ⚠️ Und ohne vollständigen Zugang auch nicht: Das Konto
              // entsteht am Ende in einem Zug, ein Tippfehler im Kennwort
              // fiele erst dort auf - nach allem anderen.
              (schritt === 'benutzer' && (besitzer === null || !zugangBereit))
            }
            loading={holen.isPending}
          >
            {schritt === 'verbinden' ? t('settings.seerr.preview') : t('common.next')}
          </Button>
        )}
        {/* ⚠️ Nicht beim Medienserver: Dort gibt es keinen Haken, den man
            weglassen könnte - der Satz versprach eine Wahl, die es nicht gibt. */}
        {bereich && bereich !== 'medienserver' && (
          <span className="text-xs text-mist-600">{t('setup.seerr.skipHint')}</span>
        )}
        {schritt === 'benutzer' && besitzer === null && (
          <span className="text-xs text-amber-200">{t('setup.seerr.ownerRequired')}</span>
        )}
        {schritt === 'benutzer' && besitzer !== null && !zugangBereit && (
          <span className="text-xs text-amber-200">{t('setup.seerr.accessRequired')}</span>
        )}
      </div>
    </div>
  )
}

type Zugang = { benutzername: string; email: string; kennwort: string; kennwort2: string }

/** Die Balkenanzeige - dieselbe Form wie im Einrichtungsassistenten. */
function Balken({ aktuell }: { aktuell: number }) {
  const { t } = useTranslation()
  return (
    <ol className="flex items-start gap-1.5" aria-label={t('settings.seerr.progress')}>
      {SCHRITTE.map((s, i) => (
        <li key={s} className="flex flex-1 flex-col gap-1.5">
          <span
            className={
              'h-1 rounded-full transition-colors ' +
              (i < aktuell ? 'bg-accent-600' : i === aktuell ? 'bg-accent-500' : 'bg-ink-700')
            }
          />
          <span
            className={
              'text-[10px] leading-tight font-medium ' +
              (i <= aktuell ? 'text-mist-300' : 'text-mist-600')
            }
          >
            {t(`setup.seerr.step.${s}.kurz`)}
          </span>
        </li>
      ))}
    </ol>
  )
}

function Hinweis({
  art,
  children,
}: {
  art: 'warn' | 'gut' | 'ruhig' | 'fehler'
  children: React.ReactNode
}) {
  const stile = {
    warn: 'border-amber-700/60 bg-amber-950/30 text-amber-200',
    gut: 'border-ok-500/40 bg-ok-500/10 text-ok-500',
    ruhig: 'border-ink-700 bg-ink-900 text-mist-400',
    fehler: 'border-accent-600/50 bg-accent-700/15 text-accent-400',
  }[art]
  return (
    <p
      role={art === 'fehler' ? 'alert' : undefined}
      className={`rounded-xl border px-4 py-3 text-sm ${stile}`}
    >
      {children}
    </p>
  )
}

function Verbinden({
  url,
  setUrl,
  schluessel,
  setSchluessel,
  bereit,
  pruefen,
  holenFehler,
}: {
  url: string
  setUrl: (v: string) => void
  schluessel: string
  setSchluessel: (v: string) => void
  bereit: boolean
  pruefen: ReturnType<typeof useMutation<Pruefung>>
  holenFehler: string | null
}) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-4">
      <Hinweis art="warn">{t('settings.seerr.stillleben')}</Hinweis>
      <Field
        label={t('settings.seerr.url')}
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://seerr.example.com"
        hint={t('settings.seerr.urlHint')}
      />
      <Field
        label={t('settings.seerr.key')}
        type="password"
        value={schluessel}
        onChange={(e) => setSchluessel(e.target.value)}
        hint={t('settings.seerr.keyHint')}
      />
      <div>
        <Button
          variant="ghost"
          disabled={!bereit}
          loading={pruefen.isPending}
          onClick={() => pruefen.mutate()}
        >
          {t('settings.seerr.check')}
        </Button>
      </div>
      {pruefen.isError && (
        <Hinweis art="fehler">
          {meldung(pruefen.error, t('settings.seerr.checkFailed'))}
        </Hinweis>
      )}
      {pruefen.isSuccess && (
        <Hinweis art={pruefen.data.geprueft ? 'gut' : 'warn'}>
          {pruefen.data.geprueft
            ? t('settings.seerr.checkOk', { version: pruefen.data.version })
            : pruefen.data.hinweis}
        </Hinweis>
      )}
      {holenFehler && <Hinweis art="fehler">{holenFehler}</Hinweis>}
    </div>
  )
}

function BereichsSchritt({
  kennung,
  anbieter,
  zeilen,
  posten,
  luecken,
  leer,
  gewaehlt,
  schalten,
}: {
  kennung: string
  anbieter: string
  zeilen: { was: string; wert: string }[]
  posten: { kennung: string; beschriftung: string; zeilen: { was: string; wert: string }[] }[]
  luecken: string[]
  leer: boolean
  gewaehlt: Set<string>
  schalten: (kennung: string, an: boolean) => void
}) {
  const { t } = useTranslation()
  // ⚠️ Der Medienserver hat **keinen** Haken, und das gilt für alle drei
  // Anbieter - der Grund ist nur je einer ein anderer. Bei Plex gibt Seerr das
  // Token nicht heraus; bei Jellyfin und Emby liegt zwar ein Schlüssel vor,
  // aber Nexview verbindet sich dort mit Benutzername und Passwort eines
  // Server-Administrators und nimmt gar keinen Schlüssel entgegen. Ein Haken
  // wäre so oder so ein Versprechen ohne Deckung. Er zeigt nur, was drüben
  // eingetragen ist.
  const nurAuskunft = kennung === 'medienserver'

  return (
    <div className="flex flex-col gap-4">
      {leer ? (
        <Hinweis art="ruhig">{t('setup.seerr.nothingHere')}</Hinweis>
      ) : (
        <>
          {zeilen.length > 0 && (
            <div className="flex flex-col gap-1 rounded-xl border border-ink-700 bg-ink-900 p-4">
              {anbieter && (
                <div className="mb-2 flex items-center gap-2">
                  <Symbol name="medienserver" className="h-5 w-5 text-accent-400" />
                  <span className="text-sm font-semibold text-mist-100">
                    {anbieter === 'plex' ? 'Plex' : anbieter === 'jellyfin' ? 'Jellyfin' : 'Emby'}
                  </span>
                </div>
              )}
              {zeilen.map((z) => (
                <div key={z.was} className="flex justify-between gap-4 text-sm">
                  <span className="text-mist-500">{z.was}</span>
                  <span className="text-right text-mist-100">{z.wert}</span>
                </div>
              ))}
            </div>
          )}

          {/* ⚠️ **Je Platz ein Haken, nicht einer für alles.** Nexview hat vier
              Plätze für Radarr und Sonarr; wer nur einen davon aus Seerr
              nehmen will, soll das können - und sehen, welche Seerr-Instanz
              auf welchem Platz landet. */}
          {posten.map((p) => (
            <label
              key={p.kennung}
              className="flex cursor-pointer items-start gap-3 rounded-xl border border-ink-700 bg-ink-900 p-4"
            >
              <input
                type="checkbox"
                checked={gewaehlt.has(p.kennung)}
                onChange={(e) => schalten(p.kennung, e.target.checked)}
                className="mt-1 h-4 w-4 shrink-0 accent-accent-500"
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-mist-100">
                  {p.beschriftung}
                </span>
                {p.zeilen.map((z) => (
                  <span key={z.was} className="mt-0.5 flex justify-between gap-4 text-xs">
                    <span className="text-mist-600">{z.was}</span>
                    <span className="truncate text-right text-mist-400">{z.wert}</span>
                  </span>
                ))}
              </span>
            </label>
          ))}

          {!nurAuskunft && posten.length === 0 && (
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                checked={gewaehlt.has(kennung)}
                onChange={(e) => schalten(kennung, e.target.checked)}
                className="mt-1 h-4 w-4 shrink-0 accent-accent-500"
              />
              <span className="text-sm text-mist-200">
                {t(`setup.seerr.take.${kennung}`)}
              </span>
            </label>
          )}
        </>
      )}

      {luecken.map((satz) => (
        <Hinweis key={satz} art="warn">
          {satz}
        </Hinweis>
      ))}
    </div>
  )
}

function EigeneAdresse({ wert, setzen }: { wert: string; setzen: (v: string) => void }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-4">
      {/* ⚠️ **Seerrs Adresse wird bewusst nicht übernommen.** Sie zeigt auf
          Seerr, nicht auf Nexview - andere Anwendung, anderer Anschluss, meist
          anderer Name. Sie zu übernehmen hieße, jede Einladungsmail auf die
          alte Anwendung zu verlinken. */}
      <Hinweis art="ruhig">{t('setup.seerr.addressWhy')}</Hinweis>
      <Field
        label={t('setup.seerr.addressLabel')}
        value={wert}
        onChange={(e) => setzen(e.target.value)}
        placeholder="https://nexview.example.com"
        hint={t('setup.seerr.addressHint')}
      />
    </div>
  )
}

function Tmdb({ wert, setzen }: { wert: string; setzen: (v: string) => void }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-4">
      {/* ⚠️ Der eine Schritt, den dieser Umzug nicht abnehmen kann: Seerr hat
          gar keinen einstellbaren TMDB-Schlüssel, er steckt dort fest im
          Programm. */}
      <Hinweis art="ruhig">{t('setup.seerr.tmdbWhy')}</Hinweis>
      <Field
        label={t('setup.seerr.tmdbLabel')}
        type="password"
        value={wert}
        onChange={(e) => setzen(e.target.value)}
        hint={t('setup.seerr.tmdbHint')}
      />
    </div>
  )
}

function Benutzer({
  vorschau,
  wahlen,
  setWahlen,
  besitzer,
  setBesitzer,
  zugang,
  setZugang,
  minKennwort,
}: {
  vorschau: Vorschau
  wahlen: Record<number, Wahl>
  setWahlen: (w: Record<number, Wahl>) => void
  besitzer: number | null
  setBesitzer: (id: number | null) => void
  zugang: { benutzername: string; email: string; kennwort: string; kennwort2: string }
  setZugang: (f: (vorher: Zugang) => Zugang) => void
  minKennwort: number
}) {
  const { t } = useTranslation()

  function setzen(seerrId: number, an: boolean) {
    setWahlen({ ...wahlen, [seerrId]: an ? { was: 'neu' } : { was: 'ueberspringen' } })
  }

  const gewaehlt = vorschau.konten.filter(
    (k) => (wahlen[k.seerr_id] ?? vorgabeFuer(k)).was === 'neu',
  ).length

  return (
    <div className="flex flex-col gap-4">
      <Hinweis art="warn">{t('setup.seerr.usersWarning')}</Hinweis>

      {/* ⚠️ **Was mitkommt, steht vor der Liste und nicht darunter.** Wer erst
          nach dem Anhaken liest, dass Passwörter nicht mitkommen, hat schon
          angehakt. */}
      <div className="rounded-xl border border-ink-700 bg-ink-900 p-4 text-sm">
        <p className="text-mist-300">{t('setup.seerr.usersTakesTitle')}</p>
        <ul className="mt-1.5 flex flex-col gap-1 text-xs text-mist-500">
          <li>{t('setup.seerr.takesEmail')}</li>
          <li>{t('setup.seerr.takesName')}</li>
          <li>{t('setup.seerr.takesQuota')}</li>
        </ul>
        <p className="mt-3 text-mist-300">{t('setup.seerr.usersLeavesTitle')}</p>
        <ul className="mt-1.5 flex flex-col gap-1 text-xs text-mist-500">
          <li>{t('setup.seerr.leavesPassword')}</li>
          <li>{t('setup.seerr.leavesRole')}</li>
          <li>{t('setup.seerr.leavesRequests')}</li>
          <li>{t('setup.seerr.leavesAvatar')}</li>
        </ul>
      </div>

      <div className="flex flex-col gap-2">
        {vorschau.konten.map((zeile) => {
          const an = (wahlen[zeile.seerr_id] ?? vorgabeFuer(zeile)).was === 'neu'
          const istBesitzer = besitzer === zeile.seerr_id
          const offen = vorschau.anfragen.filter(
            (a) =>
              a.besteller_seerr_id === zeile.seerr_id &&
              a.ziel_status === 'pending_approval',
          ).length
          return (
            <div
              key={zeile.seerr_id}
              className="flex flex-wrap items-start gap-3 border-b border-ink-800 py-2 last:border-b-0"
            >
              <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  checked={an || istBesitzer}
                  disabled={istBesitzer}
                  onChange={(e) => setzen(zeile.seerr_id, e.target.checked)}
                  className="mt-1 h-4 w-4 shrink-0 accent-accent-500"
                />
                {/* ⚠️ Das Bild kommt **nicht** mit, es hilft nur beim
                    Wiedererkennen. Seerr liefert eine Adresse (plex.tv oder
                    Gravatar), Nexview kennt nur hochgeladene Dateien - und ein
                    Abruf bei Gravatar verriete einem Dritten, dass jemand
                    dieses Konto ansieht. */}
                {zeile.bild ? (
                  <img
                    src={zeile.bild}
                    alt=""
                    className="mt-0.5 h-9 w-9 shrink-0 rounded-full border border-ink-700 object-cover"
                    referrerPolicy="no-referrer"
                  />
                ) : (
                  <span className="mt-0.5 h-9 w-9 shrink-0 rounded-full border border-ink-700 bg-ink-800" />
                )}
                <span className="min-w-0">
                  <span className="block text-sm text-mist-100">{zeile.anzeigename}</span>
                  {zeile.email && (
                    <span className="block text-xs text-mist-600">{zeile.email}</span>
                  )}
                  <span className="block text-xs text-mist-600">
                    {t('setup.seerr.wasAndBrings', {
                      rolle: zeile.rolle_seerr,
                      anmeldung: zeile.herkunft,
                    })}
                  </span>
                  {offen > 0 && (
                    <span className="mt-1 block text-xs text-amber-200">
                      {t('setup.seerr.openRequests', { count: offen })}
                    </span>
                  )}
                  {zeile.kontingent_hinweise.map((satz) => (
                    <span key={satz} className="mt-1 block text-xs text-amber-200">
                      {satz}
                    </span>
                  ))}
                </span>
              </label>

              {/* ⚠️ Genau einer wird Besitzer, und ohne ihn geht es nicht
                  weiter. Eine Installation ohne Besitzer wäre eine, die
                  niemandem gehört. */}
              <label className="flex shrink-0 cursor-pointer items-center gap-2 text-xs text-mist-500">
                <input
                  type="radio"
                  name="besitzer"
                  checked={istBesitzer}
                  onChange={() => setBesitzer(zeile.seerr_id)}
                  className="h-4 w-4 accent-accent-500"
                />
                {t('setup.seerr.owner')}
              </label>
            </div>
          )
        })}
      </div>

      {/* ⚠️ **Erst nach der Wahl, und nicht davor.** Ohne Besitzer wäre das
          ein Formular ohne Bezug; danach ist klar, für wen es gilt - der Name
          steht in der Überschrift. */}
      {besitzer !== null && (
        <div className="flex flex-col gap-3 rounded-xl border border-accent-500/40 bg-ink-900 p-4">
          <div>
            <p className="text-sm font-semibold text-mist-100">
              {t('setup.seerr.accessTitle', {
                name:
                  vorschau.konten.find((k) => k.seerr_id === besitzer)?.anzeigename ?? '',
              })}
            </p>
            <p className="mt-1 text-xs leading-relaxed text-mist-500">
              {t('setup.seerr.accessWhy')}
            </p>
          </div>

          <Field
            label={t('setup.usernameLabel')}
            hint={t('setup.usernameHint')}
            value={zugang.benutzername}
            onChange={(e) => setZugang((v) => ({ ...v, benutzername: e.target.value }))}
            autoComplete="username"
          />
          <Field
            label={t('setup.emailLabel')}
            hint={t('setup.emailHint')}
            type="email"
            value={zugang.email}
            onChange={(e) => setZugang((v) => ({ ...v, email: e.target.value }))}
            autoComplete="email"
          />
          <Field
            label={t('setup.passwordLabel')}
            hint={t('setup.passwordHint', { count: minKennwort })}
            type="password"
            value={zugang.kennwort}
            onChange={(e) => setZugang((v) => ({ ...v, kennwort: e.target.value }))}
            autoComplete="new-password"
          />
          <Field
            label={t('setup.passwordRepeatLabel')}
            type="password"
            value={zugang.kennwort2}
            onChange={(e) => setZugang((v) => ({ ...v, kennwort2: e.target.value }))}
            autoComplete="new-password"
          />
          {zugang.kennwort2 !== '' && zugang.kennwort !== zugang.kennwort2 && (
            <span className="text-xs text-amber-200">{t('setup.mismatch')}</span>
          )}
        </div>
      )}

      <p className="text-xs text-mist-600">
        {t('setup.seerr.usersNote', { count: gewaehlt })}
      </p>
    </div>
  )
}

function Fertig({
  vorschau,
  bereiche,
  tmdb,
  eigeneAdresse,
}: {
  vorschau: Vorschau
  bereiche: Set<string>
  tmdb: string
  eigeneAdresse: string
}) {
  const { t } = useTranslation()
  const posten = (vorschau.bereiche ?? []).flatMap((b) => b.posten)

  const zeilen: { was: string; an: boolean }[] = [
    ...posten.map((p) => ({ was: p.beschriftung, an: bereiche.has(p.kennung) })),
    { was: t('setup.seerr.step.mail.kurz'), an: bereiche.has('mail') },
    { was: t('setup.seerr.step.sperrliste.kurz'), an: bereiche.has('sperrliste') },
    { was: 'TMDB', an: tmdb.trim() !== '' },
    { was: t('setup.seerr.addressLabel'), an: eigeneAdresse.trim() !== '' },
  ]

  return (
    <div className="flex flex-col gap-4">
      <Hinweis art="warn">{t('setup.seerr.dryRun')}</Hinweis>

      <div className="flex flex-col gap-1 rounded-xl border border-ink-700 bg-ink-900 p-4 text-sm">
        {zeilen.map((z) => (
          <div key={z.was} className="flex justify-between gap-4">
            <span className="text-mist-500">{z.was}</span>
            <span className={z.an ? 'text-ok-500' : 'text-mist-600'}>
              {z.an ? t('setup.seerr.willTake') : t('setup.seerr.willSkip')}
            </span>
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-xs uppercase tracking-wide text-mist-600">
          {t('settings.seerr.notComingTitle')}
        </span>
        <ul className="flex flex-col gap-2 text-sm text-mist-400">
          {(vorschau.nie_dabei ?? []).map((satz) => (
            <li key={satz}>{satz}</li>
          ))}
        </ul>
      </div>
    </div>
  )
}
