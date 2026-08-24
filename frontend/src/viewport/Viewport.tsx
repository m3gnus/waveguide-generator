import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type { DecodedFrame } from '../api/frame';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { PREVIEW_FINE_IDLE_MS, previewSocket } from '../api/previewSocket';
import { compareSelection } from '../api/results';
import { runContext, runMatchesContext, useRunContext, type RunContext } from '../results/runCoherence';
import { postSymmetry, toSolveDesign } from '../jobs/actions';
import { cadApplicationName, usePreferences } from '../prefs/preferences';
import { useCadReturnStore } from '../stores/cadReturn';
import { subscribeRevision, useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { cadLinkCoordinatorBridge, showIngestedMeshInViewport } from '../shell/CadLinkCoordinator';
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
import type { ModelClipMode } from './fieldPlaneClipping';
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
import type { FieldPlaneResponseId } from '../api/fieldPlane';
import { importedMeshStore } from './importedMeshStore';
import type { CameraDirection } from './cameraMath';
import { ClientLatencyClock, formatClientLatency } from './clientLatency';
import { selectPreferredFrame } from './lodPolicy';
import { previewBadge, previewErrorMessage, staleReason, viewportSubtitle } from './presentation';
import { markParametricSolvedDomain, quadrantsForSolveMode, type DisplayQuadrants } from './symmetryScene';
import { ROLE_MATERIAL_FAMILY, SOURCE_ROLES } from './types';
import type { DisplayMode, SourceRole, ViewportTheme } from './types';
import { canRenderWebGL, resetWebGLProbe, type CameraRequest, type ZoomRequest, ViewportCanvas } from './ViewportCanvas';
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
  context: RunContext = runContext(),
): JobItem | null {
  const candidate = jobs.find((job) => job.id === selectedJobId && job.status === 'complete');
  return candidate?.field_plane_available === true && runMatchesContext(candidate, context) === 'current'
    ? candidate
    : null;
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
    case 'unsupported_axisymmetric_formulation': return 'Field plane unavailable — switch Solver mode to Full 3D with Metal or BEMPP; coupled infinite baffle must also be changed to Free-standing';
    case 'unsupported_coupled_infinite_baffle': return 'Field plane unavailable — switch Simulation type to Free-standing and re-solve with Metal or BEMPP';
    case 'unsupported_solve_mode': return 'Field plane unavailable — use a supported full-3D solve';
    case 'disabled_by_option': return 'Field plane unavailable — enable Keep field plane data and re-solve';
    default: return 'Field plane unavailable — no completed run has retained field traces';
  }
}

export function cadViewportEmptyCopy(options: {
  bundleName: string;
  bundleReadable: boolean;
  ingesting: boolean;
  ingestError: string | null;
  cadApplication: string;
}): { title: string; detail: string } {
  const { bundleName, bundleReadable, ingesting, ingestError, cadApplication } = options;
  if (!bundleReadable) {
    return {
      title: 'No CAD geometry yet',
      detail: `Bring a model back from ${cadApplication}; CAD Link will prepare it automatically.`,
    };
  }
  if (ingesting) {
    return {
      title: `Preparing ${bundleName}…`,
      detail: 'CAD Link is building the viewport and solver mesh.',
    };
  }
  if (ingestError) {
    return {
      title: 'CAD preparation failed',
      detail: 'Preparation failed. Open CAD Link and use “Prepare simulation” to retry.',
    };
  }
  return {
    title: 'CAD return selected',
    detail: 'Select a return in CAD Link to prepare it automatically.',
  };
}

