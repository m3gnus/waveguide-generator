import { useId, useMemo, useState } from 'react';
import { useDesignStore, type CrossSectionStation, type DesignDocument, type DesignValue, type FreeformPoint } from '../stores/design';
import { ToggleRow } from './SolveOptionsSections';
import type { ParameterDefinition } from './parameterRegistry';
import { HelpTipHeading } from './HelpTip';
import { formatBound, NumberField } from './NumberField';
import { numericRowCells, parseNumericRow } from './decimalInput';

function numericCells(line: string, index: number): number[] {
  return parseNumericRow(line).map((cell) => {
    if (cell.kind !== 'number') throw new Error(`Line ${index + 1} contains a non-numeric value. ${cell.kind === 'error' ? cell.message : 'Enter a valid number.'}`);
    return cell.value;
  });
}

export interface ParsedPointPaste {
  points: FreeformPoint[];
  pointsByPlane?: { H: FreeformPoint[]; V: FreeformPoint[] };
  importedLength: number;
}

export function parsePointPaste(text: string): ParsedPointPaste {
  const rows = text.split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith('#'));
  if (rows.length < 1) throw new Error('Paste at least one point row.');
  const compact = rows[0].includes(';');
  const parsed = rows.map((line, index) => {
    const values = numericCells(line, index);
    if (compact && values.length !== 3) throw new Error(`Line ${index + 1} must contain z, H, and V.`);
    if (!compact && values.length === 4) throw new Error(`Line ${index + 1}: per-anchor strength was removed; tangent speed is now solved automatically.`);
    if (!compact && (values.length < 2 || values.length > 3)) throw new Error(`Line ${index + 1} must contain 2 or 3 columns.`);
    return values;
  });
  const validate = (points: { z: number; r: number; angle_deg?: number }[]) => {
    points.forEach((point, index) => {
      if (point.z < 0) throw new Error(`Line ${index + 1}: z must be at least 0.`);
      if (!(point.r > 0)) throw new Error(`Line ${index + 1}: radius must be greater than 0.`);
      if (point.angle_deg !== undefined && (point.angle_deg < -90 || point.angle_deg > 90)) throw new Error(`Line ${index + 1}: angle must be between −90 and 90°.`);
    });
    points.sort((left, right) => left.z - right.z);
    if (points.some((point, index) => index > 0 && point.z <= points[index - 1].z)) {
      throw new Error('Profile z values must be strictly increasing; duplicate positions are not allowed.');
    }
    if (points.length > 64) throw new Error('A profile supports at most 62 interior points.');
  };
  if (compact) {
    const H = parsed.map(([z, h]) => ({ z: z * 10, r: h * 10 }));
    const V = parsed.map(([z, _h, v]) => ({ z: z * 10, r: v * 10 }));
    validate(H); validate(V);
    if (H[0].z === 0 && H[0].r !== V[0].r) throw new Error('The throat must be one circle. Use the same throat radius in H and V at z = 0.');
    const importedLength = H.at(-1)!.z;
    if (!(importedLength > 0)) throw new Error('Imported length must be greater than 0.');
    const normalize = (points: typeof H): FreeformPoint[] => points.map(({ z, ...point }) => ({ t: z / importedLength, ...point }));
    const normalizedH = normalize(H); const normalizedV = normalize(V);
    return { points: normalizedH, pointsByPlane: { H: normalizedH, V: normalizedV }, importedLength };
  }
  const points = parsed.map(([z, r, angle]) => ({
    z, r,
    ...(angle === undefined ? {} : { angle_deg: angle }),
  }));
  validate(points);
  const importedLength = points.at(-1)!.z;
  if (!(importedLength > 0)) throw new Error('Imported length must be greater than 0.');
  return {
    points: points.map(({ z, ...point }) => ({ t: z / importedLength, ...point })),
    importedLength,
  };
}

const LOOKUP_ORDER_MESSAGE = 'lookupProfile z values must be strictly increasing';

