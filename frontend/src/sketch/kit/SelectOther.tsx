import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { displayName } from '../displayName';
import { dimensionLabel } from '../dimensionGraphics';
import type { Sketch } from '../model';
import type { XY } from '../solver/types';
import type { Modifiers } from '../tools/select';
import { useWorkspaceEscape } from '../workspace/surface';

export function useSelectOther(sketch: Sketch) {
  const [menu, setMenu] = useState<{ at: XY; ids: string[] }>();
  const [hover, setHover] = useState<string>();
  const close = () => { setMenu(undefined); setHover(undefined); };
  useEffect(() => { setMenu(undefined); setHover(undefined); }, [sketch]);
  return { menu, hover, setHover, open: (at: XY, ids: string[]) => { setHover(undefined); setMenu({ at, ids }); }, close };
}

export function SelectOther({ at, ids, sketch, nameFor, container = document.body, onHover, onSelect, onClose }: {
  at: XY; ids: string[]; sketch: Sketch; nameFor?: (id: string) => string; container?: Element;
  onHover(id?: string): void; onSelect(id: string, modifiers: Modifiers): void; onClose(): void;
}) {
  const menu = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);
  const callbacks = useRef({ onClose, onHover }); callbacks.current = { onClose, onHover };
  useLayoutEffect(() => {
    const element = menu.current!;
    const box = element.getBoundingClientRect();
    element.style.left = `${Math.max(4, Math.min(at.x, window.innerWidth - box.width - 4))}px`;
    element.style.top = `${Math.max(4, Math.min(at.y, window.innerHeight - box.height - 4))}px`;
    element.querySelector('button')?.focus({ preventScroll: true });
  }, [at]);
  useEffect(() => {
    const dismiss = (event: globalThis.PointerEvent) => {
      if (event.target instanceof Node && !menu.current?.contains(event.target)) callbacks.current.onClose();
    };
    document.addEventListener('pointerdown', dismiss, true);
    return () => { document.removeEventListener('pointerdown', dismiss, true); callbacks.current.onHover(undefined); };
  }, []);
  useWorkspaceEscape(true, event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); } });
  return createPortal(<div ref={menu} role="menu" aria-label="Select other" data-workspace-own-keys
    className="sketch-menu sketch-select-other" style={{ left: at.x, top: at.y }}
    onPointerLeave={() => onHover(undefined)} onKeyDown={event => {
      event.stopPropagation();
      if (event.key === 'Escape' || event.key === 'Tab') { event.preventDefault(); onClose(); }
      else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? ids.length - 1 : (active + (event.key === 'ArrowDown' ? 1 : ids.length - 1)) % ids.length;
        setActive(next); menu.current?.querySelectorAll('button')[next]?.focus({ preventScroll: true }); onHover(ids[next]);
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); onSelect(ids[active], { shift: event.shiftKey, toggle: event.ctrlKey || event.metaKey });
      }
    }}>
    {ids.map((id, index) => {
      const constraint = sketch.constraints.find(c => c.id === id), entity = sketch.entities.find(e => e.id === id);
      const kind = constraint ? 'value' in constraint ? 'Dimension' : 'Constraint' : entity ? entity.kind[0].toUpperCase() + entity.kind.slice(1) : 'Point';
      const label = constraint && 'value' in constraint ? dimensionLabel(constraint, 'name') : nameFor?.(id) ?? displayName(sketch, id);
      const glyph = constraint ? '↔' : entity?.kind === 'arc' ? '◜' : entity ? '╱' : '•';
      return <button key={id} type="button" role="menuitem" tabIndex={index === active ? 0 : -1} className="btn"
        title={`${displayName(sketch, id)} (${id})`} onFocus={() => setActive(index)}
        onPointerEnter={() => { setActive(index); onHover(id); }}
        onClick={event => onSelect(id, { shift: event.shiftKey, toggle: event.ctrlKey || event.metaKey })}>
        <span aria-hidden="true" className="sketch-select-other-glyph">{glyph}</span><span className="sketch-select-other-kind">{kind}</span><span>{label}</span>
      </button>;
    })}
  </div>, container);
}
