/** Wiederverwendbare Grundbausteine im Nexview-Look. */

import type { ComponentPropsWithRef, InputHTMLAttributes, ReactNode } from 'react'
import { useId } from 'react'

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