/** A pasted or tabled radius at or below zero: the mesher accepts it, the geometry is rarely meant. */
export function lookupRadiusWarning(radii: readonly number[], lines?: readonly number[]): string | undefined {
  const bad = radii.map((radius, index) => (radius <= 0 ? (lines?.[index] ?? index + 1) : 0)).filter(Boolean);
  if (!bad.length) return undefined;
  const where = lines ? 'line' : 'point';
  return `${bad.length === 1 ? `Radius at ${where} ${bad[0]} is` : `Radii at ${where}s ${bad.join(', ')} are`} zero or negative; the mesher accepts it, but the horn will not be a physical surface.`;
}

/** LOOKUP preserves authored order and mm; it has no spline endpoints or angle column. */
export function parseLookupPaste(text: string): [number, number][] {
  return parseLookupPasteRows(text).pairs.map(({ z, r }): [number, number] => [z, r]);
}

/** Parsed rows keep the line they came from, so a warning can name it. */
export function parseLookupPasteRows(text: string): { pairs: { z: number; r: number; line: number }[] } {
  const rows = text.split(/\r?\n/)
    .map((raw, index) => ({ text: raw.trim(), line: index + 1 }))
    .filter((row) => row.text && !row.text.startsWith('#'));
  // A header is a first row with no numeric cell at all: "z,r", "z (mm), r (mm)".
  if (rows.length && numericRowCells(rows[0].text).cells.every((cell) => /[a-df-z]/i.test(cell))) rows.shift();
  if (rows.length < 2) throw new Error('lookupProfile must be an array of at least two [z, r] pairs');
  const pairs = rows.map((row) => {
    const values = numericCells(row.text, row.line - 1);
    if (values.length !== 2) throw new Error(`Line ${row.line} must contain exactly z and r in mm.`);
    return { z: values[0], r: values[1], line: row.line };
  });
  const unordered = pairs.findIndex(({ z }, index) => index > 0 && z <= pairs[index - 1].z);
  if (unordered > 0) throw new Error(`${LOOKUP_ORDER_MESSAGE} (line ${pairs[unordered].line})`);
  return { pairs };
}

