import { useId } from 'react'
import { useTranslation } from 'react-i18next'

import type { Genre } from '../../api/types'

export type Period = '7' | '30' | '90' | '365' | 'upcoming'
export type SortOption = 'newest' | 'rating' | 'popular'
export type ViewMode = 'grid' | 'list'

export type Filters = {
  period: Period
  language: string
  region: string
  genreId: number | null
  sort: SortOption
  /** Titel ausblenden, die schon in Radarr/Sonarr stehen. */
  hideExisting: boolean
  /** Kurzfilme ausblenden (nur bei Filmen sinnvoll). */
  featureLength: boolean
  /** Mindestbewertung; 0 = egal. */
  minRating: number
  /** Titel ohne jede Bewertung ausblenden. */
  hideUnrated: boolean
  /** Nur Titel, die in der gewählten Region erschienen sind (nur Filme). */
  releasedInRegion: boolean
  /** Titel ohne Beschreibung ausblenden. */
  hideWithoutOverview: boolean
  /** Produktionsfirma (nur Filme); null = alle. */
  studioId: number | null
  /** Nur Titel mit einer Mindestzahl an Bewertungen – hält Kleinstproduktionen fern. */
  knownOnly: boolean
}

/** Auswahlliste der Originalsprachen - für einen Familien-Server ausreichend. */
export const LANGUAGE_OPTIONS = [
  { value: '', labelKey: 'filters.languageAll' },
  { value: 'de', label: 'Deutsch' },
  { value: 'en', label: 'English' },
  { value: 'fr', label: 'Français' },
  { value: 'es', label: 'Español' },
  { value: 'it', label: 'Italiano' },
  { value: 'ja', label: '日本語' },
  { value: 'ko', label: '한국어' },
] as const

export const REGION_OPTIONS = ['DE', 'AT', 'CH', 'GB', 'US', 'FR', 'IT', 'ES'] as const

const PERIODS: { value: Period; labelKey: string }[] = [
  { value: '7', labelKey: 'filters.period7' },
  { value: '30', labelKey: 'filters.period30' },
  { value: '90', labelKey: 'filters.period90' },
  { value: '365', labelKey: 'filters.period365' },
  { value: 'upcoming', labelKey: 'filters.periodUpcoming' },
]

/** Mindestbewertung zur Auswahl. 0 bedeutet "egal". */
export const RATING_STEPS = [0, 5, 6, 7, 8] as const

const SORTS: { value: SortOption; labelKey: string }[] = [
  { value: 'newest', labelKey: 'filters.sortNewest' },
  { value: 'rating', labelKey: 'filters.sortRating' },
  { value: 'popular', labelKey: 'filters.sortPopular' },
]

function Select({
  label,
  value,
  onChange,
  children,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  children: React.ReactNode
}) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-[11px] font-medium tracking-wide text-mist-600 uppercase">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-full border border-ink-700 bg-ink-900 px-3.5 py-1.5 text-sm text-mist-100 transition-colors focus:border-accent-500 focus:outline-none"
      >
        {children}
      </select>
    </div>
  )
}