export function Viewport() {
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const design = useDesignStore((state) => state.design);
  const designRevision = useDesignStore((state) => state.designRevision);
  const solveSymmetry = useSolveOptionsStore((state) => state.symmetry);
  const designName = useDocumentStore((state) => state.designName);
  const workspaceMode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;
  const cadRecord = useCadReturnStore((state) => state.ingestRecord);
  const cadBundleReadable = useCadReturnStore((state) => state.selectedBundle?.readable === true);
  const cadName = useCadReturnStore((state) => state.selectedBundle?.documentName ?? state.selectedBundle?.name ?? 'CAD Link');
  const cadCoordinator = useSyncExternalStore(cadLinkCoordinatorBridge.subscribe, cadLinkCoordinatorBridge.getSnapshot, cadLinkCoordinatorBridge.getSnapshot);
  const theme = useViewportTheme();
  const preferences = useViewerPreferences();
  const cadApplication = cadApplicationName(usePreferences().cadApplication);
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const solveRunningOrQueued = jobs.some((job) => job.status === 'running' || job.status === 'queued');
  const cadEmpty = cadViewportEmptyCopy({
    bundleName: cadName,
    bundleReadable: cadBundleReadable,
    ingesting: cadCoordinator.ingesting,
    ingestError: cadCoordinator.ingestError,
    cadApplication,
  });
  const resultSelection = useSyncExternalStore(compareSelection.subscribe, compareSelection.getSnapshot, compareSelection.getSnapshot);
  const coherenceContext = useRunContext();
  const availableFieldJob = useMemo(
    () => fieldPlaneJob(jobs, resultSelection.primary, coherenceContext),
    [coherenceContext.designId, coherenceContext.designRevision, coherenceContext.ingestId, coherenceContext.mode, jobs, resultSelection.primary],
  );
  const fieldEnabled = useFieldPlaneStore((state) => state.enabled);
  const fieldJobId = useFieldPlaneStore((state) => state.jobId);
  const fieldStatus = useFieldPlaneStore((state) => state.status);
  const fieldError = useFieldPlaneStore((state) => state.error);
  const field = useFieldPlaneStore((state) => state.field);
  const fieldPlane = useFieldPlaneStore((state) => state.plane);
  const fieldGeometrySha256 = useFieldPlaneStore((state) => state.geometrySha256);
  const fieldMaskState = useFieldPlaneMaskStore((state) => state);
  const fieldFrequencyIndex = useFieldPlaneStore((state) => state.frequencyIndex);
  const fieldFrequencies = useFieldPlaneStore((state) => state.frequenciesHz);
  const fieldResponseId = useFieldPlaneStore((state) => state.responseId);
  const fieldMemberResponses = useFieldPlaneStore((state) => state.memberResponses);
  const [fieldFrequencySnap, setFieldFrequencySnap] = useState<{ requestedHz: number; index: number } | null>(null);
  const fieldDisplayMode = useFieldPlaneStore((state) => state.displayMode);
  const fieldRangeLocked = useFieldPlaneStore((state) => state.rangeLocked);
  const fieldAnimationSpeed = useFieldPlaneStore((state) => state.animationSpeed);
  const fieldAnimating = useFieldPlaneStore((state) => state.animating);
  const fieldIsolines = useFieldPlaneStore((state) => state.isolines);
  const storedFieldWindow = useFieldPlaneStore((state) => state.windows[state.displayMode]);
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
    if (!preferences.tintSolvedRegion || solveSymmetry !== 'auto' || previewScene === null) return undefined;
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
  }, [design.mesh.quadrants, preferences.tintSolvedRegion, previewScene, solveSymmetry, symmetryBody]);
  const solvedQuadrants = quadrantsForSolveMode(solveSymmetry, autoQuadrants);
  const scene = useMemo(
    () => previewScene && preferences.tintSolvedRegion
      ? markParametricSolvedDomain(previewScene, solvedQuadrants)
      : previewScene,
    [preferences.tintSolvedRegion, previewScene, solvedQuadrants],
  );
  const [mode, setMode] = useState<DisplayMode>('clay');
  const [clipMode, setClipMode] = useState<ModelClipMode>('off');
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
  const cadMesh = cadRecord && importedMeshState.cad?.ingestId === cadRecord.ingest_id
    ? importedMeshState.cad
    : null;
  const importedMesh = workspaceMode === 'cad'
    ? cadMesh
    : importedMeshState.showing === 'file'
      ? importedMeshState.file
      : null;
  const availableFileMesh = importedMeshState.file;
  const [dismissedPreviewError, setDismissedPreviewError] = useState<string | null>(null);
  const [refreshRequestedAt, setRefreshRequestedAt] = useState<number | null>(null);
  const [stalled, setStalled] = useState(false);
  const setCameraDirection = useCallback((direction: CameraDirection) => {
    setCameraRequest((current) => ({ direction, nonce: current.nonce + 1 }));
  }, []);
  const zoom = (direction: ZoomRequest['direction']) => setZoomRequest((current) => ({ direction, nonce: current.nonce + 1 }));
  const toggleProjection = () => setCameraProjection((current) => current === 'perspective' ? 'orthographic' : 'perspective');
  const reportClientFrame = useCallback((milliseconds: number) => setClientFrameMs(milliseconds), []);
  const activeScene = workspaceMode === 'cad' ? importedMesh?.scene ?? null : importedMesh?.scene ?? scene;
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
    ? `msh:${importedMesh.artifactToken}`
    : workspaceMode === 'cad'
      ? 'cad:empty'
      : `${selected?.header.epoch ?? 0}:${selected?.header.seq ?? 0}:${selected?.header.designRevision ?? 0}:${selected?.header.lod ?? ''}:${preferences.tintSolvedRegion ? solvedQuadrants : 'same'}`;
  const surfaces = activeScene?.surfaces ?? [];

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

  const toggleFieldPlane = () => {
    if (fieldEnabled) {
      if (clipMode === 'field-plane') setClipMode('off');
      setInvertFieldClip(false);
      useFieldPlaneStore.getState().disable();
      return;
    }
    if (!availableFieldJob || !activeScene) return;
    useFieldPlaneStore.getState().enable(availableFieldJob.id, defaultFieldPlane(activeScene));
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

  // A lost context is recoverable: mounting a fresh Canvas asks for a new one.
  // Clearing the failure is what lets that happen, and re-probing covers the
  // case where the whole WebGL2 capability went away and has since come back.
  const retryRenderer = () => {
    resetWebGLProbe();
    setRenderFailure(null);
  };

  const previousWorkspaceMode = useRef(workspaceMode);
  useEffect(() => {
    if (workspaceMode === 'parametric') {
      // Leaving CAD deliberately returns to the live parametric scene. A file
      // import remains cached and can still be reselected by its own badge.
      // Do not reset on unrelated CAD-store updates while already parametric:
      // that would make a standalone file overlay disappear under the cursor.
      if (previousWorkspaceMode.current === 'cad') importedMeshStore.showParametric();
      previousWorkspaceMode.current = workspaceMode;
      return;
    }
    previousWorkspaceMode.current = workspaceMode;
    const generation = importedMeshStore.beginIntent();
    if (!cadRecord) {
      importedMeshStore.clear('cad');
      return;
    }
    const cached = importedMeshStore.getSnapshot().cad;
    if (cached?.ingestId === cadRecord.ingest_id) {
      importedMeshStore.showCad(generation);
      return;
    }
    void showIngestedMeshInViewport(
      cadRecord,
      cadName,
      (notice) => cadLinkCoordinatorBridge.getSnapshot().reportViewportNotice(notice),
      fetch,
      generation,
    );
  }, [cadName, cadRecord, workspaceMode]);

  // The mesh may be set from outside this component (CAD Link after an
  // ingest), so the camera refit follows the store rather than the setters.
  const lastImportedMesh = useRef(importedMesh);
  useEffect(() => {
    if (lastImportedMesh.current === importedMesh) return;
    lastImportedMesh.current = importedMesh;
    setCameraRequest((current) => ({ ...current, nonce: current.nonce + 1 }));
  }, [importedMesh]);

  useEffect(() => {
    if (availableFieldJob || !fieldEnabled) return;
    useFieldPlaneStore.getState().reportUnavailable('re-solve to enable field planes');
  }, [availableFieldJob, fieldEnabled]);

  useEffect(() => {
    if (!fieldEnabled || !availableFieldJob) return;
    if (!activeScene || fieldJobId === availableFieldJob.id) return;
    useFieldPlaneStore.getState().selectJob(availableFieldJob.id, defaultFieldPlane(activeScene));
  }, [activeScene, availableFieldJob, fieldEnabled, fieldJobId]);

  useEffect(() => {
    const wasRunningOrQueued = solveWasRunningOrQueued.current;
    solveWasRunningOrQueued.current = solveRunningOrQueued;
    if (wasRunningOrQueued && !solveRunningOrQueued) {
      useFieldPlaneStore.getState().retryIfBlocked();
    }
  }, [solveRunningOrQueued]);

  useEffect(() => {
    if (availableFieldJob && fieldEnabled && fieldPlane) return;
    if (clipMode === 'field-plane') setClipMode('off');
    if (!availableFieldJob && invertFieldClip) setInvertFieldClip(false);
  }, [availableFieldJob, clipMode, fieldEnabled, fieldPlane, invertFieldClip]);

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

  return <div className="viewport-panel wg2-viewport">
    {activeScene && hasSurfaces && webgl && <ViewportCanvas
      scene={activeScene}
      sceneMarker={sceneMarker}
      mode={mode}
      showEnclosure={showEnclosure}
      clipMode={clipMode}
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
    />}

    <div className="viewport-title">
      <b>{importedMesh?.name ?? (workspaceMode === 'cad' ? cadName : designName || 'Untitled design')}</b>
      <span>{importedMesh
        ? `${importedMesh.triangleCount.toLocaleString()} display triangles${importedMesh.triangleCount !== importedMesh.solvedTriangleCount ? ` · ${importedMesh.solvedTriangleCount.toLocaleString()} solved` : ''} · ${importedMesh.physicalGroupCount} physical group${importedMesh.physicalGroupCount === 1 ? '' : 's'}`
        : workspaceMode === 'cad' ? (cadCoordinator.ingesting && cadBundleReadable ? `Preparing ${cadName}…` : cadCoordinator.ingestError && cadBundleReadable ? 'CAD preparation failed' : 'No ingested CAD viewport mesh') : viewportSubtitle(design)}</span>
    </div>
    <div className="viewport-live">
      {workspaceMode === 'parametric' && availableFileMesh && <span className="imported-mesh-badge" aria-label="Standalone mesh display">
        <button type="button" className={!importedMesh ? 'active' : ''} aria-pressed={!importedMesh} onClick={showParametric}>Parametric</button>
        <button type="button" className={importedMesh?.source === 'file' ? 'active' : ''} aria-pressed={importedMesh?.source === 'file'} onClick={() => importedMeshStore.showFile()}>Imported mesh</button>
      </span>}
      {workspaceMode === 'parametric' && <span className={badge.className}><i />{refreshRequestedAt === null ? badge.label : 'REFRESHING'}</span>}
      {workspaceMode === 'parametric' && behindDesign && !importedMesh && <button
        type="button"
        className="viewport-refresh"
        disabled={refreshRequestedAt !== null}
        title={staleReason(preferences.liveUpdate, preview.connection, preview.error)}
        aria-label={`Rebuild the preview. ${staleReason(preferences.liveUpdate, preview.connection, preview.error)}`}
        onClick={refresh}
      ><Icon name="reset"/>{preferences.liveUpdate ? 'Refresh' : 'Resume'}</button>}
      {workspaceMode === 'parametric'
        ? <span>geometry <b>{selected?.header.evalMs?.toFixed(1) ?? '—'}</b> ms · on screen <b>{formatClientLatency(clientFrameMs)}</b></span>
        : <span>CAD Link workspace</span>}
    </div>

    {!activeScene && <div className="viewport-empty" role="status" aria-live="polite">
      <i className="viewport-empty-mark"><i /></i>
      <b>{workspaceMode === 'cad' ? cadEmpty.title : preferences.liveUpdate ? 'Waiting for geometry' : 'Live updates paused'}</b>
      <span>{workspaceMode === 'cad' ? cadEmpty.detail : preview.error ?? (!preferences.liveUpdate ? 'Enable Live updates in viewer preferences, or import an ASCII Gmsh 2.2 mesh from the design file menu.' : connectionInterrupted ? `Preview engine ${preview.connection}. The viewport will resume automatically.` : 'Requesting a live FRAME-SPEC scene from the local preview engine.')}</span>
    </div>}
    {showPreviewError && <div className="viewport-error-banner" role="alert">
      <span>{previewError}</span>
      <button type="button" disabled={refreshRequestedAt !== null} onClick={refresh}>Retry</button>
      <button type="button" aria-label="Dismiss preview error" title="Dismiss preview error" onClick={() => setDismissedPreviewError(preview.error)}><Icon name="close"/></button>
    </div>}
    {activeScene && stalled && !showPreviewError && !connectionInterrupted && !importedMesh && <div className="viewport-connection-banner" role="status">
      <span><i />{staleReason(preferences.liveUpdate, preview.connection, preview.error)}</span>
      <button type="button" onClick={refresh} disabled={refreshRequestedAt !== null}>Refresh</button>
    </div>}
    {activeScene && connectionInterrupted && !importedMesh && <div className={`viewport-connection-banner${showPreviewError ? ' below-error' : ''}`} role="status">
      <span><i />{preview.connection === 'reconnecting' ? 'Reconnecting to preview engine' : 'Preview connection interrupted'}</span>
      <b>Last valid geometry retained</b>
    </div>}
    {activeScene && geometryWarnings.length > 0 && <details className="viewport-warning" role="status">
      <summary aria-label={`${geometryWarnings.length} geometry warning${geometryWarnings.length === 1 ? '' : 's'}`}>
        <i />{geometryWarnings.length} warning{geometryWarnings.length === 1 ? '' : 's'}
      </summary>
      <div className="viewport-warning-details">
        <b>Geometry warning{geometryWarnings.length === 1 ? '' : 's'}</b>
        <ul>{geometryWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
      </div>
    </details>}
    {activeScene && hasSurfaces && !webgl && !renderFailure && <div className="viewport-empty"><b>WebGL unavailable</b><span>The geometry is valid, but this environment cannot create a WebGL2 context.</span></div>}
    {activeScene && !hasSurfaces && <div className="viewport-empty" role="status"><b>No geometry surfaces</b><span>The scene is valid but contains no renderable surfaces.</span></div>}
    {activeScene && renderFailure && <div className="viewport-empty" role="status">
      <b>WebGL renderer stopped</b>
      <span>{renderFailure}. Retry to rebuild the viewport; if it stops again, check that graphics acceleration is enabled.</span>
      <button type="button" className="viewport-retry" onClick={retryRenderer}><Icon name="reset"/>Retry</button>
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

    {fieldEnabled && <div
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
      {fieldFrequencies.length > 1 && <div className="field-plane-frequency">
        {/* The slider indexes solved frequencies, so it snaps to what the
            solver actually evaluated rather than interpolating between them.
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
      <div className="field-plane-presets" aria-label="Field plane presets">
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
        {fieldStatus === 'error' && fieldJobId && <button type="button" onClick={() => useFieldPlaneStore.getState().retry()}>Retry</button>}
      </div>}
    </div>}

    {fieldEnabled && <FieldPlaneProbeTooltip fieldMaxSplDb={fieldMaxSplDb}/>}

    <div className="viewport-tools">
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
        <button className={clipMode === 'section' ? 'on' : ''} title="Section cut at X=0" aria-label="Section cut at X=0" aria-pressed={clipMode === 'section'} onClick={() => setClipMode((value) => value === 'section' ? 'off' : 'section')}><Icon name="section"/></button>
      </div>
      {availableFieldJob && <div className="viewport-tool-group">
        <button
          type="button"
          className={fieldEnabled ? 'on' : ''}
          title={!activeScene ? 'Field plane unavailable — waiting for viewport geometry' : 'Toggle acoustic field plane'}
          aria-label="Acoustic field plane overlay"
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
