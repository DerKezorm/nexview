import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type {
  ChannelKind,
  ChannelLanguage,
  ChannelLevel,
  ChannelTarget,
  NtfyAuth,
  TestResult,
} from '../../api/types'
import { Button, Card, ErrorBanner, Field, Spinner } from '../../components/ui'
import { Anleitung, HilfeKnopf, hatAnleitung } from './ChannelHelp'
import emailLogo from '../../assets/email.svg'
import gotifyLogo from '../../assets/gotify.svg'
import ntfyLogo from '../../assets/ntfy.svg'
import discordLogo from '../../assets/discord.svg'
import webhookLogo from '../../assets/webhook.svg'
import appriseLogo from '../../assets/apprise.svg'
import telegramLogo from '../../assets/telegram.svg'

/**
 * Systembenachrichtigungen – die Ziele der serverseitigen Dienste.
 *
 * Serverseitig heißt: vom Administrator eingerichtet, ein geteiltes Postfach
 * für die ganze Installation. Die persönlichen Wege (Glocke, E-Mail) stellt
 * jeder in seinem Profil ein und haben mit dieser Seite nichts zu tun.
 *
 * Manche Dienste haben zwei Ebenen. Bei Gotify ist die Application schon das
 * Postfach – eine Kachel, fertig. Bei ntfy trägt die **Instanz** Adresse und
 * Anmeldung, und darin liegen beliebig viele **Topics**; erst das Topic ist das
 * Postfach. Deshalb steckt dort eine zweite Kachelreihe im Einstellungsfeld.
 *
 * Der Ablauf ist überall derselbe: eintragen → testen → den vierstelligen Code
 * aus der Testnachricht eintippen → speichern. Der Code hängt am Postfach, denn
 * nur dort kann etwas ankommen; die ntfy-Instanz darüber wird bloß auf
 * Erreichbarkeit geprüft.
 */

type Entwurf = Record<string, string>

const KANAELE: ChannelKind[] = ['ntfy', 'gotify', 'telegram', 'discord', 'webhook', 'apprise', 'email']

/** Wie der Dienst sich selbst schreibt. */
const LABELS: Record<ChannelKind, string> = {
  ntfy: 'ntfy',
  gotify: 'Gotify',
  telegram: 'Telegram',
  discord: 'Discord',
  webhook: 'Webhook',
  apprise: 'Apprise',
  email: 'E-Mail',
}

const LOGOS: Record<ChannelKind, string> = {
  ntfy: ntfyLogo,
  gotify: gotifyLogo,
  telegram: telegramLogo,
  discord: discordLogo,
  webhook: webhookLogo,
  apprise: appriseLogo,
  email: emailLogo,
}

/** Hat dieser Dienst Instanz **und** Topic? Muss zur Aufteilung im Backend passen. */
function zweiEbenen(kanal: ChannelKind): boolean {
  return kanal === 'ntfy' || kanal === 'telegram'
}

/**
 * Braucht dieser Dienst den vierstelligen Code aus der Testnachricht?
 *
 * Bei den Push-Diensten ja - nur so ist bewiesen, dass etwas ankommt. Bei
 * E-Mail nicht: Hinter einer allgemeinen Adresse sitzt womöglich niemand, der
 * ihn ablesen könnte, und ein Verteiler schickte ihn an alle weiter.
 */
function brauchtCode(kanal: ChannelKind): boolean {
  return kanal !== 'email'
}

/** Die Meldungen, die ein serverseitiges Postfach berichten kann. */
/**
 * Die Meldungen, die ein serverseitiges Postfach berichten kann.
 *
 * `key` muss zu den Gruppen im Postausgang passen (`channel_outbox.EVENTS`).
 * `standard` ist die Dringlichkeit beim Anhaken – eine wartende Freigabe darf
 * das Handy wecken, eine entschiedene Anfrage nicht.
 */
const EREIGNISSE = [
  {
    key: 'request_pending',
    gruppe: 'anfragen',
    standard: 'high',
    labelKey: 'channels.onRequestPending',
    hintKey: 'channels.onRequestPendingHint',
  },
  {
    key: 'download_complete',
    gruppe: 'anfragen',
    standard: 'normal',
    labelKey: 'channels.onDownloadComplete',
    hintKey: 'channels.onDownloadCompleteHint',
  },
  {
    key: 'request_decided',
    gruppe: 'anfragen',
    standard: 'low',
    labelKey: 'channels.onRequestDecided',
    hintKey: 'channels.onRequestDecidedHint',
  },
  {
    key: 'request_cancelled',
    gruppe: 'anfragen',
    standard: 'normal',
    labelKey: 'channels.onRequestCancelled',
    hintKey: 'channels.onRequestCancelledHint',
  },
  {
    key: 'ticket_new',
    gruppe: 'betrieb',
    standard: 'normal',
    labelKey: 'channels.onTicketNew',
    hintKey: 'channels.onTicketNewHint',
  },
  {
    key: 'feedback',
    gruppe: 'betrieb',
    standard: 'low',
    labelKey: 'channels.onFeedback',
    hintKey: 'channels.onFeedbackHint',
  },
  {
    key: 'user_imported',
    gruppe: 'betrieb',
    standard: 'normal',
    labelKey: 'channels.onUserImported',
    hintKey: 'channels.onUserImportedHint',
  },
  {
    // Abgegeben **und** entschieden unter einem Haken: Beides gehört zum
    // selben Vorgang, und wer das eine sehen will, will auch das andere.
    //
    // ⚠️ Ein Postfach hat keinen Empfänger – ein Topic liest, wer es
    // eingerichtet hat. Diese Meldungen nennen deshalb nur den Titel, nie die
    // Person und schon gar keine Kontostände. Wer persönlich Bescheid bekommen
    // soll, bekommt es über die Glocke.
    key: 'storage_release',
    gruppe: 'betrieb',
    standard: 'normal',
    labelKey: 'channels.onStorageRelease',
    hintKey: 'channels.onStorageReleaseHint',
  },
] as const

const GRUPPEN = ['anfragen', 'betrieb'] as const

const STUFEN: ChannelLevel[] = ['low', 'normal', 'high', 'urgent']

/** Welche Felder eine Ebene kennt – und wie sie leer aussehen. */
const LEER: Record<string, Entwurf> = {
  'ntfy-instanz': { name: '', url: '', auth: 'none', username: '', password: '', token: '' },
  'ntfy-topic': { name: '', topic: '', language: 'de' },
  'gotify-instanz': { name: '', url: '', token: '', language: 'de' },
  'email-instanz': { name: '', address: '', subject: '', language: 'de' },
  'telegram-instanz': { name: '', token: '', username: '' },
  'discord-instanz': { name: '', url: '', username: '', language: 'de' },
  'webhook-instanz': { name: '', url: '', token: '', language: 'de' },
  'apprise-instanz': { name: '', url: '', topic: '', language: 'de' },
  'telegram-topic': { name: '', chat_id: '', thread_id: '', silent: '', language: 'de' },
}

/**
 * Das Symbol eines Dienstes, einfarbig in der Farbe der Umgebung.
 *
 * Als **Maske**, nicht als Bild: Ein `<img>` wäre ein eigenes Dokument und
 * brächte seine eigenen Farben mit. So nimmt das Symbol `currentColor` an -
 * rot im gewählten Reiter, blass im Hintergrund einer Kachel, und in hellem
 * wie dunklem Thema richtig.
 *
 * Eine SVG-Maske wird nach Helligkeit ausgewertet; die Dateien unter
 * `assets/` sind deshalb weiß gezeichnet.
 */
function DienstSymbol({ kanal, className = '' }: { kanal: ChannelKind; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={'inline-block shrink-0 select-none ' + className}
      style={{
        backgroundColor: 'currentColor',
        // Anführungszeichen sind Pflicht: Kleine Dateien bettet Vite als
        // Daten-URI ein, und die zerlegt ein unquotiertes url(...) in CSS.
        maskImage: `url("${LOGOS[kanal]}")`,
        WebkitMaskImage: `url("${LOGOS[kanal]}")`,
        maskSize: 'contain',
        WebkitMaskSize: 'contain',
        maskRepeat: 'no-repeat',
        WebkitMaskRepeat: 'no-repeat',
        maskPosition: 'center',
        WebkitMaskPosition: 'center',
      }}
    />
  )
}

