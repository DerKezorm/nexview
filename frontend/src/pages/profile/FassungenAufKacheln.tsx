import { useTranslation } from 'react-i18next'

import type { KachelWahl } from '../../lib/fassungen'

/**
 * Welche weiteren Fassungen Kacheln und Listenzeilen zeigen (Issue #13).
 *
 * Ab Werk keine: Eine Kachel zeigt den Zustand der Hauptfassung, sonst nichts.
 * Wer 4K oder eine andere Fassung im Blick behalten will, hakt sie hier an.
 * Darunter die zweite Frage, wann: nur wenn dort etwas angefragt ist oder
 * vorliegt (ab Werk), oder immer, auch „noch nicht angefragt".
 *
 * Ein Haken steht für einen **Namen**, nicht für eine Kennung: „4K" der
 * Filme und „4K" der Serien sind ein Haken (`kachelWahl`).
 *
 * Gespeichert wird mit dem einen Knopf der Kontoseite, wie die Darstellung
 * daneben.
 */
export function FassungenAufKacheln({
  wahl,
  hauptName,
  gewaehlt,
  setGewaehlt,
  immer,
  setImmer,
  disabled = false,
}: {
  wahl: KachelWahl[]
  /** Name der Hauptfassung, wenn er für beide Medienarten gleich ist. */
  hauptName: string | null
  gewaehlt: string[]
  setGewaehlt: (kennungen: string[]) => void
  immer: boolean
  setImmer: (wert: boolean) => void
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const an = (zeile: KachelWahl) => zeile.kennungen.every((k) => gewaehlt.includes(k))
  const keine = !wahl.some(an)

  function umschalten(zeile: KachelWahl, wert: boolean) {
    // Kennungen, die hier nicht zur Wahl stehen, bleiben stehen: Eine Fassung,
    // die gerade fehlt, hat ihren Haken wieder, sobald sie zurück ist.
    const ohne = gewaehlt.filter((k) => !zeile.kennungen.includes(k))
    setGewaehlt(wert ? [...ohne, ...zeile.kennungen] : ohne)
  }

  const zeilenKlasse =
    'flex items-start gap-3 rounded-xl border border-ink-700 px-4 py-3 transition-colors '

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <h2 className="text-lg font-semibold">{t('profile.cardVersionsTitle')}</h2>
        <p className="text-xs leading-relaxed text-mist-600">{t('profile.cardVersionsHint')}</p>
      </div>

      <p className="rounded-xl border border-dashed border-ink-700 px-4 py-3 text-xs leading-relaxed text-mist-500">
        {hauptName
          ? t('profile.cardVersionsMainNamed', { name: hauptName })
          : t('profile.cardVersionsMain')}
      </p>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-xs font-semibold tracking-wide text-mist-400 uppercase">
          {t('profile.cardVersionsOthers')}
        </legend>
        {wahl.map((zeile) => (
          <label key={zeile.name} className={zeilenKlasse + 'cursor-pointer hover:bg-ink-850'}>
            <input
              type="checkbox"
              checked={an(zeile)}
              disabled={disabled}
              onChange={(event) => umschalten(zeile, event.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
            />
            <span className="text-sm font-medium text-mist-100">{zeile.name}</span>
          </label>
        ))}
      </fieldset>

      <fieldset className="flex flex-col gap-2" disabled={disabled || keine}>
        <legend className="mb-1 text-xs font-semibold tracking-wide text-mist-400 uppercase">
          {t('profile.cardVersionsWhen')}
        </legend>
        {[
          { wert: false, titel: 'profile.cardVersionsAvailable', hint: 'profile.cardVersionsAvailableHint' },
          { wert: true, titel: 'profile.cardVersionsAlways', hint: 'profile.cardVersionsAlwaysHint' },
        ].map((o) => (
          <label
            key={String(o.wert)}
            className={zeilenKlasse + (keine ? 'opacity-50' : 'cursor-pointer hover:bg-ink-850')}
          >
            <input
              type="radio"
              name="card-versions-when"
              checked={immer === o.wert}
              onChange={() => setImmer(o.wert)}
              className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
            />
            <span>
              <span className="text-sm font-medium text-mist-100">{t(o.titel)}</span>
              <span className="mt-0.5 block text-xs leading-relaxed text-mist-600">{t(o.hint)}</span>
            </span>
          </label>
        ))}
      </fieldset>
    </div>
  )
}
