import { useTranslation } from 'react-i18next'

export const SETUP_STEPS = ['account', 'avatar', 'tmdb', 'radarr', 'sonarr', 'address', 'mail', 'done'] as const
export type SetupStep = (typeof SETUP_STEPS)[number]

/** Fortschrittsanzeige über den Schritten des Assistenten. */
export function StepIndicator({ current }: { current: SetupStep }) {
  const { t } = useTranslation()
  const steps = SETUP_STEPS.filter((step) => step !== 'done')
  const currentIndex = current === 'done' ? steps.length : steps.indexOf(current)

  return (
    <ol className="mb-6 flex items-center gap-2" aria-label={t('setup.progress')}>
      {steps.map((step, index) => {
        const done = index < currentIndex
        const active = index === currentIndex
        return (
          <li key={step} className="flex flex-1 flex-col gap-1.5">
            <span
              className={
                'h-1 rounded-full transition-colors ' +
                (done ? 'bg-accent-600' : active ? 'bg-accent-500' : 'bg-ink-700')
              }
            />
            <span
              className={
                'text-[11px] font-medium ' + (done || active ? 'text-mist-300' : 'text-mist-600')
              }
            >
              {t(`setup.step.${step}`)}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
