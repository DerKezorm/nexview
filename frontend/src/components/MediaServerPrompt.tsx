/**
 * Der Hinweis während einer Anmeldung beim Media-Server.
 *
 * Zeigt den Code und einen normalen Link an. Das ist kein Beiwerk: Browser
 * blockieren das Popup häufig, am Handy praktisch immer. Ohne diese Anzeige
 * bliebe der Ablauf dort stecken, ohne dass jemand sähe, warum.
 */

import { useTranslation } from 'react-i18next'

import { Button } from './ui'
import type { ChallengeStart } from '../lib/useMediaServerChallenge'

export function MediaServerPrompt({
  start,
  onAbbrechen,
}: {
  start: ChallengeStart
  onAbbrechen: () => void
}) {
  const { t } = useTranslation()

  return (
    <div className="mt-4 rounded-xl border border-ink-700 bg-ink-900 px-4 py-4 text-sm">
      <p className="text-mist-300">{t('mediaserver.waiting')}</p>

      <p className="mt-3 text-xs text-mist-500">{t('mediaserver.codeHint')}</p>
      <p className="mt-1 font-mono text-xl font-semibold tracking-[0.3em] text-mist-200">
        {start.code}
      </p>

      <a
        href={start.auth_url}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-3 inline-block text-xs text-accent-400 underline-offset-2 hover:underline"
      >
        {t('mediaserver.openManually')}
      </a>

      <Button variant="ghost" onClick={onAbbrechen} className="mt-4 w-full">
        {t('common.cancel')}
      </Button>
    </div>
  )
}
