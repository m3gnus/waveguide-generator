import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const OPEN_DELAY_MS = 180;
const CLOSE_DELAY_MS = 300;
const EDGE_MARGIN = 8;
const ANCHOR_GAP = 6;

type OpenMode = 'closed' | 'hover' | 'pinned';
type FocusTarget = 'first' | 'last' | null;

interface MenuPlacement {
  left: number;
  top: number;
}

export interface ActionMenuItem {
  id: string;
  label: string;
  trailing?: string;
  group?: string;
  disabled?: boolean;
  disabledReason?: string;
  busy?: boolean;
  busyLabel?: string;
  error?: string;
  onSelect(): void | Promise<void>;
}

export interface ActionMenuProps {
  items: ActionMenuItem[];
  menuLabel: string;
  triggerLabel: string;
  onPrimary?: () => void | Promise<void>;
  chevronLabel?: string;
  disabled?: boolean;
  placement?: 'auto' | 'above' | 'below';
}

function groupedItems(items: ActionMenuItem[]) {
  const chunks: Array<{ group?: string; items: Array<{ item: ActionMenuItem; index: number }> }> = [];
  items.forEach((item, index) => {
    const previous = chunks[chunks.length - 1];
    if (!previous || previous.group !== item.group) chunks.push({ group: item.group, items: [] });
    chunks[chunks.length - 1].items.push({ item, index });
  });
  return chunks;
}

