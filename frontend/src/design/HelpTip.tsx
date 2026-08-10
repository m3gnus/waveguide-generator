import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

/**
 * Hover help for a parameter row.
 *
 * The tip is portalled to `document.body` and positioned with `position: fixed`
 * rather than being nested in the row it describes. The parameter rail is an
 * `overflow: auto` column barely wider than the tip, so an absolutely
 * positioned bubble inside a row would be clipped on both sides and scrolled
 * away with its own content.
 *
 * The trigger is usually the label, which also drives NumberField's
 * drag-to-scrub, so the tip closes on pointerdown: a user who has started
 * dragging a value wants to watch the number, not read prose over it.
 */

const OPEN_DELAY_MS = 350;
/** Keeps the bubble off the viewport edge and clear of the row it describes. */
const EDGE_MARGIN = 8;
const ANCHOR_GAP = 6;
const TIP_WIDTH = 260;

interface Placement {
  left: number;
  top: number;
}

export interface HelpTipContent {
  /** Full parameter name, which the row itself may have ellipsized. */
  title?: string;
  /** What the parameter does. Absent means no tip at all. */
  text?: string;
  /** Interaction note, e.g. the drag-to-scrub affordance. */
  hint?: string;
}

export function useHelpTip(content?: HelpTipContent | string) {
  const { title, text, hint } = typeof content === 'string' ? { title: undefined, text: content, hint: undefined } : (content ?? {});
  const id = useId();
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [placement, setPlacement] = useState<Placement | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const timer = useRef<number | null>(null);

  const cancelPending = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const close = useCallback(() => {
    cancelPending();
    setRect(null);
    setPlacement(null);
  }, [cancelPending]);

  const openFrom = useCallback((element: Element, delay: number) => {
    if (!text) return;
    cancelPending();
    if (delay <= 0) {
      setRect(element.getBoundingClientRect());
      return;
    }
    timer.current = window.setTimeout(() => {
      timer.current = null;
      setRect(element.getBoundingClientRect());
    }, delay);
  }, [cancelPending, text]);

  useEffect(() => cancelPending, [cancelPending]);

  // Any scroll or resize invalidates the anchor rect this was measured from.
  // Re-deriving it would fight the rail's own scrolling, so just dismiss.
  useEffect(() => {
    if (!rect) return undefined;
    const dismiss = () => close();
    window.addEventListener('scroll', dismiss, true);
    window.addEventListener('resize', dismiss);
    return () => {
      window.removeEventListener('scroll', dismiss, true);
      window.removeEventListener('resize', dismiss);
    };
  }, [rect, close]);

  // Measure the rendered bubble, then flip it above the row when it would run
  // off the bottom and clamp it horizontally. Done in a layout effect so the
  // first painted frame is already in its final spot.
  useLayoutEffect(() => {
    const tip = tipRef.current;
    if (!rect || !tip) return;
    const { width, height } = tip.getBoundingClientRect();
    const below = rect.bottom + ANCHOR_GAP;
    const fitsBelow = below + height + EDGE_MARGIN <= window.innerHeight;
    const top = fitsBelow ? below : Math.max(EDGE_MARGIN, rect.top - ANCHOR_GAP - height);
    const maxLeft = window.innerWidth - width - EDGE_MARGIN;
    const left = Math.max(EDGE_MARGIN, Math.min(rect.left, maxLeft));
    setPlacement({ left, top });
  }, [rect]);

  // Always the same shape, so a caller can compose `onPointerDown` with its own
  // handler without narrowing a union first. `openFrom` no-ops without text.
  const triggerProps = {
    'aria-describedby': text && rect ? id : undefined,
    onPointerEnter: (event: { currentTarget: Element; pointerType?: string }) => {
      // Touch has no hover: a tap would open the tip and immediately begin a
      // drag underneath it. Leave those users the row itself.
      if (event.pointerType === 'touch') return;
      openFrom(event.currentTarget, OPEN_DELAY_MS);
    },
    onPointerLeave: close,
    onPointerDown: close,
    onFocus: (event: { currentTarget: Element }) => openFrom(event.currentTarget, 0),
    onBlur: close,
  };

  const tip = text && rect
    ? createPortal(
      <div
        id={id}
        ref={tipRef}
        className="help-tip"
        role="tooltip"
        style={{ left: placement?.left ?? rect.left, top: placement?.top ?? rect.bottom + ANCHOR_GAP, maxWidth: TIP_WIDTH, visibility: placement ? 'visible' : 'hidden' }}
      >
        {title && <b>{title}</b>}
        <p>{text}</p>
        {hint && <small>{hint}</small>}
      </div>,
      document.body,
    )
    : null;

  return { triggerProps, tip };
}

/** Wrapper for rows that just need the whole row to be the hover target. */
export function HelpTipRow({ text, className, children }: {
  text?: string;
  className?: string;
  children: React.ReactNode;
}) {
  const { triggerProps, tip } = useHelpTip(text);
  return <div className={className} {...triggerProps}>{children}{tip}</div>;
}

/**
 * Inline heading that carries its own tip, for the table editors whose caption
 * sits inside a flex row that a wrapping div would break.
 */
export function HelpTipHeading({ title, text, children }: {
  title?: string;
  text?: string;
  children: React.ReactNode;
}) {
  const { triggerProps, tip } = useHelpTip({ title, text });
  return <b {...triggerProps}>{children}{tip}</b>;
}