export function EditableLookupTable({ field, points }: { field: ParameterDefinition; points: [number, number][] }) {
  const updateValue = useDesignStore((state) => state.updateValue);
  const beginDrag = useDesignStore((state) => state.beginDrag);
  const endDrag = useDesignStore((state) => state.endDrag);
  const [text, setText] = useState('');
  const parsed = useMemo(() => {
    try { return text.trim() ? parseLookupPasteRows(text).pairs : null; }
    catch (reason) { return reason instanceof Error ? reason : new Error(String(reason)); }
  }, [text]);
  const positionError = (index: number, z: number) =>
    (index > 0 && z <= points[index - 1][0]) || (index < points.length - 1 && z >= points[index + 1][0])
      ? LOOKUP_ORDER_MESSAGE : undefined;
  const write = (index: number, axis: 0 | 1, value: number) => {
    // A label scrub bypasses `validate`, so the order rule is enforced here too.
    if (axis === 0 && positionError(index, value)) return;
    const next = structuredClone(points);
    next[index][axis] = value;
    updateValue('lookup_profile', next);
  };
  const tableWarning = lookupRadiusWarning(points.map(([, r]) => r));
  const pasteWarning = parsed && !(parsed instanceof Error) ? lookupRadiusWarning(parsed.map(({ r }) => r), parsed.map(({ line }) => line)) : undefined;
  const add = () => {
    let gap = 0;
    for (let index = 1; index < points.length - 1; index += 1) {
      if (points[index + 1][0] - points[index][0] > points[gap + 1][0] - points[gap][0]) gap = index;
    }
    const left = points[gap]; const right = points[gap + 1];
    const pair: [number, number] = [(left[0] + right[0]) / 2, (left[1] + right[1]) / 2];
    updateValue('lookup_profile', [...points.slice(0, gap + 1), pair, ...points.slice(gap + 1)]);
  };
  return <div className="editable-parameter-table" role="group" aria-label={field.label}>
    <div className="readonly-table-head"><HelpTipHeading title={field.label} text={field.description}>{field.label}</HelpTipHeading><span>{points.length} points</span></div>
    <table><thead><tr><th>z mm</th><th>r mm</th><th /></tr></thead><tbody>
      {points.map(([z, r], index) => <tr key={index}>
        <td><NumberField label={`Lookup point ${index + 1} z`} value={z} step={.1} precision={4} unit="mm"
          min={index > 0 ? points[index - 1][0] + 1e-4 : -Infinity} max={index < points.length - 1 ? points[index + 1][0] - 1e-4 : Infinity}
          formatRangeError={() => LOOKUP_ORDER_MESSAGE}
          validate={(value) => positionError(index, value)} onBeginDrag={beginDrag} onEndDrag={endDrag} onCommit={(value) => write(index, 0, value)} /></td>
        <td><NumberField label={`Lookup point ${index + 1} radius`} value={r} step={.1} precision={4} unit="mm" onBeginDrag={beginDrag} onEndDrag={endDrag} onCommit={(value) => write(index, 1, value)} /></td>
        <td><button type="button" aria-label={`Remove lookup point ${index + 1}`} disabled={points.length <= 2} onClick={() => updateValue('lookup_profile', points.filter((_point, item) => item !== index))}>−</button></td>
      </tr>)}
    </tbody></table>
    {tableWarning && <div className="field-warning" role="status">{tableWarning}</div>}
    <button type="button" onClick={add} disabled={points.length < 2}>Add point</button>
    <div className="point-paste">
      <textarea aria-label="Paste lookup points" rows={4} value={text} onChange={(event) => setText(event.target.value)} placeholder={'z_mm,r_mm\n0,12.7\n120,140'} />
      {parsed instanceof Error && <div className="field-error" role="alert">{parsed.message}</div>}
      {pasteWarning && <div className="field-warning" role="status">{pasteWarning}</div>}
      <button type="button" disabled={!parsed || parsed instanceof Error} onClick={() => {
        if (parsed && !(parsed instanceof Error)) updateValue('lookup_profile', parsed.map(({ z, r }): [number, number] => [z, r]));
      }}>Import points</button>
    </div>
  </div>;
}

export function normalizedImportedPoints(imported: FreeformPoint[], current: FreeformPoint[]): FreeformPoint[] {
  const sorted = structuredClone(imported).sort((a, b) => a.t - b.t);
  const throat = sorted[0]?.t === 0 ? sorted.shift()! : structuredClone(current[0]);
  const mouth = sorted.at(-1)?.t === 1 ? sorted.pop()! : structuredClone(current.at(-1)!);
  const interior = sorted.filter((point) => point.t > 0 && point.t < 1).slice(0, 62);
  return [{ ...throat, t: 0 }, ...interior, { ...mouth, t: 1 }];
}

export const FREEFORM_POSITION_NOTE = 'Point and station positions are measured from the throat along Length, before Scale. Changing Length stretches them proportionally.';
export const FREEFORM_IMPORT_HINT = 'One point per line in mm: z r [angle °]. Use spaces or commas. Lines starting with # are ignored. A row at z = 0 sets the throat radius; the last row sets the mouth radius and the length. Two planes at once: z;H;V in centimetres.';

export function freeformAngleOverride(field: ParameterDefinition, design: DesignDocument): string | undefined {
  if (design.formula !== 'FREEFORM') return undefined;
  if (field.id === 'freeform.throatAngle' && [design.profile_h, design.profile_v].some((profile) => profile?.points[0]?.angle_deg !== undefined)) {
    return 'Overridden by the angle on the imported throat point.';
  }
  const plane = field.id === 'freeform.mouthAngleH' ? design.profile_h : field.id === 'freeform.mouthAngleV' ? design.profile_v : undefined;
  return plane?.points.at(-1)?.angle_deg !== undefined ? 'Overridden by the angle on the imported mouth point.' : undefined;
}

