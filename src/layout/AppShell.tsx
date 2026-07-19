import React from 'react'
import { cn } from '../lib/cn'

interface AppShellProps {
  children: React.ReactNode
  className?: string
}

/**
 * 主布局框架 —— 全屏 flex 容器，深色书房基底
 */
export function AppShell({ children, className }: AppShellProps) {
  return (
    <div
      className={cn(
        'flex h-screen h-[100dvh] w-full flex-col overflow-hidden bg-ink-950 text-gray-200',
        className,
      )}
    >
      {children}
    </div>
  )
}

interface AppShellBodyProps {
  children: React.ReactNode
  className?: string
}

/** 主体区域：垂直占满剩余空间 */
export function AppShellBody({ children, className }: AppShellBodyProps) {
  return <div className={cn('flex min-h-0 flex-1', className)}>{children}</div>
}
