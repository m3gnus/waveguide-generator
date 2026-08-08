import { useCapabilities } from '../jobs/useCapabilities';
import {
  MAX_FREQUENCY_POINTS,
  parseFrequencyList,
  useSolveOptionsStore,
  type FrequencyMode,
  type FrequencySpacing,
  type MeshValidationMode,
  type ObservationOrigin,
  type PolarAxis,
  type PolarUiState,
} from '../stores/solveOptions';

export const solverModeLabels = {
  auto: 'Auto',
  full_3d: 'Full 3D',
  circsym: 'Axisymmetric (force)',
} as const;

/**
 * Sweep-point source: a generated grid, or the exact frequencies to solve.
 *
 * The note about runtime is deliberate. Every point costs the same in this BEM
 * (same-size matrix at every frequency), so a list is a tool for placing detail
 * where it matters -- not a way to make a sweep cheaper by thinning the top end.
 */
export function FrequencySweepControls() {
  const store = useSolveOptionsStore();
  const { frequencies, error } = parseFrequencyList(store.frequencyListText);
  return <>
    <div className="select-row"><label htmlFor="frequency-mode">Sweep points</label><select id="frequency-mode" value={store.frequencyMode} onChange={(event) => store.setFrequencyMode(event.target.value as FrequencyMode)}><option value="range">Generated grid</option><option value="list">Explicit list</option></select></div>
    {store.frequencyMode === 'range'
      ? <div className="select-row"><label htmlFor="frequency-spacing">Sweep spacing</label><select id="frequency-spacing" value={store.frequencySpacing} onChange={(event) => store.setFrequencySpacing(event.target.value as FrequencySpacing)}><option value="log">Logarithmic</option><option value="linear">Linear</option></select></div>
      : <div className="point-paste">
          <textarea id="frequency-list" aria-label="Solver frequencies in Hz" rows={4} value={store.frequencyListText} onChange={(event) => store.setFrequencyListText(event.target.value)} placeholder={'500, 630, 800, 1000\n1250 1600 2000'} />
          <div className="paste-meta">{frequencies
            ? <>Solving <b>{frequencies.length}</b> points · <b>{frequencies[0]}</b> to <b>{frequencies[frequencies.length - 1]}</b> Hz</>
            : <>Ascending Hz, separated by commas, spaces, or newlines · up to {MAX_FREQUENCY_POINTS}</>}</div>
          {error && <div className="field-error" role="alert">{error}</div>}
          <p className="section-note">These frequencies replace the range and count from the design, and sweep spacing no longer applies. Solve time scales with how many points you list, not where they sit — every frequency costs about the same.</p>
        </div>}
  </>;
}

export function SolveOptionsControls() {
  const store = useSolveOptionsStore();
  const { engines, error } = useCapabilities();
  const backendEngines = engines.filter((engine) => engine.name.toLowerCase() !== 'circsym');
  const selectedEngine = store.engine === 'auto'
    ? ['metal', 'bempp', 'dryrun'].flatMap((name) => backendEngines.filter((engine) => engine.available && engine.name.toLowerCase() === name))[0]
    : backendEngines.find((engine) => engine.name.toLowerCase() === store.engine);
  const fastPaths = selectedEngine?.fast_paths ?? [];
  return <>
    <div className="select-row"><label htmlFor="solve-engine">Solver backend</label><select id="solve-engine" value={store.engine} onChange={(event) => store.setEngine(event.target.value)}>
      <option value="auto">AUTO — first available</option>
      {backendEngines.map((engine) => <option key={engine.name} value={engine.name.toLowerCase()} disabled={!engine.available}>{engine.name}{engine.available ? engine.version ? ` · ${engine.version}` : '' : ` · unavailable${engine.reason ? `: ${engine.reason}` : ''}`}</option>)}
    </select></div>
    <p className="section-note">{selectedEngine?.name.toLowerCase() === 'metal' && fastPaths.includes('axisymmetric-meridian')
      ? 'Metal capability: automatic axisymmetric meridian fast path when the geometry is eligible.'
      : 'Selected backend capability: Full 3D.'}</p>
    <p className="section-note">Solver mode labels: {solverModeLabels.auto}, {solverModeLabels.full_3d}, {solverModeLabels.circsym}.</p>
    <div className="select-row"><label htmlFor="mesh-validation-mode">Mesh validation policy</label><select id="mesh-validation-mode" value={store.meshValidationMode} onChange={(event) => store.setMeshValidationMode(event.target.value as MeshValidationMode)}><option value="warn">Warn</option><option value="strict">Strict</option><option value="off">Off</option></select></div>
    <FrequencySweepControls />
    <label className="toggle-row" htmlFor="solve-verbose"><span>Verbose backend logging</span><input id="solve-verbose" type="checkbox" checked={store.verbose} onChange={(event) => store.setVerbose(event.target.checked)} /></label>
    {error && <div className="field-error" role="alert">Capabilities unavailable: {error}</div>}
  </>;
}

