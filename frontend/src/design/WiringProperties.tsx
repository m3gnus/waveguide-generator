import { useId, useRef, useState } from 'react';
import { failureWords } from '../api/httpError';
import { assemblyDriverNotice } from '../jobs/importedSubmission';
import {
  assemblyWiringProblems, editWiringNode, joinWiringDrivers, newRadiatorId, removeWiringGroup,
  setMemberProcessing, setWiringGroup, wiringDriverIds, wooferById, wooferDriveMode,
  type AssemblyWiringGroup, type AssemblyWiringNode, type DesignAssembly,
} from '../stores/assembly';
import { assemblySimulationData } from '../stores/assemblySimulation';
import { useDesignStore } from '../stores/design';
import { useScopedSolveOptions } from '../stores/solveOptionsContext';
import { ActionMenu, type ActionMenuItem } from './ActionMenu';
import { NumberField } from './NumberField';
import { SelectRow } from './SelectRow';
import { ToggleRow } from './SolveOptionsSections';

type Connection = AssemblyWiringGroup['network']['kind'];
type Part = 'R' | 'L' | 'C';
type Edit = (assembly: DesignAssembly) => DesignAssembly;
type Run = (label: string, edit: Edit) => void;
const PARTS: Record<Part, { label: string; unit: string; scale: number; initial: number }> = {
  R: { label: 'Resistance', unit: 'Ω', scale: 1, initial: 1 },
  L: { label: 'Inductance', unit: 'mH', scale: 1e-3, initial: 1 },
  C: { label: 'Capacitance', unit: 'µF', scale: 1e-6, initial: 10 },
};
const part = (kind: Part): AssemblyWiringNode => ({ kind, value: PARTS[kind].initial * PARTS[kind].scale });

/** NumberField scrubbing, like a typed value, is one completed workspace command. */
function PartValue({ node, onChange }: { node: Extract<AssemblyWiringNode, { value: number }>; onChange(value: number): void }) {
  const spec = PARTS[node.kind];
  const dragging = useRef(false);
  const revision = useRef<number | null>(null);
  const pending = useRef<number | null>(null);
  const [draft, setDraft] = useState<number | null>(null);
  return <NumberField label={spec.label} unit={spec.unit} value={draft ?? node.value / spec.scale}
    min={0} exclusiveMin precision={6} displayPrecision={3} step={0.1}
    onBeginDrag={() => { dragging.current = true; revision.current = useDesignStore.getState().designRevision; }}
    onCommit={value => {
      if (dragging.current) { pending.current = value; setDraft(value); }
      else onChange(value * spec.scale);
    }}
    onEndDrag={() => {
      dragging.current = false;
      const value = pending.current;
      pending.current = null;
      setDraft(null);
      if (revision.current === useDesignStore.getState().designRevision && value !== null && value * spec.scale !== node.value) onChange(value * spec.scale);
      revision.current = null;
    }}/>
}

function WiringNode({ assembly, group, node, path, run, available }: {
  assembly: DesignAssembly; group: AssemblyWiringGroup; node: AssemblyWiringNode; path: number[]; run: Run;
  available: NonNullable<DesignAssembly['woofers']>;
}) {
  const id = useId();
  const [wrap, setWrap] = useState<Connection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const branch = 'children' in node;
  const title = node.kind === 'driver' ? wooferById(assembly, node.radiator_id)?.label ?? 'Woofer'
    : branch ? node.kind === 'series' ? 'Series' : 'Parallel' : node.kind;
  const problems = assemblyWiringProblems(assembly).filter(problem => problem.groupId === group.id && problem.path.join('.') === path.join('.'));
  const apply = (label: string, edit: Edit) => {
    try { edit(assembly); }
    catch (reason) { setError(failureWords(reason)); return; }
    setError(null); setWrap(null);
    run(label, edit);
  };
  const change = (label: string, edit: (current: AssemblyWiringNode) => AssemblyWiringNode | null) =>
    apply(label, current => editWiringNode(current, group.id, path, edit));
  const add = (child: AssemblyWiringNode) => {
    apply('Add wiring connection', current => {
      const ids = wiringDriverIds(child);
      const joined = ids.length ? joinWiringDrivers(current, ids, wiringDriverIds(group.network)[0]) : current;
      return editWiringNode(joined, group.id, path, old => wrap
        ? { kind: wrap, children: [old, child] }
        : 'children' in old ? { ...old, children: [...old.children, child] } : { kind: 'series', children: [old, child] });
    });
  };
  const items: ActionMenuItem[] = [
    ...(['R', 'L', 'C'] as const).map(kind => ({ id: kind, label: `Add ${kind} (${PARTS[kind].initial} ${PARTS[kind].unit})`, group: 'Add part', onSelect: () => add(part(kind)) })),
    ...available.map(woofer => ({ id: woofer.id, label: `Add ${woofer.label}`, group: 'Add woofer', onSelect: () => add({ kind: 'driver', radiator_id: woofer.id }) })),
    ...(['series', 'parallel'] as const).map(kind => ({ id: `wrap-${kind}`, label: `Wrap in ${kind}…`, group: 'Connection', onSelect: () => { setWrap(kind); setError(null); } })),
    ...node.kind === 'driver' && problems.some(problem => problem.kind === 'member-processing') ? [{
      id: 'reset-member', label: 'Reset member processing', group: 'Signal', onSelect: () =>
        apply('Reset wired member processing', current => setMemberProcessing(current, node.radiator_id, { weight: 1, delay_ms: 0, invert: false })),
    }] : [],
    { id: 'remove', label: path.length ? 'Remove node' : 'Remove network', group: 'Remove', onSelect: () => path.length
      ? change('Remove wiring node', () => null)
      : apply('Remove wiring network', current => removeWiringGroup(current, group.id)) },
  ];
  return <div className="front-wiring-node" data-wiring-path={path.join('.')}>
    <div className="front-heading"><span>{title}</span>
      <ActionMenu triggerLabel="Edit…" menuLabel={`Edit ${title} connection`} items={items} placement="below"/>
    </div>
    {wrap && <p className="section-note" role="status">Choose Add part or Add woofer in Edit… to complete the {wrap} connection. <button type="button" className="btn-link" onClick={() => setWrap(null)}>Cancel</button></p>}
    {branch ? <>
      <SelectRow><label htmlFor={id}>Connection</label><select id={id} value={node.kind}
        onChange={event => { const kind = event.currentTarget.value as Connection; change('Change wiring connection', old => ({ ...old, kind } as AssemblyWiringGroup['network'])); }}>
        <option value="series">Series</option><option value="parallel">Parallel</option>
      </select></SelectRow>
    </> : node.kind === 'driver'
      ? <ToggleRow id={id} label="Invert" help="Reverses this driver's electrical connection in the wiring network." checked={!!node.invert}
        onChange={invert => change('Invert wiring driver', old => ({ ...old, invert } as typeof node))}/>
      : <>
        <SelectRow><label htmlFor={id}>Part</label><select id={id} value={node.kind}
          onChange={event => { const kind = event.currentTarget.value as Part; change('Change wiring part', () => part(kind)); }}>
          <option value="R">R</option><option value="L">L</option><option value="C">C</option>
        </select></SelectRow>
        <PartValue node={node} onChange={value => change('Wiring part value', old => ({ ...old, value } as typeof node))}/>
      </>}
    {problems.map((problem, index) => <p className="field-error" role="alert" key={index}>{problem.message}</p>)}
    {error && <p className="field-error" role="alert">{error}</p>}
    {branch && <div className="front-wiring-children">{node.children.map((child, index) =>
      <WiringNode key={`${index}:${child.kind === 'driver' ? child.radiator_id : 'node'}`} assembly={assembly} group={group} node={child}
        path={[...path, index]} run={run} available={available}/>)}</div>}
  </div>;
}

