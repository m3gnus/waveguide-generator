import { useState } from 'react';
import { editMeshImport, prepareMeshFile, saveMeshSetup, useMeshImportStore } from '../stores/meshImport';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { useCapabilities } from '../jobs/useCapabilities';

const AXES = ['+x', '-x', '+y', '-y', '+z', '-z'];

/** The ready-mesh workflow does not expose CAD generation or connection controls. */
export function MeshImportPanel() {
  const state = useMeshImportStore();
  const globalEngine = useSolveOptionsStore(s => s.engine);
  const globalAccuracy = useSolveOptionsStore(s => s.accuracy);
  const engine = state.engine ?? globalEngine;
  const capabilities = useCapabilities();
  const [preparing, setPreparing] = useState(false);
  const [saving, setSaving] = useState(false);
  const dimensions = state.registered?.summary.dimensions_mm;
  return <div className="param-panel panel-scroll" aria-label="Imported mesh setup">
    <h3>Imported mesh</h3>
    <p>{state.filename}</p>
    <p className="section-note">Full closed surface · free space. Select driven surface groups; all others are rigid.</p>
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
        {state.summary?.groups.map(group => <label className="field-row" key={group.tag}>
          <input type="checkbox" disabled={group.tag === 0} aria-label={`Drive ${group.name} (${group.tag})`} checked={state.selected.includes(group.tag)}
            onChange={e => editMeshImport({ selected: e.target.checked ? [...state.selected, group.tag] : state.selected.filter(tag => tag !== group.tag) })}/>
          {group.name} ({group.tag}) · {group.triangle_count} triangles
        </label>)}
        <button className="primary" disabled={!state.units || !state.selected.length} onClick={() => {
          setPreparing(true); void prepareMeshFile().finally(() => setPreparing(false));
        }}>{state.busy ? 'Preparing…' : state.registered ? 'Prepare again' : 'Prepare mesh'}</button>
      </fieldset>}
    {dimensions && <p role="status">Ready · {state.registered?.summary.triangle_count.toLocaleString()} triangles · {dimensions.map(value => value.toPrecision(4)).join(' × ')} mm</p>}
    <fieldset disabled={state.busy}>
      <legend>Simulation</legend>
      <p className="section-note">Each selected group is a separate unit-acceleration source (1 m/s²). No electrical driver model or crossover is applied.</p>
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
      <p className="section-note">Logarithmic sweep. Full domain, 5° horizontal/vertical/diagonal polars at 2 m from the mesh origin. The origin is not moved automatically. Use Solve above; Runs keeps the exact mesh and settings.</p>
    </fieldset>
    <button disabled={!state.registered || state.busy || saving} onClick={() => {
      setSaving(true);
      void saveMeshSetup().catch(error => useMeshImportStore.setState({ error: String(error) }))
        .finally(() => setSaving(false));
    }}>{saving ? 'Saving…' : 'Save setup'}</button>
    {state.error && <p className="field-error" role="alert">{state.error}</p>}
    {state.notice && <p role="status">{state.notice}</p>}
    <button onClick={() => importedMeshStore.showParametric()}>Return to parametric design</button>
  </div>;
}
