import { useId, useState, type Ref } from 'react';
import type { Constraint, Parameter, Sketch } from '../model';
import { dimensionEntry } from '../dimensionEntry';
import { formatValue } from '../formatValue';
import { displayValue } from '../dimensionValue';
import { formatReference } from '../expressions';

interface Props {
  sketch: Sketch; dimension: (Constraint & { value: number }) | Parameter; value: string;
  onChange(value: string): void; inputRef?: Ref<HTMLInputElement>; error?: string;
  label?: string; disabled?: boolean; preview?: boolean;
}
/** Shared by the placement prompt, canvas label editor and dimension property rows. */
export function DimensionExpressionField({ sketch, dimension, value, onChange, inputRef, error, label = 'Dimension value', disabled = false, preview = true }: Props) {
  const listId = useId(), errorId = useId(), resultId = useId();
  const [caret, setCaret] = useState(value.length), [active, setActive] = useState(0), [dismissed, setDismissed] = useState(false);
  const match = value.slice(0, caret).match(/(?:^|[+\-*/^(,;]\s*)(\[?[A-Za-z_][A-Za-z_0-9. ]*)$/);
  const token = match?.[1], start = token ? caret - token.length : caret;
  const currentId = 'name' in dimension ? dimension.name : dimension.id;
  const labels = sketch.constraints.filter(c => c.id !== currentId && 'value' in c && c.driving && c.label?.trim())
    .map(c => c.label!.trim()).concat((sketch.parameters ?? []).filter(p => p.name !== currentId).map(p => p.name));
  const names = preview && token && !dismissed ? [...new Set(labels)].filter(name => labels.filter(n => n === name).length === 1
    && name.toLowerCase().startsWith(token.replace(/^\[/, '').toLowerCase())).slice(0, 8) : [];
  const selected = Math.min(active, Math.max(0, names.length - 1));
  // Tables show already resolved values until edited; avoid revalidating every idle row.
  const entry = preview ? dimensionEntry(sketch, dimension, value) : { value: 'name' in dimension ? dimension.value : displayValue(dimension) };
  function accept(name: string, input: HTMLInputElement) {
    const replacement = formatReference(sketch, sketch.parameters?.some(p => p.name === name) ? name : sketch.constraints.find(c => c.label?.trim() === name && 'value' in c && c.driving)!.id);
    const end = value[caret] === ']' ? caret + 1 : caret;
    onChange(value.slice(0, start) + replacement + value.slice(end));
    setCaret(start + replacement.length); setDismissed(true);
    requestAnimationFrame(() => { input.focus(); input.setSelectionRange(start + replacement.length, start + replacement.length); });
  }
  return <div className="sketch-expression-entry">
    <label><span>{label}</span><input ref={inputRef} aria-label={label} value={value} disabled={disabled}
      role="combobox" aria-autocomplete="list" aria-expanded={names.length > 0} aria-controls={names.length ? listId : undefined}
      aria-activedescendant={names.length ? `${listId}-${selected}` : undefined} aria-invalid={!!error || 'error' in entry}
      aria-describedby={error || 'error' in entry ? errorId : 'value' in entry ? resultId : undefined}
      onChange={event => { onChange(event.target.value); setCaret(event.target.selectionStart ?? event.target.value.length); setActive(0); setDismissed(false); }}
      onSelect={event => setCaret(event.currentTarget.selectionStart ?? value.length)}
      onKeyDown={event => {
        if (names.length && ['ArrowDown', 'ArrowUp', 'Tab', 'Enter', 'Escape'].includes(event.key)) {
          event.preventDefault(); event.stopPropagation();
          if (event.key === 'Escape') setDismissed(true);
          else if (event.key === 'Tab' || event.key === 'Enter') accept(names[selected], event.currentTarget);
          else setActive((selected + (event.key === 'ArrowDown' ? 1 : names.length - 1)) % names.length);
        } else if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); event.currentTarget.form?.requestSubmit(); }
      }}/></label>
    {'value' in entry && <output id={resultId} className="sketch-expression-result">= {formatValue(entry.value)}{'name' in dimension ? dimension.unit === 'deg' ? '°' : dimension.unit === 'mm' ? ' mm' : '' : dimension.kind === 'angle' ? '°' : ' mm'}</output>}
    {names.length > 0 && <div id={listId} role="listbox" aria-label="Dimension names" className="sketch-menu sketch-expression-names">
      {names.map((name, index) => <div key={name} id={`${listId}-${index}`} role="option" aria-selected={index === selected}
        onPointerDown={event => { event.preventDefault(); const input = event.currentTarget.closest('.sketch-expression-entry')!.querySelector('input')!; accept(name, input); }}>{name}</div>)}
    </div>}
    {(error || 'error' in entry) && <span id={errorId} role="alert">{error || ('error' in entry ? entry.error : '')}</span>}
  </div>;
}
