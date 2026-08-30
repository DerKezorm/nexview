/** Wiederverwendbare Grundbausteine im Nexview-Look. */

import type { ComponentPropsWithRef, InputHTMLAttributes, ReactNode } from 'react'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'

/**
 * Der Stil eines Auswahlfelds - einmal, fuer alle.
 *
 * ⚠️ **Steht hier, weil dieselbe Zeile neunmal in der Anwendung stand.** Genau
 * daran krankte vorher schon der Rahmen der Einstellungsblöcke: Kopien laufen
 * auseinander, sobald jemand eine davon anfasst, und niemand kann hinterher
 * sagen, welche die richtige war. Neue Auswahlfelder nehmen diese Konstante;
 * die verbliebenen Kopien wandern nach, wenn ihre Datei ohnehin drankommt.
 */
export const AUSWAHL =
  'rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-50'


// ComponentPropsWithRef statt ButtonHTMLAttributes: so lässt sich der Knopf
// von außen ansprechen (z. B. um ihn in einem Dialog zu fokussieren).
type ButtonProps = ComponentPropsWithRef<'button'> & {
  variant?: 'primary' | 'ghost'
  loading?: boolean
}

export function Button({
  variant = 'primary',
  loading = false,
  className = '',
  children,
  disabled,
  ...rest
}: ButtonProps) {
  const base =
    'inline-flex items-center justify-center gap-2 rounded-full px-5 py-2.5 text-sm font-semibold ' +
    'transition-colors disabled:cursor-not-allowed disabled:opacity-60'
  const styles =
    variant === 'primary'
      ? 'bg-accent-500 text-white hover:bg-accent-400 shadow-lg shadow-accent-700/25'
      : 'border border-ink-700 bg-ink-850 text-mist-300 hover:bg-ink-800 hover:text-mist-100'

  return (
    <button className={`${base} ${styles} ${className}`} disabled={disabled || loading} {...rest}>
      {loading && <Spinner />}
      {children}
    </button>
  )
}

export function Spinner({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg className={`${className} animate-spin`} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" fill="none" opacity=".25" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" />
    </svg>
  )
}

/**
 * Der Platzhalter, während eine nachgelieferte Seite eintrifft.
 *
 * ⚠️ **Bewusst zurückhaltend.** Seiten, die ein Nutzer selten öffnet, sind
 * nicht mehr im ersten Laden dabei; sie kommen beim Klick nach. Das dauert
 * Bruchteile einer Sekunde - vom eigenen Server, meist schon im Zwischenspeicher
 * des Browsers. Ein großes Ladebild wäre in dieser Zeit selbst das Auffälligste
 * am Vorgang und ließe die Anwendung langsamer wirken, als sie ist.
 *
 * `role="status"` ist kein Beiwerk: Für ein Vorleseprogramm ist ein Wechsel
 * ohne Ansage sonst einfach Stille.
 */
export function SeiteLaedt() {
  const { t } = useTranslation()
  return (
    <div
      className="flex min-h-[40vh] items-center justify-center gap-2 text-sm text-mist-600"
      role="status"
      aria-live="polite"
    >
      <Spinner />
      <span>{t('common.loading')}</span>
    </div>
  )
}

type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string
  hint?: ReactNode
}

export function Field({ label, hint, className = '', ...rest }: FieldProps) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-mist-300">
        {label}
      </label>
      <input
        id={id}
        aria-describedby={hintId}
        className={
          'rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 ' +
          'placeholder:text-mist-600 transition-colors focus:border-accent-500 focus:outline-none ' +
          className
        }
        {...rest}
      />
      {hint && (
        <p id={hintId} className="text-xs text-mist-500">
          {hint}
        </p>
      )}
    </div>
  )
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="rounded-xl border border-accent-600/50 bg-accent-700/15 px-4 py-3 text-sm text-accent-400"
    >
      {message}
    </p>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={
        'rounded-2xl border border-ink-700 bg-ink-850/80 p-6 shadow-2xl shadow-black/40 backdrop-blur ' +
        className
      }
    >
      {children}
    </div>
  )
}

