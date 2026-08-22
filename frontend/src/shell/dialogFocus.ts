import { useEffect, useRef, type RefObject } from 'react';

export const focusableSelector = [
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function trapDialogFocus(dialog: RefObject<HTMLElement | null>, event: KeyboardEvent): void {
  if (event.key !== 'Tab') return;
  const focusable = [...(dialog.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? [])]
    .filter((element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true');
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1)!;
  if (event.shiftKey && (document.activeElement === first || !dialog.current?.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/** Keyboard and focus ownership shared by modal dialogs. */
export function useModalDialogFocus<T extends HTMLElement>({
  open,
  onClose,
  initialFocus,
}: {
  open: boolean;
  onClose: () => void;
  initialFocus?: string | ((dialog: T) => HTMLElement | null | undefined);
}): RefObject<T | null> {
  const dialog = useRef<T>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focus = requestAnimationFrame(() => {
      const current = dialog.current;
      if (!current) return;
      const target = typeof initialFocus === 'function'
        ? initialFocus(current)
        : current.querySelector<HTMLElement>(initialFocus ?? focusableSelector);
      target?.focus();
    });
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      trapDialogFocus(dialog, event);
    };
    document.addEventListener('keydown', keydown);
    return () => {
      cancelAnimationFrame(focus);
      document.removeEventListener('keydown', keydown);
      previous?.focus();
    };
  }, [initialFocus, onClose, open]);
  return dialog;
}
