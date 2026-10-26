import { useStore } from 'zustand';
import { selectCanRedo, selectCanUndo, useDesignStore } from '../../stores/design';
import type { WorkspaceHistory } from './historyAuthority';
import { Fragment, useEffect, useState, type ReactNode } from 'react';
import type { SketchViewPreferences } from '../viewPreferences';

export const drawingIcons = { select: 'M4 2 13 10 8 10 6 14Z', hand: 'M5 8V3a1 1 0 0 1 2 0v4-5a1 1 0 0 1 2 0v5-4a1 1 0 0 1 2 0v4-2a1 1 0 0 1 2 0v5q0 5-4 5H8q-2 0-3-2L2 8q1-2 3 0Z', line: 'M3 13 13 3', arc: 'M2 13Q2 2 14 3', dimension: 'M2 5V13M14 5V13M2 9H14M5 6 2 9 5 12M11 6 14 9 11 12', split: 'M2 12 7 7M10 4 14 0M6 2 14 10' };
export function NavigateTools({ tool, choose }: { tool: string; choose(tool: 'select' | 'hand'): void }) {
  return <div className="sketch-toolbar-group" role="group" aria-label="Navigate"><span className="sketch-group-caption">Navigate</span><div className="sketch-tool-segment">{(['select', 'hand'] as const).map(t => {
    const label = t === 'select' ? 'Select (Esc)' : 'Pan (hold Space)';
    return <Fragment key={t}><button type="button" aria-label={label} title={label} aria-pressed={tool === t} onClick={() => choose(t)}><svg aria-hidden="true" viewBox="0 0 16 16"><path d={drawingIcons[t]}/></svg><span className="sketch-tool-label">{t[0].toUpperCase() + t.slice(1)}</span></button></Fragment>;
  })}</div></div>;
}
interface Props {
  tool: string; enabled: boolean; viewOptions: SketchViewPreferences; navigation: ReactNode; children?: ReactNode;
  toggle(key: keyof SketchViewPreferences): void; undo(redo?: boolean): void; zoom(factor: number): void; fit(all?: boolean): void;
  onEscape?(): void; solver?: boolean; label?: string; historyKind?: WorkspaceHistory['historyKind'];
}
export function WorkspaceToolbar({ tool, enabled, viewOptions, navigation, children, toggle, undo, zoom, fit, onEscape, solver = true, label = 'Sketch tools', historyKind }: Props) {
  const pastStates = useStore(useDesignStore.temporal, state => state.pastStates);
  const futureStates = useStore(useDesignStore.temporal, state => state.futureStates);
  const dragSnapshot = useDesignStore(state => state.dragSnapshot);
  const canUndo = historyKind === 'project' ? selectCanUndo({ dragSnapshot }, { pastStates }) : enabled;
  const canRedo = historyKind === 'project' ? selectCanRedo({ futureStates }) : enabled;
  const [legend, setLegend] = useState(false);
  useEffect(() => { setLegend(false); }, [tool]);
  return <div role="toolbar" aria-label={label} className="sketch-toolbar" onKeyDown={event => { if (event.key === 'Escape') { onEscape?.(); setLegend(false); } }}>
    {navigation}
    {children}
    <button type="button" disabled={!enabled || !canUndo} onClick={() => undo()}>Undo</button><button type="button" disabled={!enabled || !canRedo} onClick={() => undo(true)}>Redo</button>
    <div className="sketch-view-tools" role="group" aria-label="View"><span className="sketch-group-caption">View</span>
      <button type="button" aria-label="Zoom out" title="Zoom out (−)" onClick={() => zoom(1 / 1.25)}>−</button><button type="button" aria-label="Zoom in" title="Zoom in (+)" onClick={() => zoom(1.25)}>+</button>
      <button type="button" title="Fit (F)" onClick={() => fit()}>Fit (F)</button><button type="button" title="Fit all (Shift+F)" onClick={() => fit(true)}>Fit all (Shift+F)</button>
      {(['dimensions', 'names', 'constraints'] as const).filter(key => solver || key !== 'constraints').map(key => <button type="button" key={key} aria-pressed={viewOptions[key]} onClick={() => toggle(key)}>{key[0].toUpperCase() + key.slice(1)}</button>)}
      <div className="sketch-menu-anchor"><button type="button" aria-label="Line styles" title="Line styles" aria-expanded={legend} onClick={() => setLegend(!legend)}>?</button>
        {legend && <div className="sketch-menu sketch-line-legend" role="dialog" aria-label="Line styles legend"><strong>Line styles</strong>{[
          ['not-analysed', 'Constraint state not analysed'], ['under', 'Profile: under-constrained'], ['determined', 'Profile: fully constrained'], ['construction', 'Construction'], ['projected', 'Projected context'], ['conflicting', 'Conflict'], ['under moving', 'Moving patch (2 px)'], ['under rigid', 'Rigid patch (3.5 px)'],
        ].filter(([style]) => solver || style === 'construction' || style === 'projected').map(([style, label]) => <div key={label}><svg aria-hidden="true" viewBox="0 0 40 12"><path d="M0 6H40" className={`sketch-entity ${style}`}/></svg><span>{label}</span></div>)}</div>}
      </div>
    </div>
  </div>;
}