export function AdminChannelSettings() {
  // Der erste Eintrag der Reihe - alles andere sähe aus wie ein Versehen.
  const [kanal, setKanal] = useState<ChannelKind>(KANAELE[0])

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-2">
        {KANAELE.map((eintrag) => (
          <button
            key={eintrag}
            type="button"
            onClick={() => setKanal(eintrag)}
            className={
              'inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm ' +
              'font-medium transition-colors ' +
              (kanal === eintrag
                ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
            }
          >
            <DienstSymbol kanal={eintrag} className="h-4 w-4" />
            {LABELS[eintrag]}
          </button>
        ))}
      </div>

      {/* Bewusst mit `key`: Beim Wechsel des Dienstes soll ein halb
          ausgefülltes Formular nicht stehen bleiben. */}
      <Zielverwaltung key={kanal} kanal={kanal} />
    </div>
  )
}

function Zielverwaltung({ kanal }: { kanal: ChannelKind }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  /** `null` = nichts offen, `'neu'` = anlegen, sonst die Kennung der Instanz. */
  const [offen, setOffen] = useState<number | 'neu' | null>(null)

  const zieleQuery = useQuery({
    queryKey: ['channel-targets', kanal],
    queryFn: () => api.get<ChannelTarget[]>(`/api/settings/channels/${kanal}/targets`),
  })

  const umschalten = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.put<ChannelTarget>(`/api/settings/channels/${kanal}/targets/${id}/enabled`, {
        enabled,
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['channel-targets', kanal] }),
  })

  const loeschen = useMutation({
    mutationFn: (id: number) => api.delete(`/api/settings/channels/${kanal}/targets/${id}`),
    onSuccess: () => {
      setOffen(null)
      void queryClient.invalidateQueries({ queryKey: ['channel-targets', kanal] })
    },
  })

  if (zieleQuery.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }

  const ziele = zieleQuery.data ?? []
  const bearbeitet = typeof offen === 'number' ? ziele.find((z) => z.id === offen) : undefined
  const feldOffen = offen !== null
  const auffrischen = () =>
    void queryClient.invalidateQueries({ queryKey: ['channel-targets', kanal] })

  return (
    <div className="flex flex-col gap-6">
      {/* Ist ein Feld offen, bleibt nur die Kachel stehen, zu der es gehört.
          Alles andere auszublenden sagt eindeutiger, was gerade bearbeitet
          wird, als jede Linie es könnte. */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {ziele
          .filter((ziel) => !feldOffen || offen === ziel.id)
          .map((ziel) => (
            <Kachel
              key={ziel.id}
              ziel={ziel}
              aktiv={offen === ziel.id}
              unterschrift={
                zweiEbenen(kanal)
                  ? t('channels.topicCount', { count: ziel.children?.length ?? 0 })
                  : undefined
              }
              onBearbeiten={() => setOffen(offen === ziel.id ? null : ziel.id)}
              onUmschalten={() => umschalten.mutate({ id: ziel.id, enabled: !ziel.enabled })}
              onLoeschen={() => {
                if (window.confirm(t('channels.confirmDelete', { name: ziel.name }))) {
                  loeschen.mutate(ziel.id)
                }
              }}
            />
          ))}

        {(!feldOffen || offen === 'neu') && (
          <PlusKachel
            beschriftung={
              kanal === 'email'
                ? t('channels.addAddress')
                : t('channels.addInstance', { label: LABELS[kanal] })
            }
            aktiv={offen === 'neu'}
            onClick={() => setOffen(offen === 'neu' ? null : 'neu')}
          />
        )}
      </div>

      {ziele.length === 0 && offen === null && (
        <p className="text-sm text-mist-500">{t('channels.emptyHint')}</p>
      )}

      {offen === 'neu' && (
        <InstanzFeld
          kanal={kanal}
          ziel={null}
          /* Nach dem Anlegen bleibt das Feld offen – jetzt mit der frisch
             angelegten Instanz, damit gleich Topics bzw. Meldungen folgen
             können. */
          onFertig={(neues) => {
            setOffen(neues.id)
            auffrischen()
          }}
          onAbbrechen={() => setOffen(null)}
        />
      )}

      {bearbeitet && (
        <InstanzFeld
          key={bearbeitet.id}
          kanal={kanal}
          ziel={bearbeitet}
          onFertig={auffrischen}
          onAbbrechen={() => setOffen(null)}
        />
      )}
    </div>
  )
}

/** Die „+"-Kachel mit Beschriftung darunter. */
function PlusKachel({
  beschriftung,
  aktiv,
  onClick,
}: {
  beschriftung: string
  aktiv: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        'flex min-h-28 flex-col items-center justify-center gap-2 rounded-2xl border ' +
        'border-dashed px-4 py-3 text-center transition-colors ' +
        (aktiv
          ? 'border-accent-500/60 bg-accent-500/10 text-accent-400'
          : 'border-ink-700 bg-ink-900/40 text-mist-600 hover:border-ink-600 hover:text-mist-300')
      }
    >
      <svg
        viewBox="0 0 24 24"
        className="h-7 w-7"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        aria-hidden="true"
      >
        <path d="M12 5v14M5 12h14" strokeLinecap="round" />
      </svg>
      <span className="text-sm font-medium">{beschriftung}</span>
    </button>
  )
}

type KachelProps = {
  ziel: ChannelTarget
  aktiv: boolean
  unterschrift?: string
  onBearbeiten: () => void
  onLoeschen: () => void
  onUmschalten: () => void
}

/**
 * Ein eingerichtetes Ziel: Name, Logo im Hintergrund und zwei Knöpfe.
 *
 * Die ganze Kachel öffnet die Einstellungen – der Stift daneben tut dasselbe,
 * weil man ihn dort sucht. Papierkorb und Verweis halten den Klick auf, sonst
 * öffneten sie nebenbei auch noch das Einstellungsfeld.
 */
function Kachel({
  ziel,
  aktiv,
  unterschrift,
  onBearbeiten,
  onLoeschen,
  onUmschalten,
}: KachelProps) {
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
        (ziel.enabled ? '' : ' opacity-50')
      }
    >
      {/* Nur auf Topic-Kacheln: Dort steht das Symbol des Dienstes blass im
          Hintergrund. Eine Ebene höher wäre es überflüssig - welcher Dienst
          gemeint ist, sagt dort schon die Auswahl darüber. */}
      {ziel.parent_id !== null && (
        <DienstSymbol
          kanal={ziel.channel}
          className="pointer-events-none absolute inset-0 m-auto h-20 w-20 text-mist-100 opacity-[0.14]"
        />
      )}

      <div className="relative flex items-start justify-between gap-2">
        <div>
          <p className="text-lg font-semibold text-mist-100">{ziel.name}</p>
          {unterschrift && <p className="mt-0.5 text-xs text-mist-600">{unterschrift}</p>}
        </div>
        <InstanzLink ziel={ziel} />
      </div>

      <div className="relative mt-3 flex items-center justify-between gap-2">
        {/* Ein Ziel, über das nichts mehr durchgeht, sieht genauso aus wie
            eines, über das es nichts zu berichten gibt. */}
        <span className="text-xs text-bad-500">
          {ziel.last_error ? t('channels.tileFailing') : ''}
        </span>

        <div className="flex gap-2">
          {/* Ein Klick legt still oder nimmt wieder in Betrieb – ohne das Ziel
              wegzuwerfen und ohne erneute Bestätigung, denn an der Verbindung
              ändert sich dabei nichts. */}
          <RundKnopf
            label={t(ziel.enabled ? 'channels.disable' : 'channels.enable')}
            onClick={onUmschalten}
            an={ziel.enabled}
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

/** Die Adresse, unter der man beim Dienst selbst nachsieht. */
function instanzAdresse(ziel: {
  channel: ChannelKind
  url?: string | null
  topic?: string | null
}): string | null {
  // Hinter einem Webhook steckt keine Oberflaeche - ein Klick koennte dort
  // sogar etwas ausloesen.
  if (ziel.channel === 'webhook') return null
  const basis = (ziel.url ?? '').trim().replace(/\/$/, '')
  if (!basis.startsWith('http')) return null
  // Bei ntfy führt das Topic direkt zum Nachrichtenverlauf; Gotify zeigt nach
  // der Anmeldung ohnehin alles.
  const topic = (ziel.topic ?? '').trim()
  return ziel.channel === 'ntfy' && topic ? `${basis}/${topic}` : basis
}

/** Kleiner Verweis auf die Instanz, oben rechts auf der Kachel. */
function InstanzLink({ ziel }: { ziel: ChannelTarget }) {
  const { t } = useTranslation()
  const adresse = instanzAdresse(ziel)
  if (!adresse) return null

  return (
    <a
      href={adresse}
      target="_blank"
      rel="noreferrer"
      onClick={(event) => event.stopPropagation()}
      aria-label={t('channels.openService', { label: LABELS[ziel.channel] })}
      title={adresse}
      className="mt-1 shrink-0 text-mist-600 transition-colors hover:text-accent-400"
    >
      <svg
        viewBox="0 0 24 24"
        className="h-4 w-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      >
        <path
          d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </a>
  )
}

