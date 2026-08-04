import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type { DecodedFrame } from '../api/frame';
import { previewSocket } from '../api/previewSocket';
import { subscribeRevision, useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { Icon } from '../shell/icons';
import { useViewerPreferences, viewerPreferences, type CameraProjection } from '../viewerprefs/viewerPreferences';
import { ViewerPreferencesPanel } from '../viewerprefs/ViewerPreferencesPanel';
import { frameToScene, hasRenderableSurfaces } from './frameScene';
import { createImportedMeshScene, type ImportedMeshScene } from './importedMesh';
import type { CameraDirection } from './cameraMath';
import { ClientLatencyClock, formatClientLatency } from './clientLatency';
import { selectPreferredFrame } from './lodPolicy';
import { parseMSH } from './mshParser';
import { filenameStem, previewBadge, previewErrorMessage, staleReason, viewportSubtitle } from './presentation';
import type { CameraPreset, DisplayMode, ViewportTheme } from './types';
import { canRenderWebGL, type CameraRequest, type ZoomRequest, ViewportCanvas } from './ViewportCanvas';
import './viewport.css';

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

/** How long the viewport may lag the design before it says so out loud. */
const STALL_NOTICE_MS = 1_500;

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

export function Viewport() {
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const design = useDesignStore((state) => state.design);
  const designRevision = useDesignStore((state) => state.designRevision);
  const filename = useDocumentStore((state) => state.filename);
  const theme = useViewportTheme();
  const preferences = useViewerPreferences();
  const selectedRef = useRef<DecodedFrame | null>(null);
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
  const scene = useMemo(() => selected ? frameToScene(selected) : null, [selected]);
  const [mode, setMode] = useState<DisplayMode>('clay');
  const [sectionCut, setSectionCut] = useState(false);
  const [showEnclosure, setShowEnclosure] = useState(true);
  const [showStats, setShowStats] = useState(false);
  const [clientFrameMs, setClientFrameMs] = useState<number | null>(null);
  const [renderFailure, setRenderFailure] = useState<string | null>(null);
  const [cameraRequest, setCameraRequest] = useState<CameraRequest>({ preset: 'three-quarter', nonce: 0 });
  const [zoomRequest, setZoomRequest] = useState<ZoomRequest>({ direction: 'in', nonce: 0 });
  const [cameraProjection, setCameraProjection] = useState<CameraProjection>(() => preferences.startupCameraMode);
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const [importedMesh, setImportedMesh] = useState<ImportedMeshScene | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [dismissedPreviewError, setDismissedPreviewError] = useState<string | null>(null);
  const [refreshRequestedAt, setRefreshRequestedAt] = useState<number | null>(null);
  const [stalled, setStalled] = useState(false);
  const meshInput = useRef<HTMLInputElement>(null);
  const setCamera = (preset: CameraPreset) => setCameraRequest((current) => ({ preset, nonce: current.nonce + 1 }));
  const setCameraDirection = (direction: CameraDirection) => setCameraRequest((current) => ({ direction, nonce: current.nonce + 1 }));
  const zoom = (direction: ZoomRequest['direction']) => setZoomRequest((current) => ({ direction, nonce: current.nonce + 1 }));
  const toggleProjection = () => setCameraProjection((current) => current === 'perspective' ? 'orthographic' : 'perspective');
  const reportClientFrame = useCallback((milliseconds: number) => setClientFrameMs(milliseconds), []);
  const activeScene = importedMesh?.scene ?? scene;
  const sceneMarker = importedMesh
    ? `msh:${importedMesh.name}:${importedMesh.triangleCount}`
    : `${selected?.header.epoch ?? 0}:${selected?.header.seq ?? 0}:${selected?.header.designRevision ?? 0}:${selected?.header.lod ?? ''}`;
  const surfaces = activeScene?.surfaces ?? [];
  const webgl = canRenderWebGL() && renderFailure === null;
  const hasSurfaces = hasRenderableSurfaces(activeScene);
  const connectionInterrupted = preferences.liveUpdate && preview.connection !== 'connected';
  const badge = previewBadge(preferences.liveUpdate, preview.connection, preview.error, preview.stale);
  const previewError = preview.error ? previewErrorMessage(preview.error, preview.errorRevision, designRevision) : null;
  const showPreviewError = previewError !== null && dismissedPreviewError !== preview.error;
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

  const importMesh = async (file: File | undefined) => {
    if (!file) return;
    setImportError(null);
    try {
      const imported = createImportedMeshScene(file.name, parseMSH(await file.text()));
      setImportedMesh(imported);
      setCameraRequest((current) => ({ ...current, nonce: current.nonce + 1 }));
    } catch (error) {
      setImportError(error instanceof Error ? error.message : String(error));
    } finally {
      if (meshInput.current) meshInput.current.value = '';
    }
  };

  const clearImportedMesh = () => {
    setImportedMesh(null);
    setImportError(null);
    setCameraRequest((current) => ({ ...current, nonce: current.nonce + 1 }));
  };

  useEffect(() => subscribeRevision((event) => {
    const epoch = currentEpoch.current;
    if (epoch === null) return;
    if (event.immediate) {
      if (fineRequestTimer.current) clearTimeout(fineRequestTimer.current);
      fineRequestTimer.current = null;
      queuedFineRevision.current = null;
      latencyClock.current.recordRequest(epoch, event.revision, 'fine', performance.now());
      return;
    }
    queuedFineRevision.current = event.revision;
    if (fineRequestTimer.current) return;
    latencyClock.current.recordRequest(epoch, event.revision, 'coarse', performance.now());
    fineRequestTimer.current = setTimeout(() => {
      fineRequestTimer.current = null;
      const revision = queuedFineRevision.current;
      queuedFineRevision.current = null;
      const activeEpoch = currentEpoch.current;
      if (revision !== null && activeEpoch !== null) {
        latencyClock.current.recordRequest(activeEpoch, revision, 'fine', performance.now());
      }
    }, 33);
  }), []);

  useEffect(() => {
    setClientFrameMs(null);
  }, [preview.epoch]);

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
      sectionCut={sectionCut}
      cameraRequest={cameraRequest}
      zoomRequest={zoomRequest}
      cameraProjection={cameraProjection}
      preferences={preferences}
      frameStartedAt={importedMesh || !selected ? null : latencyClock.current.requestStartedAt(selected.header)}
      onClientFrame={reportClientFrame}
      theme={theme}
      onRenderFailure={setRenderFailure}
      onCameraDirection={setCameraDirection}
    />}

    <div className="viewport-title">
      <b>{importedMesh?.name ?? filenameStem(filename)}</b>
      <span>{importedMesh
        ? `${importedMesh.triangleCount.toLocaleString()} triangles · ${importedMesh.physicalGroupCount} physical group${importedMesh.physicalGroupCount === 1 ? '' : 's'}`
        : viewportSubtitle(design)}</span>
    </div>
    <div className="viewport-live">
      {importedMesh && <span className="imported-mesh-badge">IMPORTED MESH <button type="button" onClick={clearImportedMesh}>Clear</button></span>}
      <span className={badge.className}><i />{refreshRequestedAt === null ? badge.label : 'REFRESHING'}</span>
      {behindDesign && !importedMesh && <button
        type="button"
        className="viewport-refresh"
        disabled={refreshRequestedAt !== null}
        title={staleReason(preferences.liveUpdate, preview.connection, preview.error)}
        aria-label={`Rebuild the preview. ${staleReason(preferences.liveUpdate, preview.connection, preview.error)}`}
        onClick={refresh}
      ><Icon name="reset"/>{preferences.liveUpdate ? 'Refresh' : 'Resume'}</button>}
      <span>server <b>{selected?.header.evalMs?.toFixed(1) ?? '—'}</b> + client <b>{formatClientLatency(clientFrameMs)}</b> ms</span>
    </div>

    {!activeScene && <div className="viewport-empty" role="status" aria-live="polite">
      <i className="viewport-empty-mark"><i /></i>
      <b>{preferences.liveUpdate ? 'Waiting for geometry' : 'Live updates paused'}</b>
      <span>{importError ?? preview.error ?? (!preferences.liveUpdate ? 'Enable Live updates in viewer preferences, or import an ASCII Gmsh 2.2 mesh.' : connectionInterrupted ? `Preview engine ${preview.connection}. The viewport will resume automatically.` : 'Requesting a live FRAME-SPEC scene from the local preview engine.')}</span>
    </div>}
    {showPreviewError && <div className="viewport-error-banner" role="alert">
      <span>{previewError}</span>
      <button type="button" disabled={refreshRequestedAt !== null} onClick={refresh}>Retry</button>
      <button type="button" aria-label="Dismiss preview error" title="Dismiss preview error" onClick={() => setDismissedPreviewError(preview.error)}>×</button>
    </div>}
    {activeScene && stalled && !showPreviewError && !connectionInterrupted && !importedMesh && <div className="viewport-connection-banner" role="status">
      <span><i />{staleReason(preferences.liveUpdate, preview.connection, preview.error)}</span>
      <button type="button" onClick={refresh} disabled={refreshRequestedAt !== null}>Refresh</button>
    </div>}
    {activeScene && connectionInterrupted && !importedMesh && <div className={`viewport-connection-banner${showPreviewError ? ' below-error' : ''}`} role="status">
      <span><i />{preview.connection === 'reconnecting' ? 'Reconnecting to preview engine' : 'Preview connection interrupted'}</span>
      <b>Last valid geometry retained</b>
    </div>}
    {activeScene && hasSurfaces && !webgl && !renderFailure && <div className="viewport-empty"><b>WebGL unavailable</b><span>The geometry is valid, but this environment cannot create a WebGL2 context.</span></div>}
    {activeScene && !hasSurfaces && <div className="viewport-empty" role="status"><b>No geometry surfaces</b><span>The scene is valid but contains no renderable surfaces.</span></div>}
    {activeScene && renderFailure && <div className="viewport-empty" role="status"><b>WebGL renderer stopped</b><span>{renderFailure}. Reopen the viewport after checking graphics acceleration.</span></div>}
    {activeScene && mode === 'curvature' && !activeScene.hasCurvature && <div className="viewport-mode-empty">
      <b>Curvature heatmap unavailable</b><span>This frame has no analytic curvature section. Neutral geometry remains visible while inspection data is requested.</span>
    </div>}
    {importError && activeScene && <div className="mesh-import-error" role="alert">Import failed: {importError}</div>}

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

    <div className="viewport-tools">
      <div className="viewport-tool-group display-mode-tools">
        {modes.map((item) => <button
          key={item.mode}
          className={mode === item.mode ? 'on' : ''}
          title={item.title}
          aria-label={item.title}
          aria-pressed={mode === item.mode}
          onClick={() => setMode(item.mode)}
        >{item.icon ? <Icon name={item.icon}/> : <span className="wg2-text-glyph">{item.glyph}</span>}</button>)}
      </div>
      <i className="wg2-tool-divider" />
      <div className="viewport-tool-group">
        <button className={sectionCut ? 'on' : ''} title="Section cut at X=0" aria-label="Section cut at X=0" aria-pressed={sectionCut} onClick={() => setSectionCut((value) => !value)}><Icon name="section"/></button>
        <button className={showEnclosure ? 'on' : ''} title="Show enclosure" aria-label="Show enclosure" aria-pressed={showEnclosure} onClick={() => setShowEnclosure((value) => !value)}><Icon name="box"/></button>
        <button className={showStats ? 'on' : ''} title="Frame stats" aria-label="Frame stats" aria-pressed={showStats} onClick={() => setShowStats((value) => !value)}><span className="wg2-stats-glyph">Σ</span></button>
      </div>
      <i className="wg2-tool-divider" />
      <div className="viewport-tool-group viewport-tool-segment" aria-label="View presets">
        <button className={`viewport-tool-text${cameraRequest.preset === 'front' ? ' on' : ''}`} onClick={() => setCamera('front')}>Front</button>
        <button className={`viewport-tool-text${cameraRequest.preset === 'three-quarter' ? ' on' : ''}`} onClick={() => setCamera('three-quarter')}>¾</button>
        <button className={`viewport-tool-text${cameraRequest.preset === 'top' ? ' on' : ''}`} onClick={() => setCamera('top')}>Top</button>
      </div>
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
        <input ref={meshInput} className="mesh-file-input" type="file" accept=".msh,text/plain" aria-label="Import Gmsh mesh" onChange={(event) => void importMesh(event.target.files?.[0])} />
        <button type="button" title="Import Gmsh 2.2 mesh" aria-label="Import Gmsh 2.2 mesh" onClick={() => meshInput.current?.click()}><span className="wg2-text-glyph">MSH</span></button>
        <button type="button" className={preferencesOpen ? 'on' : ''} title="Viewer preferences" aria-label="Viewer preferences" aria-expanded={preferencesOpen} onClick={() => setPreferencesOpen((value) => !value)}><span className="wg2-settings-glyph">⚙</span></button>
      </div>
    </div>
    {preferencesOpen && <ViewerPreferencesPanel preferences={preferences} onClose={() => setPreferencesOpen(false)} />}
    <div className="viewport-scale" aria-hidden="true"><i /></div>
  </div>;
}
