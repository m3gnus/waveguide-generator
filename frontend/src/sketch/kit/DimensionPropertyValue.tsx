import { useRef, useState } from 'react';
import type { Command } from '../commands';
import type { Constraint, Sketch } from '../model';
import { dimensionEntry } from '../dimensionEntry';
import { displayValue } from '../dimensionValue';
import { formatValue } from '../formatValue';
import { DimensionExpressionField } from './DimensionExpressionField';

/** Properties use the canvas entry, with the panel's existing commit-on-blur behaviour. */
export function DimensionPropertyValue({ sketch, dimension, disabled, onCommit }: {
  sketch: Sketch; dimension: Constraint & { value: number }; disabled: boolean;
  onCommit(command: Command): Promise<void>;
}) {
  const [draft, setDraft] = useState<string>();
  const [error, setError] = useState<string>();
  const baseline = useRef(''), pending = useRef(false);
  const exact = dimension.expression ?? String(displayValue(dimension));
  const value = draft ?? dimension.expression ?? formatValue(displayValue(dimension));
  async function commit() {
    if (disabled || pending.current || draft === undefined) return;
    if (draft === baseline.current) { setDraft(undefined); setError(undefined); return; }
    const entry = dimensionEntry(sketch, dimension, draft);
    if ('error' in entry) { setError(entry.error); return; }
    pending.current = true;
    try { await onCommit(entry.command); baseline.current = draft; setDraft(undefined); setError(undefined); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { pending.current = false; }
  }
  return <form className="sketch-dimension-property-value" onSubmit={event => { event.preventDefault(); void commit(); }}
    onFocus={event => { if (event.target instanceof HTMLInputElement && draft === undefined) { baseline.current = exact; setDraft(exact); } }}
    onBlur={event => { if (event.target instanceof HTMLInputElement) void commit(); }}
    onKeyDown={event => {
      // Input keys follow the same ownership as the canvas label editor.
      event.stopPropagation();
      if (event.key === 'Escape') { event.preventDefault(); setDraft(undefined); setError(undefined); }
    }}>
    <DimensionExpressionField sketch={sketch} dimension={dimension} value={value} label={`Dimension ${dimension.id} value`}
      disabled={disabled} preview={draft !== undefined && !disabled} onChange={text => { setDraft(text); setError(undefined); }} error={error}/>
    {dimension.expression && <span className="sketch-expression-marker" title={dimension.expression}>ƒ</span>}
  </form>;
}
