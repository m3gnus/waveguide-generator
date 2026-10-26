import type { WorkspaceHistory } from '../workspace/historyAuthority';
import { Fragment, useEffect, useState } from 'react';
import { WorkspaceToolbar, NavigateTools, drawingIcons } from '../workspace/WorkspaceToolbar';
import type { SketchViewPreferences } from '../viewPreferences';
import type { constraintPalette, SelectionConstraint } from '../tools/constraints';

export type Tool = 'select' | 'hand' | 'line' | 'tangent' | 'arc3' | 'dimension' | 'split' | 'constraint';

type PaletteEntry = ReturnType<typeof constraintPalette>[number] & { disabled: boolean; title?: string };
interface Props {
  tool: Tool; arcTool: 'tangent' | 'arc3'; enabled: boolean; palette: PaletteEntry[]; activeConstraint?: SelectionConstraint;
  viewOptions: SketchViewPreferences; historyKind?: WorkspaceHistory['historyKind'];
  choose(tool: Tool): void; constrain(entry: PaletteEntry): void; toggle(key: keyof SketchViewPreferences): void;
  undo(redo?: boolean): void; zoom(factor: number): void; fit(all?: boolean): void;
}
export function SketchToolbar({ tool, arcTool, enabled, palette, activeConstraint, viewOptions, choose, constrain, toggle, undo, zoom, fit, historyKind }: Props) {
  const [arcMenu, setArcMenu] = useState(false);
  useEffect(() => { setArcMenu(false); }, [tool, arcTool]);
  const drawingButton = (t: 'line' | 'arc' | 'dimension' | 'split') => {
    const next = t === 'arc' ? arcTool : t;
    const label = { select: 'Select (Esc)', hand: 'Pan (hold Space)', line: 'Line (L)', arc: arcTool === 'tangent' ? 'Tangent arc (A)' : '3-point arc (A)', dimension: 'Dimension (D)', split: 'Split (S)' }[t];
    return <Fragment key={t}><button type="button" aria-label={label} title={label} aria-pressed={tool === next} disabled={!enabled} onClick={() => { setArcMenu(false); choose(next); }}><svg aria-hidden="true" viewBox="0 0 16 16"><path d={drawingIcons[t]}/></svg><span className="sketch-tool-label">{t === 'arc' ? 'Arc' : t[0].toUpperCase() + t.slice(1)}</span></button>{t === 'arc' && <div className="sketch-menu-anchor"><button type="button" aria-label="Arc options" aria-expanded={arcMenu} onClick={() => setArcMenu(!arcMenu)}>⌄</button>
      {arcMenu && <div className="sketch-menu" role="group" aria-label="Arc modes">{(['tangent', 'arc3'] as const).map(mode => <button type="button" key={mode} aria-pressed={arcTool === mode} onClick={() => { setArcMenu(false); choose(mode); }}>{mode === 'tangent' ? 'Tangent arc (A)' : '3-point arc (A)'}</button>)}</div>}
    </div>}</Fragment>;
  };
  return <WorkspaceToolbar tool={tool} enabled={enabled} historyKind={historyKind} viewOptions={viewOptions} toggle={toggle} undo={undo} zoom={zoom} fit={fit}
    onEscape={() => setArcMenu(false)} navigation={<NavigateTools tool={tool} choose={next => { setArcMenu(false); choose(next); }}/>}>
    <div className="sketch-toolbar-group" role="group" aria-label="Sketch"><span className="sketch-group-caption">Sketch</span><div className="sketch-tool-segment">{(['line', 'arc', 'dimension', 'split'] as const).map(drawingButton)}</div></div>
    <div className="sketch-toolbar-group" role="group" aria-label="Constraints"><span className="sketch-group-caption">Constraints</span><div className="sketch-tool-segment">{palette.map(entry => <button type="button" key={entry.kind} aria-label={entry.label} title={entry.disabled && entry.title ? entry.title : entry.key ? `${entry.label} (${entry.key.toUpperCase()})` : entry.label} aria-pressed={tool === 'constraint' && activeConstraint === entry.kind} disabled={entry.disabled} onClick={() => constrain(entry)}><span aria-hidden="true" className="sketch-constraint-icon">{entry.symbol}</span></button>)}</div></div>
  </WorkspaceToolbar>;
}
