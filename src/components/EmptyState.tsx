import React from 'react'
import { cn } from '../lib/cn'

interface EmptyStateProps {
  icon?: React.ReactNode
  title: string
  description?: string
  action?: React.ReactNode
  className?: string
}

export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center px-6 py-16 text-center',
        className,
      )}
    >
      {icon && (
        <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-ink-800 text-gray-500">
          {icon}
        </div>
      )}
      <h3 className="text-base font-medium text-gray-300">{title}</h3>
      {description && (
        <p className="mt-2 max-w-sm break-words text-sm text-gray-500">{description}</p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}