export function WiringProperties({ assembly, selection, run }: { assembly: DesignAssembly; selection: string[]; run: Run }) {
  const [error, setError] = useState<string | null>(null);
  const design = useDesignStore(state => state.design);
  const engine = useScopedSolveOptions(options => options.engine);
  const notice = assemblyDriverNotice(assemblySimulationData({ ...design, assembly }, null), { engine });
  const wired = new Set((assembly.wiring_groups ?? []).flatMap(group => wiringDriverIds(group.network)));
  const available = (assembly.woofers ?? []).filter(woofer => woofer.enabled !== false && wooferDriveMode(assembly, woofer.id) === 'driver' && !wired.has(woofer.id));
  const selected = available.filter(woofer => selection.includes(woofer.id));
  const create = (kind: Connection, passive?: Part) => {
    const group: AssemblyWiringGroup = { id: newRadiatorId(), label: `Wiring ${(assembly.wiring_groups?.length ?? 0) + 1}`,
      network: { kind, children: [...selected.map(woofer => ({ kind: 'driver' as const, radiator_id: woofer.id })), ...passive ? [part(passive)] : []] } };
    const edit = (current: DesignAssembly) => setWiringGroup(joinWiringDrivers(current, selected.map(woofer => woofer.id)), group);
    try { edit(assembly); }
    catch (reason) { setError(failureWords(reason)); return; }
    setError(null); run('Wire woofers', edit);
  };
  const items: ActionMenuItem[] = selected.length === 1
    ? (['R', 'L', 'C'] as const).flatMap(kind => (['series', 'parallel'] as const).map(connection => ({
      id: `${connection}-${kind}`, label: `${connection === 'series' ? 'Series' : 'Parallel'} + ${kind} (${PARTS[kind].initial} ${PARTS[kind].unit})`, onSelect: () => create(connection, kind),
    })))
    : (['series', 'parallel'] as const).map(kind => ({ id: kind, label: kind === 'series' ? 'Series' : 'Parallel', onSelect: () => create(kind) }));
  return <>
    <div className="front-heading"><h3>Wiring</h3><ActionMenu triggerLabel="Wire woofers…" menuLabel="Wire selected woofers" items={items}
      disabled={!selected.length} placement="below" triggerTitle={selected.length ? 'Connect the selected driver woofers on one amplifier channel' : 'Select unwired driver woofers in the front view'}/></div>
    <p className="section-note">Unwired driver woofers are individual, each on its own amplifier channel.</p>
    <p className="section-note">{selected.length ? `${selected.length} selected for wiring. Wiring uses ${selected[0].label}'s signal group and DSP; edit group DSP in Simulation.` : 'Select driver woofers in the front view to wire them.'}</p>
    {selected.length === 1 && <p className="section-note">One woofer needs a passive part to form a network. Choose its connection and part in Wire woofers…</p>}
    {notice && <p className="section-note" role="status">{notice}</p>}
    {error && <p className="field-error" role="alert">{error}</p>}
    {(assembly.wiring_groups ?? []).map(group => <div key={group.id} data-wiring-group={group.id}>
      <h4>{group.label}</h4>
      <WiringNode assembly={assembly} group={group} node={group.network} path={[]} run={run} available={available}/>
    </div>)}
  </>;
}
