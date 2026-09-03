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
 * ⚠️ **Jeder Bereich lässt sich überspringen.** Wer Radarr bewusst anders
 * einstellen will, überspringt den Schritt - und bekommt am Ende nur das, was
 * er ausgewählt hat. Ein Umzug, der alles oder nichts kann, wird zu „nichts".
 *
 * ⚠️ **Geschrieben wird an genau einer Stelle, und dort alles auf einmal.**
 * Bis zum Schritt „Schreiben" ist jeder Klick folgenlos. Der eine Aufruf
 * danach (`/api/setup/seerr/abschliessen`) legt Einstellungen, Besitzer und
 * Konten in einer Transaktion an; scheitert er, steht nichts in der
 * Datenbank, und der Assistent bleibt, wo er ist. Seine Antwort ist die
 * Sitzung des Besitzers - erst damit lässt sich der Medienserver verbinden,
 * deshalb kommt der als letzter Schritt und nicht als erster.
 *
 * ⚠️ **Zwei Dinge, die Seerr nicht hergibt:** das Plex-Token (es hängt dort am
 * Konto und ist in der Schnittstelle ausgeblendet) und einen TMDB-Schlüssel
 * (den hat Seerr gar nicht einstellbar). Beide fragt dieser Assistent selbst.
 *
 * ⚠️ **Der Schlüssel wird nicht gespeichert.** Seerrs API-Schlüssel handelt
 * als Administrator und kann sich zusätzlich als jedes Konto ausgeben. Er lebt
 * für die Dauer dieses Assistenten.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, api } from '../../api/client'
import type { MediaServerOption, User } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { MediaServerPasswordForm } from '../../components/MediaServerPasswordForm'
import { MediaServerPrompt } from '../../components/MediaServerPrompt'
import { Symbol } from '../../components/Symbol'
import { Button, Field, Spinner } from '../../components/ui'
import { useConfig } from '../../hooks/useConfig'
import { providerName } from '../../lib/mediaserver'
import { useMediaServerChallenge } from '../../lib/useMediaServerChallenge'
import type { Abschluss, Bericht, Rolle, Vorschau, Wahl } from './seerr-umzug-typen'
import { abschlussKonten, benutzernameAus, vorgabeFuer } from './seerr-umzug-typen'

type Pruefung = {
  version: string
  geprueft: boolean
  hinweis: string | null
  commit: string | null
}

/**
 * Die Schritte in ihrer Reihenfolge.
 *
 * ⚠️ **Der Medienserver steht hinter dem Schreiben, und das ist keine
 * Kosmetik.** Verbinden heißt bei Plex ein Code-Verfahren bei plex.tv und bei
 * Jellyfin und Emby das Passwort eines Server-Administrators - beides läuft
 * über Adressen, die einen angemeldeten Administrator verlangen. Den gibt es
 * erst, wenn der Besitzer angelegt ist, also nach „Schreiben". Die
 * Verknüpfungen der Konten (Plex-Kennung aus Seerr) sind davon unabhängig:
 * Sie sind Zeilen, die beim ersten Anmelden gelesen werden, und bis dahin
 * steht der Server.
 */
const SCHRITTE = [
  'verbinden',
  'dienste',
  'tmdb',
  'mail',
  'kanaele',
  'adresse',
  'allgemein',
  'sperrliste',
  'benutzer',
  'schreiben',
  'medienserver',
  'fertig',
] as const
type Schritt = (typeof SCHRITTE)[number]

/** Welcher Schritt welchen Bereich der Übernahme anhakt. */
const BEREICH_JE_SCHRITT: Partial<Record<Schritt, string>> = {
  dienste: 'dienste',
  mail: 'mail',
  kanaele: 'kanaele',
  allgemein: 'allgemein',
  sperrliste: 'sperrliste',
}

/** Ab hier gibt es kein Zurück mehr: Die Einrichtung ist abgeschlossen. */
const NACH_DEM_SCHREIBEN: ReadonlySet<Schritt> = new Set(['medienserver', 'fertig'])

