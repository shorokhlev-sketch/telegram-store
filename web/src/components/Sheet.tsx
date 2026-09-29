import { useEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useScrollLock, useWide } from '../lib'
import { useBackButton } from '../tg'
import { X } from './Icons'

/*
 * Overlays render into document.body through a portal: the catalog page gets
 * a transform (stacked-card effect) or a blur (flyout) and must not become
 * the containing block of fixed-position sheets.
 */

function useOverlay(onClose: () => void) {
  useBackButton(onClose)
  useScrollLock()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
}

export function CloseButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="x-btn press" onClick={onClick} aria-label="Закрыть">
      <X />
    </button>
  )
}

interface SheetProps {
  onClose: () => void
  label: string
  children: ReactNode
  /** Centered 17px title in the head row (filters). */
  title?: string
  /** Pinned bottom row (filters: «Сбросить» + «Показать N»). */
  footer?: ReactNode
  /** Tall sheet from under the status bar (filters). */
  full?: boolean
}

/** Bottom sheet: grabber, head row with a round close button, body, optional sticky footer. */
export default function Sheet({ onClose, label, children, title, footer, full }: SheetProps) {
  useOverlay(onClose)
  return createPortal(
    <div role="dialog" aria-modal="true" aria-label={label}>
      <div className="scrim" onClick={onClose} />
      <div className={`sheet ${full ? 'full' : ''}`}>
        <div className="grabber" aria-hidden="true" />
        <div className="sheet-head">
          <span />
          {title ? <h3>{title}</h3> : <span />}
          <CloseButton onClick={onClose} />
        </div>
        {children}
        {footer && <div className="sheet-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}

/** Centered modal (desktop «Купить», admin sheets on wide screens). */
export function Modal({ onClose, label, children }: { onClose: () => void; label: string; children: ReactNode }) {
  useOverlay(onClose)
  return createPortal(
    <div className="modal-wrap" role="dialog" aria-modal="true" aria-label={label} onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <span />
          <span />
          <CloseButton onClick={onClose} />
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}

/** Sheet on phones, modal on desktop — same content (`.buy` block). */
export function Dialog({ onClose, label, children }: { onClose: () => void; label: string; children: ReactNode }) {
  const wide = useWide()
  return wide ? (
    <Modal onClose={onClose} label={label}>
      {children}
    </Modal>
  ) : (
    <Sheet onClose={onClose} label={label}>
      {children}
    </Sheet>
  )
}
