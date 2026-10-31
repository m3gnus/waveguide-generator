import { planeWords } from '../api/domainDecision';
import { useState } from 'react';
import { useDeveloperDetails } from '../prefs/preferences';
import { CadSimulationSections } from './ParamPanel';
import { editMeshImport, useMeshImportStore } from '../stores/meshImport';
import { FILE_SURFACE_ROLES, fileSymmetryLabel, sourceRoleLabel } from '../model/cadLabels';
import { useMeshFileEditable } from '../shell/CadModelStatusLine';
import { FileModelDetails } from '../shell/CadModelDetails';
import { useImportedModelStore } from '../stores/importedModel';
import type { RegisteredMesh } from '../api/meshImport';
import { workspaceModeStore } from '../stores/workspaceMode';
import { restoreParametricWorkingDesign } from '../model/currentModel';
import { noteExplicitNavigation } from '../shell/workspaceNavigation';
import { SolveOptionsStoreProvider } from '../stores/solveOptionsContext';

import '../shell/cadLinkPanel.css';

const AXES = ['+x', '-x', '+y', '-y', '+z', '-z'];

/** The ready-mesh workflow does not expose CAD generation or connection controls. */
export function MeshImportPanel({ tab, embedded = false }: { tab: 'geometry' | 'simulation'; embedded?: boolean }) {
  const state = useMeshImportStore();
  const model = useImportedModelStore();
  const draft = model.fileDraft;
  const registered = model.input === 'file' ? model.prepared as RegisteredMesh | null : null;
  const editable = useMeshFileEditable();
  const developer = useDeveloperDetails();
  const [symmetryOpen, setSymmetryOpen] = useState(false);
  const [sizeOpen, setSizeOpen] = useState(false);
  const [axesOpen, setAxesOpen] = useState(false);
  const planes = draft.domainPlanes ?? [];
  const effectivePlanes = registered?.symmetry?.domain_planes ?? planes;
  const summary = state.summary ?? registered?.summary ?? null;
  const scale = ({ mm: 1, cm: 10, m: 1000, in: 25.4 } as Record<string, number>)[draft.units];
  const rawDimensions = summary?.dimensions ?? [];
  const forwardIndex = AXES.indexOf(draft.forward) >> 1;
  const upIndex = AXES.indexOf(draft.up) >> 1;
  const rightIndex = [0, 1, 2].find(index => index !== forwardIndex && index !== upIndex) ?? 0;
  const dimensions = registered?.summary.dimensions_mm ?? summary?.dimensions_mm
    ?? [rightIndex, upIndex, forwardIndex].map(index => rawDimensions[index] * scale);
  const size = dimensions.map((value, index) => Number((value * (effectivePlanes.includes(index === 0 ? 'x0' : index === 1 ? 'y0' : '') ? 2 : 1)).toPrecision(4))).join(' × ');
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
      {/* Its status line and action live in Results, once. */}
      <header className="cad-model-identity"><b className="cad-model-name">{state.filename || 'Mesh file'}</b></header>
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
          <p className="cad-domain-line"><b>Symmetry</b> <span>{fileSymmetryLabel(effectivePlanes)}{negativePlanes.length > 0
            ? mirrored ? ' · mirrored from the negative side' : ' · kept on the negative side' : ''}</span> <button className="link-button" disabled={!editable}
              aria-label="Change symmetry" aria-expanded={symmetryOpen} onClick={() => setSymmetryOpen(!symmetryOpen)}>Change</button></p>
          {symmetryOpen && <fieldset disabled={!editable}>
            <label className="field-row">Symmetry<select aria-label="Mesh symmetry" value={draft.autoCut ? 'auto' : planes.join(',')}
              onChange={e => void editMeshImport({ domainPlanes: e.target.value && e.target.value !== 'auto' ? e.target.value.split(',') : [], autoCut: e.target.value === 'auto', reflectPlanes: null,
                removeLidPlanes: (draft.removeLidPlanes ?? []).filter(plane => e.target.value.split(',').includes(plane)) })}>
              <option value="auto">Auto · cut for speed</option><option value="">Full model</option><option value="x0">Half x = 0</option><option value="y0">Half y = 0</option><option value="x0,y0">Quarter</option>
            </select></label>
            <p className="section-note">Symmetry planes must be x = 0 or y = 0 after axes are applied.</p>
            {planes.filter(plane => summary?.lids?.[plane]?.candidate || draft.removeLidPlanes?.includes(plane)).map(plane => <label className="field-row" key={plane}>
              <input type="checkbox" checked={draft.removeLidPlanes?.includes(plane) ?? false}
                onChange={e => void editMeshImport({ removeLidPlanes: e.target.checked
                  ? [...(draft.removeLidPlanes ?? []), plane] : (draft.removeLidPlanes ?? []).filter(item => item !== plane) })}/>
              Remove the closed face on {planeWords(plane)}
            </label>)}
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
      {developer && <details className="developer-details"><summary>Full resolved setup</summary><pre aria-label="Full resolved replay setup">{JSON.stringify({ geometry: replay.geometry, options: replayOptions }, null, 2)}</pre></details>}
      <p className="section-note">Solve uses these exact settings{model.restoredRevisionId ? ', the setup revision this mesh was opened with' : ''}. Editing a control below starts a new setup from what the controls show.</p>
    </section>}
    <fieldset disabled={state.busy || !registered}>
      <CadSimulationSections searching={false} matches={() => true}/>
    </fieldset>
    </>}
    {tab === 'simulation' && state.error && <p className="field-error" role="alert">{state.error}</p>}
    {tab === 'geometry' && <button onClick={() => {
      noteExplicitNavigation(); restoreParametricWorkingDesign(); workspaceModeStore.setMode('parametric');
    }}>Back to parametric design</button>}
  </div></SolveOptionsStoreProvider>;
}
