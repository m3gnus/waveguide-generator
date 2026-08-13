import { useCapabilities } from '../jobs/useCapabilities';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { widenPolarToDerivation } from '../jobs/importedSubmission';
import { HelpTipRow, useHelpTip } from './HelpTip';
import {
  MAX_FREQUENCY_POINTS,
  parseFrequencyList,
  polarConfigFromUi,
  useSolveOptionsStore,
  type FrequencyMode,
  type FrequencySpacing,
  type MeshValidationMode,
  type ObservationOrigin,
  type PolarAxis,
  type PolarUiState,
} from '../stores/solveOptions';
import type { WorkspaceMode } from '../stores/workspaceMode';

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
export function FrequencySweepControls({ idPrefix, context }: { idPrefix: string; context: 'design' | 'imported' }) {
  const store = useSolveOptionsStore();
  const { frequencies, error } = parseFrequencyList(store.frequencyListText);
  const modeId = `${idPrefix}-frequency-mode`;
  const spacingId = `${idPrefix}-frequency-spacing`;
  const listId = `${idPrefix}-frequency-list`;
  const modeHelp = context === 'design'
    ? "Where the solved frequencies come from. Generated grid spreads them between the design's sweep start and end; Explicit list solves exactly the frequencies you type, ignoring the design's range and count."
    : "Where the solved frequencies come from. Generated grid uses the imported solve range below; Explicit list solves exactly the frequencies you type, ignoring that range and count.";
  const listHelp = context === 'design'
    ? 'These frequencies replace the range and count from the design, and sweep spacing no longer applies.'
    : 'These frequencies replace the imported solve range and count, and sweep spacing no longer applies.';
  return <>
    <HelpTipRow className="select-row" text={modeHelp}><label htmlFor={modeId}>Sweep points</label><select id={modeId} value={store.frequencyMode} onChange={(event) => store.setFrequencyMode(event.target.value as FrequencyMode)}><option value="range">Generated grid</option><option value="list">Explicit list</option></select></HelpTipRow>
    {store.frequencyMode === 'range'
      ? <HelpTipRow className="select-row" text="How the generated frequencies are spread across the range. Logarithmic gives even spacing per octave, which matches how the response is read; Linear spends most of the points on the top octave."><label htmlFor={spacingId}>Sweep spacing</label><select id={spacingId} value={store.frequencySpacing} onChange={(event) => store.setFrequencySpacing(event.target.value as FrequencySpacing)}><option value="log">Logarithmic</option><option value="linear">Linear</option></select></HelpTipRow>
      : <div className="point-paste">
          <textarea id={listId} aria-label="Solver frequencies in Hz" rows={4} value={store.frequencyListText} onChange={(event) => store.setFrequencyListText(event.target.value)} placeholder={'500, 630, 800, 1000\n1250 1600 2000'} />
          <div className="paste-meta">{frequencies
            ? <>Solving <b>{frequencies.length}</b> points · <b>{frequencies[0]}</b> to <b>{frequencies[frequencies.length - 1]}</b> Hz</>
            : <>Ascending Hz, separated by commas, spaces, or newlines · up to {MAX_FREQUENCY_POINTS}</>}</div>
          {error && <div className="field-error" role="alert">{error}</div>}
          <p className="section-note">{listHelp} Solve time scales with how many points you list, not where they sit — every frequency costs about the same.</p>
        </div>}
  </>;
}