function RundKnopf({
  label,
  onClick,
  children,
  gefahr = false,
  an,
}: {
  label: string
  onClick: () => void
  children: ReactNode
  gefahr?: boolean
  /** Schalter mit Zustand: hervorgehoben, solange er an ist. */
  an?: boolean
}) {
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation()
        onClick()
      }}
      aria-label={label}
      title={label}
      className={
        'flex h-9 w-9 items-center justify-center rounded-full border border-ink-700 ' +
        'bg-ink-850 transition-colors ' +
        (gefahr
          ? 'text-mist-500 hover:border-bad-500/50 hover:text-bad-500'
          : an
            ? 'border-ok-500/40 text-ok-500 hover:border-ok-500/70'
            : 'text-mist-500 hover:border-accent-500/50 hover:text-accent-400')
      }
    >
      <svg
        viewBox="0 0 24 24"
        className="h-4 w-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      >
        {children}
      </svg>
    </button>
  )
}

type FeldProps = {
  kanal: ChannelKind
  ziel: ChannelTarget | null
  onFertig: (ziel: ChannelTarget) => void
  onAbbrechen: () => void
}

/**
 * Das Einstellungsfeld einer Instanz.
 *
 * Bei Gotify ist die Instanz zugleich das Postfach – dann ist es genau das
 * Postfach-Formular. Bei ntfy stehen hier nur Adresse und Anmeldung, und rechts
 * liegen die Topics.
 */
function InstanzFeld({ kanal, ziel, onFertig, onAbbrechen }: FeldProps) {
  if (!zweiEbenen(kanal)) {
    return (
      <PostfachFeld
        kanal={kanal}
        ebene="instanz"
        ziel={ziel}
        parent={null}
        onFertig={onFertig}
        onAbbrechen={onAbbrechen}
      />
    )
  }

  return (
    <ZweistufigesFeld kanal={kanal} ziel={ziel} onFertig={onFertig} onAbbrechen={onAbbrechen} />
  )
}

/**
 * Die obere Ebene eines zweistufigen Dienstes: links der Zugang, rechts die
 * Postfächer darin.
 *
 * Bei ntfy sind das Adresse und Anmeldung mit ihren Topics, bei Telegram das
 * Bot-Token mit seinen Chats. Geprüft wird hier nur die Erreichbarkeit – ein
 * Bestätigungscode ergäbe keinen Sinn, weil auf dieser Ebene nichts ankommt.
 */