export function FreeformPointImport() {
  const updateValues = useDesignStore((state) => state.updateValues);
  const horizontal = useDesignStore((state) => state.design.profile_h!.points);
  const vertical = useDesignStore((state) => state.design.profile_v!.points);
  const currentLength = useDesignStore((state) => state.design.length ?? 120);
  const id = useId();
  const [text, setText] = useState('');
  const [target, setTarget] = useState<'H' | 'V' | 'both'>('H');
  const [adoptLength, setAdoptLength] = useState(true);
  const [notice, setNotice] = useState('');
  const parsed = useMemo(() => {
    try { return text.trim() ? parsePointPaste(text) : null; } catch (reason) { return reason instanceof Error ? reason : new Error(String(reason)); }
  }, [text]);
  const valid = parsed && !(parsed instanceof Error) ? parsed : null;
  const bothPlanes = Boolean(valid?.pointsByPlane);
  const effectiveTarget = bothPlanes ? 'both' : target;
  const targetLabel = effectiveTarget === 'both' ? 'both profiles' : effectiveTarget === 'H' ? 'the horizontal profile' : 'the vertical profile';
  const targetLength = valid && adoptLength ? valid.importedLength : currentLength;
  const error = parsed instanceof Error ? parsed.message : valid && (targetLength < 20 || targetLength > 1_000) ? 'Imported length must be between 20 and 1000 mm.' : undefined;
  const disabledReason = error ?? (!valid ? 'Paste valid points to import.' : undefined);
  const apply = () => {
    if (!valid || disabledReason) return;
    const H = effectiveTarget === 'V' ? structuredClone(horizontal) : normalizedImportedPoints(valid.pointsByPlane?.H ?? valid.points, horizontal);
    const V = effectiveTarget === 'H' ? structuredClone(vertical) : normalizedImportedPoints(valid.pointsByPlane?.V ?? valid.points, vertical);
    // Every compression driver has one circular throat, even when only one
    // profile is replaced. Preserve all other points in the untargeted plane.
    const throatRadius = effectiveTarget === 'V' ? V[0].r : H[0].r;
    H[0].r = throatRadius;
    V[0].r = throatRadius;
    const updates: Record<string, DesignValue> = { 'profile_h.points': H, 'profile_v.points': V };
    if (adoptLength) updates.length = valid.importedLength;
    updateValues(updates);
    setText('');
    setNotice(`Imported ${valid.points.length} points into ${targetLabel}. Undo restores the previous profile.`);
  };
  return <div className="point-paste" role="group" aria-label="Import profile points" data-control-reveal-id="control.freeformImport">
    <textarea aria-label="Paste profile points" aria-describedby={`${id}-hint`} rows={4} value={text} onChange={(event) => { setText(event.target.value); setNotice(''); }} placeholder={'z r [angle]\n0 12.7\n120 200'} />
    <p className="section-note" id={`${id}-hint`}>{FREEFORM_IMPORT_HINT}</p>
    <fieldset className="point-paste-choices">
      <legend>Import into:</legend>
      {([['H', 'Horizontal'], ['V', 'Vertical'], ['both', 'Both']] as const).map(([value, label]) => <label className="ui-check" key={value}>
        <input type="radio" name={`${id}-target`} value={value} checked={effectiveTarget === value} disabled={bothPlanes} onChange={() => { setTarget(value); setNotice(''); }} />{label}
      </label>)}
    </fieldset>
    {bothPlanes && <p className="section-note">This z;H;V paste contains both planes, so it imports into Both.</p>}
    {valid && <div className="paste-meta">{valid.points.length} points · pasted length {valid.importedLength} mm · current {currentLength} mm</div>}
    {valid && valid.importedLength !== currentLength && <fieldset className="point-paste-choices">
      <legend>Length</legend>
      <label className="ui-check"><input type="radio" name={`${id}-length`} checked={adoptLength} onChange={() => setAdoptLength(true)} />Use pasted length ({valid.importedLength} mm)</label>
      <label className="ui-check"><input type="radio" name={`${id}-length`} checked={!adoptLength} onChange={() => setAdoptLength(false)} />Keep {currentLength} mm and stretch the pasted points to fit</label>
    </fieldset>}
    {disabledReason && <div id={`${id}-reason`} className={error ? 'field-error' : 'disabled-reason'} role={error ? 'alert' : undefined}>{disabledReason}</div>}
    <button type="button" disabled={Boolean(disabledReason)} aria-describedby={disabledReason ? `${id}-reason` : undefined} onClick={apply}>Import into {targetLabel}</button>
    {notice && <p className="section-note" role="status">{notice}</p>}
  </div>;
}

