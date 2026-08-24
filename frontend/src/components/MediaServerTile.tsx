/**
 * Eine Kachel je Medienserver – Plex, Jellyfin, Emby.
 *
 * **Die Kacheln sind die Anbieter, nicht ihre Instanzen.** Das unterscheidet
 * diese Seite von den Benachrichtigungen, wo dieselbe Form für *mehrere*
 * ntfy-Postfächer steht: Von jeder Art Medienserver gibt es genau einen, also
 * drei feste Plätze und keine „+ anlegen"-Kachel.
 *
 * Der Sinn ist der Blick: Man sieht den Zustand aller drei auf einmal, statt
 * sich durch einen Reiter zu klicken, der nur von einem erzählt.
 *
 * Ein Anbieter, den diese Fassung noch nicht kann, steht trotzdem da – blass
 * und nicht anklickbar. Ihn wegzulassen hieße zu verschweigen, dass er kommt;
 * ihn anklickbar zu machen hieße, eine Kachel anzubieten, die nichts tut.
 *
 * ⚠️ **Form und Maße sind bewusst dieselben wie bei den Benachrichtigungen**
 * (`AdminChannelSettings`): gleiche Höhe, gleiche Rundung, ausgewählt mit
 * getöntem Grund statt dünnem Ring, Aktionen als runde Symbolknöpfe unten
 * rechts. Vorher war diese eine Seite anders als der Rest – ein grauer
 * Textlink „Server trennen", den man übersah, und eine Auswahl, die man kaum
 * sah. Zwei Muster für dieselbe Sache sind kein Stil, sondern ein Versehen.
 */

import { useTranslation } from 'react-i18next'

import { MediaServerLogo } from './MediaServerLogo'
import { RundKnopf } from './ui'
import { providerName } from '../lib/mediaserver'

export type TileState = {
  provider: string
  /** Kennt diese Fassung den Anbieter überhaupt? Sonst ausgegraut. */
  available: boolean
  connected: boolean
  /** Name des Servers – „Bizzy" sagt mehr als eine Adresse. */
  serverName?: string | null
  /** Mit welchem Konto Nexview dort angemeldet ist. */
  account?: string | null
  /** Adresse des Servers – stand vorher in einer eigenen Leiste darunter. */
  url?: string | null
}

export function MediaServerTile({
  state,
  selected = false,
  onOpen,
  onDisconnect,
}: {
  state: TileState
  selected?: boolean
  onOpen: () => void
  onDisconnect?: () => void
}) {
  const { t } = useTranslation()
  const name = providerName(state.provider)

  if (!state.available) {
    return (
      <div
        className="flex min-h-28 flex-col justify-between rounded-2xl border border-dashed border-ink-700 bg-ink-950/40 px-4 py-3 opacity-45"
        aria-disabled="true"
      >
        <span className="flex items-center gap-2 text-lg font-semibold text-mist-400">
          <MediaServerLogo provider={state.provider} className="h-5 w-5" />
          {name}
        </span>
        <span className="text-sm text-mist-600">{t('mediaserver.tileSoon')}</span>
        <span className="text-xs text-mist-700">{t('mediaserver.tileSoonHint')}</span>
      </div>
    )
  }

  return (
    /* Die ganze Fläche öffnet die Einstellungen – wie bei den
       Benachrichtigungen. Bewusst ein `div` mit `role="button"` und nicht ein
       `<button>`: Darin sitzen die runden Symbolknöpfe, und ein Knopf im Knopf
       ist ungültiges HTML. `RundKnopf` hält den Klick selbst auf. */
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onOpen()
        }
      }}
      className={
        'flex min-h-28 cursor-pointer flex-col justify-between rounded-2xl border ' +
        'px-4 py-3 transition-colors focus-visible:outline-none ' +
        (selected
          ? 'border-accent-500/60 bg-accent-500/10'
          : 'border-ink-700 bg-ink-900 hover:border-ink-600')
      }
    >
      <div>
        <p className="flex items-center gap-2 text-lg font-semibold text-mist-100">
          <MediaServerLogo
            provider={state.provider}
            className={'h-5 w-5 ' + (state.connected ? 'text-ok-500' : 'text-mist-700')}
          />
          {name}
        </p>

        {state.connected ? (
          <>
            <p className="mt-0.5 text-sm text-ok-500">
              {t('mediaserver.tileConnected')}
              {state.serverName ? ` · ${state.serverName}` : ''}
            </p>
            {state.account && (
              <p className="text-xs text-mist-600">
                {t('mediaserver.tileAs', { name: state.account })}
              </p>
            )}
            {/* Die Adresse stand vorher in einer eigenen Leiste unter der
                Kachel – und wiederholte dabei Name und Zustand, die schon
                hier stehen. Neu war nur sie selbst. */}
            {state.url && (
              <p className="mt-0.5 truncate text-xs text-mist-700" title={state.url}>
                {state.url}
              </p>
            )}
          </>
        ) : (
          <p className="mt-0.5 text-sm text-mist-600">
            {t('mediaserver.tileNotConnected')}
          </p>
        )}
      </div>

      <div className="mt-3 flex items-center justify-end gap-2">
        <RundKnopf label={t('common.edit')} onClick={onOpen}>
          <path d="M4 20h4L18.5 9.5a2.1 2.1 0 0 0-3-3L5 17v3Z" strokeLinejoin="round" />
        </RundKnopf>
        {state.connected && onDisconnect && (
          <RundKnopf label={t('mediaserver.disconnectServer')} onClick={onDisconnect} gefahr>
            {/* Ausgesteckter Stecker – das Gegenstück zum Verbinden. */}
            <path
              d="M9 5V2m6 3V2M7 5h10v5a5 5 0 0 1-10 0V5Zm5 10v4M4 4l16 16"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </RundKnopf>
        )}
      </div>
    </div>
  )
}