function ZweistufigesFeld({ kanal, ziel, onFertig, onAbbrechen }: FeldProps) {
  const { t } = useTranslation()

  const vorlage = LEER[`${kanal}-instanz`]
  const gespeichert: Entwurf = {
    ...vorlage,
    ...(ziel
      ? Object.fromEntries(
          Object.keys(vorlage).map((feld) => {
            if (feld === 'name') return ['name', ziel.name]
            // Geheimnisse bleiben leer: leer heißt „unverändert“.
            if (feld === 'password' || feld === 'token') return [feld, '']
            // Bei Discord ist die URL selbst das Geheimnis und kommt maskiert an.
            if (kanal === 'discord' && feld === 'url') return [feld, '']
            return [feld, (ziel as unknown as Record<string, string>)[feld] ?? vorlage[feld]]
          }),
        )
      : {}),
  }

  const [entwurf, setEntwurf] = useState<Entwurf>(gespeichert)
  const [hinweis, setHinweis] = useState<TestResult | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)
  /**
   * Die Anleitung steht so lange offen, wie sie gebraucht wird: bis das erste
   * Postfach eingerichtet ist. Danach schließt sie sich von selbst und macht
   * den Platz für die Meldungen frei – bis dahin sind es genau die Schritte,
   * die man noch vor sich hat.
   *
   * `null` heißt „noch nicht von Hand entschieden“. Sobald jemand den
   * Hilfe-Knopf drückt, gilt seine Entscheidung und nicht mehr die Automatik.
   */
  const [anleitungManuell, setAnleitungManuell] = useState<boolean | null>(null)
  const fertigEingerichtet = (ziel?.children?.length ?? 0) > 0
  const anleitung = anleitungManuell ?? (hatAnleitung(kanal) && !fertigEingerichtet)

  /**
   * Hat der Betreiber den Bot schon angeschrieben?
   *
   * Ein Bot darf niemanden von sich aus anschreiben; ohne diesen einen Schritt
   * bleibt die Chat-Liste leer, und niemand versteht warum. Deshalb steht er
   * hier ausdrücklich im Weg, statt in einer Anleitung daneben. Steht schon ein
   * Chat, ist er offensichtlich erledigt.
   */
  const [angeschrieben, setAngeschrieben] = useState(false)
  // Auch im Assistenten fuehrt der Weg ueber die "+"-Kachel - wie ueberall
  // sonst. Ein sofort aufgeklapptes Formular saehe aus wie ein Fremdkoerper.
  const [assistentChat, setAssistentChat] = useState(false)
  /**
   * Sobald der Code des Chats bestätigt ist, gehört die rechte Spalte den
   * Meldungen: Die Anleitung ist zu Ende erzählt, und die Auswahl steht dort,
   * wo der Blick ohnehin liegt. Das Chat-Formular hängt sie per Portal in
   * diesen Anker ein - es bleibt selbst links stehen.
   */
  const [chatBelegt, setChatBelegt] = useState(false)
  const [meldungenAnker, setMeldungenAnker] = useState<HTMLDivElement | null>(null)
  /** Beim Einrichten entsteht die Instanz erst am Ende - hier steht sie dann. */
  const angelegt = useRef<ChannelTarget | null>(null)

  const botGeprueft = Boolean(entwurf.username) && (ziel !== null || (hinweis?.ok ?? false))
  const botSchritt = kanal === 'telegram' && botGeprueft && !fertigEingerichtet && !angeschrieben
  /**
   * Der geführte Weg beim Einrichten: Bot prüfen, anschreiben, Chat anlegen,
   * bestätigen – und **dann** speichern. Erst der bestätigte Code belegt, dass
   * der Weg trägt; vorher etwas abzulegen hiesse, Zugangsdaten aufzubewahren,
   * von denen niemand weiss, ob sie taugen.
   */
  const imAssistenten = kanal === 'telegram' && !ziel && botGeprueft && angeschrieben
  /**
   * Derselbe Gedanke für ntfy, nur ohne den Anschreiben-Schritt: Sobald die
   * Erreichbarkeit belegt ist, geht es rechts direkt mit dem ersten Topic
   * weiter - gespeichert wird erst mit dem bestätigten Code, Instanz und
   * Topic in einem Zug.
   */
  const ntfyAssistent = kanal === 'ntfy' && !ziel && (hinweis?.ok ?? false)
  const [ntfyTopicOffen, setNtfyTopicOffen] = useState(false)

  const pfad = `/api/settings/channels/${kanal}`

  const pruefen = useMutation({
    mutationFn: () =>
      api.post<TestResult>(ziel ? `${pfad}/check?target_id=${ziel.id}` : `${pfad}/check`, entwurf),
    onMutate: () => setHinweis(null),
    onSuccess: (ergebnis) => {
      setHinweis(ergebnis)
      // Telegram nennt dabei den Benutzernamen des Bots – den muss niemand
      // abtippen.
      if (ergebnis.values) setEntwurf((aktuell) => ({ ...aktuell, ...ergebnis.values }))
    },
    onError: (caught) =>
      setHinweis({
        ok: false,
        message: caught instanceof ApiError ? caught.message : t('errors.generic'),
      }),
  })

  const speichern = useMutation({
    mutationFn: () =>
      ziel
        ? api.put<ChannelTarget>(`${pfad}/targets/${ziel.id}`, entwurf)
        : api.post<ChannelTarget>(`${pfad}/targets`, entwurf),
    onSuccess: (gespeichertesZiel) => {
      setFehler(null)
      onFertig(gespeichertesZiel)
    },
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('settings.saveFailed')),
  })

  function update(patch: Entwurf) {
    setEntwurf((aktuell) => ({ ...aktuell, ...patch }))
    setHinweis(null)
    setFehler(null)
  }

  const geaendert =
    !ziel || Object.keys(entwurf).some((feld) => entwurf[feld] !== gespeichert[feld])
  const vollstaendig =
    entwurf.name.trim() !== '' &&
    (kanal === 'telegram'
      ? entwurf.token.trim() !== '' || Boolean(ziel?.token_set)
      : entwurf.url.trim() !== '')

  return (
    <Card>
      <div className="grid grid-cols-1 gap-x-8 gap-y-6 lg:grid-cols-2 lg:items-start">
        <div className="flex flex-col gap-4">
          <div>
            <h2 className="text-lg font-semibold">
              {ziel ? t('channels.editTitle', { name: ziel.name }) : t('channels.newInstance')}
            </h2>
            <p className="mt-1 text-sm text-mist-500">
              {t(kanal === 'telegram' ? 'channels.telegramBotIntro' : 'channels.ntfyInstanceIntro')}
            </p>
          </div>

          <Field
            label={t(kanal === 'telegram' ? 'channels.botName' : 'channels.instanceName')}
            value={entwurf.name}
            onChange={(event) => update({ name: event.target.value })}
            placeholder={kanal === 'telegram' ? 'Nexview-Bot' : 'Zuhause'}
            hint={t(kanal === 'telegram' ? 'channels.botNameHint' : 'channels.instanceNameHint')}
            autoComplete="off"
          />
          {kanal === 'telegram' ? (
            <TelegramBotFelder entwurf={entwurf} update={update} ziel={ziel} />
          ) : (
            <NtfyInstanzFelder entwurf={entwurf} update={update} ziel={ziel} />
          )}

          {/* Ohne Topic kann hier nichts ankommen – zu bestätigen gibt es also
              nichts. Geprüft wird nur, ob unter der Adresse überhaupt ein ntfy
              antwortet; ein Tippfehler soll sofort auffallen. */}
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              variant="ghost"
              onClick={() => pruefen.mutate()}
              loading={pruefen.isPending}
              disabled={
                kanal === 'telegram' ? entwurf.token.trim() === '' : entwurf.url.trim() === ''
              }
            >
              {t(kanal === 'telegram' ? 'channels.checkToken' : 'channels.checkReach')}
            </Button>
            {hinweis && (
              <span className={'text-sm ' + (hinweis.ok ? 'text-ok-500' : 'text-bad-500')}>
                {hinweis.message}
              </span>
            )}
          </div>

          {/* Der eine Schritt, der außerhalb von Nexview passieren muss -
              und ohne den die Chat-Liste unerklärlich leer bleibt. Er steht
              deshalb im Weg, bis er abgehakt ist. */}
          {botSchritt && (
            <div className="mt-2 flex flex-col gap-3 rounded-xl border border-accent-600/50 bg-accent-700/10 px-4 py-3">
              <p className="text-sm font-medium text-mist-100">{t('channels.writeBotTitle')}</p>
              <p className="text-sm leading-relaxed text-mist-400">
                {t('channels.writeBotBody', { name: entwurf.username || 'Bot' })}
              </p>
              <div>
                <Button type="button" variant="ghost" onClick={() => setAngeschrieben(true)}>
                  {t('channels.writeBotDone')}
                </Button>
              </div>
            </div>
          )}

          {/* Nur solange rechts die Anleitung liegt, weichen die Postfächer
              hierher aus - so ist beides gleichzeitig zu sehen. Sonst stehen
              sie rechts, wo sie hingehören. */}
          {ziel && !botSchritt && anleitung && !chatBelegt && (
            <div className="mt-2 flex flex-col gap-4 border-t border-ink-700 pt-6">
              <Postfachkopf kanal={kanal} ziel={ziel} />
              <TopicListe kanal={kanal} instanz={ziel} onGeaendert={() => onFertig(ziel)} />
            </div>
          )}

          {/* Noch nicht gespeichert: Das Chat-Formular arbeitet mit dem Token
              aus dem Formular darüber und legt am Ende beides zusammen an. */}
          {imAssistenten && (
            <div className="mt-2 flex flex-col gap-4 border-t border-ink-700 pt-6">
              <Postfachkopf kanal={kanal} ziel={null} bereit />
              {!assistentChat ? (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <PlusKachel
                    beschriftung={t('channels.addChat')}
                    aktiv={false}
                    onClick={() => setAssistentChat(true)}
                  />
                </div>
              ) : (
                <PostfachFeld
                  kanal={kanal}
                  ebene="topic"
                  ziel={null}
                  parent={null}
                  parentEntwurf={entwurf}
                  onSpeichern={async (rumpf) => {
                    const instanz = await api.post<ChannelTarget>(`${pfad}/targets`, entwurf)
                    angelegt.current = instanz
                    return api.post<ChannelTarget>(`${pfad}/targets/${instanz.id}/children`, rumpf)
                  }}
                  onFertig={() => {
                    if (angelegt.current) onFertig(angelegt.current)
                  }}
                  onAbbrechen={() => setAssistentChat(false)}
                  meldungenAnker={meldungenAnker}
                  onBelegt={setChatBelegt}
                />
              )}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-4">
          <div className="flex items-start justify-between gap-3">
            {anleitung && !chatBelegt ? (
              <h2 className="text-lg font-semibold">{t('channels.guideHeading')}</h2>
            ) : chatBelegt ? (
              <h2 className="text-lg font-semibold">{'\u00a0'}</h2>
            ) : (
              <Postfachkopf
                kanal={kanal}
                ziel={ziel && !botSchritt ? ziel : null}
                bereit={ntfyAssistent || undefined}
              />
            )}
            {hatAnleitung(kanal) && !chatBelegt && (
              <HilfeKnopf offen={anleitung} onKlick={() => setAnleitungManuell(!anleitung)} />
            )}
          </div>

          {anleitung && !chatBelegt && (
            <Anleitung
              kanal={kanal}
              symbol={<DienstSymbol kanal={kanal} className="mt-1 h-5 w-5" />}
            />
          )}

          {!anleitung && !chatBelegt && ziel && !botSchritt && (
            <TopicListe kanal={kanal} instanz={ziel} onGeaendert={() => onFertig(ziel)} />
          )}

          {/* Hier hängt das Chat-Formular seine Meldungsliste ein. */}
          {ntfyAssistent &&
            (!ntfyTopicOffen ? (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <PlusKachel
                  beschriftung={t('channels.addTopic')}
                  aktiv={false}
                  onClick={() => setNtfyTopicOffen(true)}
                />
              </div>
            ) : (
              <PostfachFeld
                kanal={kanal}
                ebene="topic"
                ziel={null}
                parent={null}
                parentEntwurf={entwurf}
                onSpeichern={async (rumpf) => {
                  const instanz = await api.post<ChannelTarget>(`${pfad}/targets`, entwurf)
                  angelegt.current = instanz
                  return api.post<ChannelTarget>(`${pfad}/targets/${instanz.id}/children`, rumpf)
                }}
                onFertig={() => {
                  if (angelegt.current) onFertig(angelegt.current)
                }}
                onAbbrechen={() => setNtfyTopicOffen(false)}
              />
            ))}

          <div ref={setMeldungenAnker} className="flex flex-col gap-4" />
        </div>
      </div>

      {/* Im Assistenten speichert das Chat-Formular alles auf einmal - ein
          zweiter Knopf daneben wüsste nicht, was er tun soll. */}
      {!imAssistenten && (
        <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-ink-700 pt-6">
          {/* Bei einer neuen ntfy-Instanz gibt es kein Speichern für die
              Instanz allein: Gespeichert wird erst mit dem bestätigten Code,
              zusammen mit dem ersten Topic. */}
          {!(kanal === 'ntfy' && !ziel) && (
            <Button
              type="button"
              onClick={() => speichern.mutate()}
              loading={speichern.isPending}
              disabled={!geaendert || !vollstaendig}
            >
              {t('common.save')}
            </Button>
          )}
          <Button type="button" variant="ghost" onClick={onAbbrechen}>
            {t('common.cancel')}
          </Button>
          {geaendert && !speichern.isPending && !(kanal === 'ntfy' && !ziel) && (
            <span className="text-sm text-mist-600">{t('common.unsaved')}</span>
          )}
        </div>
      )}

      {fehler && <ErrorBanner message={fehler} />}
    </Card>
  )
}

/** ntfy: Adresse und Anmeldung der Instanz. */
function NtfyInstanzFelder({
  entwurf,
  update,
  ziel,
}: {
  entwurf: Entwurf
  update: (patch: Entwurf) => void
  ziel: ChannelTarget | null
}) {
  const { t } = useTranslation()

  return (
    <>
      <Field
        label={t('channels.ntfyUrl')}
        value={entwurf.url}
        onChange={(event) => update({ url: event.target.value })}
        placeholder="https://ntfy.sh"
        hint={t('channels.ntfyUrlHint')}
        autoComplete="off"
      />

      <Auswahl
        label={t('channels.auth')}
        value={entwurf.auth}
        onChange={(wert) => update({ auth: wert as NtfyAuth })}
        hint={t('channels.authHint')}
        optionen={(['none', 'basic', 'token'] as NtfyAuth[]).map((wert) => ({
          value: wert,
          label: t(`channels.auth_${wert}`),
        }))}
      />

      {entwurf.auth === 'basic' && (
        <>
          <Field
            label={t('channels.username')}
            value={entwurf.username}
            onChange={(event) => update({ username: event.target.value })}
            autoComplete="off"
          />
          <Field
            label={t('channels.password')}
            type="password"
            value={entwurf.password}
            onChange={(event) => update({ password: event.target.value })}
            placeholder={ziel?.password_set ? ziel.password : ''}
            hint={ziel?.password_set ? t('settings.keySetHint') : undefined}
            autoComplete="new-password"
          />
        </>
      )}

      {entwurf.auth === 'token' && (
        <Field
          label={t('channels.token')}
          type="password"
          value={entwurf.token}
          onChange={(event) => update({ token: event.target.value })}
          placeholder={ziel?.token_set ? ziel.token : 'tk_...'}
          hint={ziel?.token_set ? t('settings.keySetHint') : t('channels.ntfyTokenHint')}
          autoComplete="new-password"
        />
      )}
    </>
  )
}

/**
 * Telegram: das Bot-Token, und der Name, den `getMe` dazu nennt.
 *
 * Den Benutzernamen füllt die Prüfung selbst aus – er wird nur gebraucht, um
 * den `t.me`-Link zu bauen, über den ein Chat überhaupt erst zustande kommt.
 */
function TelegramBotFelder({
  entwurf,
  update,
  ziel,
}: {
  entwurf: Entwurf
  update: (patch: Entwurf) => void
  ziel: ChannelTarget | null
}) {
  const { t } = useTranslation()

  return (
    <>
      <Field
        label={t('channels.botToken')}
        type="password"
        value={entwurf.token}
        onChange={(event) => update({ token: event.target.value })}
        placeholder={ziel?.token_set ? ziel.token : '123456789:AA...'}
        hint={ziel?.token_set ? t('settings.keySetHint') : t('channels.botTokenHint')}
        autoComplete="new-password"
      />
      <a
        href="https://t.me/BotFather"
        target="_blank"
        rel="noreferrer"
        className="text-sm text-accent-400 hover:underline"
      >
        {t('channels.openBotFather')} ↗
      </a>
      {/* Nur zum Ansehen: Den Namen liest die Prüfung bei Telegram aus. Ein
          Feld zum Tippen lüde nur dazu ein, etwas anderes hineinzuschreiben
          als den Bot, zu dem das Token gehört. */}
      <Field
        label={t('channels.botUsername')}
        value={entwurf.username ? `@${entwurf.username}` : ''}
        onChange={() => undefined}
        readOnly
        placeholder={t('channels.botUsernamePlaceholder')}
        hint={t('channels.botUsernameHint')}
        className="cursor-default text-mist-400"
        autoComplete="off"
      />
      {entwurf.username && (
        <a
          href={`https://t.me/${entwurf.username.replace(/^@/, '')}`}
          target="_blank"
          rel="noreferrer"
          className="text-sm text-accent-400 hover:underline"
        >
          {t('channels.openBot', { name: entwurf.username.replace(/^@/, '') })}
        </a>
      )}
    </>
  )
}

/** Überschrift und Einleitung über den Postfächern - je nach Spalte woanders. */
function Postfachkopf({
  kanal,
  ziel,
  bereit,
}: {
  kanal: ChannelKind
  ziel: ChannelTarget | null
  /** Im Assistenten ist der Bot belegt, obwohl noch nichts gespeichert ist. */
  bereit?: boolean
}) {
  const { t } = useTranslation()

  return (
    <div>
      <h2 className="text-lg font-semibold">
        {t(kanal === 'telegram' ? 'channels.chatsSection' : 'channels.topicsSection')}
      </h2>
      <p className="mt-1 text-sm text-mist-500">
        {(bereit ?? Boolean(ziel))
          ? t(kanal === 'telegram' ? 'channels.chatsIntro' : 'channels.topicsIntro')
          : t(kanal === 'telegram' ? 'channels.chatsBlocked' : 'channels.topicsBlocked')}
      </p>
    </div>
  )
}

/** Die Postfächer einer Instanz: Kacheln, „+" und darunter das offene Formular. */
function TopicListe({
  kanal,
  instanz,
  onGeaendert,
}: {
  kanal: ChannelKind
  instanz: ChannelTarget
  onGeaendert: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [offen, setOffen] = useState<number | 'neu' | null>(null)

  const topics = instanz.children ?? []
  const bearbeitet = typeof offen === 'number' ? topics.find((k) => k.id === offen) : undefined
  const feldOffen = offen !== null

  const umschalten = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.put<ChannelTarget>(`/api/settings/channels/${kanal}/targets/${id}/enabled`, {
        enabled,
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['channel-targets', kanal] }),
  })

  const loeschen = useMutation({
    mutationFn: (id: number) => api.delete(`/api/settings/channels/${kanal}/targets/${id}`),
    onSuccess: () => {
      setOffen(null)
      void queryClient.invalidateQueries({ queryKey: ['channel-targets', kanal] })
    },
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {topics
          .filter((topic) => !feldOffen || offen === topic.id)
          .map((topic) => (
            <Kachel
              key={topic.id}
              ziel={{ ...topic, url: instanz.url }}
              aktiv={offen === topic.id}
              unterschrift={kanal === 'telegram' ? topic.chat_id : topic.topic}
              onBearbeiten={() => setOffen(offen === topic.id ? null : topic.id)}
              onUmschalten={() => umschalten.mutate({ id: topic.id, enabled: !topic.enabled })}
              onLoeschen={() => {
                if (window.confirm(t('channels.confirmDelete', { name: topic.name }))) {
                  loeschen.mutate(topic.id)
                }
              }}
            />
          ))}

        {(!feldOffen || offen === 'neu') && (
          <PlusKachel
            beschriftung={t(kanal === 'telegram' ? 'channels.addChat' : 'channels.addTopic')}
            aktiv={offen === 'neu'}
            onClick={() => setOffen(offen === 'neu' ? null : 'neu')}
          />
        )}
      </div>

      {feldOffen && (
        <PostfachFeld
          key={typeof offen === 'number' ? offen : 'neu'}
          kanal={kanal}
          ebene="topic"
          ziel={bearbeitet ?? null}
          parent={instanz}
          onFertig={(gespeichert) => {
            setOffen(gespeichert.id)
            onGeaendert()
          }}
          onAbbrechen={() => setOffen(null)}
        />
      )}
    </div>
  )
}

type PostfachProps = FeldProps & {
  ebene: 'instanz' | 'topic'
  parent: ChannelTarget | null
  /**
   * Die Verbindungsdaten der Instanz, solange sie noch nicht gespeichert ist.
   *
   * Beim Einrichten steht das Bot-Token im Formular darüber; gespeichert wird
   * erst, wenn der Weg belegt ist. Testnachricht und Chat-Suche brauchen es
   * aber schon vorher.
   */
  parentEntwurf?: Entwurf
  /** Übernimmt das Speichern, wenn dabei mehr entsteht als dieses Postfach. */
  onSpeichern?: (rumpf: Record<string, unknown>) => Promise<ChannelTarget>
  /** Wohin die Meldungsliste wandert, sobald der Code bestätigt ist. */
  meldungenAnker?: HTMLElement | null
  /** Meldet, ob der Code bestätigt ist - der Rahmen räumt dann rechts auf. */
  onBelegt?: (belegt: boolean) => void
}

/** Wo im Ablauf stehen wir? */
type Schritt = 'offen' | 'gesendet' | 'bestaetigt'

/**
 * Das Formular eines **Postfachs** – bei Gotify die Application, bei ntfy das
 * Topic. Erst die Angaben, dann die Meldungen; gespeichert wird beides
 * zusammen, denn ein Postfach ist eine Sache.
 *
 * Nebeneinander oder untereinander hängt am Platz: Die Gotify-Application
 * steht über die volle Breite, da passen zwei Spalten. Ein ntfy-Topic liegt
 * innerhalb seiner Instanz und hat nur die halbe – dort werden daraus Reihen.
 *
 * Geprüft wird nur oben: Testnachricht schicken, den vierstelligen Code aus ihr
 * eintippen. Der Code ist der Nachweis, dass wirklich etwas ankommt – ein
 * HTTP 200 vom Push-Dienst heißt nur „angenommen“.
 */
function PostfachFeld({
  kanal,
  ebene,
  ziel,
  parent,
  parentEntwurf,
  onSpeichern,
  onFertig,
  onAbbrechen,
  meldungenAnker,
  onBelegt,
}: PostfachProps) {
  const { t } = useTranslation()
  // Die Postfach-Formulare der zweiten Ebene liegen alle in der schmalen
  // linken Spalte - zwei Spalten darin zerreißt es. Also gestapelt.
  const gestapelt = ebene === 'topic'

  const vorlage = LEER[`${kanal}-${ebene}`]
  const gespeichert: Entwurf = {
    ...vorlage,
    ...(ziel
      ? Object.fromEntries(
          Object.keys(vorlage).map((feld) => {
            if (feld === 'name') return ['name', ziel.name]
            // Geheimnisse bleiben leer: leer heißt „unverändert“.
            if (feld === 'password' || feld === 'token') return [feld, '']
            // Bei Discord ist die URL selbst das Geheimnis und kommt maskiert an.
            if (kanal === 'discord' && feld === 'url') return [feld, '']
            return [feld, (ziel as unknown as Record<string, string>)[feld] ?? vorlage[feld]]
          }),
        )
      : {}),
  }
  const gespeicherteMeldungen = (ziel?.events ?? {}) as Record<string, ChannelLevel>

  const [entwurf, setEntwurf] = useState<Entwurf>(gespeichert)
  const [auswahl, setAuswahl] = useState<Record<string, ChannelLevel>>(gespeicherteMeldungen)
  const [schritt, setSchritt] = useState<Schritt>('offen')
  const [code, setCode] = useState('')
  const [hinweis, setHinweis] = useState<TestResult | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)

  const pfad = `/api/settings/channels/${kanal}`
  const botName = (parent?.username ?? parentEntwurf?.username ?? '').replace(/^@/, '')

  const testPfad = ziel
    ? `${pfad}/test?target_id=${ziel.id}`
    : parent
      ? `${pfad}/test?parent_id=${parent.id}`
      : `${pfad}/test`

  const testen = useMutation({
    mutationFn: () => api.post<TestResult>(testPfad, { ...parentEntwurf, ...entwurf }),
    onMutate: () => {
      setHinweis(null)
      setCode('')
      setSchritt('offen')
    },
    onSuccess: (ergebnis) => {
      setHinweis(ergebnis)
      setSchritt(ergebnis.ok ? 'gesendet' : 'offen')
    },
    onError: (caught) =>
      setHinweis({
        ok: false,
        message: caught instanceof ApiError ? caught.message : t('errors.generic'),
      }),
  })

  const pruefen = useMutation({
    mutationFn: () => api.post<TestResult>(`${pfad}/confirm`, { code }),
    onSuccess: (ergebnis) => {
      setHinweis(ergebnis)
      if (ergebnis.ok) setSchritt('bestaetigt')
    },
    onError: (caught) =>
      setHinweis({
        ok: false,
        message: caught instanceof ApiError ? caught.message : t('errors.generic'),
      }),
  })

  const speichern = useMutation({
    mutationFn: () => {
      const rumpf = { ...entwurf, events: auswahl }
      if (onSpeichern) return onSpeichern(rumpf)
      if (ziel) return api.put<ChannelTarget>(`${pfad}/targets/${ziel.id}`, rumpf)
      if (parent) return api.post<ChannelTarget>(`${pfad}/targets/${parent.id}/children`, rumpf)
      return api.post<ChannelTarget>(`${pfad}/targets`, rumpf)
    },
    onSuccess: (gespeichertesZiel) => {
      setCode('')
      setSchritt('offen')
      setHinweis(null)
      setFehler(null)
      onFertig(gespeichertesZiel)
    },
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('settings.saveFailed')),
  })

  /** Jede Änderung an den Angaben wirft die Prüfung weg – sie galt für ein anderes Ziel. */
  function updateVerbindung(patch: Entwurf) {
    setEntwurf((aktuell) => ({ ...aktuell, ...patch }))
    setSchritt('offen')
    setCode('')
    setHinweis(null)
    setFehler(null)
  }

  function updateMeldungen(patch: Record<string, ChannelLevel | undefined>) {
    setAuswahl((aktuell) => {
      const naechste = { ...aktuell }
      for (const [schluessel, wert] of Object.entries(patch)) {
        if (wert === undefined) delete naechste[schluessel]
        else naechste[schluessel] = wert
      }
      return naechste
    })
    setFehler(null)
  }

  const verbindungGeaendert = Object.keys(entwurf).some(
    (feld) => entwurf[feld] !== gespeichert[feld],
  )
  const geaendert =
    !ziel ||
    verbindungGeaendert ||
    JSON.stringify(auswahl) !== JSON.stringify(gespeicherteMeldungen)

  /**
   * Entweder gerade per Code bestätigt – oder ein bestehendes Postfach, an
   * dessen Angaben niemand etwas angefasst hat. Für einen Haken jedes Mal eine
   * Testnachricht zu verlangen wäre Schikane.
   */
  const verbindungBelegt =
    !brauchtCode(kanal) ||
    schritt === 'bestaetigt' ||
    Boolean(ziel?.verified && !verbindungGeaendert)
  const testbar =
    entwurf.name.trim() !== '' &&
    (ebene === 'topic'
      ? kanal === 'telegram'
        ? entwurf.chat_id.trim() !== ''
        : entwurf.topic.trim() !== ''
      : kanal === 'email'
        ? entwurf.address.trim() !== ''
        : kanal === 'apprise'
          ? entwurf.url.trim() !== '' && entwurf.topic.trim() !== ''
          : entwurf.url.trim() !== '' || Boolean(ziel?.url_set))

  // Meldet dem Rahmen, ob der Code bestätigt ist - erst dann räumt er rechts
  // die Anleitung weg und gibt der Liste den Platz.
  useEffect(() => {
    onBelegt?.(verbindungBelegt)
  }, [verbindungBelegt, onBelegt])

  const meldungen = (
    <Meldungen
      auswahl={auswahl}
      onUmstellen={updateMeldungen}
      geaendert={geaendert}
      verbindungBelegt={verbindungBelegt}
      laeuft={speichern.isPending}
      onSpeichern={() => speichern.mutate()}
      onAbbrechen={onAbbrechen}
      fehler={fehler}
      abgetrennt={gestapelt && !meldungenAnker}
    />
  )

  return (
    <Card>
      <div
        className={
          gestapelt
            ? 'flex flex-col gap-6'
            : 'grid grid-cols-1 gap-x-8 gap-y-6 lg:grid-cols-2 lg:items-start'
        }
      >
        <div className="flex flex-col gap-4">
          <div>
            <h2 className="text-lg font-semibold">
              {ziel
                ? t('channels.editTitle', { name: ziel.name })
                : t(
                    ebene !== 'topic'
                      ? 'channels.newTitle'
                      : kanal === 'telegram'
                        ? 'channels.newChat'
                        : 'channels.newTopic',
                  )}
            </h2>
            <p className="mt-1 text-sm text-mist-500">
              {t(
                ebene !== 'topic'
                  ? `channels.${kanal}Intro`
                  : kanal === 'telegram'
                    ? 'channels.chatIntro'
                    : 'channels.topicIntro',
              )}
            </p>
          </div>

          <Field
            label={t(
              ebene === 'topic'
                ? 'channels.topicName'
                : kanal === 'email'
                  ? 'channels.mailboxName'
                  : kanal === 'discord'
                    ? 'channels.discordName'
                    : kanal === 'webhook'
                      ? 'channels.webhookName'
                      : kanal === 'apprise'
                        ? 'channels.appriseName'
                        : 'channels.appName',
            )}
            value={entwurf.name}
            onChange={(event) => updateVerbindung({ name: event.target.value })}
            placeholder={ebene === 'topic' ? 'Familie' : kanal === 'email' ? 'Team' : 'Nexview'}
            hint={t(
              ebene === 'topic'
                ? 'channels.topicNameHint'
                : kanal === 'email'
                  ? 'channels.mailboxNameHint'
                  : kanal === 'discord'
                    ? 'channels.discordNameHint'
                    : kanal === 'webhook'
                      ? 'channels.webhookNameHint'
                      : kanal === 'apprise'
                        ? 'channels.appriseNameHint'
                        : 'channels.appNameHint',
            )}
            autoComplete="off"
          />

          {ebene === 'topic' && kanal === 'telegram' ? (
            <>
              <Field
                label={t('channels.chatId')}
                value={entwurf.chat_id}
                onChange={(event) => updateVerbindung({ chat_id: event.target.value })}
                placeholder="-1001234567890"
                hint={t('channels.chatIdHint')}
                autoComplete="off"
              />
              {(parent || parentEntwurf) && (
                <ChatSuche
                  kanal={kanal}
                  instanzId={parent?.id}
                  entwurf={parentEntwurf}
                  onWaehlen={(chatId, name) =>
                    updateVerbindung({
                      chat_id: chatId,
                      // Die Bezeichnung nur vorschlagen, solange keine steht -
                      // eine getippte darf nicht überschrieben werden.
                      ...(entwurf.name.trim() === '' ? { name } : {}),
                    })
                  }
                />
              )}
              <Field
                label={t('channels.threadId')}
                value={entwurf.thread_id}
                onChange={(event) => updateVerbindung({ thread_id: event.target.value })}
                placeholder="12"
                hint={t('channels.threadIdHint')}
                autoComplete="off"
              />
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  checked={entwurf.silent === 'on'}
                  onChange={(event) =>
                    updateVerbindung({ silent: event.target.checked ? 'on' : '' })
                  }
                  className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
                />
                <span>
                  <span className="text-sm font-medium text-mist-100">{t('channels.silent')}</span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-mist-600">
                    {t('channels.silentHint')}
                  </span>
                </span>
              </label>
            </>
          ) : ebene === 'topic' ? (
            <Field
              label={t('channels.ntfyTopic')}
              value={entwurf.topic}
              onChange={(event) => updateVerbindung({ topic: event.target.value })}
              placeholder="nexview-a7f3c1"
              hint={t('channels.ntfyTopicHint')}
              autoComplete="off"
            />
          ) : kanal === 'email' ? (
            <>
              <Field
                label={t('channels.address')}
                type="email"
                value={entwurf.address}
                onChange={(event) => updateVerbindung({ address: event.target.value })}
                placeholder="team@beispiel.de"
                hint={t('channels.addressHint')}
                autoComplete="off"
              />
              <Field
                label={t('channels.subject')}
                value={entwurf.subject}
                onChange={(event) => updateVerbindung({ subject: event.target.value })}
                placeholder="Nexview: {titel}"
                hint={t('channels.subjectHint')}
                autoComplete="off"
              />
            </>
          ) : kanal === 'discord' ? (
            <>
              {/* Die URL ist bei Discord selbst das Geheimnis - es gibt kein
                  getrenntes Token. Deshalb maskiert wie ein Passwort. */}
              <Field
                label={t('channels.webhookUrl')}
                type="password"
                value={entwurf.url}
                onChange={(event) => updateVerbindung({ url: event.target.value })}
                placeholder={ziel?.url_set ? ziel.url : 'https://discord.com/api/webhooks/…'}
                hint={ziel?.url_set ? t('settings.keySetHint') : t('channels.webhookUrlHint')}
                autoComplete="new-password"
              />
              <Field
                label={t('channels.senderName')}
                value={entwurf.username}
                onChange={(event) => updateVerbindung({ username: event.target.value })}
                placeholder="Nexview"
                hint={t('channels.senderNameHint')}
                autoComplete="off"
              />
            </>
          ) : kanal === 'apprise' ? (
            <>
              <Field
                label={t('channels.appriseUrl')}
                value={entwurf.url}
                onChange={(event) => updateVerbindung({ url: event.target.value })}
                placeholder="http://apprise.zuhause:8000"
                hint={t('channels.appriseUrlHint')}
                autoComplete="off"
              />
              <Field
                label={t('channels.appriseKey')}
                value={entwurf.topic}
                onChange={(event) => updateVerbindung({ topic: event.target.value })}
                placeholder="nexview"
                hint={t('channels.appriseKeyHint')}
                autoComplete="off"
              />
            </>
          ) : kanal === 'webhook' ? (
            <>
              <Field
                label={t('channels.webhookTargetUrl')}
                value={entwurf.url}
                onChange={(event) => updateVerbindung({ url: event.target.value })}
                placeholder="https://n8n.zuhause/webhook/nexview"
                hint={t('channels.webhookTargetUrlHint')}
                autoComplete="off"
              />
              <Field
                label={t('channels.authHeader')}
                type="password"
                value={entwurf.token}
                onChange={(event) => updateVerbindung({ token: event.target.value })}
                placeholder={ziel?.token_set ? ziel.token : 'Bearer …'}
                hint={ziel?.token_set ? t('settings.keySetHint') : t('channels.authHeaderHint')}
                autoComplete="new-password"
              />
            </>
          ) : (
            <>
              <Field
                label={t('channels.gotifyUrl')}
                value={entwurf.url}
                onChange={(event) => updateVerbindung({ url: event.target.value })}
                placeholder="http://gotify.zuhause:8380"
                hint={t('channels.gotifyUrlHint')}
                autoComplete="off"
              />
              <Field
                label={t('channels.token')}
                type="password"
                value={entwurf.token}
                onChange={(event) => updateVerbindung({ token: event.target.value })}
                placeholder={ziel?.token_set ? ziel.token : 'A...'}
                hint={ziel?.token_set ? t('settings.keySetHint') : t('channels.gotifyTokenHint')}
                autoComplete="new-password"
              />
            </>
          )}

          <Auswahl
            label={t('channels.language')}
            value={entwurf.language}
            onChange={(wert) => updateVerbindung({ language: wert as ChannelLanguage })}
            hint={t('channels.languageHint')}
            optionen={[
              { value: 'de', label: 'Deutsch' },
              { value: 'en', label: 'English' },
            ]}
          />

          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              variant={verbindungBelegt ? 'ghost' : 'primary'}
              onClick={() => testen.mutate()}
              loading={testen.isPending}
              disabled={!testbar}
            >
              {schritt === 'offen' ? t('channels.sendTest') : t('channels.sendTestAgain')}
            </Button>
            {hinweis && !brauchtCode(kanal) && (
              <span className={'text-sm ' + (hinweis.ok ? 'text-ok-500' : 'text-bad-500')}>
                {hinweis.message}
              </span>
            )}
            {brauchtCode(kanal) && verbindungBelegt && (
              <span className="text-sm text-ok-500">{t('channels.connectionOk')}</span>
            )}
          </div>

          {/* Der Code beweist, dass die Nachricht wirklich angekommen ist – ein
              HTTP 200 beweist nur, dass der Dienst sie angenommen hat. */}
          {brauchtCode(kanal) && schritt === 'gesendet' && (
            <div className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3">
              <p className="text-sm text-mist-400">{t('channels.codeIntro')}</p>
              <div className="flex flex-wrap items-end gap-3">
                <div className="w-36">
                  <Field
                    label={t('channels.code')}
                    value={code}
                    onChange={(event) => setCode(event.target.value.replace(/\D/g, '').slice(0, 4))}
                    placeholder="1234"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    className="font-mono text-lg tracking-[0.3em]"
                  />
                </div>
                <Button
                  type="button"
                  onClick={() => pruefen.mutate()}
                  loading={pruefen.isPending}
                  disabled={code.length !== 4}
                  className="mb-0.5"
                >
                  {t('channels.checkCode')}
                </Button>
                {kanal !== 'telegram' && kanal !== 'email' && kanal !== 'discord' && kanal !== 'webhook' && kanal !== 'apprise' && (
                  <DienstLink
                    kanal={kanal}
                    url={
                      ebene === 'topic' ? (parent?.url ?? parentEntwurf?.url ?? '') : entwurf.url
                    }
                    topic={entwurf.topic ?? ''}
                  />
                )}
                {/* Der Code steht im Chat mit dem Bot - der Weg dorthin soll
                    ein Klick sein, kein Suchen. */}
                {kanal === 'telegram' && botName && (
                  <a
                    href={`https://t.me/${botName}`}
                    target="_blank"
                    rel="noreferrer"
                    className={
                      'mb-0.5 inline-flex items-center gap-2 rounded-full border border-ink-700 ' +
                      'bg-ink-850 px-5 py-2.5 text-sm font-semibold text-mist-300 ' +
                      'transition-colors hover:bg-ink-800 hover:text-mist-100'
                    }
                  >
                    {t('channels.openBot', { name: botName })}
                  </a>
                )}
              </div>
            </div>
          )}

          {hinweis && brauchtCode(kanal) && (
            <p className={'text-sm ' + (hinweis.ok ? 'text-ok-500' : 'text-bad-500')}>
              {hinweis.message}
            </p>
          )}

          {ziel?.last_error && (
            <p className="rounded-xl border border-accent-600/50 bg-accent-700/15 px-4 py-3 text-sm text-accent-400">
              {t('channels.lastFailure', {
                zeit: ziel.last_error_at ? new Date(ziel.last_error_at).toLocaleString() : '',
              })}{' '}
              {ziel.last_error}
            </p>
          )}
        </div>

        {/* Erst nach dem bestätigten Code: Vorher wüsste niemand, ob hier je
            etwas ankommt - Häkchen zu setzen wäre eine Wette. Solange bleibt
            nur der Weg zurück. */}
        {meldungenAnker ? (
          verbindungBelegt ? (
            createPortal(meldungen, meldungenAnker)
          ) : (
            <div className={gestapelt ? 'border-t border-ink-700 pt-6' : ''}>
              <Button type="button" variant="ghost" onClick={onAbbrechen}>
                {t('common.cancel')}
              </Button>
            </div>
          )
        ) : (
          meldungen
        )}
      </div>
    </Card>
  )
}