export function EditablePointTable({ field, points }: { field: ParameterDefinition; points: FreeformPoint[] }) {
  const updateValue = useDesignStore((state) => state.updateValue);
  const length = useDesignStore((state) => state.design.length ?? 120);
  const plane = field.id.endsWith('H') ? 'H' : 'V';
  const path = `profile_${plane.toLowerCase()}.points`;
  const interior = points.slice(1, -1);
  const beginDrag = useDesignStore((state) => state.beginDrag);
  const endDrag = useDesignStore((state) => state.endDrag);
  const [showDistance, setShowDistance] = useState(false);
  const positionMessage = (index: number, z: number) => {
    const t = z / length;
    if (points.some((point, item) => item !== index + 1 && Math.abs(point.t - t) < 1e-9)) return 'Position must not match another point.';
    if (z < 1 || z > length - 1 || t <= points[index].t || t >= points[index + 2].t) return 'Position must stay between the previous and next points (no crossing).';
    return undefined;
  };
  const update = (index: number, key: 'z' | 'r' | 'angle_deg', value: number | undefined) => {
    const next = structuredClone(points);
    const target = next[index + 1];
    if (value === undefined) delete target.angle_deg;
    else if (key === 'z') {
      if (positionMessage(index, value)) return;
      target.t = value / length;
    }
    else {
      if ((key === 'r' && value <= 0) || (key === 'angle_deg' && (value <= -90 || value >= 90))) return;
      target[key] = value as never;
    }
    updateValue(path, next);
  };
  const add = () => {
    if (interior.length >= 62) return;
    let gapIndex = 0;
    for (let index = 1; index < points.length - 1; index += 1) {
      if (points[index + 1].t - points[index].t > points[gapIndex + 1].t - points[gapIndex].t) gapIndex = index;
    }
    const left = points[gapIndex]; const right = points[gapIndex + 1];
    const t = (left.t + right.t) / 2;
    const r = left.r + ((t - left.t) / (right.t - left.t || 1)) * (right.r - left.r);
    updateValue(path, [...points.slice(0, gapIndex + 1), { t, r }, ...points.slice(gapIndex + 1)]);
  };
  return <div className="editable-parameter-table" role="group" aria-label={field.label}>
    <div className="readonly-table-head"><HelpTipHeading title={field.label} text={field.description}>{field.label}</HelpTipHeading><span>{interior.length} points</span></div>
    <ToggleRow id={`freeform-spacing-${plane}`} label="Show spacing column" help="Distance from the previous point along Length, before Scale." checked={showDistance} onChange={setShowDistance} />
    {interior.length === 0 && <p className="section-note">No spline points. The curve runs from throat to mouth, shaped by the throat and mouth angles. Add point inserts one in the largest gap.</p>}
    <table><thead><tr><th>z (mm)</th>{showDistance && <th>Spacing (mm)</th>}<th>r (mm)</th><th>Angle (°)</th><th /></tr></thead>
      <tbody>{interior.map((point, index) => <tr key={index}>
        <td><NumberField label={`${plane} point ${index + 1} z`} value={positionMm(point.t, length)} min={1} max={length - 1} step={.1} precision={4} unit="mm" validate={(value) => positionMessage(index, value)} onBeginDrag={beginDrag} onEndDrag={endDrag} onCommit={(value) => update(index, 'z', value)} /></td>
        {showDistance && <td><NumberField label={`${plane} point ${index + 1} distance from previous`} value={positionMm(point.t - points[index].t, length)} step={.1} precision={4} unit="mm"
          validate={(value) => positionMessage(index, positionMm(points[index].t, length) + value)}
          onBeginDrag={beginDrag} onEndDrag={endDrag} onCommit={(value) => update(index, 'z', positionMm(points[index].t, length) + value)} /></td>}
        <td><NumberField label={`${plane} point ${index + 1} radius`} value={point.r} min={0} step={.1} precision={4} unit="mm" validate={(value) => value <= 0 ? 'Must be greater than 0 mm.' : undefined} onBeginDrag={beginDrag} onEndDrag={endDrag} onCommit={(value) => update(index, 'r', value)} /></td>
        <td><NumberField label={`${plane} point ${index + 1} angle`} value={point.angle_deg} min={-90} max={90} step={.1} precision={2} unit="°" optional placeholder="auto" validate={(value) => value <= -90 || value >= 90 ? 'Must be greater than −90 and less than 90°.' : undefined} onClear={() => update(index, 'angle_deg', undefined)} onBeginDrag={beginDrag} onEndDrag={endDrag} onCommit={(value) => update(index, 'angle_deg', value)} /></td>
        <td><button type="button" aria-label={`Remove ${plane} point ${index + 1}`} onClick={() => updateValue(path, points.filter((_point, pointIndex) => pointIndex !== index + 1))}>−</button></td>
      </tr>)}</tbody>
    </table>
    <button type="button" disabled={interior.length >= 62} onClick={add}>Add point</button>
    {interior.length >= 62 && <p className="disabled-reason">A profile supports at most 62 spline points. Remove a point before adding another.</p>}
  </div>;
}

