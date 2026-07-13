import React from 'react'
import { cn } from '../lib/cn'

/* ============================================================
 * Card
 * ============================================================ */
export function Card({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'rounded-xl border border-ink-700 bg-ink-850 p-4 shadow-[0_12px_35px_rgba(0,0,0,0.12)]',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
}

export function CardHeader({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('mb-3 flex items-center justify-between', className)} {...props}>
      {children}
    </div>
  )
}

export function CardTitle({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3 className={cn('text-sm font-medium text-gray-200', className)} {...props}>
      {children}
    </h3>
  )
}

/* ============================================================
 * Button
 * ============================================================ */
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md' | 'lg'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
}

const buttonVariants: Record<ButtonVariant, string> = {
  primary: 'bg-emerald-300 text-emerald-950 hover:bg-emerald-200 border border-transparent shadow-sm',
  secondary:
    'bg-ink-800 text-gray-200 hover:bg-ink-700 border border-ink-600',
  ghost: 'bg-transparent text-gray-300 hover:bg-ink-700 border border-transparent',
  danger: 'bg-red-600/80 text-white hover:bg-red-600 border border-transparent',
}

const buttonSizes: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-xs',
  md: 'h-9 px-4 text-sm',
  lg: 'h-11 px-5 text-sm',
}

export function Button({
  variant = 'secondary',
  size = 'md',
  className,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/50 focus-visible:ring-offset-1 focus-visible:ring-offset-ink-950',
        'disabled:cursor-not-allowed disabled:opacity-50',
        buttonVariants[variant],
        buttonSizes[size],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}

/* ============================================================
 * Input
 * ============================================================ */
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return (
      <input
        ref={ref}
        className={cn(
          'h-9 w-full rounded-lg border border-ink-600 bg-ink-950 px-3 text-sm text-gray-200',
          'placeholder:text-gray-500',
          'focus:outline-none focus:border-emerald-400/50 focus:ring-2 focus:ring-emerald-400/10',
          className,
        )}
        {...props}
      />
    )
  },
)

/* ============================================================
 * TextArea
 * ============================================================ */
export const TextArea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(function TextArea({ className, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      className={cn(
        'w-full rounded-lg border border-ink-600 bg-ink-950 px-3 py-2 text-sm leading-6 text-gray-200',
        'placeholder:text-gray-500 resize-none',
        'focus:outline-none focus:border-emerald-400/50 focus:ring-2 focus:ring-emerald-400/10',
        className,
      )}
      {...props}
    />
  )
})

/* ============================================================
 * ProgressBar
 * ============================================================ */
interface ProgressBarProps {
  value: number
  max?: number
  className?: string
}

export function ProgressBar({ value, max = 100, className }: ProgressBarProps) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100))
  return (
    <div className={cn('h-1.5 w-full overflow-hidden rounded-full bg-ink-700', className)}>
      <div
        className="h-full rounded-full bg-gradient-to-r from-gold-500 to-emerald-400 transition-all"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

/* ============================================================
 * Tabs
 * ============================================================ */
interface TabItem {
  key: string
  label: React.ReactNode
}

interface TabsProps {
  items: TabItem[]
  active: string
  onChange: (key: string) => void
  className?: string
}

export function Tabs({ items, active, onChange, className }: TabsProps) {
  return (
    <div className={cn('flex gap-1 border-b border-ink-700', className)}>
      {items.map((item) => (
        <button
          key={item.key}
          onClick={() => onChange(item.key)}
          className={cn(
            'relative px-3 py-2 text-sm transition-colors',
            active === item.key
              ? 'text-emerald-300'
              : 'text-gray-400 hover:text-gray-200',
          )}
        >
          {item.label}
          {active === item.key && (
            <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-emerald-400" />
          )}
        </button>
      ))}
    </div>
  )
}
