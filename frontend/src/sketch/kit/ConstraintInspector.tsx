import { useEffect, useMemo } from 'react';
import type { Command } from '../commands';
import { inspectConstraints, type ConstraintRow } from '../inspector';
import type { Sketch } from '../model';
import { DETERMINATION_ENTITY_LIMIT, type SolveResult } from '../solver/types';
import { rules } from '../tools/constraints';

interface Props {
  sketch: Sketch; diagnosis?: Pick<SolveResult, 'conflicting' | 'redundant' | 'partiallyRedundant'>;
  selection: string[]; enabled: boolean; pending: boolean; open: boolean;
  onOpenChange(open: boolean): void;
  onHover(id?: string): void;
  onSelect(ids: string[]): void;
  command(command: Command): Promise<unknown>;
}
const statuses: Record<ConstraintRow['status'], { icon: string; label: string }> = {
  'broken expression': { icon: '!', label: 'broken expression' },
  ok: { icon: '', label: 'Satisfied' },
  conflicting: { icon: '!', label: 'Conflicting' },
  redundant: { icon: '≈', label: 'Redundant' },
  reference: { icon: '○', label: 'Reference' },
  notAnalysed: { icon: '·', label: `Not analysed. Constraint state is not analysed above ${DETERMINATION_ENTITY_LIMIT} entities; it may also be awaiting a solve.` },
};

/** Rows, units, filtering and diagnosis belong to inspectConstraints. */
export function ConstraintInspector({ sketch, diagnosis, selection, enabled, pending, open, onOpenChange, onHover, onSelect, command }: Props) {
  const rows = useMemo(() => inspectConstraints(sketch, diagnosis, selection.length ? selection : undefined), [sketch, diagnosis, selection]);
  useEffect(() => () => onHover(undefined), [onHover, rows, open]);
  const remove = (id: string) => { if (enabled && !pending) void command({ kind: 'removeConstraint', id }); };
  return <details className="constraint-inspector" data-workspace-own-keys open={open} onToggle={event => {
    onOpenChange(event.currentTarget.open);
    if (!event.currentTarget.open) onHover(undefined);
  }}>
    <summary>Constraints <span className="constraint-inspector-count">({rows.length})</span></summary>
    {rows.length === 0 ? <p className="constraint-inspector-empty">{selection.length ? 'Nothing constrains the selection.' : 'No constraints yet. Add one from the Constraints toolbar.'}</p>
      : <div className="constraint-inspector-groups">{(['Dimensions', 'Relations'] as const).map(group => {
        const items = rows.filter(row => (row.value !== undefined) === (group === 'Dimensions'));
        return items.length > 0 && <section key={group} aria-label={group}>
          <h4>{group} <span>({items.length})</span></h4>
          <ul>{items.map(row => {
            const status = statuses[row.status];
            const symbol = row.kind in rules ? rules[row.kind as keyof typeof rules].symbol : row.kind === 'angle' ? '∠' : row.kind === 'radius' ? (row.diameter ? 'Ø' : 'R') : '↔';
            const toggle = row.driving ? 'Make reference' : 'Make driving';
            return <li key={row.id} data-inspector-id={row.id} tabIndex={0} aria-label={row.name}
              className={`constraint-inspector-row ${row.status} ${selection.includes(row.id) ? 'selected' : ''}`}
              onPointerEnter={() => onHover(row.id)} onPointerLeave={() => onHover(undefined)}
              onFocus={() => onHover(row.id)} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) onHover(undefined); }}
              onClick={() => onSelect([row.id])} onKeyDown={event => {
                if (event.target !== event.currentTarget || event.ctrlKey || event.metaKey || event.altKey) return;
                if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); event.stopPropagation(); remove(row.id); }
                else if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); onSelect([row.id]); }
              }}>
              <span className="constraint-inspector-kind" title={row.kind} aria-hidden="true">{symbol}</span>
              <div className="constraint-inspector-description">
                <div className="constraint-inspector-label"><span title={`${row.name} (${row.id})`}>{row.name}</span>
                  {row.expression !== undefined && <span className="constraint-inspector-expression" title={row.expression} aria-label={`Expression: ${row.expression}`}>ƒ</span>}
                </div>
                <div className="constraint-inspector-refs">{row.refs.map(ref => <button key={ref.id} className="btn constraint-inspector-chip" type="button" title={ref.id} aria-label={`Select ${ref.name}`}
                  onClick={event => { event.stopPropagation(); onSelect([ref.id]); }}>{ref.name}</button>)}</div>
                {row.value !== undefined && <span className="constraint-inspector-value">{row.value}{row.driving === false && ' (ref)'}</span>}
              </div>
              <span className="constraint-inspector-status" title={row.error ?? status.label} aria-label={status.label}>{status.icon}</span>
              <div className="constraint-inspector-actions">
                {row.driving !== undefined && <button className="btn" type="button" title={toggle} aria-label={`${toggle}: ${row.name}`} disabled={!enabled || pending}
                  onClick={event => { event.stopPropagation(); void command({ kind: 'setDriving', id: row.id, driving: !row.driving }); }}>{row.driving ? '○' : '●'}</button>}
                <button className="btn" type="button" title="Delete constraint" aria-label={`Delete constraint: ${row.name}`} disabled={!enabled || pending}
                  onClick={event => { event.stopPropagation(); remove(row.id); }}>×</button>
              </div>
            </li>;
          })}</ul>
        </section>;
      })}</div>}
  </details>;
}