export function SolveOptionsControls({ mode = 'parametric', ingestRecord = null }: {
  mode?: WorkspaceMode;
  ingestRecord?: CadReturnIngestRecord | null;
} = {}) {
  const store = useSolveOptionsStore();
  const { engines, error } = useCapabilities();
  const backendEngines = engines.filter((engine) => engine.name.toLowerCase() !== 'circsym');
  const selectedEngine = store.engine === 'auto'
    ? ['metal', 'bempp', 'dryrun'].flatMap((name) => backendEngines.filter((engine) => engine.available && engine.name.toLowerCase() === name))[0]
    : backendEngines.find((engine) => engine.name.toLowerCase() === store.engine);
  const fastPaths = selectedEngine?.fast_paths ?? [];
  return <>
    {mode === 'parametric' ? <>
      <HelpTipRow className="select-row" text="Which BEM engine runs the solve. AUTO takes the first backend that is actually available on this machine. All backends solve the same problem; they differ in speed and in which fast paths they support."><label htmlFor="solve-engine">Solver backend</label><select id="solve-engine" value={store.engine} onChange={(event) => store.setEngine(event.target.value)}>
        <option value="auto">AUTO — first available</option>
        {backendEngines.map((engine) => <option key={engine.name} value={engine.name.toLowerCase()} disabled={!engine.available}>{engine.name}{engine.available ? engine.version ? ` · ${engine.version}` : '' : ` · unavailable${engine.reason ? `: ${engine.reason}` : ''}`}</option>)}
      </select></HelpTipRow>
      <p className="section-note">{selectedEngine?.name.toLowerCase() === 'metal' && fastPaths.includes('axisymmetric-meridian')
        ? 'Metal capability: automatic axisymmetric meridian fast path when the geometry is eligible.'
        : 'Selected backend capability: Full 3D.'}</p>
      <p className="section-note">Solver mode labels: {solverModeLabels.auto}, {solverModeLabels.full_3d}, {solverModeLabels.circsym}.</p>
    </> : <>
      {/* Imported submissions force both values. Static facts keep the rail
          honest without creating a second control that the submit path drops. */}
      <p className="cad-solve-fact"><b>Solver</b><span>Metal · full 3-D · free space</span></p>
      <p className="cad-solve-fact"><b>Ingested cut planes</b><span>{ingestRecord?.symmetry.cut_planes?.length ? ingestRecord.symmetry.cut_planes.join(', ') : 'none · full domain'}</span></p>
    </>}
    <HelpTipRow className="select-row" text="What happens when the solver mesh fails its topology check. Warn solves anyway and reports the problem; Strict refuses to solve a mesh that is not watertight; Off hides the warning entirely. Results from an invalid mesh cannot be trusted, so leave this on Warn unless you know why."><label htmlFor="mesh-validation-mode">Mesh validation policy</label><select id="mesh-validation-mode" value={store.meshValidationMode} onChange={(event) => store.setMeshValidationMode(event.target.value as MeshValidationMode)}><option value="warn">Warn</option><option value="strict">Strict</option><option value="off">Off</option></select></HelpTipRow>
    <FrequencySweepControls idPrefix={mode === 'cad' ? 'cad-solve' : 'design-solve'} context={mode === 'cad' ? 'imported' : 'design'} />
    <ToggleRow id="solve-verbose" label="Verbose backend logging" help="Records the backend's own per-frequency diagnostics in the job log. Useful when a solve fails or looks wrong; it makes logs much longer." checked={store.verbose} onChange={store.setVerbose} />
    {error && <div className="field-error" role="alert">Capabilities unavailable: {error}</div>}
  </>;
}

/**
 * Checkbox row that carries its own hover help.
 *
 * The tip attaches to the existing `.toggle-row` label rather than wrapping it,
 * because `.section-body` is a container-query grid whose rules select direct
 * children — an extra div would become the grid item and drop the row's layout.
 */
export function ToggleRow({ id, label, help, checked, onChange }: {
  id: string; label: string; help: string; checked: boolean; onChange: (checked: boolean) => void;
}) {
  const tip = useHelpTip({ title: label, text: help });
  return <label className="toggle-row" htmlFor={id} {...tip.triggerProps}>
    <span>{label}</span>
    <input id={id} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    {tip.tip}
  </label>;
}

function PolarNumber({ id, label, help, value, unit, min, max, step = 1, disabled, update }: {
  id: string; label: string; help: string; value: number; unit: string; min?: number; max?: number; step?: number; disabled?: boolean; update: (value: number) => void;
}) {
  const tip = useHelpTip({ title: label, text: help });
  return <div className={`field-row polar-number${disabled ? ' field-disabled' : ''}`}><label className="field-label" htmlFor={id} {...tip.triggerProps}>{label}{tip.tip}</label><div className="number-control"><input id={id} type="number" value={value} min={min} max={max} step={step} disabled={disabled} onChange={(event) => update(Number(event.target.value))} /><span className="unit">{unit}</span></div></div>;
}

const AXIS_INITIALS: Record<PolarAxis, string> = { horizontal: 'H', vertical: 'V', diagonal: 'D' };

export interface EffectiveGridView {
  summary: string;
  detail: string;
  widened: boolean;
}

/** Preview the exact imported-submit mutation on a disposable options object.
 * This must call widenPolarToDerivation: presenting a separately reimplemented
 * approximation would recreate the silent-submit mismatch this readout fixes. */
export function effectiveGridView(
  polar: PolarUiState,
  derivation: Record<string, unknown>,
): EffectiveGridView {
  const before = polarConfigFromUi(polar);
  const options = {
    engine: 'metal', symmetry: 'auto' as const, mesh_validation_mode: 'warn' as const,
    verbose: false, frequency_spacing: 'log' as const, polar_config: structuredClone(before),
  };
  widenPolarToDerivation(options, derivation);
  const effective = options.polar_config;
  const [start, end, count] = effective.angle_range;
  const step = count > 1 ? (end - start) / (count - 1) : effective.angle_step;
  const axes = effective.enabled_axes.map((axis) => AXIS_INITIALS[axis]).join(' + ');
  const widened = before.angle_range.some((value, index) => value !== effective.angle_range[index])
    || before.enabled_axes.some((axis, index) => axis !== effective.enabled_axes[index])
    || before.enabled_axes.length !== effective.enabled_axes.length;
  const angle = (value: number) => `${String(value).replace('-', '−')}°`;
  return {
    summary: `Effective grid ${angle(start)} … ${angle(end)}, ${Number(step.toFixed(6))}° steps · ${axes || 'no display planes'}`,
    detail: widened
      ? 'Widened from your settings because the returned model requires additional display-plane coverage.'
      : 'Matches your settings; no widening is required.',
    widened,
  };
}