/** Verweis auf den Dienst, gleich neben dem Code-Feld. */
function DienstLink({ kanal, url, topic }: { kanal: ChannelKind; url: string; topic: string }) {
  const { t } = useTranslation()
  const adresse = instanzAdresse({ channel: kanal, url, topic })
  if (!adresse) return null

  return (
    <a
      href={adresse}
      target="_blank"
      rel="noreferrer"
      className={
        'mb-0.5 inline-flex items-center gap-2 rounded-full border border-ink-700 bg-ink-850 ' +
        'px-5 py-2.5 text-sm font-semibold text-mist-300 transition-colors ' +
        'hover:bg-ink-800 hover:text-mist-100'
      }
    >
      {t('channels.openService', { label: LABELS[kanal] })}
      <svg
        viewBox="0 0 24 24"
        className="h-4 w-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      >
        <path
          d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </a>
  )
}

type GefundenerChat = { chat_id: string; name: string; type: string }

/**
 * Die Chats, die der Bot selbst kennt – zur Auswahl statt zum Abtippen.
 *
 * `getUpdates` liefert, wer dem Bot zuletzt geschrieben hat. Wer ihm /start
 * geschickt oder ihn in eine Gruppe geholt hat, steht darin. Das erspart den
 * Umweg über einen fremden Bot, der dieselbe Auskunft gibt und dafür ein
 * Kanal-Abo verlangt.
 */