type Verbunden = { name: string; warnung: string | null }

function meldung(fehler: unknown, rueckfall: string): string {
  const roh = (fehler as { message?: string })?.message
  return roh && roh.trim() !== '' ? roh : rueckfall
}

export function SeerrStep({ onZurueck }: { onZurueck: () => void }) {
  const { t, i18n } = useTranslation()
  const { loginWithTokens, finishSetup } = useAuth()
  const [schritt, setSchritt] = useState<Schritt>('verbinden')
  const [url, setUrl] = useState('')
  const [schluessel, setSchluessel] = useState('')
  const [tmdb, setTmdb] = useState('')
  const [eigeneAdresse, setEigeneAdresse] = useState('')
  const [vorschau, setVorschau] = useState<Vorschau | null>(null)
  const [wahlen, setWahlen] = useState<Record<number, Wahl>>({})
  const [rollen, setRollen] = useState<Record<number, Rolle>>({})
  const [besitzer, setBesitzer] = useState<number | null>(null)
  // ⚠️ **Der Besitzer braucht ein Kennwort, auch wenn drüben alles über Plex
  // lief.** Nexviews erstes Konto entsteht mit denselben Feldern wie über
  // `/api/setup/admin`: Benutzername, Adresse und Kennwort. Die Anmeldung
  // über den Medienserver gibt es erst, wenn eine Verbindung steht - und die
  // entsteht erst nach dem Schreiben. Wer hier kein Kennwort vergibt, hätte
  // dazwischen eine Installation, in die niemand hineinkommt.
  const [zugang, setZugang] = useState({
    benutzername: '',
    email: '',
    kennwort: '',
    kennwort2: '',
  })
  // ⚠️ Nichts ist vorausgewählt. Jeder Bereich kostet einen bewussten Klick.
  const [bereiche, setBereiche] = useState<Set<string>>(new Set())
  const [bericht, setBericht] = useState<Bericht | null>(null)
  const [medienserver, setMedienserver] = useState<Verbunden | null>(null)

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
      setSchritt('dienste')
    },
  })

  /**
   * Der eine Aufruf, der schreibt.
   *
   * ⚠️ **Von hier gehen Namen und Nummern, keine Werte.** Bereiche als
   * Kennungen, Konten als Seerr-Nummern samt gewählter Rolle, dazu das, was
   * der Betreiber selbst getippt hat. Alles andere (Passwörter, Schlüssel,
   * Kontingente) holt der Server ein zweites Mal bei Seerr - nichts davon
   * hat je im Browser gelegen.
   */
  const schreiben = useMutation<Abschluss>({
    mutationFn: () => {
      if (vorschau === null || besitzer === null) throw new Error('unreachable')
      return api.post<Abschluss>(
        '/api/setup/seerr/abschliessen',
        {
          url,
          api_key: schluessel,
          bereiche: [...bereiche],
          tmdb_api_key: tmdb.trim(),
          public_url: eigeneAdresse.trim(),
          besitzer: {
            seerr_id: besitzer,
            username: zugang.benutzername.trim(),
            password: zugang.kennwort,
            email: zugang.email.trim(),
            language: i18n.language,
          },
          konten: abschlussKonten(vorschau.konten, wahlen, rollen, besitzer),
        },
        { auth: false },
      )
    },
    onSuccess: async (daten) => {
      // Ab jetzt gibt es einen Besitzer, und das hier ist seine Sitzung. Der
      // Seerr-Schlüssel wird nicht mehr gebraucht - er soll nicht länger im
      // Zustand liegen als nötig.
      setSchluessel('')
      setBericht(daten.bericht)
      await loginWithTokens({
        access_token: daten.access_token,
        token_type: daten.token_type,
        expires_in: daten.expires_in,
      })
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
    if (schritt === 'schreiben') {
      schreiben.mutate()
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
  const serverAusSeerr = vorschau?.bereiche?.find((b) => b.kennung === 'medienserver')
  const eigeneKnoepfe = NACH_DEM_SCHREIBEN.has(schritt)

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
          rollen={rollen}
          setRollen={setRollen}
          besitzer={besitzer}
          setBesitzer={besitzerWaehlen}
          zugang={zugang}
          setZugang={setZugang}
          minKennwort={minKennwort}
        />
      )}

      {schritt === 'schreiben' && vorschau && besitzer !== null && (
        <Schreiben
          vorschau={vorschau}
          bereiche={bereiche}
          tmdb={tmdb}
          eigeneAdresse={eigeneAdresse}
          besitzername={zugang.benutzername}
          konten={abschlussKonten(vorschau.konten, wahlen, rollen, besitzer).length}
          fehler={
            schreiben.isError ? meldung(schreiben.error, t('setup.seerr.writeFailed')) : null
          }
        />
      )}

      {schritt === 'medienserver' && (
        <MedienserverVerbinden
          verbindung={serverAusSeerr?.verbindung ?? null}
          zeilen={serverAusSeerr?.zeilen ?? []}
          onFertig={(ergebnis) => {
            setMedienserver(ergebnis)
            setSchritt('fertig')
          }}
        />
      )}

      {schritt === 'fertig' && bericht && (
        <Fertig bericht={bericht} medienserver={medienserver} onFertig={finishSetup} />
      )}

      {!eigeneKnoepfe && (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="ghost"
            onClick={zurueck}
            disabled={schreiben.isPending}
          >
            ← {index === 0 ? t('common.cancel') : t('common.back')}
          </Button>
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
            loading={holen.isPending || schreiben.isPending}
          >
            {schritt === 'verbinden'
              ? t('settings.seerr.preview')
              : schritt === 'schreiben'
                ? t('setup.seerr.write')
                : t('common.next')}
          </Button>
          {bereich && (
            <span className="text-xs text-mist-600">{t('setup.seerr.skipHint')}</span>
          )}
          {schritt === 'benutzer' && besitzer === null && (
            <span className="text-xs text-amber-200">{t('setup.seerr.ownerRequired')}</span>
          )}
          {schritt === 'benutzer' && besitzer !== null && !zugangBereit && (
            <span className="text-xs text-amber-200">{t('setup.seerr.accessRequired')}</span>
          )}
        </div>
      )}
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

/*
 * ⚠️ Hier stand einmal der Hinweis "Leg Seerr vorher still". Er galt der
 * Anfragehistorie: Wer waehrend des Umzugs drueben anfragte, dessen Anfrage
 * kam nicht mit. Anfragen werden nicht mehr uebernommen, damit ist der
 * Grund weg - und ein Hinweis ohne Grund kostet nur Aufmerksamkeit.
 */
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
  zeilen,
  posten,
  luecken,
  leer,
  gewaehlt,
  schalten,
}: {
  kennung: string
  zeilen: { was: string; wert: string }[]
  posten: { kennung: string; beschriftung: string; zeilen: { was: string; wert: string }[] }[]
  luecken: string[]
  leer: boolean
  gewaehlt: Set<string>
  schalten: (kennung: string, an: boolean) => void
}) {
  const { t } = useTranslation()

  return (
    <div className="flex flex-col gap-4">
      {leer ? (
        <Hinweis art="ruhig">{t('setup.seerr.nothingHere')}</Hinweis>
      ) : (
        <>
          {zeilen.length > 0 && (
            <div className="flex flex-col gap-1 rounded-xl border border-ink-700 bg-ink-900 p-4">
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

          {posten.length === 0 && (
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
          Programm. Geprüft wird er beim Schreiben, vor allem anderen. */}
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

/**
 * Das Profilbild aus Seerr, und wenn es nicht lädt, die Initiale.
 *
 * ⚠️ **Ein leerer Kreis sieht nach Fehler aus, eine Initiale nach Absicht.**
 * Seerrs Bildadressen liegen bei plex.tv oder bei Gravatar (gemessen, beide
 * als volle Adresse). Ob der Browser des Betreibers sie bekommt, entscheidet
 * dessen Netz und Nexviews Inhaltsrichtlinie - deshalb der Rückfall, statt zu
 * raten. Was beim Schreiben daraus wird, steht am Abschluss: Dort holt der
 * Server das Bild selbst und legt es als Datei ab.
 */
function Bild({ adresse, name }: { adresse: string | null; name: string }) {
  const [kaputt, setKaputt] = useState(false)
  if (adresse && !kaputt) {
    return (
      <img
        src={adresse}
        alt=""
        onError={() => setKaputt(true)}
        className="mt-0.5 h-9 w-9 shrink-0 rounded-full border border-ink-700 object-cover"
        referrerPolicy="no-referrer"
      />
    )
  }
  return (
    <span
      aria-hidden="true"
      className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-ink-700 bg-ink-800 text-sm font-semibold text-mist-400"
    >
      {(name.trim()[0] ?? '?').toUpperCase()}
    </span>
  )
}

const ROLLEN: Rolle[] = ['user', 'approver', 'admin']

function rollenText(t: (k: string) => string, rolle: string): string {
  return rolle === 'admin'
    ? t('adminUsers.roleAdmin')
    : rolle === 'approver'
      ? t('adminUsers.roleApprover')
      : t('adminUsers.roleUser')
}

function Benutzer({
  vorschau,
  wahlen,
  setWahlen,
  rollen,
  setRollen,
  besitzer,
  setBesitzer,
  zugang,
  setZugang,
  minKennwort,
}: {
  vorschau: Vorschau
  wahlen: Record<number, Wahl>
  setWahlen: (w: Record<number, Wahl>) => void
  rollen: Record<number, Rolle>
  setRollen: (r: Record<number, Rolle>) => void
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

  const gewaehlt = abschlussKonten(vorschau.konten, wahlen, rollen, besitzer).length

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
          <li>{t('setup.seerr.takesLink')}</li>
          <li>{t('setup.seerr.takesAvatar')}</li>
        </ul>
        <p className="mt-3 text-mist-300">{t('setup.seerr.usersLeavesTitle')}</p>
        <ul className="mt-1.5 flex flex-col gap-1 text-xs text-mist-500">
          <li>{t('setup.seerr.leavesPassword')}</li>
          <li>{t('setup.seerr.leavesRole')}</li>
          <li>{t('setup.seerr.leavesRequests')}</li>
        </ul>
      </div>

      <div className="flex flex-col gap-2">
        {vorschau.konten.map((zeile) => {
          const istBesitzer = besitzer === zeile.seerr_id
          const an = (wahlen[zeile.seerr_id] ?? vorgabeFuer(zeile)).was === 'neu'
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
                <Bild adresse={zeile.bild} name={zeile.anzeigename} />
                <span className="min-w-0">
                  <span className="block text-sm text-mist-100">{zeile.anzeigename}</span>
                  {zeile.email && (
                    <span className="block text-xs text-mist-600">{zeile.email}</span>
                  )}
                  <span className="block text-xs text-mist-600">
                    {t('setup.seerr.wasAndBrings', {
                      rolle: rollenText(t, zeile.rolle_seerr),
                      anmeldung: zeile.herkunft,
                    })}
                  </span>
                  {zeile.rolle_verlust && (
                    <span className="mt-1 block text-xs text-amber-200">{zeile.rolle_verlust}</span>
                  )}
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

              {/* ⚠️ **Die Rolle steht je Zeile zur Wahl, und vorausgewählt ist
                  Nutzer.** Bei einer frischen Installation darf ein Seerr-
                  Administrator wieder Administrator werden - aber erst, wenn
                  ein Mensch das an dieser Zeile sagt. Was drüben galt, steht
                  als Hinweis in der Zeile, nicht im Feld. */}
              {an && !istBesitzer && (
                <label className="flex shrink-0 items-center gap-2 text-xs text-mist-500">
                  <span className="sr-only">{t('setup.seerr.roleLabel')}</span>
                  <select
                    value={rollen[zeile.seerr_id] ?? 'user'}
                    onChange={(e) =>
                      setRollen({ ...rollen, [zeile.seerr_id]: e.target.value as Rolle })
                    }
                    className="rounded-xl border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-xs text-mist-100 focus:border-accent-500 focus:outline-none"
                  >
                    {ROLLEN.map((r) => (
                      <option key={r} value={r}>
                        {rollenText(t, r)}
                      </option>
                    ))}
                  </select>
                </label>
              )}

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

/**
 * Die letzte Seite vor dem Schreiben.
 *
 * ⚠️ **Die Zusammenfassung ist die Wahrheit, nicht der Haken.** Sie wird aus
 * denselben Zuständen gebaut, die gleich abgeschickt werden - wer hier
 * „übersprungen" liest, bekommt es auch übersprungen.
 */
function Schreiben({
  vorschau,
  bereiche,
  tmdb,
  eigeneAdresse,
  besitzername,
  konten,
  fehler,
}: {
  vorschau: Vorschau
  bereiche: Set<string>
  tmdb: string
  eigeneAdresse: string
  besitzername: string
  konten: number
  fehler: string | null
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
      <Hinweis art="warn">{t('setup.seerr.writeNoWayBack')}</Hinweis>

      <div className="flex flex-col gap-1 rounded-xl border border-ink-700 bg-ink-900 p-4 text-sm">
        <div className="flex justify-between gap-4">
          <span className="text-mist-500">{t('setup.seerr.owner')}</span>
          <span className="text-mist-100">{besitzername}</span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-mist-500">{t('setup.seerr.step.benutzer.kurz')}</span>
          <span className="text-mist-100">
            {t('setup.seerr.accountsToCreate', { count: konten })}
          </span>
        </div>
        {zeilen.map((z) => (
          <div key={z.was} className="flex justify-between gap-4">
            <span className="text-mist-500">{z.was}</span>
            <span className={z.an ? 'text-ok-500' : 'text-mist-600'}>
              {z.an ? t('setup.seerr.willTake') : t('setup.seerr.willSkip')}
            </span>
          </div>
        ))}
      </div>

      {fehler && <Hinweis art="fehler">{fehler}</Hinweis>}

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

type VerbindungsErgebnis = {
  user: User
  server_name: string
  server_url: string
  reachable: boolean
  warning: string | null
}

/**
 * Den Medienserver verbinden - mit der Sitzung, die es seit dem Schreiben gibt.
 *
 * ⚠️ **Dieselben Adressen wie in den Einstellungen, nichts Eigenes.** Plex
 * läuft über das Code-Verfahren bei plex.tv (`connect/start`, `poll`,
 * `select`), Jellyfin und Emby über Adresse und Admin-Zugang
 * (`connect/password`). Der Assistent bringt nur eines mit: Er weiß aus
 * Seerr, **welcher** Server gemeint war, und zeigt ihn in der Auswahl an
 * beziehungsweise füllt die Adresse vor.
 *
 * ⚠️ **Überspringbar, und das muss so sein.** Wer sein Plex-Kennwort gerade
 * nicht zur Hand hat, darf nicht in einem Assistenten festsitzen, der schon
 * alles geschrieben hat. Der Weg in den Einstellungen bleibt offen.
 */
function MedienserverVerbinden({
  verbindung,
  zeilen,
  onFertig,
}: {
  verbindung: { art: string; name: string; adresse: string; kennung: string } | null
  zeilen: { was: string; wert: string }[]
  onFertig: (ergebnis: Verbunden | null) => void
}) {
  const { t } = useTranslation()
  const { updateUser } = useAuth()
  const queryClient = useQueryClient()
  const [auswahl, setAuswahl] = useState<MediaServerOption[] | null>(null)
  const [pollToken, setPollToken] = useState<string | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)

  const art = verbindung?.art ?? ''

  function verbunden(ergebnis: VerbindungsErgebnis) {
    // Das eigene Konto wurde dabei verknüpft - sonst zeigte das Profil
    // weiterhin „nicht verbunden". Und die Konfiguration muss neu: An ihr
    // hängt, ob es den Anmeldeknopf über den Medienserver überhaupt gibt.
    updateUser(ergebnis.user)
    void queryClient.invalidateQueries({ queryKey: ['settings'] })
    void queryClient.invalidateQueries({ queryKey: ['config'] })
    onFertig({ name: ergebnis.server_name, warnung: ergebnis.warning })
  }

  const plex = useMediaServerChallenge<{
    status: string
    servers: MediaServerOption[]
    shared_hidden: number
  }>({
    startPfad: '/api/admin/mediaserver/connect/start',
    abfragePfad: '/api/admin/mediaserver/connect/poll',
    onFertig: (ergebnis) => setAuswahl(ergebnis.servers),
  })

  useEffect(() => {
    if (plex.start) setPollToken(plex.start.poll_token)
  }, [plex.start])

  const waehlen = useMutation({
    mutationFn: (machine_id: string) =>
      api.post<VerbindungsErgebnis>('/api/admin/mediaserver/connect/select', {
        poll_token: pollToken,
        machine_id,
      }),
    onSuccess: verbunden,
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  if (!verbindung || !art) {
    return (
      <div className="flex flex-col gap-4">
        <Hinweis art="ruhig">{t('setup.seerr.mediaserverNone')}</Hinweis>
        <div>
          <Button onClick={() => onFertig(null)}>{t('setup.seerr.mediaserverWithout')}</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1 rounded-xl border border-ink-700 bg-ink-900 p-4">
        <div className="mb-2 flex items-center gap-2">
          <Symbol name="medienserver" className="h-5 w-5 text-accent-400" />
          <span className="text-sm font-semibold text-mist-100">{providerName(art)}</span>
        </div>
        {zeilen.map((z) => (
          <div key={z.was} className="flex justify-between gap-4 text-sm">
            <span className="text-mist-500">{z.was}</span>
            <span className="text-right text-mist-100">{z.wert}</span>
          </div>
        ))}
      </div>

      {art === 'plex' ? (
        <>
          <p className="text-sm text-mist-500">{t('setup.seerr.mediaserverPlexIntro')}</p>
          {auswahl === null && (
            <div>
              <Button onClick={() => void plex.starten()} loading={plex.laeuft}>
                {t('setup.seerr.plexSignIn')}
              </Button>
            </div>
          )}
          {plex.start && plex.laeuft && (
            <MediaServerPrompt start={plex.start} onAbbrechen={plex.abbrechen} />
          )}
          {plex.fehler && <Hinweis art="fehler">{plex.fehler}</Hinweis>}
          {auswahl !== null && (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-mist-300">{t('mediaserver.selectServer')}</p>
              {auswahl.length === 0 && (
                <Hinweis art="warn">{t('mediaserver.noServers')}</Hinweis>
              )}
              {auswahl.map((server) => {
                const ausSeerr = verbindung.kennung !== '' && server.machine_id === verbindung.kennung
                return (
                  <button
                    key={server.machine_id}
                    type="button"
                    disabled={waehlen.isPending}
                    onClick={() => waehlen.mutate(server.machine_id)}
                    className={
                      'flex items-center justify-between gap-3 rounded-xl border p-4 text-left transition-colors hover:border-accent-600 ' +
                      (ausSeerr ? 'border-accent-500/60 bg-ink-900' : 'border-ink-700 bg-ink-900')
                    }
                  >
                    <span className="min-w-0">
                      <span className="block text-sm font-semibold text-mist-100">
                        {server.name}
                      </span>
                      <span className="block truncate text-xs text-mist-500">{server.url}</span>
                    </span>
                    {ausSeerr && (
                      <span className="shrink-0 rounded-full border border-accent-500/60 px-2 py-0.5 text-[11px] text-accent-400">
                        {t('setup.seerr.mediaserverFromSeerr')}
                      </span>
                    )}
                  </button>
                )
              })}
              {waehlen.isPending && (
                <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
                  {t('mediaserver.connectSlow')}
                </p>
              )}
              <p className="text-xs text-mist-600">{t('mediaserver.selectServerHint')}</p>
            </div>
          )}
          {fehler && <Hinweis art="fehler">{fehler}</Hinweis>}
        </>
      ) : (
        <>
          <Hinweis art="ruhig">{t('setup.seerr.mediaserverPasswordIntro')}</Hinweis>
          <MediaServerPasswordForm
            provider={art}
            initialUrl={verbindung.adresse}
            onVerbunden={verbunden}
          />
        </>
      )}

      <div>
        <button
          type="button"
          onClick={() => onFertig(null)}
          disabled={waehlen.isPending}
          className="text-sm text-mist-500 underline-offset-4 transition-colors hover:text-mist-100 hover:underline"
        >
          {t('setup.seerr.mediaserverLater')}
        </button>
      </div>
    </div>
  )
}

type Versand = { sent: boolean; error: string | null }

/**
 * Der Bericht am Ende - zum Lesen, bevor man weiterklickt.
 *
 * ⚠️ **Je Konto steht, wie die Person hereinkommt.** Das ist die eine
 * Auskunft, die der Betreiber nach dem Umzug braucht und sonst nirgends
 * findet: Plex-Konten melden sich über Plex an, alle anderen haben kein
 * Kennwort und brauchen eines - über „Kennwort vergessen" oder von ihm.
 *
 * Die Bestätigungsmail für den Besitzer wird hier nachgeholt, wie im
 * normalen Assistenten (`DoneStep`): Beim Anlegen gab es noch keinen
 * Mailserver, jetzt vielleicht schon.
 */
function Fertig({
  bericht,
  medienserver,
  onFertig,
}: {
  bericht: Bericht
  medienserver: Verbunden | null
  onFertig: () => void
}) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { data: config } = useConfig()

  const offen = user !== null && !user.email_verified
  const kannSenden = config?.mail_configured ?? false

  const senden = useMutation({
    mutationFn: () => api.post<Versand>('/api/auth/me/resend-verification'),
  })

  useEffect(() => {
    if (offen && kannSenden && senden.isIdle) senden.mutate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offen, kannSenden])

  function weg(zugang: string, mailBekannt: boolean): string {
    if (zugang === 'plex') return t('setup.seerr.wayPlex')
    if (zugang === 'jellyfin' || zugang === 'emby') {
      return t('setup.seerr.wayServer', { name: providerName(zugang) })
    }
    return mailBekannt ? t('setup.seerr.wayPassword') : t('setup.seerr.wayPasswordNoMail')
  }

  return (
    <div className="flex flex-col gap-4">
      <Hinweis art="gut">{t('setup.seerr.doneWritten')}</Hinweis>

      <div className="flex flex-col gap-1 rounded-xl border border-ink-700 bg-ink-900 p-4 text-sm">
        <div className="flex justify-between gap-4">
          <span className="text-mist-500">{t('setup.seerr.owner')}</span>
          <span className="text-mist-100">{bericht.besitzer.username}</span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-mist-500">{t('setup.seerr.doneFields')}</span>
          <span className="text-mist-100">{bericht.felder}</span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-mist-500">{t('setup.seerr.doneBlocked')}</span>
          <span className="text-mist-100">{bericht.gesperrt}</span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-mist-500">{t('setup.seerr.doneChannels')}</span>
          <span className="text-mist-100">{bericht.kanaele}</span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-mist-500">{t('setup.seerr.doneAvatars')}</span>
          <span className="text-mist-100">{bericht.bilder}</span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-mist-500">{t('setup.seerr.tmdbLabel')}</span>
          <span className={bericht.tmdb ? 'text-ok-500' : 'text-mist-600'}>
            {bericht.tmdb ? t('setup.seerr.willTake') : t('setup.seerr.willSkip')}
          </span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-mist-500">{t('setup.seerr.addressLabel')}</span>
          <span className={bericht.public_url ? 'text-ok-500' : 'text-mist-600'}>
            {bericht.public_url ? t('setup.seerr.willTake') : t('setup.seerr.willSkip')}
          </span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-mist-500">{t('setup.seerr.step.medienserver.kurz')}</span>
          <span className={medienserver ? 'text-ok-500' : 'text-mist-600'}>
            {medienserver
              ? t('setup.seerr.doneMediaserver', { name: medienserver.name })
              : t('setup.seerr.doneMediaserverSkipped')}
          </span>
        </div>
      </div>
      {medienserver?.warnung && <Hinweis art="warn">{medienserver.warnung}</Hinweis>}

      <div className="flex flex-col gap-2">
        <span className="text-xs uppercase tracking-wide text-mist-600">
          {t('setup.seerr.doneAccountsTitle', { count: bericht.konten.length })}
        </span>
        {bericht.konten.length === 0 ? (
          <p className="text-sm text-mist-500">{t('setup.seerr.doneNoAccounts')}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {bericht.konten.map((k) => (
              <li
                key={k.seerr_id}
                className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 text-sm"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-mist-100">
                    {k.username}
                    {k.anzeigename !== k.username && (
                      <span className="ml-2 text-xs text-mist-500">{k.anzeigename}</span>
                    )}
                  </span>
                  <span className="text-xs text-mist-500">{rollenText(t, k.rolle)}</span>
                </div>
                <p className="mt-1 text-xs text-mist-500">
                  {weg(k.zugang, kannSenden)}
                  {k.bild === 'nicht_geladen' && (
                    <span className="ml-1 text-mist-600">· {t('setup.seerr.noAvatar')}</span>
                  )}
                </p>
              </li>
            ))}
          </ul>
        )}
        {bericht.abgelehnt.length > 0 && (
          <>
            <span className="mt-2 text-xs uppercase tracking-wide text-mist-600">
              {t('setup.seerr.doneRejectedTitle')}
            </span>
            <ul className="flex flex-col gap-1 text-sm text-amber-200">
              {bericht.abgelehnt.map((a) => (
                <li key={a.seerr_id}>
                  {a.anzeigename}: {a.grund}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-xs uppercase tracking-wide text-mist-600">
          {t('setup.seerr.doneByHandTitle')}
        </span>
        <ul className="flex flex-col gap-2 text-sm text-mist-400">
          {bericht.kanaele > 0 && <li>{t('setup.seerr.doneChannelsByHand')}</li>}
          {!medienserver && <li>{t('setup.seerr.doneMediaserverByHand')}</li>}
          {bericht.nie_dabei.map((satz) => (
            <li key={satz}>{satz}</li>
          ))}
        </ul>
      </div>

      {offen && (
        <div className="rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3 text-sm">
          {!kannSenden ? (
            <p className="text-warn-500">{t('setup.verifyImpossible')}</p>
          ) : senden.isPending ? (
            <p className="flex items-center gap-2 text-mist-500">
              <Spinner /> {t('common.loading')}
            </p>
          ) : senden.data?.sent ? (
            <p className="text-ok-500">{t('setup.verifySent', { email: user?.email ?? '' })}</p>
          ) : (
            <p className="text-warn-500">{senden.data?.error ?? t('setup.verifyFailed')}</p>
          )}
        </div>
      )}

      <div>
        <Button onClick={onFertig}>{t('setup.seerr.toNexview')}</Button>
      </div>
    </div>
  )
}