/**
 * Die gestrichelte „+"-Kachel am Ende eines Kachelrasters.
 *
 * Steht dort, wo das nächste Ding hinkäme - damit ist ohne Beschriftung klar,
 * was sie anlegt. Wird von den Benachrichtigungs-Zielen und den Kinderkonten
 * benutzt; beide sollen gleich aussehen, deshalb steht sie hier und nicht
 * zweimal.
 */
export function PlusKachel({
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

/**
 * Der runde Symbolknopf in der Fußleiste einer Kachel.
 *
 * `stopPropagation` ist Pflicht: Die Kachel selbst ist anklickbar, sonst
 * öffnete der Papierkorb nebenbei auch noch das Bearbeiten-Feld.
 */
export function RundKnopf({
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

/**
 * Ein abgegrenzter Einstellungsbereich - Überschrift plus Inhalt in einer Karte.
 *
 * ⚠️ **Warum das hier steht und nicht mehr in einer einzelnen Seite.** Diese
 * Form gab es nur in den Dienste-Einstellungen, lokal definiert. Alle anderen
 * Seiten bauten ihre Bereiche selbst — mal mit Karte, mal ohne, mal mit
 * Überschrift darüber statt darin. Das Ergebnis: Beim Wechseln zwischen den
 * Menüpunkten sah jede Seite anders aus, und man konnte den Bereich, in dem
 * man wirklich etwas einstellt, nicht mehr auf einen Blick erkennen.
 *
 * `breit` für Inhalte, die die volle Breite brauchen — Tabellen etwa. Ohne
 * das bleibt der Text bei ``max-w-3xl``, weil sehr lange Zeilen sich schlecht
 * lesen.
 */
export function Section({
  title,
  breit = false,
  children,
  className = '',
}: {
  title: string
  breit?: boolean
  children: ReactNode
  className?: string
}) {
  return (
    <Card className={'flex flex-col gap-4 ' + className}>
      <h2 className="text-lg font-semibold">{title}</h2>
      <div className={'flex flex-col gap-4' + (breit ? '' : ' max-w-3xl')}>{children}</div>
    </Card>
  )
}

/**
 * Eine große Zahl mit Beschriftung.
 *
 * ⚠️ **Warum das hier steht und nicht mehr in der Statistik-Seite.** Sie war
 * dort lokal definiert (`KeyFigure`) und damit für niemanden sonst zu haben.
 * Das Admin-Dashboard braucht genau dieselbe Kachel — und eine zweite,
 * nachgebaute Fassung wäre nach dem ersten Feinschliff eine andere: minimal
 * andere Polsterung, minimal andere Schrift, und beim Wechsel zwischen den
 * Seiten wirkt es unruhig, ohne dass man den Grund benennen könnte.
 *
 * `ton` färbt nur ein, es ist keine Schwere-Angabe: Eine Zahl ist kein Befund.
 */
export function Kennzahl({
  label,
  wert,
  hinweis,
  ton = 'normal',
}: {
  label: string
  wert: string
  hinweis?: string
  ton?: 'normal' | 'warn'
}) {
  return (
    <div
      className={
        'rounded-2xl border px-4 py-3 ' +
        (ton === 'warn' ? 'border-bad-500/50 bg-bad-500/10' : 'border-ink-700 bg-ink-850/60')
      }
    >
      <p className="text-xs font-medium tracking-wide text-mist-600 uppercase">{label}</p>
      <p
        className={
          'mt-1 text-3xl font-bold tabular-nums ' +
          (ton === 'warn' ? 'text-bad-500' : 'text-mist-100')
        }
      >
        {wert}
      </p>
      {hinweis && <p className="mt-0.5 text-xs text-mist-600">{hinweis}</p>}
    </div>
  )
}