function PolarNumber({ id, label, value, unit, min, max, step = 1, disabled, update }: {
  id: string; label: string; value: number; unit: string; min?: number; max?: number; step?: number; disabled?: boolean; update: (value: number) => void;
}) {
  return <div className={`field-row polar-number${disabled ? ' field-disabled' : ''}`}><label className="field-label" htmlFor={id}>{label}</label><div className="number-control"><input id={id} type="number" value={value} min={min} max={max} step={step} disabled={disabled} onChange={(event) => update(Number(event.target.value))} /><span className="unit">{unit}</span></div></div>;
}

export function DirectivityMapControls() {
  const polar = useSolveOptionsStore((state) => state.polar);
  const update = useSolveOptionsStore((state) => state.updatePolar);
  const toggleAxis = useSolveOptionsStore((state) => state.toggleAxis);
  const numeric = (key: keyof Pick<PolarUiState, 'angleStart' | 'angleEnd' | 'angleStep' | 'distance' | 'normAngle' | 'diagonalAngle'>) => (value: number) => {
    if (Number.isFinite(value)) update({ [key]: value });
  };
  return <>
    <PolarNumber id="polar-angle-start" label="Sweep start" value={polar.angleStart} unit="°" step={1} update={numeric('angleStart')} />
    <PolarNumber id="polar-angle-end" label="Sweep end" value={polar.angleEnd} unit="°" step={1} update={numeric('angleEnd')} />
    <PolarNumber id="polar-angle-step" label="Angular step" value={polar.angleStep} unit="°" min={1} step={1} update={numeric('angleStep')} />
    <PolarNumber id="polar-distance" label="Measurement distance" value={polar.distance} unit="m" min={.1} step={.1} update={numeric('distance')} />
    <PolarNumber id="polar-norm-angle" label="Normalization angle" value={polar.normAngle} unit="°" step={1} update={numeric('normAngle')} />
    <div className="axis-toggles" role="group" aria-label="Directivity planes">{(['horizontal', 'vertical', 'diagonal'] as PolarAxis[]).map((axis) => <label key={axis}><input type="checkbox" checked={polar.enabledAxes.includes(axis)} onChange={() => toggleAxis(axis)} /> {axis}</label>)}</div>
    <PolarNumber id="polar-diagonal-angle" label="Diagonal plane angle" value={polar.diagonalAngle} unit="°" step={1} disabled={!polar.enabledAxes.includes('diagonal')} update={numeric('diagonalAngle')} />
    <div className="select-row"><label htmlFor="polar-observation-origin">Measurement origin</label><select id="polar-observation-origin" value={polar.observationOrigin} onChange={(event) => update({ observationOrigin: event.target.value as ObservationOrigin })}><option value="mouth">Mouth</option><option value="throat">Throat</option></select></div>
    <label className="toggle-row" htmlFor="polar-spherical-sampling"><span>3D balloon sampling</span><input id="polar-spherical-sampling" type="checkbox" checked={polar.sphericalSampling} onChange={(event) => update({ sphericalSampling: event.target.checked })} /></label>
    <p className="section-note">When enabled, the solve request asks the backend to sample a spherical balloon; availability depends on the selected engine.</p>
  </>;
}