/**
 * The exponent the mesher uses for a superellipse station that omits one
 * (`hornlab_mesher/freeform.py`, `_normalise_stations`): an ellipse.
 */
export const MESHER_SUPERELLIPSE_EXPONENT = 2;
/** Written when a station becomes a superellipse, so it visibly squares off. */
export const NEW_SUPERELLIPSE_EXPONENT = 4;
/** Written when a station becomes a rounded rectangle; the mesher has no default. */
export const NEW_STATION_CORNER_RADIUS_MM = 10;

/**
 * Switch a station to another shape, storing the parameter the new shape needs
 * and dropping the one it does not.
 *
 * The station field shows these values, so they have to be in the design: the
 * mesher requires `cornerRadiusMm` on every rounded rectangle, and rejects an
 * exponent or a corner radius on a shape that does not take it.
 */
export function withStationShape(station: CrossSectionStation, shape: CrossSectionStation['shape']): CrossSectionStation {
  const { exponent, corner_radius_mm: cornerRadius, ...rest } = station;
  if (shape === 'superellipse') return { ...rest, shape, exponent: exponent ?? NEW_SUPERELLIPSE_EXPONENT };
  if (shape === 'rounded_rectangle') return { ...rest, shape, corner_radius_mm: cornerRadius ?? NEW_STATION_CORNER_RADIUS_MM };
  return { ...rest, shape };
}

/** Authored axial positions use the same normalized length axis for points and stations. */
function positionMm(t: number, span: number): number { return t * span; }

/** Canonicalize only an edited ratio, removing unit-conversion roundoff. */
export function stationPosition(value: number, span: number): number {
  return Number((value / span).toPrecision(15));
}

