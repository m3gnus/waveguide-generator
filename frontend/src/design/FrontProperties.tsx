import { EmptyState } from '../shell/EmptyState';
import { Notice } from '../shell/Notice';
/**
 * The front view's side panel: the sources on the baffle, the selected woofer
 * and the enclosure they sit on. Every woofer control is one workspace command,
 * so one project history step; the enclosure fields are the design's own
 * parameters, written as the Geometry panel writes them.
 */
import {
  DEFAULT_WOOFER_GAP_MM, commitAssembly, deleteWoofer, setMemberProcessing, setPlacement, setWooferDrive, updateWoofer, wiringDriverIds, wooferById, wooferDriveMode,
  type AssemblyAnchor, type AssemblyPlacement, type AssemblyWoofer, type DesignAssembly,
} from '../stores/assembly';
import { isNativeFamily, useDesignStore, type DesignDocument } from '../stores/design';
import { addWooferCommand, anchoredPlacement, clears, freePlacementClosed, noRoomForWoofer, placementProblems, radiusProblem, type FrontCommand, type FrontViewContext } from '../sketch/adapters/frontView';
import type { DomainAdapter, PropertiesSection, WorkspaceSession } from '../sketch/workspace/types';
import { ActionMenu } from './ActionMenu';
import { NumberField } from './NumberField';
import { outerBodyUpdates } from './outerBodyUpdates';
import { LEGACY_PARAMETER_REGISTRY } from './parameterRegistry';
import { SelectRow } from './SelectRow';
import { ToggleRow } from './SolveOptionsSections';
import { WooferDriveSelect } from './WooferDrivers';
import { assemblyDriverForm } from '../stores/assemblySimulation';
import { exteriorTransducerError } from '../stores/exteriorTransducer';
import { WiringProperties } from './WiringProperties';

type Session = WorkspaceSession<DesignAssembly, FrontCommand>;
const ANCHORS: Array<[AssemblyAnchor, string]> = [['above', 'Above the mouth'], ['below', 'Below the mouth'], ['free', 'Free position']];

function run(session: Session, label: string, edit: FrontCommand['edit']) {
  void session.command({ label, edit }).catch(() => {});
}

function member(assembly: DesignAssembly, id: string) {
  return (assembly.signal_groups ?? []).flatMap(group => group.members).find(item => item.radiator_id === id);
}

const removeWoofers = (session: Session, woofers: AssemblyWoofer[], select: (ids: string[]) => void) => {
  void session.command(woofers.map(woofer => ({ label: `Delete ${woofer.label}`, edit: (assembly: DesignAssembly) => deleteWoofer(assembly, woofer.id) })))
    .then(() => select([]), () => {});
};

function Clearances({ id, assembly, resolved }: { id: string; assembly: DesignAssembly; resolved: FrontViewContext | undefined }) {
  const clearance = resolved?.context.clearances?.[id];
  if (!clearance) return null;
  const minimum = resolved?.context.minimum_clearance_mm ?? 0;
  const rows: Array<[string, number]> = [['Mouth', clearance.mouth_mm],
    ...clearance.baffle_edge_mm === undefined ? [] : [['Baffle edge', clearance.baffle_edge_mm] as [string, number]],
    ...Object.entries(clearance.woofers).map(([other, distance]): [string, number] => [wooferById(assembly, other)?.label ?? 'Woofer', distance])];
  return <dl className="front-clearances">
    {rows.map(([label, distance]) => <div key={label} className={clears(distance, minimum) ? undefined : 'problem'}><dt>{label}</dt><dd>{distance.toFixed(1)} mm</dd></div>)}
  </dl>;
}

