import { useEffect, useState, useSyncExternalStore } from 'react';
import { jobsSocket } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { useCapabilities } from '../jobs/useCapabilities';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { widenPolarToDerivation } from '../jobs/importedSubmission';
import { HelpTipRow, useHelpTip } from './HelpTip';
import {
  MAX_FREQUENCY_POINTS,
  parseFrequencyList,
  polarConfigFromUi,
  polarValidationError,
  polarUiFromConfig,
  useSolveOptionsStore,
  type FrequencyMode,
  type FrequencySpacing,
  type MeshValidationMode,
  type ObservationOrigin,
  type PolarAxis,
  type PolarUiState,
  type SolverMode,
} from '../stores/solveOptions';
import { runDisplayName } from '../prefs/preferences';
import type { WorkspaceMode } from '../stores/workspaceMode';

export const solverModeLabels = {
  auto: 'Auto (fastest eligible)',
  full_3d: 'Full 3D',
  circsym: 'Axisymmetric (meridian)',
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
  const backendEngines = engines.filter((engine) => !['axisym', 'circsym'].includes(engine.name.toLowerCase()));
  const axisymEngine = engines.find((engine) => engine.name.toLowerCase() === 'axisym');
  const meridianAvailable = axisymEngine?.available === true;
  const metalAvailable = engines.some((engine) => engine.name.toLowerCase() === 'metal' && engine.available);
  return <>
    {mode === 'parametric' ? <>
      <HelpTipRow className="select-row" text="Which BEM engine runs the solve. AUTO takes the first backend that is actually available on this machine. All backends solve the same problem; they differ in speed and in which fast paths they support."><label htmlFor="solve-engine">Solver backend</label><select id="solve-engine" value={store.engine} onChange={(event) => store.setEngine(event.target.value)}>
        <option value="auto">AUTO — first available</option>
        {backendEngines.map((engine) => <option key={engine.name} value={engine.name.toLowerCase()} disabled={!engine.available}>{engine.name}{engine.available ? engine.version ? ` · ${engine.version}` : '' : ` · unavailable${engine.reason ? `: ${engine.reason}` : ''}`}</option>)}
      </select></HelpTipRow>
      <p className="section-note">{meridianAvailable
        ? 'Axisymmetric meridian capability: AUTO uses it for eligible circular designs on any OS; the selected backend handles full 3D fallback.'
        : 'Selected backend capability: Full 3D. The axisymmetric runner is unavailable.'}</p>
      <HelpTipRow className="select-row" text="Machine-local formulation choice. AUTO uses the platform-neutral axisymmetric meridian solver for eligible circular designs and the selected full-3D backend otherwise. The choice is not saved into design files."><label htmlFor="solve-mode">Solver path</label><select id="solve-mode" value={store.solverMode} onChange={(event) => store.setSolverMode(event.target.value as SolverMode)}>
        <option value="auto">{solverModeLabels.auto}</option>
        <option value="full_3d">{solverModeLabels.full_3d}</option>
        {(meridianAvailable || store.solverMode === 'circsym') && <option value="circsym" disabled={!meridianAvailable}>{solverModeLabels.circsym}{meridianAvailable ? '' : ' · unavailable'}</option>}
      </select></HelpTipRow>
    </> : <>
      {/* Imported submissions force both values. Static facts keep the rail
          honest without creating a second control that the submit path drops.
          "Metal" is a requirement, not an observation, so where there is no
          Metal engine the rail has to report that rather than assert it. */}
      <p className={`cad-solve-fact${metalAvailable ? '' : ' cad-solve-fact-unavailable'}`}><b>Solver</b><span>Metal · full 3-D · free space{metalAvailable ? '' : ' · unavailable on this machine'}</span></p>
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
export function ToggleRow({ id, label, help, checked, onChange, revealId }: {
  id: string; label: string; help: string; checked: boolean; onChange: (checked: boolean) => void; revealId?: string;
}) {
  const tip = useHelpTip({ title: label, text: help });
  return <label className="toggle-row" htmlFor={id} data-control-reveal-id={revealId} {...tip.triggerProps}>
    <span>{label}</span>
    <input id={id} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    {tip.tip}
  </label>;
}

function PolarNumber({ id, label, help, value, unit, min, max, step = 1, disabled, gridInvalid, errorId, update }: {
  id: string; label: string; help: string; value: number; unit: string; min?: number; max?: number; step?: number; disabled?: boolean; gridInvalid?: boolean; errorId?: string; update: (value: number) => void;
}) {
  const tip = useHelpTip({ title: label, text: help });
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const draftInvalid = draft.trim() === '' || !Number.isFinite(Number(draft));
  return <div className={`field-row polar-number${disabled ? ' field-disabled' : ''}`}><label className="field-label" htmlFor={id} {...tip.triggerProps}>{label}{tip.tip}</label><div className="number-control"><input
    id={id}
    type="number"
    value={draft}
    min={min}
    max={max}
    step={step}
    disabled={disabled}
    aria-invalid={draftInvalid || gridInvalid ? true : undefined}
    aria-describedby={gridInvalid ? errorId : undefined}
    onChange={(event) => {
      const next = event.target.value;
      setDraft(next);
      if (next.trim() && Number.isFinite(Number(next))) update(Number(next));
    }}
    onBlur={() => { if (draftInvalid) setDraft(String(value)); }}
  /><span className="unit">{unit}</span></div></div>;
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

const POLAR_FIELD_LABELS: Array<[keyof PolarUiState, string]> = [
  ['angleStart', 'sweep start'],
  ['angleEnd', 'sweep end'],
  ['angleStep', 'angular step'],
  ['distance', 'measurement distance'],
  ['normAngle', 'normalization angle'],
  ['diagonalAngle', 'diagonal plane angle'],
  ['observationOrigin', 'measurement origin'],
  ['sphericalSampling', '3D balloon'],
  ['fieldPlane', 'field plane'],
];

/** Which directivity settings the selected run differs from on screen. */
export function polarDifferences(run: PolarUiState, current: PolarUiState): string[] {
  const differences = POLAR_FIELD_LABELS.flatMap(([key, label]) => {
    const left = run[key];
    const right = current[key];
    const same = typeof left === 'number' && typeof right === 'number'
      ? Math.abs(left - right) <= Math.max(1e-9, Math.abs(left) * 1e-9)
      : left === right;
    return same ? [] : [label];
  });
  const sameAxes = run.enabledAxes.length === current.enabledAxes.length
    && run.enabledAxes.every((axis, index) => axis === current.enabledAxes[index]);
  return sameAxes ? differences : [...differences, 'planes'];
}

function polarSummary(polar: PolarUiState): string {
  const angle = (value: number) => `${String(Number(value.toFixed(6))).replace('-', '−')}°`;
  const axes = polar.enabledAxes.map((axis) => AXIS_INITIALS[axis]).join(' + ');
  return `${polar.distance} m from ${polar.observationOrigin} · ${angle(polar.angleStart)} … ${angle(polar.angleEnd)}, ${angle(polar.angleStep)} step · norm ${angle(polar.normAngle)} · ${axes}`;
}

/**
 * What the selected run was actually measured with.
 *
 * Reviewing an old result used to leave these fields showing the current
 * draft, so the settings a run was made with were only visible if the
 * Simulation Summary panel happened to be open. This states them where they
 * are read, and only when they differ from what is on screen -- a run that
 * matches the current settings needs no note.
 */
export function SolvedWithReadout() {
  const selection = useSyncExternalStore(compareSelection.subscribe, compareSelection.getSnapshot, compareSelection.getSnapshot);
  const snapshot = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot);
  const current = useSolveOptionsStore((state) => state.polar);
  const updatePolar = useSolveOptionsStore((state) => state.updatePolar);

  const job = selection.primary ? snapshot.jobs.find((item) => item.id === selection.primary) : undefined;
  const runPolar = polarUiFromConfig(job?.solve_options?.polar_config);
  if (!job || !runPolar) return null;
  const differences = polarDifferences(runPolar, current);
  if (!differences.length) return null;

  return <div className="solved-with-readout" role="status">
    <b>{runDisplayName(job, 'short')} was solved with</b>
    <span>{polarSummary(runPolar)}</span>
    <small>Differs from the settings above in {differences.join(', ')}. The charts show the run; these fields describe the next solve.</small>
    <button type="button" onClick={() => updatePolar(runPolar)}>Use the run’s settings</button>
  </div>;
}

export function DirectivityMapControls({ effectiveDerivation }: { effectiveDerivation?: Record<string, unknown> } = {}) {
  const polar = useSolveOptionsStore((state) => state.polar);
  const update = useSolveOptionsStore((state) => state.updatePolar);
  const toggleAxis = useSolveOptionsStore((state) => state.toggleAxis);
  const validationError = polarValidationError(polar);
  const validationErrorId = 'polar-grid-error';
  const numeric = (key: keyof Pick<PolarUiState, 'angleStart' | 'angleEnd' | 'angleStep' | 'distance' | 'normAngle' | 'diagonalAngle'>) => (value: number) => {
    if (Number.isFinite(value)) update({ [key]: value });
  };
  const axisHelp = useHelpTip({ title: 'Directivity planes', text: 'Which planes through the horn axis are measured. Horizontal and vertical are the usual pair; diagonal catches what a non-round mouth does between them.' });
  const axisDescribedBy = [axisHelp.triggerProps['aria-describedby'], validationError ? validationErrorId : null]
    .filter(Boolean).join(' ') || undefined;
  const effective = effectiveDerivation && !validationError ? effectiveGridView(polar, effectiveDerivation) : null;
  // A diagonal on a multiple of 90 degrees is literally the horizontal or the
  // vertical plane, so the solve measures the same plane twice. It is a legal
  // request and stays one -- the file round-trips it -- but it costs a second
  // identical chart, which is worth saying rather than leaving to be noticed.
  const cardinalDiagonal = polar.enabledAxes.includes('diagonal')
    && Number.isFinite(polar.diagonalAngle)
    && Math.abs(polar.diagonalAngle % 90) < 1e-6;
  return <>
    <PolarNumber id="polar-angle-start" label="Sweep start" help="First off-axis angle measured in each directivity plane. 0° is on-axis; negative angles cover the other side." value={polar.angleStart} unit="°" step={1} gridInvalid={Boolean(validationError)} errorId={validationErrorId} update={numeric('angleStart')} />
    <PolarNumber id="polar-angle-end" label="Sweep end" help="Last off-axis angle measured in each directivity plane. 90° reaches the baffle plane." value={polar.angleEnd} unit="°" step={1} gridInvalid={Boolean(validationError)} errorId={validationErrorId} update={numeric('angleEnd')} />
    <PolarNumber id="polar-angle-step" label="Angular step" help="Spacing between measured angles. Finer steps give smoother directivity maps and cost almost nothing, because the field is evaluated after the solve rather than solved again." value={polar.angleStep} unit="°" min={1} step={1} gridInvalid={Boolean(validationError)} errorId={validationErrorId} update={numeric('angleStep')} />
    <PolarNumber id="polar-distance" label="Measurement distance" help="How far from the horn the virtual microphone sits. Keep it well beyond the mouth so the result is a far-field pattern." value={polar.distance} unit="m" min={.1} step={.1} gridInvalid={Boolean(validationError)} errorId={validationErrorId} update={numeric('distance')} />
    <PolarNumber id="polar-norm-angle" label="Normalization angle" help="The angle held at 0 dB in normalized directivity plots. Every polar curve is shifted so this angle reads flat; it changes only the display, never the solve." value={polar.normAngle} unit="°" step={1} gridInvalid={Boolean(validationError)} errorId={validationErrorId} update={numeric('normAngle')} />
    <div className="axis-toggles" role="group" aria-label="Directivity planes" {...axisHelp.triggerProps} aria-invalid={validationError ? true : undefined} aria-describedby={axisDescribedBy}>{(['horizontal', 'vertical', 'diagonal'] as PolarAxis[]).map((axis) => <label key={axis}><input type="checkbox" checked={polar.enabledAxes.includes(axis)} onChange={() => toggleAxis(axis)} /> {axis}</label>)}{axisHelp.tip}</div>
    <PolarNumber id="polar-diagonal-angle" label="Diagonal plane angle" help="Where the diagonal plane sits, measured from the horizontal. 45° is the corner of a square mouth. Only used when the diagonal plane is enabled." value={polar.diagonalAngle} unit="°" step={1} disabled={!polar.enabledAxes.includes('diagonal')} gridInvalid={Boolean(validationError)} errorId={validationErrorId} update={numeric('diagonalAngle')} />
    {validationError && <div id={validationErrorId} className="field-error polar-grid-error" role="alert">{validationError}</div>}
    {cardinalDiagonal && <p className="section-note" role="status">A diagonal at {Number(polar.diagonalAngle.toFixed(6))}° is the {Math.abs((polar.diagonalAngle % 180 + 180) % 180 - 90) < 1e-6 ? 'vertical' : 'horizontal'} plane, so it will be measured and plotted twice. Use an angle between the planes, such as 45°.</p>}
    <HelpTipRow className="select-row" text="The point the measurement angles pivot around. Mouth rotates about the mouth centre, which is what a measured polar set matches; Throat pivots at the driver instead."><label htmlFor="polar-observation-origin">Measurement origin</label><select id="polar-observation-origin" value={polar.observationOrigin} onChange={(event) => update({ observationOrigin: event.target.value as ObservationOrigin })}><option value="mouth">Mouth</option><option value="throat">Throat</option></select></HelpTipRow>
    <ToggleRow id="polar-spherical-sampling" label="Keep 3D balloon result" help="WG samples a spherical field for Directivity Index independently of the selected H/V/D display planes. Enable this to retain that grid for the 3D balloon and forward-beam views; availability depends on the backend." checked={polar.sphericalSampling} onChange={(sphericalSampling) => update({ sphericalSampling })} />
    <ToggleRow id="polar-field-plane" label="Keep field plane data" help="Retains the surface data needed for acoustic field planes. This needs a full-3D solve (not axisymmetric) and adds ~0.1–1 MB per typical parametric job, with larger results for CAD-link imports." checked={polar.fieldPlane !== false} onChange={(fieldPlane) => update({ fieldPlane })} />
    <p className="section-note">Directivity Index always uses the complete spherical field. “Keep 3D balloon result” controls whether WG also stores that field for 3D views.</p>
    {effective && <div className={`effective-grid-readout${effective.widened ? ' widened' : ''}`} role="status"><b>{effective.summary}</b><span>{effective.detail}</span><small>Display planes and angle range only; Directivity Index always uses the complete spherical field.</small></div>}
    <SolvedWithReadout/>
  </>;
}
