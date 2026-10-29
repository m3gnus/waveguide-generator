/**
 * The front view's properties: add woofers on their anchors, and edit the
 * selected woofer's aperture, placement, spacing relation, drive and its
 * member processing in its signal group. Every control is one workspace
 * command, so one project history step.
 */
import type { ReactNode } from 'react';
import {
  DEFAULT_WOOFER_GAP_MM, setMemberProcessing, setPlacement, updateWoofer, wooferById,
  type AssemblyAnchor, type AssemblyPlacement, type DesignAssembly,
} from '../stores/assembly';
import { addWooferCommand, anchoredPlacement, type FrontCommand, type FrontViewContext } from '../sketch/adapters/frontView';
import type { DomainAdapter, PropertiesSection, WorkspaceSession } from '../sketch/workspace/types';
import { NumberField } from './NumberField';

type Session = WorkspaceSession<DesignAssembly, FrontCommand>;
const ANCHORS: Array<[AssemblyAnchor, string]> = [['above', 'Above the mouth'], ['below', 'Below the mouth'], ['free', 'Free position']];

function run(session: Session, label: string, edit: FrontCommand['edit']) {
  void session.command({ label, edit }).catch(() => {});
}

function member(assembly: DesignAssembly, id: string) {
  return (assembly.signal_groups ?? []).flatMap(group => group.members).find(item => item.radiator_id === id);
}

