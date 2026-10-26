import { useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { editMeshImport, prepareMeshFile, saveMeshSetup, setMeshMotion, useMeshImportStore, meshImportChoice, loadMeshFile } from '../stores/meshImport';
import { FILE_SURFACE_ROLES, fileSymmetryLabel, sourceRoleLabel } from '../model/cadLabels';
import { CadModelStatusLine } from '../shell/CadModelStatusLine';
import { FileModelDetails } from '../shell/CadModelDetails';
import { useImportedModelStore } from '../stores/importedModel';
import type { RegisteredMesh } from '../api/meshImport';
import { workspaceModeStore } from '../stores/workspaceMode';
import { restoreParametricWorkingDesign } from '../model/currentModel';
import { noteExplicitNavigation } from '../shell/workspaceNavigation';
import { useImportedSolveOptionsStore } from '../stores/solveOptions';
import { SolveOptionsStoreProvider } from '../stores/solveOptionsContext';
import { DirectivityMapControls, SolveOptionsControls } from './SolveOptionsSections';

import '../shell/cadLinkPanel.css';

const AXES = ['+x', '-x', '+y', '-y', '+z', '-z'];

/** The ready-mesh workflow does not expose CAD generation or connection controls. */
export function MeshImportPanel({ tab, embedded = false }: { tab: 'geometry' | 'simulation'; embedded?: boolean }) {
  const state = useMeshImportStore();
  const model = useImportedModelStore();
  const draft = model.fileDraft;
  const registered = model.input === 'file' ? model.prepared as RegisteredMesh | null : null;
  // The geometry can change whenever an original exists to prepare it from:
  // this browser read it, or the server kept it for the model.
  const editable = state.original !== null || (model.input === 'file' && model.modelId !== null && model.originalAvailable);
  const frequencyMode = useImportedSolveOptionsStore(s => s.frequencyMode);
  const sweep = useImportedSolveOptionsStore(useShallow(s => ({ startHz: s.sweepStartHz, endHz: s.sweepEndHz, count: s.sweepCount })));
  const setSweep = useImportedSolveOptionsStore(s => s.setSweep);
  const [symmetryOpen, setSymmetryOpen] = useState(false);
  const [sizeOpen, setSizeOpen] = useState(false);
  const [axesOpen, setAxesOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const planes = draft.domainPlanes ?? [];
  const summary = state.summary ?? registered?.summary ?? null;
  const scale = ({ mm: 1, cm: 10, m: 1000, in: 25.4 } as Record<string, number>)[draft.units];
  const rawDimensions = summary?.dimensions ?? [];
  const forwardIndex = AXES.indexOf(draft.forward) >> 1;
  const upIndex = AXES.indexOf(draft.up) >> 1;
  const rightIndex = [0, 1, 2].find(index => index !== forwardIndex && index !== upIndex) ?? 0;
  const dimensions = registered?.summary.dimensions_mm ?? summary?.dimensions_mm
    ?? [rightIndex, upIndex, forwardIndex].map(index => rawDimensions[index] * scale);
  const size = dimensions.map((value, index) => Number((value * (planes.includes(index === 0 ? 'x0' : index === 1 ? 'y0' : '') ? 2 : 1)).toPrecision(4))).join(' × ');
  const negativePlanes = planes.filter(plane => summary?.kept_sides?.[plane] === 'negative'
    || registered?.symmetry?.reflected_planes?.includes(plane));
  const mirrored = negativePlanes.length > 0 && (draft.reflectPlanes === undefined || draft.reflectPlanes === null
    || negativePlanes.some(plane => draft.reflectPlanes?.includes(plane)));
  const roleOf = (tag: number) => draft.roles?.[tag] ?? (draft.selected.includes(tag) ? 'HF' : 'RIGID');
  const groupName = (group: NonNullable<typeof summary>['groups'][number]) => group.display_name
    ?? (group.tag === 0 ? 'Untagged surface' : group.name.startsWith('wg-import-v1|') ? sourceRoleLabel(roleOf(group.tag), { driver: true }) : group.name);

  // The setup this file was opened with (a run, a saved setup, a replay), until it is edited.
  const replay = model.input === 'file' && registered && model.setup ? model.setup : null;
  const replayOptions = replay?.options;
  const replayPolar = replayOptions?.polar_config;
  const replayAngleStep = replayPolar?.angle_step ?? (replayPolar
    ? (replayPolar.angle_range[1] - replayPolar.angle_range[0]) / (replayPolar.angle_range[2] - 1) : null);
  const frequencies = replayOptions?.frequencies_hz;
  const frequencySummary = frequencies?.length
    ? `${frequencies.length} explicit frequencies: ${frequencies.slice(0, 8).join(', ')}${frequencies.length > 8 ? ', …' : ''} Hz`
    : `${replayOptions?.frequency_spacing} sweep: ${replayOptions?.frequency_range?.join('–')} Hz, ${replayOptions?.num_frequencies} frequencies`;
  return <SolveOptionsStoreProvider mode="cad"><div className={`param-panel mesh-import-panel${embedded ? '' : ' panel-scroll'}`} aria-label={`Imported mesh ${tab}`}>
    {tab === 'geometry' && <>
    <section className="cad-workflow cad-model-card">
      <header className="cad-model-identity"><b className="cad-model-name">{state.filename || 'Mesh file'}</b></header>
      <CadModelStatusLine file={{ input: 'file', label: state.filename,
        reading: state.busy && !summary, preparing: state.busy && !!summary,
        choice: summary ? meshImportChoice(draft) : null, failureMessage: state.error,
        canPrepare: editable, prepared: !!registered, triangles: summary?.triangle_count ?? null,
        symmetryLabel: fileSymmetryLabel(planes, false),
      }} onRetry={() => !summary && state.original ? loadMeshFile(state.filename, state.original).catch(() => undefined) : prepareMeshFile()}/>
      {summary && <>
        <fieldset className="mesh-surfaces" disabled={!editable}>
          <legend>Surfaces</legend>
          {summary.groups.map(group => <label className="field-row" key={group.tag}>
            <span className="mesh-surface-name"><i className="mesh-surface-swatch" data-role={roleOf(group.tag)} aria-hidden="true"/>{groupName(group)}
              <small>{group.triangle_count.toLocaleString()} triangles</small></span>
            <select aria-label={`Role for ${groupName(group)}`}
              aria-invalid={draft.roles?.[group.tag] === null || !draft.selected.length || undefined}
              value={draft.roles?.[group.tag] === null ? '' : roleOf(group.tag)}
              onChange={e => void editMeshImport({ roles: { ...draft.roles, [group.tag]: e.target.value } })}>
              {draft.roles?.[group.tag] === null && <option value="">Choose a role…</option>}
              {FILE_SURFACE_ROLES.map(role => <option key={role} value={role}>{sourceRoleLabel(role, { driver: true })}</option>)}
            </select>
          </label>)}
        </fieldset>
        {!editable && <p className="section-note">Import the original mesh to change its settings.</p>}
        <div className="cad-domain">
          <p className="cad-domain-line"><b>Symmetry</b> <span>{fileSymmetryLabel(planes)}{negativePlanes.length > 0
            ? mirrored ? ' · mirrored from the negative side' : ' · kept on the negative side' : ''}</span> <button className="link-button" disabled={!editable}
              aria-label="Change symmetry" aria-expanded={symmetryOpen} onClick={() => setSymmetryOpen(!symmetryOpen)}>Change</button></p>
          {symmetryOpen && <fieldset disabled={!editable}>
            <label className="field-row">Symmetry<select aria-label="Mesh symmetry" value={planes.join(',')}
              onChange={e => void editMeshImport({ domainPlanes: e.target.value ? e.target.value.split(',') : [], reflectPlanes: null })}>
              <option value="">Full model</option><option value="x0">Half x = 0</option><option value="y0">Half y = 0</option><option value="x0,y0">Quarter</option>
            </select></label>
            <p className="section-note">Symmetry planes must be x = 0 or y = 0 after axes are applied.</p>
            {negativePlanes.length > 0 && <label className="field-row"><input type="checkbox" aria-label="Mirror from the negative side" checked={mirrored}
              onChange={e => void editMeshImport({ reflectPlanes: e.target.checked ? null : [] })}/>Mirror from the negative side</label>}
          </fieldset>}
        </div>
        <div className="cad-domain">
          <p className="cad-domain-line"><b>Size</b> <span>{draft.units ? `${size} mm · file in ${draft.units === 'm' ? 'metres' : draft.units === 'mm' ? 'millimetres' : draft.units === 'cm' ? 'centimetres' : 'inches'}` : "Choose the file's units."}</span> <button className="link-button" disabled={!editable}
            aria-label="Change size" aria-expanded={sizeOpen || !draft.units} onClick={() => setSizeOpen(!sizeOpen)}>Change</button></p>
          {(sizeOpen || !draft.units) && <label className="field-row">File units<select aria-label="Mesh units" aria-invalid={!draft.units || undefined} disabled={!editable} value={draft.units}
            onChange={e => void editMeshImport({ units: e.target.value })}>
            <option value="">Choose units…</option>{['mm', 'cm', 'm', 'in'].map(unit => <option key={unit}>{unit}</option>)}
          </select></label>}
        </div>
        <FileModelDetails record={registered} summary={summary} setup={replay} axes={<>
          <p className="cad-domain-line">Faces {draft.forward} · up {draft.up} <button className="link-button" disabled={!editable}
            aria-label="Change axes" aria-expanded={axesOpen} onClick={() => setAxesOpen(!axesOpen)}>Change</button></p>
          {axesOpen && <fieldset disabled={!editable}>
            {(['forward', 'up'] as const).map(axis => <label className="field-row" key={axis}>{axis === 'forward' ? 'Forward axis' : 'Up axis'}
              <select aria-label={`Mesh ${axis} axis`} value={draft[axis]} onChange={e => void editMeshImport({ [axis]: e.target.value })}>
                {AXES.map(value => <option key={value} disabled={value.slice(1) === draft[axis === 'forward' ? 'up' : 'forward'].slice(1)}>{value}</option>)}
              </select>
            </label>)}
          </fieldset>}
        </>}/>
      </>}
    </section>
    </>}
    {tab === 'simulation' && <>
    {replay && replayOptions && <section aria-label="Exact mesh replay">
      <h4>Exact replay settings</h4>
      <p>{frequencySummary}</p>
      <p>{replayOptions.polar_config.enabled_axes.join(', ')} polars · {replayAngleStep}° steps · {replayOptions.polar_config.distance} m</p>
      <p>Engine: {replayOptions.engine} · Accuracy: {replayOptions.accuracy ?? 'fast'}</p>
      <details><summary>Full resolved setup</summary><pre aria-label="Full resolved replay setup">{JSON.stringify({ geometry: replay.geometry, options: replayOptions }, null, 2)}</pre></details>
      <p className="section-note">Solve uses these exact settings{model.restoredRevisionId ? ', the setup revision this mesh was opened with' : ''}. Editing a control below starts a new setup from what the controls show.</p>
    </section>}
    <fieldset disabled={state.busy}>
      <legend>{replay ? 'New setup controls' : 'Simulation'}</legend>
      {!replay && <p className="section-note">Each selected group is a separate unit-acceleration source (1 m/s²). No electrical driver model or crossover is applied.</p>}
      {Object.keys(registered?.source_tags ?? {}).map(id => <label className="field-row" key={id}>{state.summary?.groups.find(group => group.tag === registered?.source_tags[id])?.display_name ?? registered?.sources?.find(source => source.id === id)?.name ?? id}
        <select aria-label={`Motion ${id}`} value={state.motions[id] ?? 'normal'} onChange={e => setMeshMotion(id, e.target.value as 'normal' | 'axial')}>
          <option value="normal">Normal to surface</option><option value="axial">Along source axis</option>
        </select>
      </label>)}
      {frequencyMode === 'range' && (['startHz', 'endHz', 'count'] as const).map((key, index) => <label className="field-row" key={key}>{['Start (Hz)', 'End (Hz)', 'Frequencies'][index]}
        <input aria-label={['Mesh sweep start', 'Mesh sweep end', 'Mesh frequency count'][index]} type="number" value={Number.isFinite(sweep[key]) ? sweep[key] : ''}
          min={1} max={key === 'count' ? 401 : undefined} step={key === 'count' ? 1 : 'any'}
          onChange={e => setSweep({ [key]: e.target.valueAsNumber })}/>
      </label>)}
      <SolveOptionsControls mode="cad" />
      {!replay && <p className="section-note">Full domain, free space. The directivity rig below starts as a copy of the parametric settings; the origin of the mesh is not moved automatically. Use Solve above; Runs keeps the exact mesh and settings.</p>}
    </fieldset>
    <fieldset disabled={state.busy}>
      <legend>Directivity map</legend>
      <DirectivityMapControls />
    </fieldset>
    <button disabled={!registered || state.busy || saving} onClick={() => {
      setSaving(true);
      void saveMeshSetup().catch(error => useMeshImportStore.setState({ error: String(error) }))
        .finally(() => setSaving(false));
    }}>{saving ? 'Saving…' : 'Save setup'}</button>
    </>}
    {tab === 'simulation' && state.error && <p className="field-error" role="alert">{state.error}</p>}
    {tab === 'simulation' && state.notice && <p role="status">{state.notice}</p>}
    {tab === 'geometry' && <button onClick={() => {
      noteExplicitNavigation(); restoreParametricWorkingDesign(); workspaceModeStore.setMode('parametric');
    }}>Back to parametric design</button>}
  </div></SolveOptionsStoreProvider>;
}
