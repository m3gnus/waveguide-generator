import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';

/**
 * A preferences panel that hangs off a toolbar gear button.
 *
 * It portals to `document.body` and positions itself `fixed`, for the same
 * reason HelpTip does. Every dock panel in this workspace sits inside a chain of
 * `overflow: hidden` dockview containers, and in the jobs rail the panel body is
 * the scroll container itself. Rendered in place, an open preferences popover
 * was clipped to the rail's own width -- 253px, narrow enough to fold "Prefix
 * job name with date" into a five-line stack beside its checkbox and to clip the
 * sort and rating selects -- and scrolling the run list carried the open panel
 * out of the window while the button it belongs to stayed where it was.
 *
 * Fixed positioning also means the panel is sized by its content rather than by
 * whichever rail happens to host it, so the same form can be laid out once and
 * used from any panel.
 */

/** Keeps the panel off the window edge. */
const EDGE_MARGIN = 8;
/** Clearance between the trigger and the panel it opens. */
const ANCHOR_GAP = 6;

interface Placement {
  left: number;
  top: number;
  maxHeight: number;
}

function place(anchor: DOMRect, panel: DOMRect): Placement {
  // Right-aligned to the trigger: these gear buttons all sit at the right end of
  // their toolbar, so the panel opens inward and never off the window.
  const left = Math.max(EDGE_MARGIN, Math.min(anchor.right - panel.width, window.innerWidth - panel.width - EDGE_MARGIN));
  const below = anchor.bottom + ANCHOR_GAP;
  const roomBelow = window.innerHeight - below - EDGE_MARGIN;
  const roomAbove = anchor.top - ANCHOR_GAP - EDGE_MARGIN;
  // Flip above only when that genuinely gives more room; a panel that merely
  // scrolls is better than one that jumps over its own button.
  if (panel.height > roomBelow && roomAbove > roomBelow) {
    const maxHeight = roomAbove;
    return { left, top: Math.max(EDGE_MARGIN, anchor.top - ANCHOR_GAP - Math.min(panel.height, maxHeight)), maxHeight };
  }
  return { left, top: below, maxHeight: roomBelow };
}

export function AnchoredPanel({ anchorRef, onClose, className, label, children }: {
  anchorRef: RefObject<HTMLElement | null>;
  onClose?: () => void;
  className?: string;
  label: string;
  children: ReactNode;
}) {
  const panelRef = useRef<HTMLElement | null>(null);
  const [placement, setPlacement] = useState<Placement | null>(null);

  // Measured in a layout effect so the first painted frame is already in place,
  // and re-measured on scroll and resize because the trigger can move under it.
  useLayoutEffect(() => {
    const reposition = () => {
      const anchor = anchorRef.current;
      const panel = panelRef.current;
      if (!anchor || !panel) return;
      setPlacement(place(anchor.getBoundingClientRect(), panel.getBoundingClientRect()));
    };
    reposition();
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    return () => {
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
    };
  }, [anchorRef]);

  // Escape closes, and so does a press anywhere outside the panel and its own
  // trigger -- the trigger is excluded so its click toggles rather than closing
  // and immediately reopening.
  useEffect(() => {
    if (!onClose) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      onClose();
    };
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (panelRef.current?.contains(target) || anchorRef.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onPointerDown, true);
    };
  }, [onClose, anchorRef]);

  // Keyboard users get focus moved into the panel, and back to the gear button
  // when it closes, so opening preferences never drops them at the top of the
  // document.
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panelRef.current?.focus({ preventScroll: true });
    return () => previous?.focus?.({ preventScroll: true });
  }, []);

  return createPortal(
    <section
      ref={panelRef}
      className={`panel-preferences-popover${className ? ` ${className}` : ''}`}
      aria-label={label}
      tabIndex={-1}
      style={{
        left: placement?.left ?? 0,
        top: placement?.top ?? 0,
        maxHeight: placement?.maxHeight,
        visibility: placement ? 'visible' : 'hidden',
      }}
    >
      {children}
    </section>,
    document.body,
  );
}
