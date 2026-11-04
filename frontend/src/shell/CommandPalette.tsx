import { useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from './icons';
import { commandShortcutLabel } from './platformKeys';
import { trapDialogFocus } from './dialogFocus';

export type PaletteKind = 'Parameters' | 'Jobs' | 'Commands';
const PALETTE_KINDS: PaletteKind[] = ['Parameters', 'Jobs', 'Commands'];

export interface PaletteEntry {
  id: string;
  kind: PaletteKind;
  label: string;
  detail?: string;
  keywords?: string;
  disabled?: boolean;
  matches?: (query: string) => boolean;
  run: () => void;
}

export function entryMatches(entry: PaletteEntry, query: string): boolean {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return true;
  if (entry.matches?.(query)) return true;
  return [entry.label, entry.detail, entry.keywords].some((value) => value?.toLocaleLowerCase().includes(normalized));
}

export function CommandPalette({ entries }: { entries: PaletteEntry[] }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const isOpen = useRef(false);
  const previousFocus = useRef<HTMLElement | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const visible = useMemo(() => {
    const matches = entries.filter((entry) => entryMatches(entry, query));
    return PALETTE_KINDS.flatMap(kind => matches.filter(entry => entry.kind === kind));
  }, [entries, query]);
  const selectedIndex = Math.min(activeIndex, Math.max(0, visible.length - 1));
  const activeEntry = visible[selectedIndex];
  const shortcut = commandShortcutLabel('K');

  const openPalette = () => {
    if (isOpen.current) return;
    isOpen.current = true;
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setQuery('');
    setActiveIndex(0);
    setOpen(true);
  };
  const closePalette = () => {
    isOpen.current = false;
    setOpen(false);
    requestAnimationFrame(() => previousFocus.current?.focus());
  };
  const run = (entry: PaletteEntry | undefined) => {
    if (!entry || entry.disabled) return;
    closePalette();
    queueMicrotask(entry.run);
  };

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === 'k') {
        event.preventDefault();
        openPalette();
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, []);

  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => input.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [open]);

  useEffect(() => setActiveIndex(0), [query]);
  useEffect(() => setActiveIndex(selectedIndex), [selectedIndex]);
  useEffect(() => {
    if (open && activeEntry) document.getElementById(`palette-${activeEntry.id}`)?.scrollIntoView?.({ block: 'nearest' });
  }, [open, activeEntry]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Tab') {
      trapDialogFocus(dialog, event.nativeEvent);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      closePalette();
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex(visible.length ? (selectedIndex + 1) % visible.length : 0);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex(visible.length ? (selectedIndex - 1 + visible.length) % visible.length : 0);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      run(activeEntry);
    }
  };

  return <>
    <button className="command-affordance" onClick={openPalette} aria-haspopup="dialog" aria-expanded={open}><Icon name="search"/><span>Search parameters, designs, jobs, commands…</span><kbd>{shortcut}</kbd></button>
    {open && <div className="modal-backdrop command-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) closePalette(); }}>
      <div ref={dialog} className="command-palette" role="dialog" aria-modal="true" aria-label="Command palette" onKeyDown={onKeyDown}>
        <div className="command-search"><Icon name="search"/><input ref={input} role="combobox" aria-label="Search commands" aria-controls="command-palette-results" aria-expanded="true" aria-activedescendant={activeEntry ? `palette-${activeEntry.id}` : undefined} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search parameters, jobs, and commands…"/><kbd>{shortcut}</kbd></div>
        <div id="command-palette-results" className="command-results" role="listbox">
          {!visible.length && <div className="command-empty" role="status"><b>No matches</b><span>Try a parameter label, key, symbol, job name, or command.</span></div>}
          {PALETTE_KINDS.map((kind) => {
            const group = visible.filter((entry) => entry.kind === kind);
            if (!group.length) return null;
            return <section key={kind} role="group" aria-labelledby={`command-group-${kind.toLocaleLowerCase()}`}><h3 id={`command-group-${kind.toLocaleLowerCase()}`}>{kind}</h3>{group.map((entry) => {
              const index = visible.indexOf(entry);
              return <button id={`palette-${entry.id}`} key={entry.id} role="option" aria-selected={index === selectedIndex} disabled={entry.disabled} onMouseMove={() => setActiveIndex(index)} onClick={() => run(entry)}><span>{entry.label}<small>{entry.detail}</small></span><em>{entry.kind === 'Parameters' ? 'Parameter' : entry.kind === 'Jobs' ? 'Result' : 'Run'}</em></button>;
            })}</section>;
          })}
        </div>
        <footer><span><kbd>↑</kbd><kbd>↓</kbd> navigate</span><span><kbd>↵</kbd> run</span><span><kbd>esc</kbd> close</span></footer>
      </div>
    </div>}
  </>;
}
