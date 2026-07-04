import React from 'react'
import { cn } from '../lib/cn'

export type BadgeVariant =
  | 'gray'
  | 'blue'
  | 'green'
  | 'amber'
  | 'red'
  | 'gold'
  | 'outline'

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant
}

const variantStyles: Record<BadgeVariant, string> = {
  gray: 'bg-gray-600/20 text-gray-300 border-gray-500/30',
  blue: 'bg-blue-600/20 text-blue-300 border-blue-500/30',
  green: 'bg-green-600/20 text-green-300 border-green-500/30',
  amber: 'bg-amber-600/20 text-amber-300 border-amber-500/30',
  red: 'bg-red-600/20 text-red-300 border-red-500/30',
  gold: 'bg-gold-500/15 text-gold-400 border-gold-500/30',
  outline: 'bg-transparent text-gray-400 border-ink-600',
}

export function Badge({ variant = 'gray', className, children, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium',
        variantStyles[variant],
        className,
      )}
      {...props}
    >
      {children}
    </span>
  )
}
