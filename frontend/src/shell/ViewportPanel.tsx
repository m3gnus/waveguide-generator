import { useState, useSyncExternalStore } from 'react';
import { previewSocket } from '../api/previewSocket';
import { useDesignStore } from '../stores/design';
import { Icon } from './icons';

const displayModes = [
  ['clay', 'Clay'], ['wire', 'Solid + wireframe'], ['wire', 'Wireframe'], ['xray', 'X-ray'],
  ['zebra', 'Zebra'], ['curve', 'Curvature'], ['section', 'Section cut'], ['box', 'Show enclosure'],
] as const;

export function ViewportPanel() {
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const design = useDesignStore((state) => state.design);
  const surfaces = preview.frame?.header.surfaces ?? [];
  const [activeMode, setActiveMode] = useState(1);
  return <div className="viewport-panel">
    <div className="viewport-title"><b>tritonia_mk2</b><span>{design.formula} · 84° × 60° · Ø {((design.R ?? 150) * 2).toFixed(0)} mm · half-sym</span></div>
    <div className="viewport-live"><span className={preview.stale ? 'stale-badge' : 'live-badge'}><i />{preview.stale ? 'STALE' : 'LIVE'}</span><span>preview <b>{preview.frame?.header.evalMs?.toFixed(1) ?? '—'}</b> ms</span></div>
    <div className="frame-stat-card">
      <span>latest binary frame</span>
      <dl>
        <div><dt>revision</dt><dd>{preview.frame?.header.designRevision ?? 'waiting'}</dd></div>
        <div><dt>LOD</dt><dd>{preview.frame?.header.lod ?? '—'}</dd></div>
        <div><dt>eval</dt><dd>{preview.frame?.header.evalMs !== undefined ? `${preview.frame.header.evalMs.toFixed(2)} ms` : '—'}</dd></div>
      </dl>
      <div className="surface-list">{surfaces.length ? surfaces.map((surface) => {
        const descriptor = preview.frame?.header.sections.find((section) => section.name === surface.positions);
        return <div key={surface.role}><span>{surface.role}</span><b>{descriptor?.shape[0]?.toLocaleString() ?? '—'} vertices</b></div>;
      }) : <p>Connect to the local preview engine to inspect surface roles and vertex counts.</p>}</div>
    </div>
    <div className="viewport-tools">{displayModes.map(([icon, title], index) => <button key={`${title}-${index}`} className={activeMode === index ? 'on' : ''} title={title} onClick={() => setActiveMode(index)}><Icon name={icon}/></button>)}</div>
    <div className="axis-gizmo"><i className="axis x">x</i><i className="axis y">y</i><i className="axis z">z</i></div>
    <div className="camera-tools"><div><button>Front</button><button className="on">¾</button><button>Top</button><button>Section</button></div><span>100 mm<i /></span></div>
  </div>;
}
