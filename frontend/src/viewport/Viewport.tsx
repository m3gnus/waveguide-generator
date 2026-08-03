import { useCallback, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type { DecodedFrame } from '../api/frame';
import { previewSocket } from '../api/previewSocket';
import { useDesignStore } from '../stores/design';
import { Icon } from '../shell/icons';
import { frameToScene } from './frameScene';
import { selectPreferredFrame } from './lodPolicy';
import type { CameraPreset, DisplayMode } from './types';
import { canRenderWebGL, type CameraRequest, ViewportCanvas } from './ViewportCanvas';
import './viewport.css';

const modes: Array<{ mode: DisplayMode; title: string; icon: 'clay' | 'wire' | 'xray' | 'zebra' | 'curve' | 'section' }> = [
  { mode: 'clay', title: 'Clay', icon: 'clay' },
  { mode: 'solid-wire', title: 'Solid + wireframe', icon: 'wire' },
  { mode: 'wireframe', title: 'Wireframe', icon: 'wire' },
  { mode: 'xray', title: 'X-ray', icon: 'xray' },
  { mode: 'zebra', title: 'Zebra', icon: 'zebra' },
  { mode: 'curvature', title: 'Curvature', icon: 'curve' },
  { mode: 'edges', title: 'Hard-boundary edges', icon: 'section' },
];

export function Viewport() {
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const design = useDesignStore((state) => state.design);
  const selectedRef = useRef<DecodedFrame | null>(null);
  const selectedStartedAt = useRef(performance.now());
  const selected = selectPreferredFrame(selectedRef.current, preview.frame);
  if (selected !== selectedRef.current) {
    selectedRef.current = selected;
    selectedStartedAt.current = performance.now();
  }
  const scene = useMemo(() => selected ? frameToScene(selected) : null, [selected]);
  const [mode, setMode] = useState<DisplayMode>('clay');
  const [sectionCut, setSectionCut] = useState(false);
  const [showEnclosure, setShowEnclosure] = useState(true);
  const [showStats, setShowStats] = useState(false);
  const [clientFrameMs, setClientFrameMs] = useState<number | null>(null);
  const [cameraRequest, setCameraRequest] = useState<CameraRequest>({ preset: 'three-quarter', nonce: 0 });
  const setCamera = (preset: CameraPreset) => setCameraRequest((current) => ({ preset, nonce: current.nonce + 1 }));
  const reportClientFrame = useCallback((milliseconds: number) => setClientFrameMs(milliseconds), []);
  const surfaces = selected?.header.surfaces ?? [];
  const webgl = canRenderWebGL();

  return <div className="viewport-panel wg2-viewport">
    {selected && webgl && <ViewportCanvas
      frame={selected}
      mode={mode}
      showEnclosure={showEnclosure}
      sectionCut={sectionCut}
      cameraRequest={cameraRequest}
      frameStartedAt={selectedStartedAt.current}
      onClientFrame={reportClientFrame}
    />}

    <div className="viewport-title">
      <b>tritonia_mk2</b>
      <span>{design.formula} · 84° × 60° · Ø {((design.R ?? 150) * 2).toFixed(0)} mm · half-sym</span>
    </div>
    <div className="viewport-live">
      <span className={preview.stale ? 'stale-badge' : 'live-badge'}><i />{preview.stale ? 'STALE' : 'LIVE'}</span>
      <span>server <b>{selected?.header.evalMs?.toFixed(1) ?? '—'}</b> + client <b>{clientFrameMs?.toFixed(1) ?? '—'}</b> ms</span>
    </div>

    {!selected && <div className="viewport-empty">
      <b>Waiting for geometry</b>
      <span>{preview.error ?? 'Connect to the local preview engine to render a live FRAME-SPEC scene.'}</span>
    </div>}
    {selected && !webgl && <div className="viewport-empty"><b>WebGL unavailable</b><span>The live geometry is valid, but this environment cannot create a WebGL context.</span></div>}
    {selected && mode === 'curvature' && !scene?.hasCurvature && <div className="viewport-mode-empty">
      <b>Curvature needs inspection LOD</b><span>This frame has no analytic curvature section. Geometry remains available in every other display mode.</span>
    </div>}

    {showStats && <div className="frame-stat-card wg2-stats">
      <span>latest displayed binary frame</span>
      <dl>
        <div><dt>revision</dt><dd>{selected?.header.designRevision ?? 'waiting'}</dd></div>
        <div><dt>LOD</dt><dd>{selected?.header.lod ?? '—'}</dd></div>
        <div><dt>eval</dt><dd>{selected?.header.evalMs !== undefined ? `${selected.header.evalMs.toFixed(2)} ms` : '—'}</dd></div>
      </dl>
      <div className="surface-list">{surfaces.length ? surfaces.map((surface, index) => {
        const descriptor = selected?.header.sections.find((section) => section.name === surface.positions);
        return <div key={`${index}:${surface.role}`}><span>{surface.role}</span><b>{descriptor?.shape[0]?.toLocaleString() ?? '—'} vertices</b></div>;
      }) : <p>No rendered surfaces in this frame.</p>}</div>
    </div>}

    <div className="viewport-tools">
      {modes.map((item) => <button
        key={item.mode}
        className={mode === item.mode ? 'on' : ''}
        title={item.title}
        aria-label={item.title}
        aria-pressed={mode === item.mode}
        onClick={() => setMode(item.mode)}
      ><Icon name={item.icon}/></button>)}
      <i className="wg2-tool-divider" />
      <button className={sectionCut ? 'on' : ''} title="Section cut at X=0" aria-label="Section cut at X=0" aria-pressed={sectionCut} onClick={() => setSectionCut((value) => !value)}><Icon name="section"/></button>
      <button className={showEnclosure ? 'on' : ''} title="Show enclosure" aria-label="Show enclosure" aria-pressed={showEnclosure} onClick={() => setShowEnclosure((value) => !value)}><Icon name="box"/></button>
      <button className={showStats ? 'on' : ''} title="Frame stats" aria-label="Frame stats" aria-pressed={showStats} onClick={() => setShowStats((value) => !value)}><span className="wg2-stats-glyph">Σ</span></button>
    </div>
    <div className="axis-gizmo"><i className="axis x">x</i><i className="axis y">y</i><i className="axis z">z</i></div>
    <div className="camera-tools"><div>
      <button className={cameraRequest.preset === 'front' ? 'on' : ''} onClick={() => setCamera('front')}>Front</button>
      <button className={cameraRequest.preset === 'three-quarter' ? 'on' : ''} onClick={() => setCamera('three-quarter')}>¾</button>
      <button className={cameraRequest.preset === 'top' ? 'on' : ''} onClick={() => setCamera('top')}>Top</button>
      <button className={sectionCut ? 'on' : ''} onClick={() => setSectionCut((value) => !value)}>Section</button>
    </div><span>100 mm<i /></span></div>
  </div>;
}