function WooferProperties({ assembly, id, session, resolved }: { assembly: DesignAssembly; id: string; session: Session; resolved: FrontViewContext | undefined }) {
  const woofer = wooferById(assembly, id)!;
  const placement = woofer.placement;
  const processing = member(assembly, id);
  const clearance = resolved?.context.clearances?.[id];
  const others = (assembly.woofers ?? []).filter(item => item.id !== id);
  const place = (next: AssemblyPlacement) => run(session, 'Move woofer', current => setPlacement(current, id, next));
  return <>
    <label className="field-row"><span>Label</span>
      <input type="text" defaultValue={woofer.label} key={`${id}:${woofer.label}`} maxLength={64}
        onBlur={event => { const label = event.currentTarget.value.trim(); if (label && label !== woofer.label) run(session, 'Rename woofer', current => updateWoofer(current, id, { label })); }}/>
    </label>
    <label className="field-row"><input type="checkbox" checked={woofer.enabled !== false}
      onChange={event => { const enabled = event.currentTarget.checked; run(session, enabled ? 'Enable woofer' : 'Disable woofer', current => updateWoofer(current, id, { enabled })); }}/>
      <span>Enabled</span></label>
    <NumberField label="Radius" unit="mm" value={woofer.radius_mm} min={0} exclusiveMin max={1000} precision={1}
      onCommit={radius_mm => run(session, 'Woofer radius', current => updateWoofer(current, id, { radius_mm }))}/>
    <label className="field-row"><span>Diaphragm</span>
      <select value={woofer.shape.kind} onChange={event => {
        const kind = event.currentTarget.value as 'piston' | 'ath_cone';
        run(session, 'Woofer diaphragm', current => updateWoofer(current, id, { shape: { kind } }));
      }}>
        <option value="piston">Flat piston</option>
        <option value="ath_cone">ATH cone</option>
      </select>
    </label>
    {woofer.shape.kind === 'piston' && <NumberField label="Recess" unit="mm" value={woofer.shape.recess_mm ?? 0} min={0} max={1000} precision={1}
      onCommit={recess_mm => run(session, 'Woofer recess', current => updateWoofer(current, id, { shape: { kind: 'piston', recess_mm } }))}/>}
    <label className="field-row"><span>Drive</span>
      <select value={woofer.drive ?? 'prescribed'} onChange={event => {
        const drive = event.currentTarget.value as 'prescribed' | 'rigid';
        run(session, 'Woofer drive', current => updateWoofer(current, id, { drive }));
      }}>
        <option value="prescribed">Driven</option>
        <option value="rigid">Rigid (not driven)</option>
      </select>
    </label>
    <label className="field-row"><span>Placement</span>
      <select value={placement.anchor} onChange={event => {
        const anchor = event.currentTarget.value as AssemblyAnchor;
        run(session, 'Woofer placement', current => setPlacement(current, id, anchoredPlacement(current, id, anchor, resolved, DEFAULT_WOOFER_GAP_MM)));
      }}>
        {ANCHORS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </label>
    {placement.anchor === 'free'
      ? <>
        <NumberField label="Centre x" unit="mm" value={placement.x_mm} min={-10000} max={10000} precision={1} onCommit={x_mm => place({ ...placement, x_mm })}/>
        <NumberField label="Centre y" unit="mm" value={placement.y_mm} min={-10000} max={10000} precision={1} onCommit={y_mm => place({ ...placement, y_mm })}/>
      </>
      : <NumberField label="Gap to mouth" unit="mm" value={placement.gap_mm} min={0} max={10000} precision={1} onCommit={gap_mm => place({ ...placement, gap_mm })}/>}
    {others.length > 0 && <label className="field-row"><span>Spacing from</span>
      <select value={placement.spacing_to?.radiator_id ?? ''} onChange={event => {
        const target = event.currentTarget.value;
        const { spacing_to: _old, ...rest } = placement;
        place(target ? { ...rest, spacing_to: { radiator_id: target, distance_mm: placement.spacing_to?.distance_mm ?? 2 * woofer.radius_mm + DEFAULT_WOOFER_GAP_MM } } : rest);
      }}>
        <option value="">None</option>
        {others.map(other => <option key={other.id} value={other.id}>{other.label}</option>)}
      </select>
    </label>}
    {placement.spacing_to && <NumberField label="Centre spacing" unit="mm" value={placement.spacing_to.distance_mm} min={0} exclusiveMin max={10000} precision={1}
      onCommit={distance_mm => place({ ...placement, spacing_to: { ...placement.spacing_to!, distance_mm } })}/>}
    {processing && <>
      <NumberField label="Weight" value={processing.weight ?? 1} min={0} max={100} precision={3}
        onCommit={weight => run(session, 'Woofer weight', current => setMemberProcessing(current, id, { weight }))}/>
      <NumberField label="Delay" unit="ms" value={processing.delay_ms ?? 0} min={0} max={1000} precision={3}
        onCommit={delay_ms => run(session, 'Woofer delay', current => setMemberProcessing(current, id, { delay_ms }))}/>
      <label className="field-row"><input type="checkbox" checked={!!processing.invert}
        onChange={event => { const invert = event.currentTarget.checked; run(session, 'Woofer polarity', current => setMemberProcessing(current, id, { invert })); }}/>
        <span>Invert polarity</span></label>
    </>}
    {clearance && <p className="section-note">
      Clearance to the mouth {clearance.mouth_mm.toFixed(1)} mm
      {clearance.baffle_edge_mm !== undefined && <>, to the baffle edge {clearance.baffle_edge_mm.toFixed(1)} mm</>}.
    </p>}
  </>;
}

function AssemblyProperties({ assembly, session, resolved, select }: { assembly: DesignAssembly; session: Session; resolved: FrontViewContext | undefined; select(ids: string[]): void }): ReactNode {
  const add = (anchor: AssemblyAnchor) => {
    let added = '';
    void session.command(addWooferCommand(anchor, resolved, id => { added = id; })).then(() => { if (added) select([added]); }, () => {});
  };
  return <>
    <div className="button-row">
      <button type="button" onClick={() => add('above')}>Add woofer above</button>
      <button type="button" onClick={() => add('below')}>Add woofer below</button>
      <button type="button" onClick={() => add('free')}>Add free woofer</button>
    </div>
    <ul className="front-woofer-list">
      {(assembly.woofers ?? []).map(woofer => <li key={woofer.id}>
        <button type="button" onClick={() => select([woofer.id])}>{woofer.label}{woofer.enabled === false ? ' (disabled)' : ''}</button>
      </li>)}
    </ul>
    {resolved?.context.refusal && <p className="section-note" role="alert">{resolved.context.refusal}</p>}
  </>;
}

/** The properties of the front view adapter, reading the latest server context. */
export function frontViewProperties(resolved: () => FrontViewContext | undefined): DomainAdapter<DesignAssembly, DesignAssembly, FrontCommand>['properties'] {
  return (state, selection, session, actions): PropertiesSection[] => {
    const selected = selection.filter(id => wooferById(state, id));
    const sections: PropertiesSection[] = [{ id: 'assembly', title: 'Sources and baffle',
      render: () => <AssemblyProperties assembly={state} session={session} resolved={resolved()} select={actions.setSelection}/> }];
    if (selected.length === 1) {
      sections.push({ id: `woofer:${selected[0]}`, title: wooferById(state, selected[0])!.label,
        render: () => <WooferProperties key={selected[0]} assembly={state} id={selected[0]} session={session} resolved={resolved()}/> });
    }
    return sections;
  };
}