/** Kompakter Ein/Aus-Schalter mit Beschriftung. */
function Toggle({
  label,
  checked,
  onChange,
  disabled = false,
  title,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  title?: string
}) {
  return (
    <label
      title={title}
      className={
        'flex cursor-pointer items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm ' +
        'transition-colors select-none ' +
        (disabled
          ? 'cursor-not-allowed border-ink-700 text-mist-600 opacity-60 '
          : checked
            ? 'border-accent-500/60 bg-accent-500/15 text-accent-400 '
            : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100 ')
      }
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="h-3.5 w-3.5 accent-accent-500"
      />
      {label}
    </label>
  )
}

type FilterBarProps = {
  filters: Filters
  onChange: (filters: Filters) => void
  genres: Genre[]
  view: ViewMode
  onViewChange: (view: ViewMode) => void
  /** Nur Filme haben eine sinnvolle Laufzeit-Grenze. */
  showFeatureLength: boolean
  /** Ohne Radarr/Sonarr lässt sich nichts ausblenden. */
  arrConfigured: boolean
  /** Studios gibt es nur bei Filmen. */
  studios: Genre[]
}

export function FilterBar({
  filters,
  onChange,
  genres,
  view,
  onViewChange,
  showFeatureLength,
  arrConfigured,
  studios,
}: FilterBarProps) {
  const { t } = useTranslation()
  const update = (patch: Partial<Filters>) => onChange({ ...filters, ...patch })

  return (
    <div className="flex flex-wrap items-end gap-3">
      <Select
        label={t('filters.period')}
        value={filters.period}
        onChange={(value) => update({ period: value as Period })}
      >
        {PERIODS.map((period) => (
          <option key={period.value} value={period.value}>
            {t(period.labelKey)}
          </option>
        ))}
      </Select>

      <Select
        label={t('filters.language')}
        value={filters.language}
        onChange={(value) => update({ language: value })}
      >
        {LANGUAGE_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {'labelKey' in option ? t(option.labelKey) : option.label}
          </option>
        ))}
      </Select>

      <Select
        label={t('filters.region')}
        value={filters.region}
        onChange={(value) => update({ region: value })}
      >
        {REGION_OPTIONS.map((region) => (
          <option key={region} value={region}>
            {region}
          </option>
        ))}
      </Select>

      <Select
        label={t('filters.genre')}
        value={filters.genreId === null ? '' : String(filters.genreId)}
        onChange={(value) => update({ genreId: value ? Number(value) : null })}
      >
        <option value="">{t('filters.genreAll')}</option>
        {genres.map((genre) => (
          <option key={genre.id} value={genre.id}>
            {genre.name}
          </option>
        ))}
      </Select>

      <Select
        label={t('filters.minRating')}
        value={String(filters.minRating)}
        onChange={(value) => update({ minRating: Number(value) })}
      >
        {RATING_STEPS.map((step) => (
          <option key={step} value={step}>
            {step === 0 ? t('filters.ratingAny') : t('filters.ratingFrom', { value: step })}
          </option>
        ))}
      </Select>

      {showFeatureLength && studios.length > 0 && (
        <Select
          label={t('filters.studio')}
          value={filters.studioId === null ? '' : String(filters.studioId)}
          onChange={(value) => update({ studioId: value ? Number(value) : null })}
        >
          <option value="">{t('filters.studioAll')}</option>
          {studios.map((studio) => (
            <option key={studio.id} value={studio.id}>
              {studio.name}
            </option>
          ))}
        </Select>
      )}

      <Select
        label={t('filters.sort')}
        value={filters.sort}
        onChange={(value) => update({ sort: value as SortOption })}
      >
        {SORTS.map((sort) => (
          <option key={sort.value} value={sort.value}>
            {t(sort.labelKey)}
          </option>
        ))}
      </Select>

      <div className="flex w-full flex-wrap items-center gap-2">
        <Toggle
          label={t('filters.knownOnly')}
          checked={filters.knownOnly}
          title={t('filters.knownOnlyHint')}
          onChange={(checked) => update({ knownOnly: checked })}
        />
        <Toggle
          label={t('filters.hideExisting')}
          checked={filters.hideExisting}
          disabled={!arrConfigured}
          title={arrConfigured ? undefined : t('filters.hideExistingUnavailable')}
          onChange={(checked) => update({ hideExisting: checked })}
        />
        {showFeatureLength && (
          <>
            <Toggle
              label={t('filters.featureLength')}
              checked={filters.featureLength}
              title={t('filters.featureLengthHint')}
              onChange={(checked) => update({ featureLength: checked })}
            />
            <Toggle
              label={t('filters.releasedInRegion', { region: filters.region })}
              checked={filters.releasedInRegion}
              title={t('filters.releasedInRegionHint')}
              onChange={(checked) => update({ releasedInRegion: checked })}
            />
          </>
        )}
        <Toggle
          label={t('filters.hideUnrated')}
          checked={filters.hideUnrated}
          onChange={(checked) => update({ hideUnrated: checked })}
        />
        <Toggle
          label={t('filters.hideWithoutOverview')}
          checked={filters.hideWithoutOverview}
          title={t('filters.hideWithoutOverviewHint')}
          onChange={(checked) => update({ hideWithoutOverview: checked })}
        />
      </div>

      <div className="ml-auto flex flex-col gap-1">
        <span className="text-[11px] font-medium tracking-wide text-mist-600 uppercase">
          {t('filters.view')}
        </span>
        <div
          className="flex rounded-full border border-ink-700 bg-ink-900 p-0.5"
          role="group"
          aria-label={t('filters.view')}
        >
          {(['grid', 'list'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => onViewChange(mode)}
              aria-pressed={view === mode}
              title={t(mode === 'grid' ? 'filters.viewGrid' : 'filters.viewList')}
              className={
                'rounded-full px-3 py-1 text-xs font-semibold transition-colors ' +
                (view === mode ? 'bg-accent-500 text-white' : 'text-mist-500 hover:text-mist-100')
              }
            >
              {t(mode === 'grid' ? 'filters.viewGrid' : 'filters.viewList')}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
