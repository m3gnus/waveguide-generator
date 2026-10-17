import { useState } from 'react';
import { editMeshImport, prepareMeshFile, saveMeshSetup, useMeshImportStore } from '../stores/meshImport';
import { workspaceModeStore } from '../stores/workspaceMode';
import { restoreParametricWorkingDesign } from '../jobs/showJobModel';
import { noteExplicitNavigation } from '../shell/workspaceNavigation';
import { useImportedSolveOptionsStore } from '../stores/solveOptions';
import { useCapabilities } from '../jobs/useCapabilities';

const AXES = ['+x', '-x', '+y', '-y', '+z', '-z'];

/** The ready-mesh workflow does not expose CAD generation or connection controls. */
export function MeshImportPanel() {
  const state = useMeshImportStore();
  const globalEngine = useImportedSolveOptionsStore(s => s.engine);
  const globalAccuracy = useImportedSolveOptionsStore(s => s.accuracy);
  const engine = state.engine ?? globalEngine;
  const capabilities = useCapabilities();
  const [preparing, setPreparing] = useState(false);
  const [saving, setSaving] = useState(false);
  const dimensions = state.registered?.summary.dimensions_mm;
  const replay = state.exactSubmission;
  const replayOptions = replay?.options;
  const replayPolar = replayOptions?.polar_config;
  const replayAngleStep = replayPolar?.angle_step ?? (replayPolar
    ? (replayPolar.angle_range[1] - replayPolar.angle_range[0]) / (replayPolar.angle_range[2] - 1) : null);
  const frequencies = replayOptions?.frequencies_hz;
  const frequencySummary = frequencies?.length
    ? `${frequencies.length} explicit frequencies: ${frequencies.slice(0, 8).join(', ')}${frequencies.length > 8 ? ', …' : ''} Hz`
    : `${replayOptions?.frequency_spacing} sweep: ${replayOptions?.frequency_range?.join('–')} Hz, ${replayOptions?.num_frequencies} frequencies`;
  return <div className="param-panel mesh-import-panel panel-scroll" aria-label="Imported mesh setup">
    <h3>Imported mesh</h3>
    <p>{state.filename}</p>
    <p className="section-note">Full closed surface · free space. Choose radiating surfaces below; all other labels are rigid.</p>
    {state.summary?.groups.some(group => group.tag === 0) && <p className="section-note">Unassigned triangles are available for inspection only. Assign physical surface groups in the authoring tool and import again to solve.</p>}
    {!state.original && state.registered
      ? <p className="section-note">This retained mesh uses {state.units}, forward {state.forward}, up {state.up}. Import the original file again to change geometry or groups.</p>
      : <fieldset disabled={state.busy || preparing}>
        <legend>Geometry</legend>
        <label className="field-row">Units<select aria-label="Mesh units" value={state.units} onChange={e => editMeshImport({ units: e.target.value })}>
          <option value="">Choose units…</option>{['mm', 'cm', 'm', 'in'].map(unit => <option key={unit}>{unit}</option>)}
        </select></label>
        {(['forward', 'up'] as const).map(axis => <label className="field-row" key={axis}>{axis === 'forward' ? 'Forward axis' : 'Up axis'}
          <select aria-label={`Mesh ${axis} axis`} value={state[axis]} onChange={e => editMeshImport({ [axis]: e.target.value })}>
            {AXES.map(value => <option key={value}>{value}</option>)}
          </select>
        </label>)}
        <p className="section-note">Forward becomes +Z and up becomes +Y. Axes must be perpendicular.</p>
      </fieldset>}
    <fieldset disabled={state.busy || preparing || (!state.original && !!state.registered)}>
      <legend>Radiating surfaces</legend>
      <p className="section-note">Check the mesh labels that act as sound sources. Each checked label is a separate source; unchecked labels are rigid.</p>
      {!state.selected.length && <p role="status">No radiating surfaces selected. Select at least one label to prepare and solve.</p>}
      {state.summary?.groups.map(group => <label className="field-row" key={group.tag}>
          <input type="checkbox" disabled={group.tag === 0} aria-label={`Use ${group.name} (${group.tag}) as a source`} checked={state.selected.includes(group.tag)}
            onChange={e => editMeshImport({ selected: e.target.checked ? [...state.selected, group.tag] : state.selected.filter(tag => tag !== group.tag) })}/>
          {group.name} ({group.tag}) · {group.triangle_count} triangles
        </label>)}
      {!state.original && state.registered && <p className="section-note">These are the retained source assignments. Import the original mesh to change them.</p>}
    </fieldset>
    {state.original && <fieldset disabled={state.busy || preparing}>
        <button className="primary" disabled={!state.units || !state.selected.length} onClick={() => {
          setPreparing(true); void prepareMeshFile().finally(() => setPreparing(false));
        }}>{state.busy ? 'Preparing…' : state.registered ? 'Prepare again' : 'Prepare mesh'}</button>
      </fieldset>}
    {dimensions && <p role="status">Ready · {state.registered?.summary.triangle_count.toLocaleString()} triangles · {dimensions.map(value => value.toPrecision(4)).join(' × ')} mm</p>}
    {replay && replayOptions && <section aria-label="Exact mesh replay">
      <h4>Exact replay settings</h4>
      <p>{frequencySummary}</p>
      <p>{replayOptions.polar_config.enabled_axes.join(', ')} polars · {replayAngleStep}° steps · {replayOptions.polar_config.distance} m</p>
      <p>Engine: {replayOptions.engine} · Accuracy: {replayOptions.accuracy ?? 'fast'}</p>
      <details><summary>Full resolved setup</summary><pre aria-label="Full resolved replay setup">{JSON.stringify({ geometry: replay.geometry, options: replayOptions }, null, 2)}</pre></details>
      <p className="section-note">Solve uses these exact settings. Editing a control below or a global solve control starts a new setup with unit-acceleration sources, a logarithmic sweep and default 5° polars at 2 m.</p>
    </section>}
    <fieldset disabled={state.busy}>
      <legend>{replay ? 'New setup controls' : 'Simulation'}</legend>
      {!replay && <p className="section-note">Each selected group is a separate unit-acceleration source (1 m/s²). No electrical driver model or crossover is applied.</p>}
      {Object.keys(state.registered?.source_tags ?? {}).map(id => <label className="field-row" key={id}>{state.summary?.groups.find(group => group.tag === state.registered?.source_tags[id])?.name ?? id}
        <select aria-label={`Motion ${id}`} value={state.motions[id] ?? 'normal'} onChange={e => useMeshImportStore.setState({ motions: { ...state.motions, [id]: e.target.value as 'normal' | 'axial' } })}>
          <option value="normal">Normal to surface</option><option value="axial">Along source axis</option>
        </select>
      </label>)}
      {(['startHz', 'endHz', 'count'] as const).map((key, index) => <label className="field-row" key={key}>{['Start (Hz)', 'End (Hz)', 'Frequencies'][index]}
        <input aria-label={['Mesh sweep start', 'Mesh sweep end', 'Mesh frequency count'][index]} type="number" value={Number.isFinite(state[key]) ? state[key] : ''}
          min={1} max={key === 'count' ? 401 : undefined} step={key === 'count' ? 1 : 'any'}
          onChange={e => useMeshImportStore.setState({ [key]: e.target.valueAsNumber })}/>
      </label>)}
      <label className="field-row">Engine<select aria-label="Mesh solve engine" value={engine} onChange={e => useMeshImportStore.setState({ engine: e.target.value })}>
        <option value="auto">Auto</option>
        {capabilities.engines.filter(item => item.geometry_sources?.includes('imported')).map(item => <option key={item.name} value={item.name}>{item.label || item.name}{item.available ? '' : ' (unavailable)'}</option>)}
        {engine !== 'auto' && !capabilities.engines.some(item => item.name === engine && item.geometry_sources?.includes('imported')) && <option value={engine}>{engine} (unsupported)</option>}
      </select></label>
      <label className="field-row">Accuracy<select aria-label="Mesh solve accuracy" value={state.accuracy ?? globalAccuracy} onChange={e => useMeshImportStore.setState({ accuracy: e.target.value as 'fast' | 'accurate' })}>
        <option value="fast">Fast</option><option value="accurate">Accurate</option>
      </select></label>
      {!replay && <p className="section-note">Logarithmic sweep. Full domain, 5° horizontal/vertical/diagonal polars at 2 m from the mesh origin. The origin is not moved automatically. Use Solve above; Runs keeps the exact mesh and settings.</p>}
    </fieldset>
    <button disabled={!state.registered || state.busy || saving} onClick={() => {
      setSaving(true);
      void saveMeshSetup().catch(error => useMeshImportStore.setState({ error: String(error) }))
        .finally(() => setSaving(false));
    }}>{saving ? 'Saving…' : 'Save setup'}</button>
    {state.error && <p className="field-error" role="alert">{state.error}</p>}
    {state.notice && <p role="status">{state.notice}</p>}
    <button onClick={() => {
      noteExplicitNavigation(); restoreParametricWorkingDesign(); workspaceModeStore.setMode('parametric');
    }}>Return to parametric design</button>
  </div>;
}
