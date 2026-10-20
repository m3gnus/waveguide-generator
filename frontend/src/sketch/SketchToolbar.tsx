import { Fragment, useEffect, useState } from 'react';
import type { SketchViewPreferences } from './viewPreferences';
import type { constraintPalette, SelectionConstraint } from './tools/constraints';

export type Tool = 'select' | 'hand' | 'line' | 'tangent' | 'arc3' | 'dimension' | 'split' | 'constraint';
type PaletteEntry = ReturnType<typeof constraintPalette>[number] & { disabled: boolean };
interface Props {
  tool: Tool; arcTool: 'tangent' | 'arc3'; enabled: boolean; palette: PaletteEntry[]; activeConstraint?: SelectionConstraint;
  viewOptions: SketchViewPreferences;
  choose(tool: Tool): void; constrain(entry: PaletteEntry): void; toggle(key: keyof SketchViewPreferences): void;
  undo(redo?: boolean): void; zoom(factor: number): void; fit(all?: boolean): void;
}
export function SketchToolbar({ tool, arcTool, enabled, palette, activeConstraint, viewOptions, choose, constrain, toggle, undo, zoom, fit }: Props) {
  const [arcMenu, setArcMenu] = useState(false), [legend, setLegend] = useState(false);
  useEffect(() => { setArcMenu(false); setLegend(false); }, [tool, arcTool]);
  const drawingButton = (t: 'select' | 'hand' | 'line' | 'arc' | 'dimension' | 'split') => {
    const next = t === 'arc' ? arcTool : t;
    const label = { select: 'Select (Esc)', hand: 'Pan (hold Space)', line: 'Line (L)', arc: arcTool === 'tangent' ? 'Tangent arc (A)' : '3-point arc (A)', dimension: 'Dimension (D)', split: 'Split (S)' }[t];
    const icons = { select: 'M4 2 13 10 8 10 6 14Z', hand: 'M5 8V3a1 1 0 0 1 2 0v4-5a1 1 0 0 1 2 0v5-4a1 1 0 0 1 2 0v4-2a1 1 0 0 1 2 0v5q0 5-4 5H8q-2 0-3-2L2 8q1-2 3 0Z', line: 'M3 13 13 3', arc: 'M2 13Q2 2 14 3', dimension: 'M2 5V13M14 5V13M2 9H14M5 6 2 9 5 12M11 6 14 9 11 12', split: 'M2 12 7 7M10 4 14 0M6 2 14 10' };
    return <Fragment key={t}><button type="button" aria-label={label} title={label} aria-pressed={tool === next} disabled={!enabled && t !== 'select' && t !== 'hand'} onClick={() => { setArcMenu(false); choose(next); }}><svg aria-hidden="true" viewBox="0 0 16 16"><path d={icons[t]}/></svg><span className="sketch-tool-label">{t === 'arc' ? 'Arc' : t[0].toUpperCase() + t.slice(1)}</span></button>{t === 'arc' && <div className="sketch-menu-anchor"><button type="button" aria-label="Arc options" aria-expanded={arcMenu} onClick={() => setArcMenu(!arcMenu)}>⌄</button>
      {arcMenu && <div className="sketch-menu" role="group" aria-label="Arc modes">{(['tangent', 'arc3'] as const).map(mode => <button type="button" key={mode} aria-pressed={arcTool === mode} onClick={() => { setArcMenu(false); choose(mode); }}>{mode === 'tangent' ? 'Tangent arc (A)' : '3-point arc (A)'}</button>)}</div>}
    </div>}</Fragment>;
  };
  return <div role="toolbar" aria-label="Sketch tools" className="sketch-toolbar" onKeyDown={event => { if (event.key === 'Escape') { setArcMenu(false); setLegend(false); } }}>
    <div className="sketch-toolbar-group" role="group" aria-label="Navigate"><span className="sketch-group-caption">Navigate</span><div className="sketch-tool-segment">{(['select', 'hand'] as const).map(drawingButton)}</div></div>
    <div className="sketch-toolbar-group" role="group" aria-label="Sketch"><span className="sketch-group-caption">Sketch</span><div className="sketch-tool-segment">{(['line', 'arc', 'dimension', 'split'] as const).map(drawingButton)}</div></div>
    <div className="sketch-toolbar-group" role="group" aria-label="Constraints"><span className="sketch-group-caption">Constraints</span><div className="sketch-tool-segment">{palette.map(entry => <button type="button" key={entry.kind} aria-label={entry.label} title={entry.key ? `${entry.label} (${entry.key.toUpperCase()})` : entry.label} aria-pressed={tool === 'constraint' && activeConstraint === entry.kind} disabled={entry.disabled} onClick={() => constrain(entry)}><span aria-hidden="true" className="sketch-constraint-icon">{entry.symbol}</span></button>)}</div></div>
    <button type="button" disabled={!enabled} onClick={() => undo()}>Undo</button><button type="button" disabled={!enabled} onClick={() => undo(true)}>Redo</button>
    <div className="sketch-view-tools" role="group" aria-label="View"><span className="sketch-group-caption">View</span>
      <button type="button" aria-label="Zoom out" title="Zoom out (−)" onClick={() => zoom(1 / 1.25)}>−</button><button type="button" aria-label="Zoom in" title="Zoom in (+)" onClick={() => zoom(1.25)}>+</button>
      <button type="button" title="Fit (F)" onClick={() => fit()}>Fit (F)</button><button type="button" title="Fit all (Shift+F)" onClick={() => fit(true)}>Fit all (Shift+F)</button>
      {(['dimensions', 'names', 'constraints'] as const).map(key => <button type="button" key={key} aria-pressed={viewOptions[key]} onClick={() => toggle(key)}>{key[0].toUpperCase() + key.slice(1)}</button>)}
      <div className="sketch-menu-anchor"><button type="button" aria-label="Line styles" title="Line styles" aria-expanded={legend} onClick={() => setLegend(!legend)}>?</button>
        {legend && <div className="sketch-menu sketch-line-legend" role="dialog" aria-label="Line styles legend"><strong>Line styles</strong>{[
          ['under', 'Profile: under-constrained'], ['determined', 'Profile: fully constrained'], ['construction', 'Construction'], ['projected', 'Projected context'], ['conflicting', 'Conflict'], ['under moving', 'Moving patch (2 px)'], ['under rigid', 'Rigid patch (3.5 px)'],
        ].map(([style, label]) => <div key={label}><svg aria-hidden="true" viewBox="0 0 40 12"><path d="M0 6H40" className={`sketch-entity ${style}`}/></svg><span>{label}</span></div>)}</div>}
      </div>
    </div>
  </div>;
}