function ChatSuche({
  kanal,
  instanzId,
  entwurf,
  onWaehlen,
}: {
  kanal: ChannelKind
  /** Gespeicherte Instanz - beim Einrichten gibt es die noch nicht. */
  instanzId?: number
  /** Dann kommt das Token aus dem Formular. */
  entwurf?: Entwurf
  onWaehlen: (chatId: string, name: string) => void
}) {
  const { t } = useTranslation()
  const [gefunden, setGefunden] = useState<GefundenerChat[] | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)

  const suchen = useMutation({
    mutationFn: () =>
      instanzId
        ? api.post<GefundenerChat[]>(
            `/api/settings/channels/${kanal}/targets/${instanzId}/chats`,
            {},
          )
        : api.post<GefundenerChat[]>(`/api/settings/channels/${kanal}/chats`, entwurf ?? {}),
    onMutate: () => {
      setFehler(null)
      setGefunden(null)
    },
    onSuccess: setGefunden,
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          variant="ghost"
          onClick={() => suchen.mutate()}
          loading={suchen.isPending}
        >
          {t('channels.findChats')}
        </Button>
        {gefunden?.length === 0 && (
          <span className="text-sm text-mist-600">{t('channels.noChats')}</span>
        )}
        {fehler && <span className="text-sm text-bad-500">{fehler}</span>}
      </div>

      {gefunden && gefunden.length > 0 && (
        <div className="flex flex-col gap-2">
          {gefunden.map((chat) => (
            <button
              key={chat.chat_id}
              type="button"
              onClick={() => onWaehlen(chat.chat_id, chat.name)}
              className={
                'flex items-center justify-between gap-3 rounded-xl border border-ink-700 ' +
                'bg-ink-900/60 px-4 py-2.5 text-left transition-colors ' +
                'hover:border-accent-500/50 hover:text-accent-400'
              }
            >
              <span className="text-sm font-medium text-mist-100">{chat.name}</span>
              <span className="font-mono text-xs text-mist-600">{chat.chat_id}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

type MeldungenProps = {
  auswahl: Record<string, ChannelLevel>
  onUmstellen: (patch: Record<string, ChannelLevel | undefined>) => void
  geaendert: boolean
  verbindungBelegt: boolean
  laeuft: boolean
  onSpeichern: () => void
  onAbbrechen: () => void
  fehler: string | null
  /** Untereinander statt nebeneinander? Dann braucht es eine Trennlinie. */
  abgetrennt?: boolean
}

/**
 * Worüber dieses Postfach informiert – und die Knöpfe für das Ganze.
 *
 * Die Haken sind immer bedienbar; ob gespeichert werden darf, entscheidet der
 * Knopf. Zu sperren, was man ohnehin gleich einträgt, wäre nur im Weg.
 */
function Meldungen({
  auswahl,
  onUmstellen,
  geaendert,
  verbindungBelegt,
  laeuft,
  onSpeichern,
  onAbbrechen,
  fehler,
  abgetrennt = false,
}: MeldungenProps) {
  const { t } = useTranslation()

  return (
    <div className={'flex flex-col gap-4' + (abgetrennt ? ' border-t border-ink-700 pt-6' : '')}>
      <div>
        <h2 className="text-lg font-semibold">{t('channels.eventsSection')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('channels.eventsIntro')}</p>
      </div>

      <div className="flex flex-col gap-4">
        {GRUPPEN.map((gruppe) => (
          <div key={gruppe} className="flex flex-col gap-3">
            <p className="text-[11px] font-medium tracking-wide text-mist-600 uppercase">
              {t(`channels.gruppe_${gruppe}`)}
            </p>
            {EREIGNISSE.filter((ereignis) => ereignis.gruppe === gruppe).map((ereignis) => {
              const stufe = auswahl[ereignis.key]
              const an = stufe !== undefined

              return (
                <div
                  key={ereignis.key}
                  className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3"
                >
                  <label className="flex cursor-pointer items-start gap-3">
                    <input
                      type="checkbox"
                      checked={an}
                      onChange={(event) =>
                        onUmstellen({
                          [ereignis.key]: event.target.checked ? ereignis.standard : undefined,
                        })
                      }
                      className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
                    />
                    <span>
                      <span className="text-sm font-medium text-mist-100">
                        {t(ereignis.labelKey)}
                      </span>
                      <span className="mt-0.5 block text-xs leading-relaxed text-mist-600">
                        {t(ereignis.hintKey)}
                      </span>
                    </span>
                  </label>

                  {/* Die Dringlichkeit gehört zur Meldung, nicht zum Postfach: eine
                  wartende Freigabe darf das Handy wecken, eine Rückmeldung zur
                  Bildqualität nicht. */}
                  <div className="pl-7">
                    <Auswahl
                      label={t('channels.level')}
                      value={stufe ?? ereignis.standard}
                      disabled={!an}
                      onChange={(wert) => onUmstellen({ [ereignis.key]: wert as ChannelLevel })}
                      optionen={STUFEN.map((wert) => ({
                        value: wert,
                        label: t(`channels.level_${wert}`),
                      }))}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          onClick={onSpeichern}
          loading={laeuft}
          disabled={!geaendert || !verbindungBelegt}
        >
          {t('common.save')}
        </Button>
        <Button type="button" variant="ghost" onClick={onAbbrechen}>
          {t('common.cancel')}
        </Button>
        {!verbindungBelegt && (
          <span className="text-sm text-mist-600">{t('channels.verifyFirst')}</span>
        )}
        {verbindungBelegt && geaendert && !laeuft && (
          <span className="text-sm text-mist-600">{t('common.unsaved')}</span>
        )}
      </div>

      {fehler && <ErrorBanner message={fehler} />}

      <p className="text-xs text-mist-600">{t('channels.sharedHint')}</p>
    </div>
  )
}

type AuswahlProps = {
  label: string
  value: string
  onChange: (wert: string) => void
  optionen: { value: string; label: string }[]
  hint?: string
  disabled?: boolean
}

/** Auswahlliste im Stil der übrigen Formularfelder. */
function Auswahl({ label, value, onChange, optionen, hint, disabled }: AuswahlProps) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-sm font-medium text-mist-300">{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-60"
      >
        {optionen.map((eintrag) => (
          <option key={eintrag.value} value={eintrag.value}>
            {eintrag.label}
          </option>
        ))}
      </select>
      {hint && <span className="text-xs text-mist-500">{hint}</span>}
    </label>
  )
}
