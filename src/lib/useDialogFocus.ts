import { useEffect, useRef, type RefObject } from 'react'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  'summary',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

let bodyLockDepth = 0
let bodyOverflowBeforeLock = ''

function lockBodyScroll() {
  if (bodyLockDepth === 0) {
    bodyOverflowBeforeLock = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  bodyLockDepth += 1
}

function unlockBodyScroll() {
  bodyLockDepth = Math.max(0, bodyLockDepth - 1)
  if (bodyLockDepth === 0) document.body.style.overflow = bodyOverflowBeforeLock
}

function focusableElements(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) =>
      element.getAttribute('aria-hidden') !== 'true' &&
      element.getClientRects().length > 0,
  )
}

/**
 * Keeps keyboard focus inside a mounted dialog, closes it with Escape, restores
 * focus to its opener, and safely composes body scroll locks for nested dialogs.
 */
export function useDialogFocus<T extends HTMLElement>(
  open: boolean,
  onClose: () => void,
): RefObject<T> {
  const dialogRef = useRef<T>(null)
  const openerRef = useRef<HTMLElement | null>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open || typeof document === 'undefined') return

    openerRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    lockBodyScroll()

    const frame = window.requestAnimationFrame(() => {
      const dialog = dialogRef.current
      if (!dialog) return
      const elements = focusableElements(dialog)
      const preferred = elements.find((element) => element.hasAttribute('data-dialog-initial-focus'))
      ;(preferred ?? elements[0] ?? dialog).focus()
    })

    const handleKeyDown = (event: KeyboardEvent) => {
      const dialog = dialogRef.current
      if (!dialog) return
      if (event.key === 'Escape') {
        event.preventDefault()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab') return

      const elements = focusableElements(dialog)
      if (elements.length === 0) {
        event.preventDefault()
        dialog.focus()
        return
      }
      const first = elements[0]
      const last = elements[elements.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      window.cancelAnimationFrame(frame)
      document.removeEventListener('keydown', handleKeyDown)
      unlockBodyScroll()
      const opener = openerRef.current
      if (opener?.isConnected) opener.focus()
    }
  }, [open])

  return dialogRef
}
