import { useCadModelState, type CadModelPhase } from '../stores/cadModelState';
import { designFingerprint } from '../results/runCoherence';
import { importedModelBlocker, useImportedModelStore } from '../stores/importedModel';
import { useShallow } from 'zustand/react/shallow';
import { LiveDimensions } from '../design/LiveDimensions';
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type { DecodedFrame } from '../api/frame';
import { isActiveJobStatus, jobsSocket, type JobItem } from '../api/jobsSocket';
import { PREVIEW_FINE_IDLE_MS, previewSocket } from '../api/previewSocket';
import { compareSelection, provisionalResults } from '../api/results';
import { currentModel, currentModelScene, currentCadScenes, spatialResult, useCurrentModel, type CurrentModel } from '../model/currentModel';
import { postSymmetry, toSolveDesign } from '../jobs/actions';
import { planAdjustmentNotice } from '../jobs/planAdjustments';
import { useOptionalSolveControl } from '../shell/JobsCoordinator';
import { postSolverMesh, solverMeshArtifactToken, solverMeshDesignKey, solverMeshOptions, solverMeshScene } from '../api/solverMesh';
import { cadApplicationName, usePreferences } from '../prefs/preferences';
import { useCadReturnStore } from '../stores/cadReturn';
import { useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { cadCameraAxes } from './cadCamera';
import { currentDocumentLoad, subscribeRevision, useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { useScopedSolveOptions } from '../stores/solveOptionsContext';
import { workspaceModeStore } from '../stores/workspaceMode';
import { cadWorkspace } from '../stores/cadWorkspace';
import { showIngestedMeshInViewport, showIngestedSolverMeshInViewport } from '../shell/cadlink/artifactLoader';
import { Icon } from '../shell/icons';
import { readChartTokens } from '../results/EChart';
import { useViewerPreferences, viewerPreferences, type CameraProjection } from '../viewerprefs/viewerPreferences';
import { ViewerPreferencesPanel } from '../viewerprefs/ViewerPreferencesPanel';
import { frameToScene, hasRenderableSurfaces, MAX_EDGE_TRIANGLES } from './frameScene';
import {
  defaultFieldPlaneWindow,
  fieldPlaneColormap,
  maxFieldSplDb,
  normalizedSplDb,
  phaseDegrees,
  splDb,
  FIELD_PLANE_DISPLAY_MODES,
  FIELD_PLANE_INSTANTANEOUS_PERCENTILE,
  type FieldPlaneDisplayMode,
  type FieldPlaneValueWindow,
} from './fieldPlaneColor';
import { nextSectionCut, SECTION_AXES, type ModelClipMode, type SectionAxis } from './fieldPlaneClipping';
import {
  fieldPlaneOffsetMetres,
  fieldPlanePreset,
  withFieldPlaneExtents,
  withFieldPlaneOffset,
  withFieldPlaneOrigin,
} from './fieldPlaneMath';
import { useFieldPlaneProbeStore } from './fieldPlaneProbe';
import { maskMatchesGeometry, useFieldPlaneMaskStore } from './fieldPlaneMaskStore';
import { defaultFieldPlane, nearestFieldPlaneFrequencyIndex, useFieldPlaneStore } from './fieldPlaneStore';
import { useObservationStore } from './observationStore';
import { FemFieldControls } from './FemFieldControls';
import { FemFieldScene, femInteriorDisplayValues } from './FemFieldScene';
import type { FemFieldRendering, FemDisplayMode } from './FemFieldView';
import { useFemField, useFemPlane } from './useFemField';
import type { FieldPlaneResponseId } from '../api/fieldPlane';
import type { ImportedMeshScene } from './importedMesh';
import { importedMeshStore, type ImportedMeshShowing } from './importedMeshStore';
import { SolverMeshRefreshController, type SolverMeshRefreshState } from './solverMeshRefresh';
import type { CameraDirection } from './cameraMath';
import { ClientLatencyClock, formatClientLatency } from './clientLatency';
import { selectPreferredFrame } from './lodPolicy';
import { previewBadge, previewErrorMessage, staleReason, viewportSubtitle } from './presentation';
import { markParametricSolvedDomain, quadrantsForSolveMode, solvedDomainLabel, type DisplayQuadrants } from './symmetryScene';
import { ROLE_MATERIAL_FAMILY, SOURCE_ROLES } from './types';
import type { DisplayMode, SourceRole, ViewportTheme } from './types';
import { canRenderWebGL, resetWebGLProbe, type CameraRequest, type ZoomRequest, ViewportCanvas } from './ViewportCanvas';
import { cadMeshRecoveryNotice } from '../model/cadMeshRecovery';
import './viewport.css';

/** The role names as the CAD document says them, not as the mesh spells them. */
const SOURCE_ROLE_LABELS: Record<SourceRole, string> = {
  HF: 'HF', MF: 'MF', LF: 'LF', PORT_EXIT: 'Port exit',
};

const modes: Array<{ mode: DisplayMode; title: string; icon?: 'clay' | 'wire' | 'xray' | 'zebra' | 'curve' | 'section'; glyph?: string }> = [
  { mode: 'clay', title: 'Clay', icon: 'clay' },
  { mode: 'solid-wire', title: 'Solid + wireframe', icon: 'wire' },
  { mode: 'wireframe', title: 'Wireframe', icon: 'wire' },
  { mode: 'xray', title: 'X-ray', icon: 'xray' },
  { mode: 'zebra', title: 'Zebra', icon: 'zebra' },
  { mode: 'curvature', title: 'Curvature', icon: 'curve' },
  { mode: 'normals', title: 'Normals — back faces magenta', glyph: 'N' },
  { mode: 'edges', title: 'Hard-boundary edges', icon: 'section' },
];

export function nextDisplayMode(mode: DisplayMode): DisplayMode {
  const index = modes.findIndex((item) => item.mode === mode);
  return modes[(index + 1) % modes.length].mode;
}

/** How long the viewport may lag the design before it says so out loud. */
const STALL_NOTICE_MS = 1_500;
const SYMMETRY_TINT_DEBOUNCE_MS = 400;

function formatPressurePa(value: number): string {
  const magnitude = Math.abs(value);
  if (magnitude === 0) return '0';
  if (magnitude < 0.01 || magnitude >= 1_000) return magnitude.toExponential(3);
  return magnitude.toPrecision(4);
}

export function fieldPlaneWindowReadout(
  mode: FieldPlaneDisplayMode,
  window: FieldPlaneValueWindow,
): string {
  if (mode === 'spl') {
    return `${window.minimum.toFixed(1)}…${window.maximum.toFixed(1)} dB re 20 µPa`;
  }
  if (mode === 'normalized') {
    return `${window.minimum.toFixed(1)}…${window.maximum.toFixed(1)} dB re field max`;
  }
  if (mode === 'phase') {
    return `${window.minimum.toFixed(0)}…${window.maximum.toFixed(0)}°`;
  }
  return `± ${formatPressurePa(window.maximum)} Pa (${FIELD_PLANE_INSTANTANEOUS_PERCENTILE}th pct)`;
}

function displayQuadrants(value: number): DisplayQuadrants {
  return value === 1 || value === 12 || value === 14 ? value : 1234;
}

function documentTheme(): ViewportTheme {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

function useViewportTheme(): ViewportTheme {
  const [theme, setTheme] = useState<ViewportTheme>(documentTheme);
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(documentTheme()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

interface FieldPlaneNumberCellProps {
  label?: string;
  ariaLabel: string;
  value: number;
  step?: number;
  min?: number;
  decimals?: number;
  onCommit: (value: number) => void;
}

/** Numeric cell for the plane transform. It shows the store's value except
 * while it holds focus, and always resyncs on blur so a rejected or clamped
 * entry snaps back to what the plane actually is. */
function FieldPlaneNumberCell({
  label,
  ariaLabel,
  value,
  step = 0.1,
  min,
  decimals = 2,
  onCommit,
}: FieldPlaneNumberCellProps) {
  const canonical = value.toFixed(decimals);
  const [text, setText] = useState(canonical);
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setText(canonical);
  }, [canonical]);
  const commit = () => {
    focused.current = false;
    const next = Number(text);
    setText(canonical);
    if (Number.isFinite(next) && text.trim() !== '') onCommit(next);
  };
  return <label className="field-plane-number">
    {label !== undefined && <span>{label}</span>}
    <input
      type="number"
      aria-label={ariaLabel}
      step={step}
      min={min}
      value={text}
      onFocus={() => { focused.current = true; }}
      onChange={(event) => setText(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur();
      }}
    />
  </label>;
}

const FIELD_PLANE_ORIGIN_AXES: ReadonlyArray<{ label: string; index: 0 | 1 | 2 }> = [
  { label: 'X', index: 0 },
  { label: 'Y', index: 1 },
  { label: 'Z', index: 2 },
];

/** Absolute placement and extents in millimetres, the keyboard counterpart to
 * the viewport gizmo's arrows and corner grips. */
function FieldPlaneTransformInputs() {
  const plane = useFieldPlaneStore((state) => state.plane);
  const origin = plane?.origin_m ?? [0, 0, 0];
  const offsetMm = plane ? fieldPlaneOffsetMetres(plane) * 1_000 : 0;
  return <div className="field-plane-transform">
    <div className="field-plane-transform-row">
      <span>Centre</span>
      {FIELD_PLANE_ORIGIN_AXES.map(({ label, index }) => <FieldPlaneNumberCell
        key={label}
        label={label}
        ariaLabel={`Field plane centre ${label} in millimetres`}
        value={origin[index] * 1_000}
        onCommit={(next) => {
          if (!plane) return;
          const moved = [...plane.origin_m] as [number, number, number];
          moved[index] = next / 1_000;
          useFieldPlaneStore.getState().setPlane(withFieldPlaneOrigin(plane, moved));
        }}
      />)}
    </div>
    <div className="field-plane-transform-row">
      <span>Size</span>
      <FieldPlaneNumberCell
        label="W"
        ariaLabel="Field plane width in millimetres"
        value={(plane?.width_m ?? 0) * 1_000}
        step={1}
        min={1}
        decimals={1}
        onCommit={(next) => {
          if (plane) useFieldPlaneStore.getState().setPlane(withFieldPlaneExtents(plane, next / 1_000, plane.height_m));
        }}
      />
      <FieldPlaneNumberCell
        label="H"
        ariaLabel="Field plane height in millimetres"
        value={(plane?.height_m ?? 0) * 1_000}
        step={1}
        min={1}
        decimals={1}
        onCommit={(next) => {
          if (plane) useFieldPlaneStore.getState().setPlane(withFieldPlaneExtents(plane, plane.width_m, next / 1_000));
        }}
      />
      <FieldPlaneNumberCell
        label="Off"
        ariaLabel="Field plane normal offset in millimetres"
        value={offsetMm}
        onCommit={(next) => {
          if (plane) useFieldPlaneStore.getState().setPlane(withFieldPlaneOffset(plane, next / 1_000));
        }}
      />
    </div>
  </div>;
}

/** Reads out the complex pressure under the pointer. The canvas host is
 * pinned to the panel box, so the probe's canvas-local coordinates place the
 * card directly. */
function FieldPlaneProbeTooltip({ fieldMaxSplDb }: { fieldMaxSplDb: number | null }) {
  const reading = useFieldPlaneProbeStore((state) => state.reading);
  const displayMode = useFieldPlaneStore((state) => state.displayMode);
  if (!reading) return null;
  const flipX = reading.localX > reading.hostWidth - 150;
  const flipY = reading.localY > reading.hostHeight - 96;
  const style = {
    left: `${reading.localX + (flipX ? -12 : 14)}px`,
    top: `${reading.localY + (flipY ? -12 : 16)}px`,
    transform: `translate(${flipX ? '-100%' : '0'}, ${flipY ? '-100%' : '0'})`,
  };
  if (reading.masked) {
    return <div className="field-plane-probe" style={style} role="status">
      <b>inside model</b>
      <span>no exterior field here</span>
    </div>;
  }
  const level = splDb(reading.real, reading.imag);
  return <div className="field-plane-probe" style={style} role="status">
    <b>{level.toFixed(1)} dB</b>
    <span>{phaseDegrees(reading.real, reading.imag).toFixed(0)}° phase</span>
    {displayMode === 'normalized' && fieldMaxSplDb !== null
      && <span>{normalizedSplDb(reading.real, reading.imag, fieldMaxSplDb).toFixed(1)} dB re max</span>}
    {displayMode === 'instantaneous'
      && <span>{formatPressurePa(Math.hypot(reading.real, reading.imag))} Pa peak</span>}
    <span className="field-plane-probe-position">
      u {(reading.offsetU_m * 1_000).toFixed(0)} · v {(reading.offsetV_m * 1_000).toFixed(0)} mm
    </span>
  </div>;
}

export function fieldPlaneJob(
  jobs: readonly JobItem[],
  selectedJobId: string | null,
  context: CurrentModel = currentModel(),
  provisional = provisionalResults.getSnapshot().entries,
): JobItem | null {
  const candidate = spatialResult(context, { primary: selectedJobId }, jobs, provisional);
  return candidate?.field_plane_available === true && candidate.status === 'complete'
    ? candidate
    : null;
}

export function femFieldJob(
  jobs: readonly JobItem[], selectedJobId: string | null, context: CurrentModel = currentModel(),
  provisional = provisionalResults.getSnapshot().entries,
): JobItem | null {
  const candidate = spatialResult(context, { primary: selectedJobId }, jobs, provisional);
  // The server projects this marker from task metadata AND WG2_FEM_REGIONS.
  // Geometry with declared FEM regions alone does not advertise a field.
  return candidate?.has_fem_field_artifact === true && candidate.status === 'complete'
    ? candidate : null;
}

export function fieldPlaneUnavailableTooltip(jobs: readonly JobItem[]): string {
  const completed = jobs.find((job) => job.status === 'complete');
  if (!completed) return 'Field plane unavailable — complete a full-3D solve first';
  switch (completed.unavailable_reason) {
    case 'solve_predates_traces': return 'Field plane unavailable — re-solve this design to retain field traces';
    case 'artifact_pruned': return 'Field plane unavailable — retained traces expired; re-solve the design';
    case 'artifact_persistence_failed': return 'Field plane unavailable — the solve could not retain its field traces';
    case 'size_cap_exceeded': return 'Field plane unavailable — reduce the mesh or sweep size, then re-solve';
    case 'trace_output_missing': return 'Field plane unavailable — the solver returned no field traces; re-solve the design';
    case 'unsupported_axisymmetric_formulation': return 'This historical Axisymmetric result has no field-plane traces. Re-solve with a field-plane-capable configuration.';
    case 'unsupported_coupled_infinite_baffle': return 'Field plane unavailable — set Mounting to Bare shell, Thickened waveguide (freestanding), or Waveguide in enclosure and re-solve with Metal or BEMPP';
    case 'unsupported_per_band_mesh_ladder': return 'Field plane unavailable — this run used multiple per-band meshes. Load its design, enable Keep field plane data, and submit a new solve from the UI';
    case 'unsupported_ground_plane': return 'Field plane unavailable — the field carries no ground image; turn the ground plane off and re-solve. Polar and impedance results do include the ground';
    case 'unsupported_solve_mode': return 'Field plane unavailable — use a supported full-3D solve';
    case 'disabled_by_option': return 'Field plane unavailable — enable Keep field plane data and re-solve';
    default: return 'Field plane unavailable — no completed run has retained field traces';
  }
}

export function cadViewportEmptyCopy(options: {
  bundleName: string;
  bundleReadable: boolean;
  phase: CadModelPhase;
  cadApplication: string;
  /** WG's own words for a request whose preparation failed on the server. */
  failureMessage?: string | null;
}): { title: string; detail: string } {
  const { bundleName, bundleReadable, phase, cadApplication, failureMessage } = options;
  // A Fusion request WG prepares itself has no readable bundle of its own yet;
  // its server-side state is still this model's state.
  if (failureMessage && phase === 'prepare-failed') {
    return { title: `${bundleName} is not prepared`, detail: failureMessage };
  }
  if (!bundleReadable && phase !== 'preparing' && phase !== 'waiting') {
    return {
      title: 'No CAD geometry yet',
      detail: `Bring a model back from ${cadApplication}; CAD Link will prepare it automatically.`,
    };
  }
  if (phase === 'preparing') {
    return {
      title: `Preparing ${bundleName}…`,
      detail: 'CAD Link is building the viewport and solver mesh.',
    };
  }
  if (phase === 'waiting') {
    return {
      title: `${bundleName} is waiting for you`,
      detail: 'WG needs your choice before it can continue with this model.',
    };
  }
  if (phase === 'prepare-failed') {
    return {
      title: 'CAD preparation failed',
      detail: 'Preparation failed. Open CAD Link and use “Prepare simulation” to retry.',
    };
  }
  return {
    title: `${bundleName} is not prepared yet`,
    detail: 'Prepare simulation in CAD Link',
  };
}

/** Whether replacing one viewport scene with another should re-frame the camera.
 *
 * Re-framing is right when the subject changes and wrong when the same subject
 * is merely re-rendered: a refit the user did not ask for reads as the viewport
 * jumping on its own, and both cases below happen repeatedly during ordinary
 * work. A solver mesh rebuilds on every parametric edit, and a CAD return's
 * solve mesh is replaced by its display tessellation the moment that finishes
 * building -- same model, same scale, so the framing already on screen is
 * still the right one.
 */
export function refitsCamera(
  previous: ImportedMeshScene | null,
  next: ImportedMeshScene | null,
): boolean {
  if (previous?.source === 'solver' && next?.source === 'solver') return false;
  if (previous?.source === 'cad' && next?.source === 'cad'
    && previous.ingestId !== null && previous.ingestId === next.ingestId) return false;
  return true;
}

function trackHeaderHeight(header: HTMLDivElement | null) {
  if (!header || typeof ResizeObserver === 'undefined') return;
  const update = () => header.parentElement?.style.setProperty('--viewport-header-height', `${header.getBoundingClientRect().height}px`);
  const observer = new ResizeObserver(update);
  observer.observe(header);
  update();
  return () => {
    observer.disconnect();
    header.parentElement?.style.removeProperty('--viewport-header-height');
  };
}

function currentSolverMeshKey(): string {
  return JSON.stringify([
    solverMeshDesignKey(useDesignStore.getState().design),
    solverMeshOptions(useSolveOptionsStore.getState()),
  ]);
}

export function Viewport() {
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const design = useDesignStore((state) => state.design);
  const designRevision = useDesignStore((state) => state.designRevision);
  const solveSymmetry = useSolveOptionsStore((state) => state.symmetry);
  const designName = useDocumentStore((state) => state.designName);
  const workspaceMode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;
  const cad = useCadModelState();
  const cadRecord = cad.record;
  const cadBundleReadable = cad.bundle?.readable === true;
  const cadName = cad.label;
  const theme = useViewportTheme();
  const preferences = useViewerPreferences();
  const cadApplication = cadApplicationName(usePreferences().cadApplication);
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const solveRunningOrQueued = jobs.some((job) => isActiveJobStatus(job.status));
  const cadEmpty = cadViewportEmptyCopy({
    bundleName: cadName,
    bundleReadable: cadBundleReadable,
    phase: cad.phase,
    cadApplication,
    failureMessage: cad.failureMessage,
  });
  const resultSelection = useSyncExternalStore(compareSelection.subscribe, compareSelection.getSnapshot, compareSelection.getSnapshot);
  const coherenceContext = useCurrentModel();
  const provisional = useSyncExternalStore(provisionalResults.subscribe, provisionalResults.getSnapshot, provisionalResults.getSnapshot);
  const availableFieldJob = useMemo(
    () => fieldPlaneJob(jobs, resultSelection.primary, coherenceContext, provisional.entries),
    [coherenceContext.key, coherenceContext.lineage, coherenceContext.kind, coherenceContext.viewportShowing, jobs, resultSelection.primary, provisional.entries],
  );
  const availableFemJob = useMemo(
    () => femFieldJob(jobs, resultSelection.primary, coherenceContext, provisional.entries),
    [coherenceContext.key, coherenceContext.lineage, coherenceContext.kind, coherenceContext.viewportShowing, jobs, resultSelection.primary, provisional.entries],
  );
  const availablePlaneJob = availableFemJob ?? availableFieldJob;
  const resultRun = spatialResult(coherenceContext, resultSelection, jobs, provisional.entries);
  const observationVisible = useObservationStore((state) => state.visible);
  const observationBasis = useObservationStore((state) => state.basis);
  const observationSource = useObservationStore((state) => state.sourceLabel);
  const observationHovered = useObservationStore((state) => state.hovered);
  // The observation readout follows the workspace: Imported has its own options.
  const polar = useScopedSolveOptions((state) => state.polar);
  // resolve_submission uses only these options for engine/domain/geometry.
  // Sampling and post-build validation cannot change the mesh.
  const meshOptionsKey = useSolveOptionsStore((state) => JSON.stringify(solverMeshOptions(state)));
  const fieldEnabled = useFieldPlaneStore((state) => state.enabled);
  const fieldSource = useFieldPlaneStore((state) => state.source);
  const [femRendering, setFemRendering] = useState<FemFieldRendering>('smooth');
  const [femScope, setFemScope] = useState<import('../api/femField').FemPlaneScope>('interior');
  const [femQuantity, setFemQuantity] = useState<import('../api/femField').FemPlaneQuantity>('pressure');
  const [femDisplayMode, setFemDisplayMode] = useState<FemDisplayMode | null>(null);
  const [femManualWindow, setFemManualWindow] = useState<FieldPlaneValueWindow | null>(null);
  const fieldJobId = useFieldPlaneStore((state) => state.jobId);
  const fieldStatus = useFieldPlaneStore((state) => state.status);
  const fieldError = useFieldPlaneStore((state) => state.error);
  const field = useFieldPlaneStore((state) => state.field);
  const fieldPlane = useFieldPlaneStore((state) => state.plane);
  const fieldGeometrySha256 = useFieldPlaneStore((state) => state.geometrySha256);
  const fieldMaskState = useFieldPlaneMaskStore((state) => state);
  const fieldFrequencyIndex = useFieldPlaneStore((state) => state.frequencyIndex);
  const fieldFrequencies = useFieldPlaneStore((state) => state.frequenciesHz);
  const fieldFrequencyStatus = useFieldPlaneStore((state) => state.frequencyStatus);
  const fieldResponseId = useFieldPlaneStore((state) => state.responseId);
  const fieldMemberResponses = useFieldPlaneStore((state) => state.memberResponses);
  const [fieldFrequencySnap, setFieldFrequencySnap] = useState<{ requestedHz: number; index: number } | null>(null);
  const fieldDisplayMode = useFieldPlaneStore((state) => state.displayMode);
  const fieldRangeLocked = useFieldPlaneStore((state) => state.rangeLocked);
  const fieldAnimationSpeed = useFieldPlaneStore((state) => state.animationSpeed);
  const fieldAnimating = useFieldPlaneStore((state) => state.animating);
  const fieldIsolines = useFieldPlaneStore((state) => state.isolines);
  const storedFieldWindow = useFieldPlaneStore((state) => state.windows[state.displayMode]);
  const femActive = fieldEnabled && fieldSource === 'fem' && fieldJobId === availableFemJob?.id;
  const femField = useFemField(femActive ? fieldJobId : null);
  const effectiveFemScope = !femField.field || femField.field.payload.plane_scopes?.includes(femScope) ? femScope : 'interior';
  const femPlane = useFemPlane(femActive ? fieldJobId : null, femField.field, fieldPlane, effectiveFemScope, femQuantity);
  const femInterior = useMemo(() => femField.field ? femInteriorDisplayValues(femField.field, fieldPlane, femQuantity) : null,
    [femField.field, fieldPlane, femQuantity]);
  const selectedRef = useRef<DecodedFrame | null>(null);
  const solveWasRunningOrQueued = useRef(solveRunningOrQueued);
  const latencyClock = useRef(new ClientLatencyClock());
  const currentEpoch = useRef<number | null>(preview.epoch);
  const queuedFineRevision = useRef<number | null>(null);
  const fineRequestTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  currentEpoch.current = preview.epoch;
  if (preview.epoch === null) latencyClock.current.disconnect();
  else latencyClock.current.beginEpoch(preview.epoch, designRevision, performance.now());
  const selected = selectPreferredFrame(selectedRef.current, preview.frame);
  if (selected !== selectedRef.current) {
    selectedRef.current = selected;
  }
  const previewScene = useMemo(() => selected ? frameToScene(selected) : null, [selected]);
  const [autoQuadrants, setAutoQuadrants] = useState<DisplayQuadrants>(() => displayQuadrants(design.mesh.quadrants));
  const symmetryBody = useMemo(() => JSON.stringify(toSolveDesign(design)), [design]);
  useEffect(() => {
    if (workspaceMode !== 'parametric' || solveSymmetry !== 'auto' || previewScene === null) return undefined;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void postSymmetry(symmetryBody, fetch, controller.signal)
        .then((resolution) => setAutoQuadrants(resolution.quadrants))
        .catch(() => {
          if (!controller.signal.aborted) setAutoQuadrants(displayQuadrants(design.mesh.quadrants));
        });
    }, SYMMETRY_TINT_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [design.mesh.quadrants, workspaceMode, previewScene, solveSymmetry, symmetryBody]);
  const recordedQuadrants = resultRun?.symmetry?.resolved_quadrants;
  const resultQuadrants = recordedQuadrants === 1 || recordedQuadrants === 12
    || recordedQuadrants === 14 || recordedQuadrants === 1234 ? recordedQuadrants : null;
  const solvedQuadrants = resultQuadrants ?? quadrantsForSolveMode(solveSymmetry, autoQuadrants);
  const scene = useMemo(
    () => previewScene ? markParametricSolvedDomain(previewScene, solvedQuadrants) : null,
    [previewScene, solvedQuadrants],
  );
  const [mode, setMode] = useState<DisplayMode>('clay');
  const [clipMode, setClipMode] = useState<ModelClipMode>('off');
  const [sectionAxis, setSectionAxis] = useState<SectionAxis>(SECTION_AXES[0]);
  const [invertFieldClip, setInvertFieldClip] = useState(false);
  const [showEnclosure, setShowEnclosure] = useState(true);
  const [showStats, setShowStats] = useState(false);
  const [clientFrameMs, setClientFrameMs] = useState<number | null>(null);
  const [renderFailure, setRenderFailure] = useState<string | null>(null);
  // Start square to the mouth. The former three-quarter perspective made a
  // circular aperture look elliptical/tilted before the user touched the view.
  const [cameraRequest, setCameraRequest] = useState<CameraRequest>({ preset: 'front', nonce: 0 });
  const [zoomRequest, setZoomRequest] = useState<ZoomRequest>({ direction: 'in', nonce: 0 });
  const [cameraProjection, setCameraProjection] = useState<CameraProjection>(() => preferences.startupCameraMode);
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const importedMeshState = useSyncExternalStore(
    importedMeshStore.subscribe,
    importedMeshStore.getSnapshot,
    importedMeshStore.getSnapshot,
  );
  const cadSolverMesh = currentCadScenes(coherenceContext, importedMeshState).solver;
  const solverViewSelected = workspaceMode === 'parametric' && importedMeshState.showing === 'solver';
  const cadSolverViewSelected = workspaceMode === 'cad' && importedMeshState.showing === 'cadSolver';
  const fileInputActive = coherenceContext.kind === 'file';
  const importedMesh = currentModelScene(coherenceContext, importedMeshState);
  const [cadSolverLoading, setCadSolverLoading] = useState(false);
  const [cadSolverError, setCadSolverError] = useState<string | null>(null);
  const [cadSolverRetry, setCadSolverRetry] = useState(0);
  // Without an independently tessellated display artifact the CAD geometry on
  // screen already *is* the solve mesh, so there is nothing to switch between.
  const cadHasSeparateViewportMesh = cadRecord?.viewport_mesh?.available === true;
  const cadIngestId = fileInputActive ? null : cadRecord?.ingest_id ?? null;
  const cadFrame = useCadSolverFrameStore((state) => cadIngestId ? state.frames[cadIngestId] : undefined);
  const cadFrameOption = cadFrame && !cadFrame.linked && cadFrame.axis
    ? cadFrame.frame?.axes.find((option) => option.axis === cadFrame.axis) : undefined;
  const cadFrameMatrix = cadFrameOption?.previewFromRecord;
  const cadFrameKey = cadFrameMatrix ? JSON.stringify(cadFrameMatrix) : null;
  const lastCameraWorkspaceMode = useRef(workspaceMode);
  useEffect(() => {
    const previousMode = lastCameraWorkspaceMode.current;
    lastCameraWorkspaceMode.current = workspaceMode;
    if (workspaceMode === 'parametric') {
      if (previousMode === 'cad') {
        setCameraRequest((previous) => ({ preset: 'front', nonce: previous.nonce + 1 }));
      }
      return;
    }
    if (!importedMesh || fileInputActive) return;
    if (cadFrame?.linked) {
      setCameraRequest((previous) => ({ preset: 'front', nonce: previous.nonce + 1 }));
      return;
    }
    if (!cadFrameMatrix) return;
    const axes = cadCameraAxes({ previewFromRecord: cadFrameMatrix });
    setCameraRequest((previous) => ({ ...axes, nonce: previous.nonce + 1 }));
  }, [workspaceMode, importedMesh?.ingestId, cadFrame?.linked, cadFrameKey, fileInputActive]);
  const [solverMeshState, setSolverMeshState] = useState<SolverMeshRefreshState>({
    building: false, stale: false, staleReason: null, error: null,
  });
  const solveControl = useOptionalSolveControl();
  const retainedPlan = useRef(solveControl?.plan);
  if (solveControl?.plan || !solveControl?.planPending) retainedPlan.current = solveControl?.plan;
  const planAdjustments = retainedPlan.current?.adjustments ?? [];
  const [solverMeshRefusal, setSolverMeshRefusal] = useState<string | null>(null);
  // Created once: the build reads the design, symmetry mode, and job list at
  // call time, so the latest design always wins without re-instantiating.
  const solverRefreshRef = useRef<SolverMeshRefreshController | null>(null);
  if (solverRefreshRef.current === null) {
    solverRefreshRef.current = new SolverMeshRefreshController({
      runBuild: async (signal) => {
        const generation = importedMeshStore.beginIntent();
        const started = performance.now();
        try {
          const settings = useSolveOptionsStore.getState();
          const modelDesign = useDesignStore.getState().design;
          const modelKey = designFingerprint(modelDesign);
          const body = JSON.stringify({
            design: toSolveDesign(modelDesign),
            options: solverMeshOptions(settings),
          });
          const result = await postSolverMesh(body, fetch, signal);
          if (signal.aborted) return { ok: false, durationMs: performance.now() - started };
          setSolverMeshRefusal(result.plan?.refusal ?? null);
          const token = solverMeshArtifactToken(result.stats.mesh_cache_key);
          const current = importedMeshStore.getSnapshot().solver;
          // Keep the scene object when the resolved artifact is unchanged.
          if (current?.artifactToken !== token || current.modelKey !== modelKey) {
            importedMeshStore.setSolver(
              current?.artifactToken === token ? { ...current, modelKey } : { ...solverMeshScene('Simulation mesh', result), modelKey },
              generation,
              importedMeshStore.getSnapshot().showing === 'solver',
            );
          }
          return { ok: true, durationMs: performance.now() - started };
        } catch (error) {
          if (!signal.aborted) setSolverMeshRefusal(null);
          return {
            ok: false,
            durationMs: performance.now() - started,
            error: error instanceof Error ? error.message : String(error),
          };
        }
      },
      isSolveActive: () => jobsSocket.getSnapshot().jobs
        .some((job) => isActiveJobStatus(job.status)),
      onState: setSolverMeshState,
    });
  }
  const solverRefresh = solverRefreshRef.current;
  const [dismissedPreviewError, setDismissedPreviewError] = useState<string | null>(null);
  const [refreshRequestedAt, setRefreshRequestedAt] = useState<number | null>(null);
  const [stalled, setStalled] = useState(false);
  const setCameraDirection = useCallback((direction: CameraDirection) => {
    setCameraRequest((current) => ({ direction, nonce: current.nonce + 1 }));
  }, []);
  const zoom = (direction: ZoomRequest['direction']) => setZoomRequest((current) => ({ direction, nonce: current.nonce + 1 }));
  const toggleProjection = () => setCameraProjection((current) => current === 'perspective' ? 'orthographic' : 'perspective');
  const reportClientFrame = useCallback((milliseconds: number) => setClientFrameMs(milliseconds), []);
  // Solver artifacts reconstruct the whole physical model too. A displayed
  // result owns its domain even if live options have since rebuilt that mesh.
  const resultMeshScene = useMemo(() => workspaceMode === 'parametric' && importedMesh && resultQuadrants !== null
    ? markParametricSolvedDomain(importedMesh.scene, resultQuadrants) : importedMesh?.scene,
  [workspaceMode, importedMesh, resultQuadrants]);
  const activeScene = workspaceMode === 'cad' ? importedMesh?.scene ?? null : resultMeshScene ?? scene;
  const fieldColormap = useMemo(() => readChartTokens().colormap, [theme]);
  const activeFieldColormap = useMemo(
    () => fieldPlaneColormap(fieldDisplayMode, fieldColormap),
    [fieldColormap, fieldDisplayMode],
  );
  const fieldWindow = storedFieldWindow ?? defaultFieldPlaneWindow(fieldDisplayMode);
  const fieldMaxSplDb = useMemo(() => (field ? maxFieldSplDb(field.real, field.imag) : null), [field]);
  const fieldFrequencyHz = fieldFrequencies[fieldFrequencyIndex] ?? field?.header.frequency_hz ?? null;
  // The notice survives only while the snapped index is still what is shown;
  // moving the slider or activating another run retires it naturally.
  const fieldFrequencySnapNotice = fieldFrequencySnap !== null
    && fieldFrequencySnap.index === fieldFrequencyIndex
    && fieldFrequencies[fieldFrequencySnap.index] !== undefined
    && Math.round(fieldFrequencySnap.requestedHz) !== Math.round(fieldFrequencies[fieldFrequencySnap.index])
    ? `requested ${Math.round(fieldFrequencySnap.requestedHz).toLocaleString()} Hz → showing ${Math.round(fieldFrequencies[fieldFrequencySnap.index]).toLocaleString()} Hz`
    : null;
  const sceneMarker = importedMesh
    ? `msh:${importedMesh.artifactToken}:${workspaceMode === 'parametric' ? resultQuadrants ?? 'artifact' : 'cad'}`
    : workspaceMode === 'cad'
      ? 'cad:empty'
      : `${selected?.header.epoch ?? 0}:${selected?.header.seq ?? 0}:${selected?.header.designRevision ?? 0}:${selected?.header.lod ?? ''}:${preferences.tintSolvedRegion ? solvedQuadrants : 'same'}`;
  const surfaces = activeScene?.surfaces ?? [];
  const domainLabel = activeScene ? solvedDomainLabel(activeScene) : null;
  const recoveryNotice = coherenceContext.kind === 'cad' && importedMesh
    ? cadMeshRecoveryNotice(cadRecord, resultRun?.status === 'complete') : null;

  // Zebra, normals and curvature replace the surface colour outright, and edge
  // mode does not fill at all, so a colour key would be describing something
  // that is not on screen in any of them.
  const roleColorsVisible = preferences.sourceRoleColors
    && mode !== 'zebra' && mode !== 'normals' && mode !== 'curvature' && mode !== 'edges';
  const sourceLegend = useMemo(() => {
    // Counted over distinct surface roles, not over surfaces: symmetry puts the
    // solved quadrant and its mirrored display copies on screen as separate
    // surfaces sharing one role, and a cap reflected four ways is still one
    // driver. Two genuinely separate sources carry distinct labels (HF, HF-2).
    const labels = new Map<SourceRole, Set<string>>();
    for (const surface of surfaces) {
      if (!surface.sourceRole) continue;
      const seen = labels.get(surface.sourceRole) ?? new Set<string>();
      seen.add(surface.role);
      labels.set(surface.sourceRole, seen);
    }
    // Fixed role order rather than mesh order, so the key does not reshuffle
    // itself between two returns of the same design.
    return SOURCE_ROLES.filter((role) => labels.has(role))
      .map((role) => ({ role, count: (labels.get(role) as Set<string>).size }));
  }, [surfaces]);
  const webgl = canRenderWebGL() && renderFailure === null;
  const hasSurfaces = hasRenderableSurfaces(activeScene);
  const edgeModeUnavailable = activeScene?.edgeModeUnavailable === true
    || Boolean(activeScene?.surfaces.some((surface) => Math.floor(surface.indices.length / 3) > MAX_EDGE_TRIANGLES));
  const activeMode = modes.find((item) => item.mode === mode) ?? modes[0];
  const upcomingMode = modes.find((item) => item.mode === nextDisplayMode(mode)) ?? modes[0];
  const sectionOn = clipMode === 'section';
  const upcomingSection = nextSectionCut({ clipMode, sectionAxis });
  // Says where the cut is and where the next press puts it, in one sentence, and
  // names the whole cycle so nobody has to press three times to discover it.
  const sectionCutLabel = `${sectionOn ? `Section cut on ${sectionAxis.toUpperCase()}` : 'Section cut off'}. `
    + `Click to ${upcomingSection.clipMode === 'section' ? `cut on ${upcomingSection.sectionAxis.toUpperCase()}` : 'switch it off'}`
    + ' — cycles X, Y, Z, off.';
  const connectionInterrupted = preferences.liveUpdate && preview.connection !== 'connected';
  const badge = previewBadge(preferences.liveUpdate, preview.connection, preview.error, preview.stale);
  const previewError = preview.error ? previewErrorMessage(preview.error, preview.errorRevision, designRevision) : null;
  const showPreviewError = workspaceMode === 'parametric' && previewError !== null && dismissedPreviewError !== preview.error;
  // Geometry that builds but is not what was asked for. An unreachable guiding
  // curve is the motivating case: the coverage solver clamps, the mouth quietly
  // leaves the guide shape, and from the rail it looks like the parameters
  // stopped responding. An imported mesh is not ours to diagnose.
  const geometryWarnings = workspaceMode === 'cad' || importedMesh ? [] : selected?.header.previewMetadata?.warnings ?? [];
  const behindDesign = preview.stale || preview.displayedRevision !== designRevision;
  const showStalledPreview = Boolean(activeScene && stalled && !showPreviewError && !connectionInterrupted && !importedMesh);
  const showConnectionNotice = Boolean(activeScene && connectionInterrupted && !importedMesh);
  const showSolverMeshNotice = solverViewSelected && solverMeshState.stale;
  const showCadSolverError = cadSolverViewSelected && cadSolverError !== null;
  const showNoticeRefresh = showPreviewError || showStalledPreview || showSolverMeshNotice || showCadSolverError;
  const dimensionsFrame = preview.dimensionsFrame ?? preview.frame;
  const dimensionsMetadata = dimensionsFrame?.header.previewMetadata;
  const currentDimensions = !behindDesign && !preview.error && !preview.awaitingDocumentFrame
    && !preview.dimensionsUnavailable && dimensionsFrame?.header.designRevision === designRevision
    && (dimensionsFrame.documentLoad === undefined || dimensionsFrame.documentLoad === currentDocumentLoad())
    && dimensionsMetadata?.dimensions_status !== 'pending' && dimensionsMetadata?.dimensions_status !== 'unavailable'
    ? dimensionsMetadata?.dimensions_mm : null;
  const nativeOpening = design.formula === 'SOURCE-DISK'
    ? currentDimensions?.source_diameter : currentDimensions?.mouth_opening;
  const refresh = () => {
    setDismissedPreviewError(null);
    setRefreshRequestedAt(performance.now());
    // Paused means the socket is deliberately stopped. Reconnecting behind the
    // preference would leave the badge lying about which mode the viewport is
    // in, so say plainly that this resumes live updates and let the existing
    // gate do the connecting.
    if (!preferences.liveUpdate) viewerPreferences.update({ liveUpdate: true });
    else previewSocket.refresh();
  };

  // One visible action retries every failed/stalled source named by the notice.
  const refreshNotice = () => {
    if (showSolverMeshNotice) solverRefresh.refresh();
    if (showPreviewError || showStalledPreview) refresh();
    if (showCadSolverError) setCadSolverRetry((value) => value + 1);
  };

  const toggleFieldPlane = () => {
    if (fieldEnabled) {
      if (clipMode === 'field-plane') setClipMode('off');
      setInvertFieldClip(false);
      useFieldPlaneStore.getState().disable();
      return;
    }
    if (!availablePlaneJob || !activeScene) return;
    const store = useFieldPlaneStore.getState();
    if (availableFemJob) store.enableFem(availableFemJob.id, defaultFieldPlane(activeScene));
    else store.enable(availablePlaneJob.id, defaultFieldPlane(activeScene));
  };

  const applyFieldPlanePreset = (preset: 'h' | 'v' | 'mouth') => {
    if (!fieldPlane || !activeScene) return;
    useFieldPlaneStore.getState().setPlane(fieldPlanePreset(fieldPlane, preset, {
      min: activeScene.bounds.min.toArray(),
      max: activeScene.bounds.max.toArray(),
      unitsPerMetre: activeScene.unitsPerMetre,
    }));
  };

  const showParametric = () => {
    importedMeshStore.showParametric();
  };

  /** Which artifact the viewport is drawing, as the source switch sees it. */
  const activeMeshSource: ImportedMeshShowing = fileInputActive ? 'file' : workspaceMode === 'cad'
    ? cadSolverViewSelected ? 'cadSolver' : 'cad'
    : solverViewSelected ? 'solver' : 'parametric';

  /** The switch between the smooth display model and the real solve triangles.
   * Both workspaces offer it; only the artifacts behind it differ. CAD Link
   * omits it when the return carries no independent display artifact, because
   * the geometry already on screen is then the solve mesh itself. */
  const meshSources: { key: ImportedMeshShowing; label: string; title: string; select: () => void }[] =
    fileInputActive ? [] : workspaceMode === 'cad'
      ? cadRecord && cadHasSeparateViewportMesh
        ? [
          { key: 'cad', label: 'Geometry', title: 'Show the tessellated CAD display model', select: () => importedMeshStore.showCad() },
          { key: 'cadSolver', label: 'Mesh', title: 'Show the exact triangles this CAD return will be solved on', select: () => importedMeshStore.showCadSolver() },
        ]
        : []
      : [
        { key: 'parametric', label: 'Geometry', title: 'Show the smooth parametric preview model', select: showParametric },
        { key: 'solver', label: 'Mesh', title: 'Show the real solver mesh triangles instead of the smooth preview', select: () => importedMeshStore.showSolver() },
      ];

  // A lost context is recoverable: mounting a fresh Canvas asks for a new one.
  // Clearing the failure is what lets that happen, and re-probing covers the
  // case where the whole WebGL2 capability went away and has since come back.
  const retryRenderer = () => {
    resetWebGLProbe();
    setRenderFailure(null);
  };

  const opening = useImportedModelStore(useShallow(state => ({ input: state.input, phase: state.phase, generation: state.generation })));
  const previousWorkspaceMode = useRef(workspaceMode);
  useEffect(() => {
    if (workspaceMode === 'parametric') {
      // Leaving Imported mesh returns to the live parametric scene. Both imported
      // inputs remain cached for the next visit. Ignore unrelated CAD updates
      // while already parametric.
      if (previousWorkspaceMode.current === 'cad') importedMeshStore.showParametric();
      previousWorkspaceMode.current = workspaceMode;
      return;
    }
    previousWorkspaceMode.current = workspaceMode;
    if (fileInputActive) {
      if (importedMeshStore.getSnapshot().showing !== 'file') importedMeshStore.showFile(importedMeshStore.captureGeneration());
      return;
    }
    // An open of a CAD model owns both the target and its artifact load.
    // A passive mode/record effect must not advance its generation mid-open.
    if (opening.input === 'cad' && opening.phase !== 'ready' && importedModelBlocker('cad')) return;
    if (!cadRecord) {
      importedMeshStore.beginIntent();
      importedMeshStore.clear('cad');
      return;
    }
    const snapshot = importedMeshStore.getSnapshot();
    // The CAD Link panel re-publishes its record while it polls the CAD
    // application, so this effect re-runs constantly for one unchanged
    // ingestion. A refresh must not throw the user out of the mesh view they
    // chose -- and decisively must not reach `beginIntent` below, which would
    // cancel the solve-mesh fetch running for exactly this record. Only a
    // genuinely different ingestion resets the source.
    if (snapshot.showing === 'cadSolver'
      && (snapshot.cadSolver?.ingestId === cadRecord.ingest_id
        || snapshot.cad?.ingestId === cadRecord.ingest_id)) return;
    const generation = importedMeshStore.beginIntent();
    const cached = snapshot.cad;
    if (cached?.ingestId === cadRecord.ingest_id) {
      importedMeshStore.showCad(generation);
      return;
    }
    void showIngestedMeshInViewport(
      cadRecord,
      cadName,
      (notice) => cadWorkspace.getSnapshot().reportViewportNotice(notice),
      fetch,
      generation,
    );
  }, [cadName, cadRecord, workspaceMode, fileInputActive, opening.input, opening.phase, opening.generation]);

  // The mesh may be set from outside this component (CAD Link after an
  // ingest), so the camera refit follows the store rather than the setters.
  const lastImportedMesh = useRef(importedMesh);
  useEffect(() => {
    const previous = lastImportedMesh.current;
    if (previous === importedMesh) return;
    lastImportedMesh.current = importedMesh;
    if (!refitsCamera(previous, importedMesh)) return;
    setCameraRequest((current) => ({ ...current, nonce: current.nonce + 1 }));
  }, [importedMesh]);

  // Simulation-mesh view lifecycle: activation is user intent, so it builds
  // (or re-serves from the shared artifact cache) immediately and defaults the
  // display mode to solid + wireframe; the mode picker stays live after that.
  useEffect(() => {
    if (!solverViewSelected) return undefined;
    setMode('solid-wire');
    solverRefresh.activate(currentSolverMeshKey());
    return () => solverRefresh.deactivate();
  }, [solverRefresh, solverViewSelected]);

  useEffect(() => {
    return subscribeRevision((event) => {
      // A load replaces the document; a solver-mesh build already in flight
      // for the one it replaced is still answering a question nobody is
      // asking any more. `designChanged` alone does not stop it -- it only
      // marks the edit missed and lets the in-flight build finish first -- so
      // without this, that build's own (still-current) generation can commit
      // the replaced design's mesh into `importedMeshStore` for one frame
      // before the follow-up build `designChanged` triggers overwrites it.
      // Bumping the generation here makes that commit refuse itself instead.
      if (event.reason === 'load') {
        importedMeshStore.beginIntent();
        solverRefresh.documentLoaded();
      }
      solverRefresh.designChanged(currentSolverMeshKey());
    });
  }, [solverRefresh]);

  const previousMeshOptions = useRef(meshOptionsKey);
  useEffect(() => {
    if (previousMeshOptions.current === meshOptionsKey) return;
    previousMeshOptions.current = meshOptionsKey;
    if (solverViewSelected) importedMeshStore.beginIntent();
    solverRefresh.designChanged(currentSolverMeshKey());
  }, [meshOptionsKey, solverRefresh, solverViewSelected]);

  useEffect(() => {
    if (!solveRunningOrQueued) solverRefresh.solveSettled();
  }, [solveRunningOrQueued, solverRefresh]);

  // CAD mesh view: the ingestion record is immutable, so the solve artifact is
  // fetched once per ingestion and re-served from its slot after that. There
  // is no refresh policy to run -- nothing the user edits in CAD mode can
  // change the mesh a completed return will be solved on. A failed fetch is
  // not retried on its own; `cadSolverRetry` is what the banner's Retry moves.
  //
  // Keyed on the ingestion id rather than the record object: CAD Link
  // re-publishes an equal record on every poll, and restarting the fetch each
  // time would leave it permanently loading.
  useEffect(() => {
    if (!cadSolverViewSelected || cadIngestId === null) return undefined;
    setMode('solid-wire');
    if (cadSolverMesh !== null) return undefined;
    const record = useCadReturnStore.getState().ingestRecord;
    if (record === null || record.ingest_id !== cadIngestId) return undefined;
    let cancelled = false;
    setCadSolverLoading(true);
    setCadSolverError(null);
    void showIngestedSolverMeshInViewport(record, cadName).then((error) => {
      if (cancelled) return;
      setCadSolverLoading(false);
      setCadSolverError(error);
    });
    return () => {
      cancelled = true;
      setCadSolverLoading(false);
    };
  }, [cadIngestId, cadName, cadSolverMesh, cadSolverRetry, cadSolverViewSelected]);

  useEffect(() => {
    if (availablePlaneJob || !fieldEnabled) return;
    useFieldPlaneStore.getState().reportUnavailable('re-solve to enable field planes');
  }, [availablePlaneJob, fieldEnabled]);

  useEffect(() => {
    if (!fieldEnabled || !availablePlaneJob || !activeScene) return;
    const source = availableFemJob ? 'fem' : 'exterior';
    if (fieldJobId === availablePlaneJob.id && fieldSource === source) return;
    const store = useFieldPlaneStore.getState();
    if (source === 'fem') store.enableFem(availablePlaneJob.id, defaultFieldPlane(activeScene));
    else store.selectJob(availablePlaneJob.id, defaultFieldPlane(activeScene));
  }, [activeScene, availablePlaneJob, availableFemJob, fieldEnabled, fieldJobId, fieldSource]);

  useEffect(() => {
    const wasRunningOrQueued = solveWasRunningOrQueued.current;
    solveWasRunningOrQueued.current = solveRunningOrQueued;
    if (wasRunningOrQueued && !solveRunningOrQueued) {
      useFieldPlaneStore.getState().retryIfBlocked();
    }
  }, [solveRunningOrQueued]);

  useEffect(() => {
    if (availablePlaneJob && fieldEnabled && fieldPlane) return;
    if (clipMode === 'field-plane') setClipMode('off');
    if (!availablePlaneJob && invertFieldClip) setInvertFieldClip(false);
  }, [availablePlaneJob, clipMode, fieldEnabled, fieldPlane, invertFieldClip]);

  useEffect(() => {
    useFieldPlaneStore.getState().applyRenderingPreferences({
      fieldPlaneDisplayMode: preferences.fieldPlaneDisplayMode,
      fieldPlaneRangeLocked: preferences.fieldPlaneRangeLocked,
      fieldPlaneAnimationSpeed: preferences.fieldPlaneAnimationSpeed,
    });
  }, [
    preferences.fieldPlaneAnimationSpeed,
    preferences.fieldPlaneDisplayMode,
    preferences.fieldPlaneRangeLocked,
  ]);

  useEffect(() => subscribeRevision((event) => {
    // Design revisions replace geometry inside the current view. Keeping the
    // exact framing is what makes undo/redo and parameter changes comparable.
    // A new imported source still gets an initial fit in the store effect above.
    const epoch = currentEpoch.current;
    if (epoch === null) return;
    if (fineRequestTimer.current) clearTimeout(fineRequestTimer.current);
    queuedFineRevision.current = event.revision;
    latencyClock.current.recordRequest(epoch, event.revision, 'coarse', performance.now());
    fineRequestTimer.current = setTimeout(() => {
      fineRequestTimer.current = null;
      const revision = queuedFineRevision.current;
      queuedFineRevision.current = null;
      const activeEpoch = currentEpoch.current;
      if (revision !== null && activeEpoch !== null) {
        latencyClock.current.recordRequest(activeEpoch, revision, 'fine', performance.now());
      }
    }, PREVIEW_FINE_IDLE_MS);
  }), []);

  useEffect(() => {
    setClientFrameMs(null);
  }, [preview.epoch]);

  // Curvature sections cost a third of a fine frame to build, and only this one
  // mode reads them. Ask for them while it is on, and stop when it is not.
  useEffect(() => {
    previewSocket.setCurvatureWanted(mode === 'curvature');
  }, [mode]);

  useEffect(() => {
    if (preview.error === null) setDismissedPreviewError(null);
  }, [preview.error]);

  // A refresh is over when the engine answers — with geometry or with a reason.
  // The timeout is the third outcome: an engine that never answers at all.
  useEffect(() => {
    setRefreshRequestedAt(null);
  }, [preview.displayedRevision, preview.error]);
  useEffect(() => {
    if (refreshRequestedAt === null) return undefined;
    const timer = setTimeout(() => setRefreshRequestedAt(null), 8_000);
    return () => clearTimeout(timer);
  }, [refreshRequestedAt]);

  // Every keystroke goes stale for a few tens of milliseconds while the engine
  // answers, so only say so once it has lasted long enough to be a real stall.
  useEffect(() => {
    if (!behindDesign) {
      setStalled(false);
      return undefined;
    }
    const timer = setTimeout(() => setStalled(true), STALL_NOTICE_MS);
    return () => clearTimeout(timer);
  }, [behindDesign, designRevision]);

  useEffect(() => () => {
    if (fineRequestTimer.current) clearTimeout(fineRequestTimer.current);
  }, []);

  return <div className={`viewport-panel wg2-viewport${workspaceMode === 'parametric' ? ' parametric-viewport' : ''}`}>
    {activeScene && hasSurfaces && webgl && <ViewportCanvas
      scene={activeScene}
      sceneMarker={sceneMarker}
      spatialResultId={availablePlaneJob?.id ?? null}
      mode={mode}
      showEnclosure={showEnclosure}
      clipMode={clipMode}
      sectionAxis={sectionAxis}
      invertFieldClip={invertFieldClip}
      cameraRequest={cameraRequest}
      zoomRequest={zoomRequest}
      cameraProjection={cameraProjection}
      preferences={preferences}
      frameStartedAt={workspaceMode === 'cad' || importedMesh || !selected ? null : latencyClock.current.requestStartedAt(selected.header)}
      onClientFrame={reportClientFrame}
      theme={theme}
      onRenderFailure={setRenderFailure}
      onCameraDirection={setCameraDirection}
    >
      {femActive && femField.field && <FemFieldScene interiorValues={femInterior} field={femField.field} planeField={femPlane.field} quantity={femQuantity} scope={effectiveFemScope} displayMode={femDisplayMode ?? fieldDisplayMode} manualWindow={femManualWindow} rendering={femRendering}
        sequential={fieldColormap} unitsPerMetre={activeScene.unitsPerMetre}/>}
    </ViewportCanvas>}

    <div className="viewport-header" ref={workspaceMode === 'parametric' ? trackHeaderHeight : undefined}>
      <div className="viewport-title">
        <b>{importedMesh?.source === 'solver'
          ? designName || 'Untitled design'
          : importedMesh?.name ?? (fileInputActive ? 'Imported mesh' : workspaceMode === 'cad' ? cadName : designName || 'Untitled design')}</b>
        <span>{importedMesh
          ? `${importedMesh.triangleCount.toLocaleString()} display triangles${importedMesh.triangleCount !== importedMesh.solvedTriangleCount ? ` · ${importedMesh.solvedTriangleCount.toLocaleString()} solved` : ''} · ${importedMesh.physicalGroupCount} physical group${importedMesh.physicalGroupCount === 1 ? '' : 's'}`
          : fileInputActive ? 'Mesh file · configure in Geometry' : workspaceMode === 'cad' ? (cad.phase === 'preparing' && cadBundleReadable ? `Preparing ${cadName}…` : cad.phase === 'prepare-failed' && cadBundleReadable ? 'CAD preparation failed' : cadEmpty.title) : viewportSubtitle(design, nativeOpening)}</span>
      </div>
      <div className="viewport-live">
        {workspaceMode === 'parametric' && <span className={badge.className}><i />{refreshRequestedAt === null ? badge.label : 'REFRESHING'}</span>}
        {workspaceMode === 'parametric' && behindDesign && !importedMesh && !showNoticeRefresh && <button
          type="button"
          className="btn viewport-refresh"
          disabled={refreshRequestedAt !== null}
          title={staleReason(preferences.liveUpdate, preview.connection, preview.error)}
          aria-label={`Rebuild the preview. ${staleReason(preferences.liveUpdate, preview.connection, preview.error)}`}
          onClick={refresh}
        ><Icon name="reset"/>{preferences.liveUpdate ? 'Refresh' : 'Resume'}</button>}
        {workspaceMode === 'parametric'
          ? solverViewSelected
            ? <span>{solverMeshState.building
              ? 'building solver mesh…'
              : importedMesh
                ? solverMeshState.stale ? 'solver mesh out of date' : 'solver mesh current'
                : 'solver mesh pending'}</span>
            : <span>geometry <b>{selected?.header.evalMs?.toFixed(1) ?? '—'}</b> ms · on screen <b>{formatClientLatency(clientFrameMs)}</b></span>
          : cadSolverViewSelected
            ? <span>{cadSolverLoading
              ? 'loading solve mesh…'
              : cadSolverMesh ? 'prepared solve mesh' : 'solve mesh unavailable'}</span>
            : <span>{fileInputActive ? 'Mesh file' : 'CAD Link'} workspace</span>}
        {workspaceMode === 'parametric' && <LiveDimensions />}
      </div>
    </div>

    {!activeScene && <div className="viewport-empty" role="status" aria-live="polite">
      <i className="viewport-empty-mark"><i /></i>
      <b>{fileInputActive ? 'Import a mesh file' : workspaceMode === 'cad' ? cadEmpty.title : preferences.liveUpdate ? 'Waiting for geometry' : 'Live updates paused'}</b>
      <span>{fileInputActive ? 'Use Import mesh above, then choose Radiating surfaces in Geometry.' : workspaceMode === 'cad' ? cadEmpty.detail : preview.error ?? (!preferences.liveUpdate ? 'Enable Live updates in viewer preferences.' : connectionInterrupted ? `Preview engine ${preview.connection}. The viewport will resume automatically.` : 'Requesting a live FRAME-SPEC scene from the local preview engine.')}</span>
    </div>}
    <div className="viewport-notices">
      {(showNoticeRefresh || showConnectionNotice) && <div
        className={`viewport-connection-banner${showPreviewError ? ' viewport-error-banner' : ''}${showSolverMeshNotice || showCadSolverError ? ' solver-mesh-banner' : ''}`}
        role={showPreviewError || (showSolverMeshNotice && solverMeshState.error) || showCadSolverError ? 'alert' : 'status'}
      >
        <div className="viewport-notice-messages">
          {showSolverMeshNotice && <>
            <span>{solverMeshState.error ? 'Mesh build failed' : 'Mesh out of date'}</span>
            {solverMeshState.error && !(showPreviewError && solverMeshState.error === preview.error)
              && <b>{solverMeshState.error}</b>}
            {solverMeshState.staleReason === 'solve-running' && <b>auto-rebuild paused while a solve runs</b>}
            {solverMeshState.staleReason === 'build-slow' && <b>auto-rebuild paused — the last build was slow</b>}
          </>}
          {showPreviewError && <span>{previewError}</span>}
          {showStalledPreview && <span>{staleReason(preferences.liveUpdate, preview.connection, preview.error)}</span>}
          {showConnectionNotice && <span>
            {preview.connection === 'reconnecting' ? 'Reconnecting to preview engine' : 'Preview connection interrupted'}. Last valid geometry retained.
          </span>}
          {showCadSolverError && <><span>Solve mesh unavailable</span><b>{cadSolverError}</b></>}
        </div>
        {showNoticeRefresh && <button type="button" className="btn"
          disabled={(showPreviewError || showStalledPreview) && refreshRequestedAt !== null
            || showSolverMeshNotice && solverMeshState.building || showCadSolverError && cadSolverLoading}
          onClick={refreshNotice}>Refresh</button>}
        {showPreviewError && <button type="button" aria-label="Dismiss preview error" title="Dismiss preview error" onClick={() => setDismissedPreviewError(preview.error)}><Icon name="close"/></button>}
      </div>}
      {solverViewSelected && planAdjustments.flatMap((adjustment) => {
        const notice = planAdjustmentNotice(adjustment);
        return notice === null ? [] : [notice];
      }).map((notice) => (
        <div className="solver-plan-adjustment-note" role="note" key={notice}>{notice}</div>
      ))}
      {solverViewSelected && solverMeshRefusal && <div className="solver-plan-adjustment-note" role="note">
        Solve unavailable: {solverMeshRefusal}
      </div>}
      {activeScene && domainLabel && <div className="viewport-domain-note" role="note" aria-label="Solved domain">
        Solve domain: {domainLabel}
      </div>}
      {recoveryNotice && <div className="viewport-domain-note" role="note">{recoveryNotice}</div>}
      {activeScene && geometryWarnings.length > 0 && <details className="viewport-warning" role="status">
        <summary aria-label={`${geometryWarnings.length} geometry warning${geometryWarnings.length === 1 ? '' : 's'}`}>
          <i />{geometryWarnings.length} warning{geometryWarnings.length === 1 ? '' : 's'}
        </summary>
        <div className="viewport-warning-details">
          <b>Geometry warning{geometryWarnings.length === 1 ? '' : 's'}</b>
          <ul>{geometryWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </div>
      </details>}
    </div>
    {activeScene && hasSurfaces && !webgl && !renderFailure && <div className="viewport-empty"><b>WebGL unavailable</b><span>The geometry is valid, but this environment cannot create a WebGL2 context.</span></div>}
    {activeScene && !hasSurfaces && <div className="viewport-empty" role="status"><b>No geometry surfaces</b><span>The scene is valid but contains no renderable surfaces.</span></div>}
    {activeScene && renderFailure && <div className="viewport-empty" role="status">
      <b>WebGL renderer stopped</b>
      <span>{renderFailure}. Retry to rebuild the viewport; if it stops again, check that graphics acceleration is enabled.</span>
      <button type="button" className="btn viewport-retry" onClick={retryRenderer}><Icon name="reset"/>Retry</button>
    </div>}
    {activeScene && mode === 'curvature' && !activeScene.hasCurvature && <div className="viewport-mode-empty">
      <b>Curvature heatmap unavailable</b><span>This frame has no analytic curvature section. Neutral geometry remains visible while inspection data is requested.</span>
    </div>}
    {activeScene && mode === 'edges' && edgeModeUnavailable && <div className="viewport-mode-empty">
      <b>Edge inspection unavailable</b><span>This frame exceeds the {MAX_EDGE_TRIANGLES.toLocaleString()}-triangle edge limit. Filled geometry remains visible while edge extraction is skipped.</span>
    </div>}
    {showStats && <div className="frame-stat-card wg2-stats">
      <span>latest displayed binary frame</span>
      <dl>
        <div><dt>revision</dt><dd>{importedMesh ? 'imported' : selected?.header.designRevision ?? 'waiting'}</dd></div>
        <div><dt>LOD</dt><dd>{importedMesh ? 'source' : selected?.header.lod ?? '—'}</dd></div>
        <div><dt>eval</dt><dd>{selected?.header.evalMs !== undefined ? `${selected.header.evalMs.toFixed(2)} ms` : '—'}</dd></div>
      </dl>
      <div className="surface-list">{surfaces.length ? surfaces.map((surface, index) => {
        const vertexCount = surface.positions.length / 3;
        return <div key={`${index}:${surface.role}`}><span>{surface.role}</span><b>{vertexCount.toLocaleString()} vertices</b></div>;
      }) : <p>No rendered surfaces in this frame.</p>}</div>
    </div>}

    {activeScene && roleColorsVisible && sourceLegend.length > 0 && <div
      className="source-role-legend"
      role="group"
      aria-label="Acoustic source colours"
    >
      <b>Sources</b>
      <ul>{sourceLegend.map(({ role, count }) => <li key={role}>
        <i style={{ background: `var(--vp-${ROLE_MATERIAL_FAMILY[role]}-material)` }} />
        <span>{SOURCE_ROLE_LABELS[role]}</span>
        {count > 1 && <em>×{count}</em>}
      </li>)}</ul>
    </div>}

    {femActive && <FemFieldControls interiorValues={femInterior} field={femField} planeResult={femPlane} scope={effectiveFemScope} setScope={setFemScope}
      quantity={femQuantity} setQuantity={(q) => { setFemQuantity(q); setFemDisplayMode(q === 'pressure' ? null : 'real'); setFemManualWindow(null); }}
      displayMode={femDisplayMode ?? fieldDisplayMode} setDisplayMode={(m) => {
        if (m === 'real' || m === 'imaginary') setFemDisplayMode(m);
        else { setFemDisplayMode(null); useFieldPlaneStore.getState().setDisplayMode(m); }
        setFemManualWindow(null);
      }}
      manualWindow={femManualWindow} setManualWindow={setFemManualWindow} rendering={femRendering} setRendering={setFemRendering} sequential={fieldColormap}>
      <div className="field-plane-presets" role="group" aria-label="FEM field plane presets">
        <button type="button" onClick={() => applyFieldPlanePreset('h')}>H-plane</button>
        <button type="button" onClick={() => applyFieldPlanePreset('v')}>V-plane</button>
        <button type="button" onClick={() => applyFieldPlanePreset('mouth')}>Mouth</button>
      </div>
      <FieldPlaneTransformInputs/>
    </FemFieldControls>}
    {fieldEnabled && fieldSource === 'exterior' && <div
      className="field-plane-legend"
      role="group"
      aria-label="Field plane controls"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
        event.preventDefault();
        const delta = event.key === 'ArrowLeft' ? -1 : 1;
        useFieldPlaneStore.getState().setFrequencyIndex(fieldFrequencyIndex + delta);
      }}
    >
      <div className="field-plane-legend-title">
        <b>Field plane</b>
        <span>{fieldFrequencyHz === null ? '—' : `${Math.round(fieldFrequencyHz).toLocaleString()} Hz`}</span>
      </div>
      {fieldFrequencyStatus && <p role="note">Adaptive sampling · {fieldFrequencyStatus[fieldFrequencyIndex]} frequency. Fields use reconstructed surface traces at interpolated frequencies; narrow resonances can be missed.</p>}
      {fieldFrequencies.length > 1 && <div className="field-plane-frequency">
        {/* The slider indexes requested frequencies. Adaptive rows can use
            reconstructed traces, as the disclosure above states.
            The numeric cell snaps a typed frequency to the nearest solved one
            and says so when they differ; it never solves new frequencies. */}
        <div className="field-plane-frequency-row">
          <input
            type="range"
            aria-label="Field plane frequency"
            aria-valuetext={`${Math.round(fieldFrequencies[fieldFrequencyIndex] ?? 0).toLocaleString()} Hz`}
            min={0}
            max={fieldFrequencies.length - 1}
            step={1}
            value={fieldFrequencyIndex}
            onChange={(event) => useFieldPlaneStore.getState().setFrequencyIndex(Number(event.target.value))}
          />
          <FieldPlaneNumberCell
            ariaLabel="Field plane frequency in hertz"
            value={fieldFrequencies[fieldFrequencyIndex] ?? 0}
            step={1}
            min={1}
            decimals={0}
            onCommit={(requestedHz) => {
              if (requestedHz <= 0) return;
              const index = nearestFieldPlaneFrequencyIndex(fieldFrequencies, requestedHz);
              useFieldPlaneStore.getState().setFrequencyIndex(index);
              setFieldFrequencySnap({ requestedHz, index });
            }}
          />
        </div>
        <div className="field-plane-frequency-scale">
          <span>{Math.round(fieldFrequencies[0]).toLocaleString()}</span>
          <span>{fieldFrequencyIndex + 1}/{fieldFrequencies.length}</span>
          <span>{Math.round(fieldFrequencies[fieldFrequencies.length - 1]).toLocaleString()} Hz</span>
        </div>
        {fieldFrequencySnapNotice && <div className="field-plane-note" role="status">{fieldFrequencySnapNotice}</div>}
      </div>}
      {fieldMemberResponses.length > 0 && <div className="field-plane-mode-row">
        <label>
          <span>Response</span>
          <select
            aria-label="Field plane response"
            value={fieldResponseId}
            onChange={(event) => useFieldPlaneStore.getState().setResponseId(event.target.value as FieldPlaneResponseId)}
          >
            <option value="system">Combined</option>
            {fieldMemberResponses.map(({ id, label }) => <option key={id} value={`member:${id}`}>{label}</option>)}
          </select>
        </label>
      </div>}
      <div className="field-plane-mode-row">
        <label>
          <span>Mode</span>
          <select
            aria-label="Field plane display mode"
            value={fieldDisplayMode}
            onChange={(event) => useFieldPlaneStore.getState().setDisplayMode(event.target.value as FieldPlaneDisplayMode)}
          >
            {FIELD_PLANE_DISPLAY_MODES.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <button
          type="button"
          className={fieldAnimating ? 'on' : ''}
          aria-label={fieldAnimating ? 'Pause field phase animation' : 'Play field phase animation'}
          aria-pressed={fieldAnimating}
          title={fieldAnimating ? 'Pause phase animation' : 'Animate instantaneous pressure'}
          onClick={() => useFieldPlaneStore.getState().setAnimating(!fieldAnimating)}
        >{fieldAnimating ? 'Ⅱ' : '▶'}</button>
      </div>
      <i className="field-plane-gradient" style={{ backgroundImage: `linear-gradient(90deg, ${activeFieldColormap.join(', ')})` }}/>
      <div className="field-plane-window">
        <span>{fieldPlaneWindowReadout(fieldDisplayMode, fieldWindow)}</span>
        <button
          type="button"
          className={fieldRangeLocked ? 'on' : ''}
          aria-label={fieldRangeLocked ? 'Unlock field plane range' : 'Lock field plane range'}
          aria-pressed={fieldRangeLocked}
          onClick={() => useFieldPlaneStore.getState().setRangeLocked(!fieldRangeLocked)}
        >{fieldRangeLocked ? 'Locked' : 'Auto'}</button>
      </div>
      <div className="field-plane-meta">
        <span>grid {field ? `${field.header.nx}×${field.header.ny}` : '—'}</span>
        <span>response {field?.header.response_id ?? 'system'}</span>
      </div>
      <div className="field-plane-render-controls">
        <button
          type="button"
          className={fieldIsolines ? 'on' : ''}
          aria-label="Toggle 6 dB field plane isolines"
          aria-pressed={fieldIsolines}
          disabled={fieldDisplayMode === 'phase' || fieldDisplayMode === 'instantaneous'}
          onClick={() => useFieldPlaneStore.getState().setIsolines(!fieldIsolines)}
        >6 dB lines</button>
        {fieldDisplayMode === 'instantaneous' && <label className="field-plane-animation-speed">
          <span>Cycle</span>
          <input
            type="range"
            aria-label="Field phase animation speed"
            min="0.1"
            max="4"
            step="0.1"
            value={fieldAnimationSpeed}
            onChange={(event) => useFieldPlaneStore.getState().setAnimationSpeed(Number(event.target.value))}
          />
          <output>{fieldAnimationSpeed.toFixed(1)}×</output>
        </label>}
      </div>
      <div className="field-plane-presets" role="group" aria-label="Field plane presets">
        <button type="button" onClick={() => applyFieldPlanePreset('h')}>H-plane</button>
        <button type="button" onClick={() => applyFieldPlanePreset('v')}>V-plane</button>
        <button type="button" onClick={() => applyFieldPlanePreset('mouth')}>Mouth</button>
      </div>
      <FieldPlaneTransformInputs />
      {maskMatchesGeometry(fieldMaskState, fieldJobId, fieldGeometrySha256)
        && fieldMaskState.watertight === false
        && <div className="field-plane-note" role="note">open mesh: interior not masked</div>}
      {maskMatchesGeometry(fieldMaskState, fieldJobId, fieldGeometrySha256)
        && fieldMaskState.snappedVertexCount !== null
        && fieldMaskState.snappedVertexCount > 0
        && <div className="field-plane-note" role="note">{fieldMaskState.snappedVertexCount.toLocaleString()} vertices snapped to symmetry plane</div>}
      {fieldStatus !== 'ready' && <div className={`field-plane-status ${fieldStatus}`} role="status" aria-live="polite">
        <span>{fieldStatus === 'loading' ? 'loading field plane…' : fieldError ?? 'field plane idle'}</span>
        {fieldStatus === 'error' && fieldJobId && <button type="button" className="btn" onClick={() => useFieldPlaneStore.getState().retry()}>Retry</button>}
      </div>}
    </div>}

    {fieldEnabled && fieldSource === 'exterior' && fieldJobId === availableFieldJob?.id
      && <FieldPlaneProbeTooltip fieldMaxSplDb={fieldMaxSplDb}/>}

    {observationVisible && observationBasis && <div className="observation-readout" role="status" aria-live="polite">
      <b>{observationHovered
        ? `${observationHovered.plane} ${Number(observationHovered.angleDeg.toFixed(3))}°`
        : `${polar.distance} m from ${polar.observationOrigin}`}</b>
      <span>drag a microphone to read it · shift-drag to change distance</span>
      {observationSource && <small>frame from {observationSource}</small>}
    </div>}

    <div className="viewport-tools">
      {meshSources.length > 1 && <>
        <div className="viewport-tool-group viewport-tool-segment mesh-source-tools" role="group" aria-label="Viewport mesh source">
          {meshSources.map((source) => <button
            key={source.key}
            type="button"
            className={`viewport-tool-text${source.key === activeMeshSource ? ' on' : ''}`}
            title={source.title}
            aria-pressed={source.key === activeMeshSource}
            onClick={source.select}
          >{source.label}</button>)}
        </div>
        <i className="wg2-tool-divider" />
      </>}
      <div className="viewport-tool-group display-mode-tools">
        <button
          className="on"
          title={`${activeMode.title} display. Click for ${upcomingMode.title}.`}
          aria-label={`Display mode: ${activeMode.title}. Switch to ${upcomingMode.title}.`}
          onClick={() => setMode(nextDisplayMode(mode))}
        >{activeMode.icon ? <Icon name={activeMode.icon}/> : <span className="wg2-text-glyph">{activeMode.glyph}</span>}</button>
      </div>
      <i className="wg2-tool-divider" />
      <div className="viewport-tool-group">
        <button
          type="button"
          className={`section-cut-toggle${sectionOn ? ' on' : ''}`}
          title={sectionCutLabel}
          aria-label={sectionCutLabel}
          aria-pressed={sectionOn}
          onClick={() => {
            const upcoming = nextSectionCut({ clipMode, sectionAxis });
            setClipMode(upcoming.clipMode);
            setSectionAxis(upcoming.sectionAxis);
          }}
        >
          <Icon name="section"/>
          {/* Which of the three cuts is open, on the control that changes it --
              the icon alone cannot say, and the tooltip only answers on hover. */}
          {sectionOn && <span className="section-cut-axis" aria-hidden="true">{sectionAxis.toUpperCase()}</span>}
        </button>
      </div>
      {/* Only offered once a run has published the frame it measured in. The
          arc is where the solve put its microphones, and nothing here guesses
          one for a geometry that has not been solved. */}
      {observationBasis && <div className="viewport-tool-group">
        <button
          type="button"
          className={observationVisible ? 'on' : ''}
          title="Show the measurement positions the polars were taken at"
          aria-label="Measurement positions overlay"
          aria-pressed={observationVisible}
          onClick={() => useObservationStore.getState().toggle()}
        ><Icon name="microphone-arc"/></button>
      </div>}
      {availablePlaneJob && <div className="viewport-tool-group">
        <button
          type="button"
          className={fieldEnabled ? 'on' : ''}
          title={!activeScene ? 'Field plane unavailable — waiting for viewport geometry' : availableFemJob ? 'Toggle FEM interior field' : 'Toggle acoustic field plane'}
          aria-label={availableFemJob ? 'FEM interior field overlay' : 'Acoustic field plane overlay'}
          aria-pressed={fieldEnabled}
          disabled={!fieldEnabled && !activeScene}
          onClick={toggleFieldPlane}
        ><Icon name="field-plane"/></button>
        {fieldEnabled && <button
          type="button"
          className={clipMode === 'field-plane' ? 'on' : ''}
          title="Clip model to field plane"
          aria-label="Clip model to field plane"
          aria-pressed={clipMode === 'field-plane'}
          disabled={!fieldEnabled || !fieldPlane}
          onClick={() => setClipMode((value) => value === 'field-plane' ? 'off' : 'field-plane')}
        ><Icon name="section"/></button>}
        {fieldEnabled && <button
          type="button"
          className={invertFieldClip ? 'on' : ''}
          title="Invert field-plane clip side"
          aria-label="Invert field-plane clip side"
          aria-pressed={invertFieldClip}
          disabled={clipMode !== 'field-plane'}
          onClick={() => setInvertFieldClip((value) => !value)}
        ><span className="wg2-text-glyph">↔</span></button>}
      </div>}
      <i className="wg2-tool-divider" />
      <div className="viewport-tool-group viewport-tool-segment">
        <button className="viewport-tool-text projection-toggle" aria-label={`Switch to ${cameraProjection === 'perspective' ? 'orthographic' : 'perspective'} camera`} onClick={toggleProjection}>{cameraProjection === 'perspective' ? 'Persp' : 'Ortho'}</button>
      </div>
      <i className="wg2-tool-divider" />
      <div className="viewport-tool-group viewport-tool-segment">
        <button aria-label="Zoom out" title="Zoom out" onClick={() => zoom('out')}>−</button>
        <button aria-label="Zoom in" title="Zoom in" onClick={() => zoom('in')}>+</button>
      </div>
      <i className="wg2-tool-divider" />
      <div className="viewport-tool-group">
        <button type="button" className={preferencesOpen ? 'on' : ''} title="Viewer preferences" aria-label="Viewer preferences" aria-expanded={preferencesOpen} onClick={() => setPreferencesOpen((value) => !value)}><Icon name="settings"/></button>
      </div>
    </div>
    {preferencesOpen && <ViewerPreferencesPanel
      preferences={preferences}
      showEnclosure={showEnclosure}
      showStats={showStats}
      onShowEnclosure={setShowEnclosure}
      onShowStats={setShowStats}
      onClose={() => setPreferencesOpen(false)}
    />}
    <div className="viewport-scale" aria-hidden="true"><i /></div>
  </div>;
}