export function DirectivityMapControls({ effectiveDerivation }: { effectiveDerivation?: Record<string, unknown> } = {}) {
  const polar = useSolveOptionsStore((state) => state.polar);
  const update = useSolveOptionsStore((state) => state.updatePolar);
  const toggleAxis = useSolveOptionsStore((state) => state.toggleAxis);
  const numeric = (key: keyof Pick<PolarUiState, 'angleStart' | 'angleEnd' | 'angleStep' | 'distance' | 'normAngle' | 'diagonalAngle'>) => (value: number) => {
    if (Number.isFinite(value)) update({ [key]: value });
  };
  const axisHelp = useHelpTip({ title: 'Directivity planes', text: 'Which planes through the horn axis are measured. Horizontal and vertical are the usual pair; diagonal catches what a non-round mouth does between them.' });
  const effective = effectiveDerivation ? effectiveGridView(polar, effectiveDerivation) : null;
  return <>
    <PolarNumber id="polar-angle-start" label="Sweep start" help="First off-axis angle measured in each directivity plane. 0° is on-axis; negative angles cover the other side." value={polar.angleStart} unit="°" step={1} update={numeric('angleStart')} />
    <PolarNumber id="polar-angle-end" label="Sweep end" help="Last off-axis angle measured in each directivity plane. 90° reaches the baffle plane." value={polar.angleEnd} unit="°" step={1} update={numeric('angleEnd')} />
    <PolarNumber id="polar-angle-step" label="Angular step" help="Spacing between measured angles. Finer steps give smoother directivity maps and cost almost nothing, because the field is evaluated after the solve rather than solved again." value={polar.angleStep} unit="°" min={1} step={1} update={numeric('angleStep')} />
    <PolarNumber id="polar-distance" label="Measurement distance" help="How far from the horn the virtual microphone sits. Keep it well beyond the mouth so the result is a far-field pattern." value={polar.distance} unit="m" min={.1} step={.1} update={numeric('distance')} />
    <PolarNumber id="polar-norm-angle" label="Normalization angle" help="The angle held at 0 dB in normalized directivity plots. Every polar curve is shifted so this angle reads flat; it changes only the display, never the solve." value={polar.normAngle} unit="°" step={1} update={numeric('normAngle')} />
    <div className="axis-toggles" role="group" aria-label="Directivity planes" {...axisHelp.triggerProps}>{(['horizontal', 'vertical', 'diagonal'] as PolarAxis[]).map((axis) => <label key={axis}><input type="checkbox" checked={polar.enabledAxes.includes(axis)} onChange={() => toggleAxis(axis)} /> {axis}</label>)}{axisHelp.tip}</div>
    <PolarNumber id="polar-diagonal-angle" label="Diagonal plane angle" help="Where the diagonal plane sits, measured from the horizontal. 45° is the corner of a square mouth. Only used when the diagonal plane is enabled." value={polar.diagonalAngle} unit="°" step={1} disabled={!polar.enabledAxes.includes('diagonal')} update={numeric('diagonalAngle')} />
    <HelpTipRow className="select-row" text="The point the measurement angles pivot around. Mouth rotates about the mouth centre, which is what a measured polar set matches; Throat pivots at the driver instead."><label htmlFor="polar-observation-origin">Measurement origin</label><select id="polar-observation-origin" value={polar.observationOrigin} onChange={(event) => update({ observationOrigin: event.target.value as ObservationOrigin })}><option value="mouth">Mouth</option><option value="throat">Throat</option></select></HelpTipRow>
    <ToggleRow id="polar-spherical-sampling" label="Keep 3D balloon result" help="WG samples a spherical field for Directivity Index independently of the selected H/V/D display planes. Enable this to retain that grid for the 3D balloon and forward-beam views; availability depends on the backend." checked={polar.sphericalSampling} onChange={(sphericalSampling) => update({ sphericalSampling })} />
    <p className="section-note">Directivity Index always uses the complete spherical field. This option controls whether WG also stores that field for 3D views.</p>
    {effective && <div className={`effective-grid-readout${effective.widened ? ' widened' : ''}`} role="status"><b>{effective.summary}</b><span>{effective.detail}</span><small>Display planes and angle range only; Directivity Index always uses the complete spherical field.</small></div>}
  </>;
}
