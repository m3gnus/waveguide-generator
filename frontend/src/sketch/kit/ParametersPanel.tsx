import { useEffect, useMemo, useRef, useState } from 'react';
import type { Command } from '../commands';
import type { Parameter, Sketch } from '../model';
import { applyCommand } from '../ops';
import { expressionDependents, resolveExpressions } from '../expressions';
import { displayName } from '../displayName';
import { dimensionEntry } from '../dimensionEntry';
import { DimensionExpressionField } from './DimensionExpressionField';

interface Props {
  sketch: Sketch; enabled: boolean; pending: boolean;
  command(command: Command): Promise<unknown>;
}

function ParameterRow({ parameter, sketch, disabled, command, focusName, onFocused, problem }: {
  parameter: Parameter; sketch: Sketch; disabled: boolean; command: Props['command'];
  focusName: boolean; onFocused(): void; problem?: string;
}) {
  const [name, setName] = useState(parameter.name), [expression, setExpression] = useState(parameter.expression);
  const [error, setError] = useState('');
  const nameInput = useRef<HTMLInputElement>(null), renaming = useRef<string | undefined>(undefined);
  useEffect(() => { setName(parameter.name); setError(''); }, [parameter.name]);
  useEffect(() => { setExpression(parameter.expression); setError(''); }, [parameter.expression]);
  useEffect(() => {
    if (focusName && !disabled) { nameInput.current?.focus(); nameInput.current?.select(); onFocused(); }
  }, [focusName, disabled, onFocused]);
  const users = useMemo(() => expressionDependents(sketch, parameter.name), [sketch, parameter.name]);
  const reason = users.length ? `Used by ${users.map(id => sketch.parameters?.some(p => p.name === id) ? id : displayName(sketch, id)).join(', ')}. Clear those expressions before deleting it.` : '';
  async function submit(edit: Command) {
    const preview = applyCommand(sketch, edit);
    if ('error' in preview) { setError(preview.error); return false; }
    setError('');
    try { await command(edit); return true; } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); return false; }
  }
  function rename() {
    if (name === parameter.name || disabled || renaming.current === name) return;
    renaming.current = name;
    void submit({ kind: 'renameParameter', from: parameter.name, to: name }).then(ok => { if (!ok) renaming.current = undefined; });
  }
  return <li className="sketch-parameter-row" aria-label={`Parameter ${parameter.name}`}>
    <div className="sketch-parameter-heading">
      <label>Name<input ref={nameInput} aria-label={`Parameter name: ${parameter.name}`} value={name} disabled={disabled}
        aria-invalid={!!error} onChange={event => { setName(event.target.value); setError(''); }} onBlur={rename}
        onKeyDown={event => {
          if (event.key === 'Enter') { event.preventDefault(); rename(); }
          if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setName(parameter.name); setError(''); }
        }}/></label>
      <label>Unit<select aria-label={`Parameter unit: ${parameter.name}`} value={parameter.unit} disabled={disabled}
        onChange={event => void submit({ kind: 'setParameter', name: parameter.name, expression: parameter.expression, unit: event.target.value as Parameter['unit'] })}>
        <option value="mm">mm</option><option value="deg">°</option><option value="none">none</option>
      </select></label>
      <button className="btn" type="button" aria-label={`Delete parameter: ${parameter.name}`} title={reason || 'Delete parameter'}
        disabled={disabled || !!users.length} onClick={() => void submit({ kind: 'removeParameter', name: parameter.name })}>×</button>
    </div>
    {/* Escape cancels the field edit only; it must not reach the dialog and close the editor. */}
    <form onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setExpression(parameter.expression); setError(''); } }} onSubmit={event => {
      event.preventDefault(); if (disabled) return;
      const entry = dimensionEntry(sketch, parameter, expression);
      if ('error' in entry) setError(entry.error); else void submit(entry.command);
    }}>
      <DimensionExpressionField sketch={sketch} dimension={parameter} value={expression} label={`Expression: ${parameter.name}`}
        disabled={disabled} preview={expression !== parameter.expression} error={error || problem}
        onChange={value => { setExpression(value); setError(''); }}/>
      <div className="sketch-parameter-footer"><span title={reason || 'No expressions reference this parameter'}>used by {users.length}</span>
        <button className="btn" type="submit" disabled={disabled || expression === parameter.expression}>Apply</button></div>
    </form>
  </li>;
}

/** All previews and writes use the same commands as dimensions and the existing session history. */
export function ParametersPanel({ sketch, enabled, pending, command }: Props) {
  const [open, setOpen] = useState(true), [focusName, setFocusName] = useState<string>(), [error, setError] = useState('');
  const parameters = sketch.parameters ?? [], disabled = !enabled || pending;
  const problems = useMemo(() => resolveExpressions(sketch).errors, [sketch]);
  async function add() {
    let index = 1;
    while (parameters.some(p => p.name === `p${index}`) || sketch.constraints.some(c => 'value' in c && (c.id === `p${index}` || c.label?.trim() === `p${index}`))) index++;
    const name = `p${index}`;
    setError(''); setFocusName(name);
    try { await command({ kind: 'addParameter', name, expression: '0', unit: 'mm' }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setFocusName(undefined); }
  }
  return <details className="constraint-inspector sketch-parameters" data-workspace-own-keys open={open}
    onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Parameters <span className="constraint-inspector-count">({parameters.length})</span></summary>
    {!parameters.length && <p className="constraint-inspector-empty">No parameters. Add one to reuse a value in several dimensions.</p>}
    <ul>{parameters.map(parameter => <ParameterRow key={parameter.name} parameter={parameter} sketch={sketch}
      disabled={disabled} command={command} focusName={focusName === parameter.name} onFocused={() => setFocusName(undefined)}
      problem={problems.find(problem => problem.id === parameter.name)?.message}/>)}</ul>
    <button className="btn" type="button" disabled={disabled} onClick={() => void add()}>Add parameter</button>
    {error && <p role="alert">{error}</p>}
  </details>;
}