export function ActionMenu({
  items,
  menuLabel,
  triggerLabel,
  onPrimary,
  chevronLabel = 'More options',
  disabled = false,
  placement: preferredPlacement = 'auto',
}: ActionMenuProps) {
  const reactId = useId();
  const menuId = `action-menu-${reactId.replace(/:/g, '')}`;
  const rootRef = useRef<HTMLDivElement>(null);
  const chevronRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const openTimer = useRef<number | null>(null);
  const closeTimer = useRef<number | null>(null);
  const pendingFocus = useRef<FocusTarget>(null);
  const [openMode, setOpenMode] = useState<OpenMode>('closed');
  const [activeIndex, setActiveIndex] = useState(0);
  const [menuPlacement, setMenuPlacement] = useState<MenuPlacement | null>(null);
  const open = openMode !== 'closed';

  const clearTimers = useCallback(() => {
    if (openTimer.current !== null) window.clearTimeout(openTimer.current);
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    openTimer.current = null;
    closeTimer.current = null;
  }, []);

  const close = useCallback((restoreFocus = false) => {
    clearTimers();
    pendingFocus.current = null;
    setOpenMode('closed');
    setMenuPlacement(null);
    if (restoreFocus) window.requestAnimationFrame(() => chevronRef.current?.focus());
  }, [clearTimers]);

  const edgeIndex = useCallback((edge: 'first' | 'last') => {
    const enabled = items.map((item, index) => ({ item, index })).filter(({ item }) => !item.disabled);
    if (enabled.length) return enabled[edge === 'first' ? 0 : enabled.length - 1].index;
    return items.length ? (edge === 'first' ? 0 : items.length - 1) : -1;
  }, [items]);

  const openAndFocus = useCallback((target: Exclude<FocusTarget, null>) => {
    clearTimers();
    const index = edgeIndex(target);
    pendingFocus.current = target;
    if (index >= 0) setActiveIndex(index);
    setOpenMode('pinned');
  }, [clearTimers, edgeIndex]);

  const updatePlacement = useCallback(() => {
    const anchor = rootRef.current;
    const menu = menuRef.current;
    if (!anchor?.isConnected) {
      close();
      return;
    }
    if (!menu) return;

    const anchorRect = anchor.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const availableBelow = window.innerHeight - anchorRect.bottom - ANCHOR_GAP - EDGE_MARGIN;
    const availableAbove = anchorRect.top - ANCHOR_GAP - EDGE_MARGIN;
    const below = preferredPlacement === 'below'
      || (preferredPlacement === 'auto' && (menuRect.height <= availableBelow || availableBelow >= availableAbove));
    const unclampedTop = below
      ? anchorRect.bottom + ANCHOR_GAP
      : anchorRect.top - ANCHOR_GAP - menuRect.height;
    const maxTop = Math.max(EDGE_MARGIN, window.innerHeight - EDGE_MARGIN - menuRect.height);
    const maxLeft = Math.max(EDGE_MARGIN, window.innerWidth - EDGE_MARGIN - menuRect.width);
    const next = {
      left: Math.max(EDGE_MARGIN, Math.min(anchorRect.left, maxLeft)),
      top: Math.max(EDGE_MARGIN, Math.min(unclampedTop, maxTop)),
    };
    setMenuPlacement((current) => current?.left === next.left && current.top === next.top ? current : next);
  }, [close, preferredPlacement]);

  useEffect(() => clearTimers, [clearTimers]);

  useEffect(() => {
    if (disabled && open) close();
  }, [close, disabled, open]);

  useLayoutEffect(() => {
    if (open) updatePlacement();
  });

  useLayoutEffect(() => {
    if (!open || pendingFocus.current === null) return;
    const index = edgeIndex(pendingFocus.current);
    pendingFocus.current = null;
    if (index >= 0) itemRefs.current[index]?.focus();
  }, [edgeIndex, open]);

  useEffect(() => {
    if (!open) return undefined;

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!rootRef.current?.contains(target) && !menuRef.current?.contains(target)) close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close(true);
      }
    };
    const onScroll = () => close();
    const onResize = () => updatePlacement();
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onResize);

    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(updatePlacement);
    if (rootRef.current) observer?.observe(rootRef.current);
    if (menuRef.current) observer?.observe(menuRef.current);

    // A keyed job card can move without this component's props changing. Poll its
    // live rect while open so a websocket/list reorder never strands the portal.
    let frame = 0;
    const trackAnchor = () => {
      updatePlacement();
      if (rootRef.current?.isConnected) frame = window.requestAnimationFrame(trackAnchor);
    };
    frame = window.requestAnimationFrame(trackAnchor);

    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onResize);
      observer?.disconnect();
      window.cancelAnimationFrame(frame);
    };
  }, [close, open, updatePlacement]);

  const enterHoverRegion = (pointerType: string) => {
    if (pointerType !== 'mouse' || disabled) return;
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    closeTimer.current = null;
    if (openMode !== 'closed' || openTimer.current !== null) return;
    openTimer.current = window.setTimeout(() => {
      openTimer.current = null;
      setOpenMode('hover');
    }, OPEN_DELAY_MS);
  };

  const leaveHoverRegion = (pointerType: string) => {
    if (pointerType !== 'mouse' || openMode === 'pinned') return;
    if (openTimer.current !== null) window.clearTimeout(openTimer.current);
    openTimer.current = null;
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => close(), CLOSE_DELAY_MS);
  };

  const onTriggerKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      openAndFocus('first');
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      openAndFocus('last');
    }
  };

  const moveActive = (direction: 1 | -1) => {
    if (!items.length) return;
    let candidate = activeIndex;
    for (let count = 0; count < items.length; count += 1) {
      candidate = (candidate + direction + items.length) % items.length;
      if (!items[candidate].disabled) {
        setActiveIndex(candidate);
        itemRefs.current[candidate]?.focus();
        return;
      }
    }
  };

  const activate = async (index: number) => {
    const item = items[index];
    if (!item || item.disabled || item.busy) return;
    try {
      await item.onSelect();
      close();
    } catch {
      setActiveIndex(index);
      itemRefs.current[index]?.focus();
    }
  };

  const runPrimary = async () => {
    if (!onPrimary) return;
    try {
      await onPrimary();
      close();
    } catch {
      // The consumer owns operation feedback; a rejected action leaves an
      // already-visible menu available for retry, matching item selection.
    }
  };

  const onMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Tab') {
      close();
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      moveActive(1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      moveActive(-1);
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      const index = edgeIndex(event.key === 'Home' ? 'first' : 'last');
      if (index >= 0) {
        setActiveIndex(index);
        itemRefs.current[index]?.focus();
      }
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      void activate(activeIndex);
    }
  };

  const chunks = groupedItems(items);
  const renderedMenu = open ? createPortal(
    <div
      id={menuId}
      ref={menuRef}
      className="design-menu-popover action-menu-popover"
      role="menu"
      aria-label={menuLabel}
      onKeyDown={onMenuKeyDown}
      onPointerEnter={(event) => enterHoverRegion(event.pointerType)}
      onPointerLeave={(event) => leaveHoverRegion(event.pointerType)}
      style={{
        left: menuPlacement?.left ?? 0,
        top: menuPlacement?.top ?? 0,
        visibility: menuPlacement ? 'visible' : 'hidden',
      }}
    >
      {chunks.map((chunk, chunkIndex) => {
        const headingId = `${menuId}-group-${chunkIndex}`;
        const content = <>
          {chunk.group && <div id={headingId} className="action-menu-group-heading">{chunk.group}</div>}
          {chunk.items.map(({ item, index }) => {
            const reasonId = item.disabledReason ? `${menuId}-reason-${index}` : undefined;
            const errorId = item.error ? `${menuId}-error-${index}` : undefined;
            const describedBy = [reasonId, errorId].filter(Boolean).join(' ') || undefined;
            // Native `disabled` removes a button from focus navigation and often
            // prevents its description being announced. aria-disabled preserves
            // discovery; `activate` suppresses both pointer and keyboard actions.
            return <button
              key={item.id}
              ref={(element) => { itemRefs.current[index] = element; }}
              type="button"
              role="menuitem"
              className="design-menu-item action-menu-item"
              aria-disabled={item.disabled || undefined}
              aria-busy={item.busy || undefined}
              aria-describedby={describedBy}
              tabIndex={index === activeIndex ? 0 : -1}
              onFocus={() => setActiveIndex(index)}
              onClick={() => void activate(index)}
            >
              <span className="action-menu-item-content">
                <span className="action-menu-item-label">{item.busy && item.busyLabel ? item.busyLabel : item.label}</span>
                {item.disabledReason && <small id={reasonId} className="action-menu-item-reason">{item.disabledReason}</small>}
                {item.error && <small id={errorId} className="action-menu-item-error" role="alert">{item.error}</small>}
              </span>
              {item.trailing && <span className="action-menu-item-trailing">{item.trailing}</span>}
            </button>;
          })}
        </>;
        return chunk.group
          ? <div key={`${chunk.group}-${chunkIndex}`} role="group" aria-labelledby={headingId} className="action-menu-group">{content}</div>
          : <div key={`ungrouped-${chunkIndex}`} className="action-menu-group">{content}</div>;
      })}
    </div>,
    document.body,
  ) : null;

  return <div
    ref={rootRef}
    className="action-menu"
    onPointerEnter={(event) => enterHoverRegion(event.pointerType)}
    onPointerLeave={(event) => leaveHoverRegion(event.pointerType)}
  >
    <div className="action-menu-split">
      <button
        type="button"
        className="action-menu-primary"
        disabled={disabled}
        aria-haspopup={onPrimary ? undefined : 'menu'}
        aria-expanded={onPrimary ? undefined : open}
        aria-controls={onPrimary ? undefined : menuId}
        onKeyDown={onTriggerKeyDown}
        onClick={() => {
          if (onPrimary) void runPrimary();
          else openAndFocus('first');
        }}
      >{triggerLabel}</button>
      <button
        ref={chevronRef}
        type="button"
        className="action-menu-chevron"
        disabled={disabled}
        aria-label={chevronLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        onKeyDown={onTriggerKeyDown}
        onClick={() => {
          clearTimers();
          if (openMode === 'pinned') close();
          else setOpenMode('pinned');
        }}
      ><span aria-hidden="true">⌄</span></button>
    </div>
    {renderedMenu}
  </div>;
}