export function EditableStationTable({ field, stations }: { field: ParameterDefinition; stations: CrossSectionStation[] }) {
  const updateValue = useDesignStore((state) => state.updateValue);
  const length = useDesignStore((state) => state.design.length ?? 120);
  const beginDrag = useDesignStore((state) => state.beginDrag);
  const endDrag = useDesignStore((state) => state.endDrag);
  const write = (index: number, replacement: CrossSectionStation) => {
    const next = stations.map((station, item) => item === index ? replacement : station).sort((a, b) => a.t - b.t);
    updateValue('cross_sections', next);
  };
  const update = (index: number, patch: Partial<CrossSectionStation>) => {
    if (patch.t !== undefined && positionMessage(index, patch.t)) return;
    write(index, { ...stations[index], ...patch });
  };
  const positionMessage = (index: number, value: number) => {
    if (value <= 0 || value >= 1) return 'Position must be between the throat and mouth.';
    if (stations.some((station, item) => item !== index && Math.abs(station.t - value) < 1e-9)) return 'Position must not match another station.';
    if (value <= stations[index - 1]?.t || value >= stations[index + 1]?.t) return 'Position must stay between the previous and next stations (no crossing).';
    return undefined;
  };
  const add = () => {
    if (stations.length >= 32) return;
    let gap = 0;
    for (let index = 1; index < stations.length - 1; index += 1) if (stations[index + 1].t - stations[index].t > stations[gap + 1].t - stations[gap].t) gap = index;
    const station = { t: (stations[gap].t + stations[gap + 1].t) / 2, shape: 'ellipse' as const };
    updateValue('cross_sections', [...stations, station].sort((a, b) => a.t - b.t));
  };
  return <div className="editable-parameter-table" role="group" aria-label={field.label}>
    <div className="readonly-table-head"><HelpTipHeading title={field.label} text={field.description}>{field.label}</HelpTipHeading><span>{stations.length} / 32 stations</span></div>
    <table><thead><tr><th>mm from throat / %</th><th>shape</th><th>n / radius</th><th /></tr></thead><tbody>
      {stations.map((station, index) => <tr key={index}>
        <td><NumberField label={`Station ${index + 1} position`} value={positionMm(station.t, length)} min={0} max={length} step={.1} precision={15} displayPrecision={4} unit="mm" disabled={index === 0 || index === stations.length - 1}
          validate={index === 0 || index === stations.length - 1 ? undefined : (value) => positionMessage(index, stationPosition(value, length))} onBeginDrag={beginDrag} onEndDrag={endDrag}
          onCommit={(value) => update(index, { t: stationPosition(value, length) })} />
          <NumberField label={`Station ${index + 1} position percent`} value={positionMm(station.t, 100)} min={0} max={100} step={.1} precision={15} displayPrecision={4} unit="%" disabled={index === 0 || index === stations.length - 1}
            validate={index === 0 || index === stations.length - 1 ? undefined : (value) => positionMessage(index, stationPosition(value, 100))} onBeginDrag={beginDrag} onEndDrag={endDrag}
            onCommit={(value) => update(index, { t: stationPosition(value, 100) })} />
          <span className="station-position-readout">{formatBound(positionMm(station.t, length), 4)} mm from the throat — {formatBound(positionMm(station.t, 100), 4)}%</span>
        </td>
        <td><select aria-label={`Station ${index + 1} shape`} value={station.shape} onChange={(event) => write(index, withStationShape(station, event.target.value as CrossSectionStation['shape']))}><option value="circle">Circle (local H/V radii)</option><option value="ellipse">Ellipse</option>{index > 0 && <><option value="superellipse">Superellipse</option><option value="rounded_rectangle">Rounded rectangle</option></>}</select></td>
        {/* A value missing from a loaded file shows what the mesher will use: its
            default exponent, or an empty required corner radius, which it has no
            default for and rejects the station without. */}
        <td>{station.shape === 'superellipse' ? <NumberField label={`Station ${index + 1} exponent`} value={station.exponent ?? MESHER_SUPERELLIPSE_EXPONENT} min={2} max={16} step={.1} precision={2} onBeginDrag={beginDrag} onEndDrag={endDrag} onCommit={(value) => update(index, { exponent: value })} /> : station.shape === 'rounded_rectangle' ? <NumberField label={`Station ${index + 1} corner radius`} value={station.corner_radius_mm} min={1} step={1} precision={2} unit="mm" onBeginDrag={beginDrag} onEndDrag={endDrag} onCommit={(value) => update(index, { corner_radius_mm: value })} /> : '—'}</td>
        <td><button type="button" aria-label={`Remove station ${index + 1}`} disabled={stations.length <= 2 || index === 0 || index === stations.length - 1} onClick={() => updateValue('cross_sections', stations.filter((_station, item) => item !== index))}>−</button></td>
      </tr>)}
    </tbody></table>
    <button type="button" disabled={stations.length >= 32} onClick={add}>Add station</button>
  </div>;
}