function WooferProperties({ assembly, id, session, resolved, select }: { assembly: DesignAssembly; id: string; session: Session; resolved: FrontViewContext | undefined; select(ids: string[]): void }) {
  const woofer = wooferById(assembly, id)!;
  const placement = woofer.placement;
  const processing = member(assembly, id);
  const wired = (assembly.wiring_groups ?? []).some(group => wiringDriverIds(group.network).includes(id));
  const others = (assembly.woofers ?? []).filter(item => item.id !== id);
  const place = (next: AssemblyPlacement) => run(session, 'Move woofer', current => setPlacement(current, id, next));
  const drive = wooferDriveMode(assembly, id);
  const form = assemblyDriverForm(assembly.drivers?.[id]);
  const driverProblem = form ? exteriorTransducerError(form) : null;
  return <>
    <div className="front-heading">
      <h3>{woofer.label}</h3>
      <button className="btn" type="button" onClick={() => removeWoofers(session, [woofer], select)}>Remove woofer</button>
    </div>
    <label className="field-row front-text-row"><span>Name</span>
      <input type="text" defaultValue={woofer.label} key={`${id}:${woofer.label}`} maxLength={64}
        onBlur={event => { const label = event.currentTarget.value.trim(); if (label && label !== woofer.label) run(session, 'Rename woofer', current => updateWoofer(current, id, { label })); }}/>
    </label>
    <ToggleRow id="front-woofer-enabled" label="Enabled" help="A disabled woofer keeps its settings and is left out of the geometry and the solve." checked={woofer.enabled !== false}
      onChange={enabled => run(session, enabled ? 'Enable woofer' : 'Disable woofer', current => updateWoofer(current, id, { enabled }))}/>
    <NumberField label="Radius" unit="mm" value={woofer.radius_mm} min={0} exclusiveMin max={1000} precision={1}
      validate={radius => radiusProblem(assembly, id, radius, resolved)}
      onCommit={radius_mm => run(session, 'Woofer radius', current => updateWoofer(current, id, { radius_mm }))}/>
    <SelectRow>
      <label htmlFor="front-woofer-diaphragm">Diaphragm</label>
      <select id="front-woofer-diaphragm" value={woofer.shape.kind} onChange={event => {
        const kind = event.currentTarget.value as 'piston' | 'ath_cone';
        run(session, 'Woofer diaphragm', current => updateWoofer(current, id, { shape: { kind } }));
      }}>
        <option value="piston">Flat piston</option>
        <option value="ath_cone">ATH cone</option>
      </select>
    </SelectRow>
    {woofer.shape.kind === 'piston' && <NumberField label="Recess" unit="mm" value={woofer.shape.recess_mm ?? 0} min={0} max={1000} precision={1}
      onCommit={recess_mm => run(session, 'Woofer recess', current => updateWoofer(current, id, { shape: { kind: 'piston', recess_mm } }))}/>}
    <WooferDriveSelect id="front-woofer-drive" woofer={woofer} mode={drive}
      onChange={mode => run(session, 'Woofer drive', current => setWooferDrive(current, id, mode))}/>
    {drive === 'driver' && <p className="section-note">{driverProblem
      ? `Pick its driver, or enter its values, under Drivers in Simulation. ${driverProblem}`
      : 'Its driver is set up under Drivers in Simulation.'}</p>}

    <h4>Position</h4>
    <SelectRow>
      <label htmlFor="front-woofer-placement">Placement</label>
      <select id="front-woofer-placement" value={placement.anchor} onChange={event => {
        const anchor = event.currentTarget.value as AssemblyAnchor;
        run(session, 'Woofer placement', current => setPlacement(current, id, anchoredPlacement(current, id, anchor, resolved, DEFAULT_WOOFER_GAP_MM)));
      }}>
        {ANCHORS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </SelectRow>
    {placement.anchor === 'free'
      ? <>
        <NumberField label="Centre x" unit="mm" value={placement.x_mm} min={-10000} max={10000} precision={1} onCommit={x_mm => place({ ...placement, x_mm })}/>
        <NumberField label="Centre y" unit="mm" value={placement.y_mm} min={-10000} max={10000} precision={1} onCommit={y_mm => place({ ...placement, y_mm })}/>
      </>
      : <NumberField label="Gap to mouth" unit="mm" value={placement.gap_mm} min={0} max={10000} precision={1} onCommit={gap_mm => place({ ...placement, gap_mm })}/>}
    {others.length > 0 && <SelectRow>
      <label htmlFor="front-woofer-spacing">Spacing from</label>
      <select id="front-woofer-spacing" value={placement.spacing_to?.radiator_id ?? ''} onChange={event => {
        const target = event.currentTarget.value;
        const { spacing_to: _old, ...rest } = placement;
        place(target ? { ...rest, spacing_to: { radiator_id: target, distance_mm: placement.spacing_to?.distance_mm ?? 2 * woofer.radius_mm + DEFAULT_WOOFER_GAP_MM } } : rest);
      }}>
        <option value="">None</option>
        {others.map(other => <option key={other.id} value={other.id}>{other.label}</option>)}
      </select>
    </SelectRow>}
    {placement.spacing_to && <>
      <NumberField label="Centre spacing" unit="mm" value={placement.spacing_to.distance_mm} min={0} exclusiveMin max={10000} precision={1}
        onCommit={distance_mm => place({ ...placement, spacing_to: { ...placement.spacing_to!, distance_mm } })}/>
      <p className="section-note">The spacing sets the distance. The position above only sets the direction.</p>
    </>}
    <Clearances id={id} assembly={assembly} resolved={resolved}/>

    {processing && <>
      <h4>Signal</h4>
      {wired && <p className="section-note">This woofer is wired. Set DSP for its signal group in Simulation, and polarity with Invert under Wiring.</p>}
      <NumberField label="Weight" value={processing.weight ?? 1} min={0} max={100} precision={3}
        disabled={wired}
        onCommit={weight => run(session, 'Woofer weight', current => setMemberProcessing(current, id, { weight }))}/>
      <NumberField label="Delay" unit="ms" value={processing.delay_ms ?? 0} min={0} max={1000} precision={3}
        disabled={wired}
        onCommit={delay_ms => run(session, 'Woofer delay', current => setMemberProcessing(current, id, { delay_ms }))}/>
      <ToggleRow id="front-woofer-polarity" label="Invert polarity" help="Reverses this woofer's polarity in the combined result." checked={!!processing.invert} disabled={wired}
        onChange={invert => run(session, 'Woofer polarity', current => setMemberProcessing(current, id, { invert }))}/>
    </>}
  </>;
}

function SourcesProperties({ assembly, session, resolved, selection, select, hasAssembly }: {
  assembly: DesignAssembly; session: Session; resolved: FrontViewContext | undefined; selection: string[]; select(ids: string[]): void; hasAssembly: boolean;
}) {
  const add = (anchor: AssemblyAnchor) => {
    let added = '';
    void session.command(addWooferCommand(anchor, resolved, id => { added = id; })).then(() => { if (added) select([added]); }, () => {});
  };
  const woofers = assembly.woofers ?? [], problems = placementProblems(assembly, resolved);
  const design = useDesignStore(state => state.design);
  const updateValues = useDesignStore(state => state.updateValues);
  const blocked = mountingBlock(design);
  const noRoom = (anchor: AssemblyAnchor) => noRoomForWoofer(anchor, resolved, field => design.enclosure[field.split('.')[1] as 'space_b' | 'space_t' | 'space_l' | 'space_r']);
  const full = noRoom('free');
  // The plain Add only ever makes a woofer that fits; with no such place it opens the choices.
  const primary = (['below', 'above'] as const).find(anchor => !noRoom(anchor)) ?? (full ? undefined : 'free');
  const menu = <ActionMenu menuLabel="Where to add the woofer" triggerLabel="Add woofer" triggerTitle={blocked ? 'Woofers need an enclosure.' : full?.reason ?? 'Add a woofer'} chevronLabel="Choose where to add it" onPrimary={primary ? () => add(primary) : undefined} placement="below" disabled={!!blocked}
    items={ANCHORS.map(([anchor, label]) => {
      const room = noRoom(anchor);
      const closed = anchor === 'free' ? freePlacementClosed(resolved) : !!room;
      return { id: anchor, label, disabled: closed, disabledReason: closed ? room?.reason : undefined, onSelect: () => add(anchor) };
    })}/>;
  return <>
    <div className="front-heading"><h3>Sources</h3>{woofers.length > 0 && menu}</div>
    <div className="front-source hf"><span className="front-swatch" aria-hidden="true"/><span>HF</span><small>Horn throat</small></div>
    {woofers.length === 0
      ? full && !blocked
        ? <Notice tone="info" role={null} title="No room for a woofer yet" actions={<>{full.action && <button className="btn-primary" type="button"
          onClick={() => updateValues(full.updates)}>{full.action}</button>}{menu}</>}>{full.reason}</Notice>
        : <EmptyState title="No woofers yet" detail="Add one on the front baffle. It is solved together with the horn." action={menu}/>
      : <ul className="front-sources">
        {woofers.map(woofer => <li key={woofer.id}>
          <button type="button" className={`front-source${problems.has(woofer.id) ? ' problem' : ''}`} aria-pressed={selection.includes(woofer.id)} onClick={() => select([woofer.id])}>
            <span className="front-swatch" aria-hidden="true"/><span>{woofer.label}</span>
            <small>{woofer.enabled === false ? 'Disabled' : woofer.drive === 'rigid' ? 'Rigid' : `⌀ ${Number((2 * woofer.radius_mm).toFixed(1))} mm`}</small>
          </button>
        </li>)}
      </ul>}
    {selection.length > 1 && <div className="front-heading">
      <p className="section-note">{selection.length} woofers selected.</p>
      <button className="btn" type="button" onClick={() => removeWoofers(session, woofers.filter(woofer => selection.includes(woofer.id)), select)}>Remove woofers</button>
    </div>}
    {hasAssembly && <button className="btn-link front-remove" type="button" title="Return this design to an ordinary horn. Undo restores it." onClick={() => commitAssembly(null)}>Remove assembly</button>}
  </>;
}

/** Why this design has no front baffle to put a woofer on, if it has none. */
function mountingBlock(design: DesignDocument): 'native' | 'mounting' | null {
  if (design.simulation.sim_type !== 'infinite-baffle' && design.enclosure.depth > 0) return null;
  return isNativeFamily(design.formula) ? 'native' : 'mounting';
}

/** The enclosure the woofers sit on, as the Geometry panel's Mounting section edits it. */
function BaffleProperties() {
  const design = useDesignStore(state => state.design);
  const updateValues = useDesignStore(state => state.updateValues);
  const blocked = mountingBlock(design);
  if (blocked === 'native') {
    return <><div className="front-heading"><h3>Baffle</h3></div>
      <Notice tone="info" role={null}>This horn family cannot sit in an enclosure, so it cannot carry woofers.</Notice></>;
  }
  if (blocked) {
    return <><div className="front-heading"><h3>Baffle</h3></div>
      <Notice tone="info" role={null} title="Woofers need an enclosure" actions={<button className="btn-primary" type="button"
        onClick={() => updateValues(outerBodyUpdates(design, 'enclosure', null, true))}>Use an enclosure</button>}>
        This horn is not mounted in a box yet, so it has no front baffle to cut.
      </Notice></>;
  }
  const fields = ['enclosure.depth', 'enclosure.space_l', 'enclosure.space_r', 'enclosure.space_t', 'enclosure.space_b'].map(id => LEGACY_PARAMETER_REGISTRY.find(field => field.id === id)!);
  return <><div className="front-heading"><h3>Baffle</h3></div>
    {fields.map(field => <NumberField key={field.id} label={field.label} unit="mm" min={field.min} max={field.max} precision={2} description={field.description}
      value={design.enclosure[field.id.split('.')[1] as 'depth' | 'space_l' | 'space_r' | 'space_t' | 'space_b']} onCommit={value => updateValues({ [field.id]: value })}/>)}
  </>;
}

/** The properties of the front view adapter, reading the latest server context. */
export function frontViewProperties(resolved: () => FrontViewContext | undefined, hasAssembly: () => boolean): DomainAdapter<DesignAssembly, DesignAssembly, FrontCommand>['properties'] {
  return (state, selection, session, actions): PropertiesSection[] => {
    const selected = selection.filter(id => wooferById(state, id));
    const sections: PropertiesSection[] = [{ id: 'sources', title: 'Sources',
      render: () => <SourcesProperties assembly={state} session={session} resolved={resolved()} selection={selected} select={actions.setSelection} hasAssembly={hasAssembly()}/> }];
    if (selected.length === 1) {
      sections.push({ id: `woofer:${selected[0]}`, title: wooferById(state, selected[0])!.label,
        render: () => <WooferProperties key={selected[0]} assembly={state} id={selected[0]} session={session} resolved={resolved()} select={actions.setSelection}/> });
    }
    if ((state.woofers ?? []).some(woofer => woofer.enabled !== false && wooferDriveMode(state, woofer.id) === 'driver')) {
      sections.push({ id: 'wiring', title: 'Wiring', render: () => <WiringProperties assembly={state} selection={selected} run={(label, edit) => run(session, label, edit)}/> });
    }
    sections.push({ id: 'baffle', title: 'Baffle', render: () => <BaffleProperties/> });
    return sections;
  };
}
