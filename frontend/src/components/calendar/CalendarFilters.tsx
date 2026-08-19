import { useTranslation } from 'react-i18next'

export type CalendarQuelle = 'all' | 'mine'
export type CalendarDatumsart = 'digital' | 'kino'
export type CalendarSchaerfe = 'studios' | 'known' | 'none'

type Props = {
  quelle: CalendarQuelle
  datumsart: CalendarDatumsart
  schaerfe: CalendarSchaerfe
  onQuelle: (wert: CalendarQuelle) => void
  onDatumsart: (wert: CalendarDatumsart) => void
  onSchaerfe: (wert: CalendarSchaerfe) => void
}

/**
 * Eine Pille - der Filterknopf, den die Anfragen-Seiten schon benutzen.
 *
 * Bewusst kein Auswahlfeld: Jede dieser Fragen hat zwei oder drei Antworten,
 * und die stehen alle nebeneinander lesbar da, statt sich hinter einem Klick
 * zu verstecken.
 */
function Pille({
  aktiv,
  onClick,
  disabled,
  title,
  children,
}: {
  aktiv: boolean
  onClick: () => void
  disabled?: boolean
  title?: string
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-pressed={aktiv}
      className={
        'rounded-full border px-3.5 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40 ' +
        (aktiv
          ? 'border-accent-500 bg-accent-500/15 text-accent-400'
          : 'border-ink-700 text-mist-400 hover:border-ink-600 hover:text-mist-200')
      }
    >
      {children}
    </button>
  )
}

function Gruppe({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-medium tracking-wide text-mist-600 uppercase">
        {label}
      </span>
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  )
}

export function CalendarFilters({
  quelle,
  datumsart,
  schaerfe,
  onQuelle,
  onDatumsart,
  onSchaerfe,
}: Props) {
  const { t } = useTranslation()
  // Der Rauschfilter wirkt nur auf die Neuerscheinungen. Wer nur den eigenen
  // Bestand sehen will, hat nichts zu filtern - dann bleibt er sichtbar, aber
  // stillgelegt, statt ohne Erklärung zu verschwinden.
  const rauschenAus = quelle === 'mine'

  return (
    <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
      <Gruppe label={t('calendar.filterSource')}>
        <Pille aktiv={quelle === 'all'} onClick={() => onQuelle('all')}>
          {t('calendar.sourceAll')}
        </Pille>
        <Pille
          aktiv={quelle === 'mine'}
          onClick={() => onQuelle('mine')}
          title={t('calendar.sourceMineHint')}
        >
          {t('calendar.sourceOnlyMine')}
        </Pille>
      </Gruppe>

      <Gruppe label={t('calendar.filterDateType')}>
        <Pille aktiv={datumsart === 'digital'} onClick={() => onDatumsart('digital')}>
          {t('calendar.dateDigital')}
        </Pille>
        <Pille aktiv={datumsart === 'kino'} onClick={() => onDatumsart('kino')}>
          {t('calendar.dateCinema')}
        </Pille>
      </Gruppe>

      <Gruppe label={t('calendar.filterNoise')}>
        <Pille
          aktiv={schaerfe === 'studios'}
          disabled={rauschenAus}
          title={rauschenAus ? t('calendar.noiseOnlyNew') : undefined}
          onClick={() => onSchaerfe('studios')}
        >
          {t('calendar.noiseStudios')}
        </Pille>
        <Pille
          aktiv={schaerfe === 'known'}
          disabled={rauschenAus}
          title={rauschenAus ? t('calendar.noiseOnlyNew') : undefined}
          onClick={() => onSchaerfe('known')}
        >
          {t('calendar.noiseKnown')}
        </Pille>
        <Pille
          aktiv={schaerfe === 'none'}
          disabled={rauschenAus}
          title={rauschenAus ? t('calendar.noiseOnlyNew') : undefined}
          onClick={() => onSchaerfe('none')}
        >
          {t('calendar.noiseAll')}
        </Pille>
      </Gruppe>
    </div>
  )
}
