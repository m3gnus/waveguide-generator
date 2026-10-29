import { Fragment, useEffect, useId, useMemo, useState } from 'react';
import { useDesignStore, type CrossSectionStation, type DesignDocument, type FreeformPoint } from '../stores/design';
import type { CurveAnchor, CurveDeviation, InsertMode } from '../api/freeformProfile';
import type { GeneratedPlaneChange } from '../api/profileSamples';
import { Notice } from '../shell/Notice';
import {
  activePlanes, angleField, deleteAnchor, formatAngle, formatMm, horizontalThroatForVertical, insertAnchorBuilder,
  isSharedProfile, isUnseparatedVertical, MAX_FREEFORM_ANCHORS, moveMouthBuilder, previewAcceptable, separateVerticalBlocker, setAnchorAngle, setAnchorJoin,
  pasteBuilder, setAnchorValue, setSeparateVertical, stretchLengthBuilder, useCurrentProfileCurves, useProfileEdit, type Plane,
} from './customProfileEdit';
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
  /** Every row is a plain `z, r` pair, as a LOOKUP table pastes. */
  lookupStyle?: boolean;
}

/** A header is a first row with no numeric cell at all: "z,r", "z (mm), r (mm)". */
function isHeaderRow(line: string): boolean {
  return numericRowCells(line).cells.every((cell) => /[a-df-z]/i.test(cell));
}

export function parsePointPaste(text: string): ParsedPointPaste {
  const rows = text.split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith('#'));
  if (rows.length && isHeaderRow(rows[0])) rows.shift();
  if (rows.length < 1) throw new Error('Paste at least one point row.');
  const compact = rows[0].includes(';');
  const parsed = rows.map((line, index) => {
    const values = numericCells(line, index);
    if (compact && values.length !== 3) throw new Error(`Line ${index + 1} must contain z, H, and V. Rows with semicolons are read as z;H;V in centimetres; for z and r in mm, separate them with a comma, tab or space.`);
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
    if (points.length > 64) throw new Error('A Custom profile supports at most 64 anchors including the throat and mouth. Larger straight-line tables stay in legacy LOOKUP designs.');
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
    lookupStyle: parsed.every((values) => values.length === 2),
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
  if (rows.length && isHeaderRow(rows[0].text)) rows.shift();
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

export { normalizedImportedPoints } from './customProfileEdit';

export const FREEFORM_POSITION_NOTE = 'Anchor and station positions are measured from the throat along Length, in mm before Scale. Changing Length asks whether to stretch the profile or move only the mouth.';
export const CUSTOM_PROFILE_TABLE_HELP = 'Every anchor, throat and mouth included, at z mm from the throat before Scale. A smooth anchor has one tangent (automatic unless an angle or handle pins it); a corner may take different incoming and outgoing angles, and two neighbouring corners without facing angles join with a straight span. Each edit is previewed with its curve deviation and generated-profile change before it applies.';
export const FREEFORM_IMPORT_HINT = 'One anchor per line in mm: z r [angle °], with commas, tabs or spaces; lookup z,r tables paste as they are. Lines starting with # and a header row are ignored. A row at z = 0 sets the throat radius; the last row sets the mouth radius and the length. Both planes at once: z;H;V in centimetres.';

/** Why a profile-level endpoint angle is ignored in one plane (precedence: handle, point angle, corner chord). */
export function endpointAngleOverride(design: DesignDocument, plane: Plane, end: 'throat' | 'mouth'): string | undefined {
  const points = (plane === 'H' ? design.profile_h : design.profile_v)?.points;
  const point = end === 'throat' ? points?.[0] : points?.at(-1);
  if (!point) return undefined;
  if ((end === 'throat' ? point.handle_out : point.handle_in) != null) return `the explicit ${end} handle in ${plane}`;
  if (point.angle_deg != null) return `the angle on the ${plane} ${end} anchor`;
  if (point.join === 'corner') return `the chord from the ${plane} ${end} corner`;
  return undefined;
}

function endpointOverrides(field: ParameterDefinition, design: DesignDocument): { planes: Plane[]; reasons: string[] } | undefined {
  if (design.formula !== 'FREEFORM') return undefined;
  const target = field.id === 'freeform.throatAngle' ? { end: 'throat' as const, planes: activePlanes(design) }
    : field.id === 'freeform.mouthAngleH' ? { end: 'mouth' as const, planes: ['H' as const] }
      : field.id === 'freeform.mouthAngleV' ? { end: 'mouth' as const, planes: ['V' as const] } : undefined;
  if (!target) return undefined;
  const reasons = target.planes.map((plane) => endpointAngleOverride(design, plane, target.end));
  return { planes: target.planes, reasons: reasons.filter((reason): reason is string => Boolean(reason)) };
}

/** A note when an endpoint angle is ignored in some, but not all, of its planes. */
export function freeformAngleOverride(field: ParameterDefinition, design: DesignDocument): string | undefined {
  const overrides = endpointOverrides(field, design);
  if (!overrides?.reasons.length || overrides.reasons.length === overrides.planes.length) return undefined;
  return `Ignored where ${overrides.reasons.join(' and ')} sets the direction instead.`;
}

/** Disable an endpoint angle every plane ignores; its stored value is kept. */
export function freeformAngleDisabledReason(field: ParameterDefinition, design: DesignDocument): string | undefined {
  const overrides = endpointOverrides(field, design);
  if (!overrides?.reasons.length || overrides.reasons.length !== overrides.planes.length) return undefined;
  return `Not used: ${overrides.reasons.join(' and ')} sets this direction (explicit handle, then point angle, then corner chord, then this value). The stored value is kept.`;
}

type ProfileEdit = ReturnType<typeof useProfileEdit>;

function formatDeviation(value: number | null | undefined): string {
  return value == null ? '—' : value === 0 ? '0' : Math.abs(value) < 1e-6 ? value.toExponential(2) : formatMm(Number(value.toPrecision(6)));
}

function curveLine(plane: string, deviation: CurveDeviation): string {
  const { extents } = deviation;
  if (extents.comparison_domain === 'no_overlap') return `${plane}: no common z range (before ${extents.before.map(formatMm).join('–')} mm, after ${extents.after.map(formatMm).join('–')} mm); nothing to compare.`;
  const where = extents.comparison_domain === 'overlap_only'
    ? ` over the overlap z = ${extents.overlap!.map(formatMm).join('–')} mm only (extents differ; not a full-domain bound)`
    : ' over the full profile';
  return `${plane}: curve deviation ${formatDeviation(deviation.curve_deviation_mm)} mm at z = ${formatDeviation(deviation.curve_at_z_mm)} mm${where}; production evaluator ${formatDeviation(deviation.evaluator_deviation_mm)} mm at z = ${formatDeviation(deviation.evaluator_at_z_mm)} mm. Sampled estimate over ${deviation.sample_count} z values, not a certified maximum.`;
}

function generatedLine(plane: string, change: GeneratedPlaneChange): string {
  if (change.domain === 'unavailable') return `${plane}: unavailable — ${change.reason}`;
  if (change.domain === 'no_overlap') return `${plane}: no common z range in the generated profiles; nothing to compare.`;
  const where = change.domain === 'overlap_only'
    ? ` Over the overlap only (before ${change.extents.before.map(formatMm).join('–')} mm, after ${change.extents.after.map(formatMm).join('–')} mm).`
    : '';
  const shared = change.maxChangeMm === null
    ? 'no exported z row is shared by both profiles'
    : `largest radius change ${formatDeviation(change.maxChangeMm)} mm at z = ${formatDeviation(change.atZMm)} mm over the ${change.sampleCount} z rows both exports share`;
  return `${plane}: ${shared}; between rows ${formatDeviation(change.interpolatedMaxChangeMm)} mm at z = ${formatDeviation(change.interpolatedAtZMm)} mm (linear between ${change.interpolatedSampleCount} exported rows, so it includes rows added or removed by the edit).${where}`;
}

/** The before/after feedback of one proposed edit, with Accept and Cancel. */
export function ProfileEditPreviewPanel({ edit }: { edit: ProfileEdit }) {
  const { preview, notice } = edit;
  if (!preview) {
    return notice ? <Notice className="profile-edit-notice" actions={<button type="button" className="btn" onClick={edit.dismissNotice}>Dismiss</button>}>{notice}</Notice> : null;
  }
  if (preview.status !== 'ready') {
    return <Notice tone={preview.status === 'refused' ? 'error' : 'info'} title={preview.label} className="profile-edit-preview"
      actions={<button type="button" className="btn" onClick={edit.cancel}>{preview.status === 'refused' ? 'Close' : 'Cancel'}</button>}>
      {preview.status === 'refused' ? `Not applied: ${preview.message}` : 'Preparing the edit…'}
    </Notice>;
  }
  const after = preview.after!;
  const planes = activePlanes(after);
  const { curve, generated } = preview;
  const shared = isSharedProfile(after);
  const scale = after.scale;
  const blocked = preview.validation.status === 'error' ? 'The edited profile is not valid, so the current design is kept.'
    : preview.validation.status === 'pending' || preview.curve.status === 'pending' || preview.generated.status === 'pending' ? 'Waiting for the curve and generated-profile measurements.'
      : preview.curve.status === 'error' || preview.generated.status === 'error' ? 'A required measurement is unavailable, so the edit cannot be accepted yet.' : undefined;
  return <Notice tone={preview.validation.status === 'error' ? 'error' : 'info'} title={`Preview: ${preview.label}`} className="profile-edit-preview"
    actions={<>
      <button type="button" className="btn" disabled={!previewAcceptable(preview)} title={blocked} onClick={edit.accept}>Accept</button>
      <button type="button" className="btn" onClick={edit.cancel}>Cancel</button>
    </>}>
    <ul className="profile-edit-notes">{preview.notes.map((note) => <li key={note}>{note}</li>)}</ul>
    {preview.validation.status === 'error' && <p className="field-error" role="alert">Not valid: {preview.validation.message}</p>}
    {preview.validation.status === 'ready' && preview.validation.value.warnings.map((warning) => <p key={warning} className="field-warning">{warning}</p>)}
    <div className="profile-edit-measure" role="group" aria-label="Curve deviation">
      <b>Curve deviation</b> <span>(mesher comparison of the authored curves, design mm before Scale)</span>
      {curve.status === 'pending' && <p>Measuring…</p>}
      {curve.status === 'error' && <p className="field-error">Unavailable: {curve.message}</p>}
      {curve.status === 'ready' && planes.map((plane) => <p key={plane}>{curveLine(shared ? 'H (both planes)' : plane, curve.value[plane])}</p>)}
    </div>
    <div className="profile-edit-measure" role="group" aria-label="Generated profile change">
      <b>Generated profile change</b> <span>(profile export, physical mm with Scale = {scale}; z = design z × Scale; H and V meridians only, not the whole wall, mouth or source cap)</span>
      {generated.status === 'pending' && <p>Measuring…</p>}
      {generated.status === 'error' && <p className="field-error">Unavailable: {generated.message}. No generated-geometry identity is claimed.</p>}
      {generated.status === 'ready' && (['H', 'V'] as const).map((plane) => <p key={plane}>{generatedLine(plane, generated.value[plane])}</p>)}
    </div>
    {blocked && <p className="disabled-reason">{blocked}</p>}
  </Notice>;
}

/**
 * Separate vertical profile: on reveals V; off keeps V stored and inactive.
 * The first separation of a new design starts V from H (no geometry change);
 * a different stored V is restored only by explicit choice.
 */
function SeparateVerticalControl({ edit }: { edit: ProfileEdit }) {
  const design = useDesignStore((state) => state.design);
  const [choosing, setChoosing] = useState(false);
  const shared = isSharedProfile(design);
  const blocker = separateVerticalBlocker(design);
  const id = useId();
  const seed = () => { setChoosing(false); edit.propose('Separate the vertical profile, starting from the horizontal profile', (current) => setSeparateVertical(current, true, true)); };
  return <div className="profile-separate-vertical">
    <ToggleRow id={`${id}-separate`} label="Separate vertical profile" checked={!shared}
      help="Off: the horizontal table shapes both planes and the vertical profile is kept, inactive. On: the vertical plane uses its own table, started from the horizontal profile the first time."
      onChange={(checked) => {
        setChoosing(false);
        if (!checked) edit.propose('Use one profile for both planes', (current) => setSeparateVertical(current, false));
        else if (isUnseparatedVertical(design)) seed();
        else if (!blocker && JSON.stringify(design.profile_h) === JSON.stringify(design.profile_v)) edit.propose('Separate the vertical profile', (current) => setSeparateVertical(current, true));
        else setChoosing(true);
      }} />
    {shared && choosing && <div className="family-switch-choice" role="group" aria-label="Separate the vertical profile">
      <span>A different vertical profile is stored with this design.</span>
      <div>
        <button type="button" disabled={Boolean(blocker)} title={blocker} onClick={() => { setChoosing(false); edit.propose('Restore the stored vertical profile', (current) => setSeparateVertical(current, true)); }}>Restore stored vertical profile</button>
        <button type="button" onClick={seed}>Start from horizontal</button>
        <button type="button" onClick={() => setChoosing(false)}>Cancel</button>
      </div>
      {blocker && <p className="field-warning" role="status">{blocker}{' '}
        <button type="button" className="btn" onClick={() => { setChoosing(false); edit.propose('Use H throat for V', horizontalThroatForVertical); }}>Use H throat for V</button>
      </p>}
    </div>}
  </div>;
}

function anchorState(point: FreeformPoint, index: number, count: number, anchor: CurveAnchor | undefined, span: { straight: boolean } | undefined): string {
  const parts: string[] = [];
  const pins = [point.handle_in != null ? 'in' : null, point.handle_out != null ? 'out' : null].filter(Boolean);
  if (pins.length) parts.push(`pinned ${pins.join('+')}`);
  if (index === 0 || index === count - 1) {
    const end = index === 0 ? 'throat' : 'mouth';
    const handle = index === 0 ? point.handle_out : point.handle_in;
    parts.push(handle != null ? 'angle from handle' : point.angle_deg != null ? 'angle from point' : point.join === 'corner' ? 'angle from chord' : `profile ${end} angle`);
  } else if (!pins.length && point.angle_deg == null && point.join !== 'corner') parts.push('auto tangent');
  if (anchor) {
    const angle = index === 0 ? anchor.angleOutDeg : anchor.angleInDeg;
    if (angle != null) parts.push(formatAngle(Number(angle.toFixed(2))));
  }
  if (span) parts.push(span.straight ? 'straight →' : 'curved →');
  return parts.join(' · ');
}

/** The active Custom profile table for one plane: every anchor, including throat and mouth. */
export function CustomProfileTable({ plane }: { field: ParameterDefinition; plane: Plane }) {
  const design = useDesignStore((state) => state.design);
  const points = (plane === 'H' ? design.profile_h : design.profile_v)?.points ?? [];
  const length = design.length ?? 120;
  const shared = isSharedProfile(design);
  const { curves, error, stale } = useCurrentProfileCurves();
  const edit = useProfileEdit();
  const id = useId();
  const [insertZ, setInsertZ] = useState<number | undefined>();
  const [insertMode, setInsertMode] = useState<InsertMode>('exact');
  const plan = curves?.[plane];
  const aligned = plan && plan.anchors.length === points.length ? plan : undefined;
  const label = (index: number) => index === 0 ? 'throat' : index === points.length - 1 ? 'mouth' : `point ${index}`;
  const check = (build: () => unknown) => { try { build(); return undefined; } catch (reason) { return reason instanceof Error ? reason.message : String(reason); } };
  const title = shared && plane === 'H' ? 'Profile anchors (both planes)' : `${plane === 'H' ? 'Horizontal' : 'Vertical'} profile anchors`;
  const insertReason = insertZ === undefined ? 'Enter the z position to insert at.'
    : !(insertZ > 0 && insertZ < length) ? `Insert z must be strictly between 0 and ${formatMm(length)} mm.`
      : points.length >= MAX_FREEFORM_ANCHORS ? `A Custom profile supports at most ${MAX_FREEFORM_ANCHORS} anchors, including the throat and mouth.`
        : points.some((point) => point.t * length === insertZ) ? 'An anchor is already at this z.' : undefined;
  return <div className="editable-parameter-table custom-profile-table" role="group" aria-label={title} data-control-reveal-id={`control.customProfile${plane}`}>
    <div className="readonly-table-head"><HelpTipHeading title={title} text={CUSTOM_PROFILE_TABLE_HELP}>{title}</HelpTipHeading><span>{points.length} / {MAX_FREEFORM_ANCHORS} anchors</span></div>
    {plane === 'H' && <SeparateVerticalControl edit={edit} />}
    <div className="custom-profile-scroll">
      <table><thead><tr><th>z (mm)</th><th>r (mm)</th><th>Join</th><th>In (°)</th><th>Out (°)</th><th /></tr></thead>
        <tbody>{points.map((point, index) => {
          const endpoint = index === 0 || index === points.length - 1;
          const inField = angleField(point, index, points.length, 'in');
          const outField = angleField(point, index, points.length, 'out');
          const anchor = aligned?.anchors[index];
          const resolvedIn = anchor?.angleInDeg; const resolvedOut = anchor?.angleOutDeg;
          const outShared = outField === 'angle_deg' && !endpoint;
          const angleCell = (side: 'in' | 'out', key: typeof inField) => {
            const resolved = side === 'in' ? resolvedIn : resolvedOut;
            const reason = key === null ? (index === 0 ? 'The throat has no incoming side.' : 'The mouth has no outgoing side.')
              : side === 'out' && outShared ? 'A smooth anchor has one tangent: edit In (°).' : undefined;
            const stored = key ? point[key] : undefined;
            return <NumberField label={`${plane} ${label(index)} angle ${side}`} value={reason ? undefined : stored} min={endpoint ? -90 : -89.9999} max={endpoint ? 90 : 89.9999} step={.1} precision={6} displayPrecision={2} optional
              placeholder={reason ? '—' : side === 'out' && key === 'angle_out_deg' && point.angle_deg != null ? '= in' : resolved != null ? `auto ${resolved.toFixed(1)}` : 'auto'}
              disabled={Boolean(reason)} disabledReason={reason}
              validate={(value) => check(() => setAnchorAngle(design, plane, index, side, value))}
              onClear={() => edit.propose(`Clear ${plane} ${label(index)} angle`, (current) => setAnchorAngle(current, plane, index, side, undefined))}
              onCommit={(value) => edit.propose(`Set ${plane} ${label(index)} ${side === 'in' ? 'incoming' : 'outgoing'} angle`, (current) => setAnchorAngle(current, plane, index, side, value))} />;
          };
          return <Fragment key={index}><tr data-anchor-index={index}>
            <td><NumberField label={`${plane} ${label(index)} z`} value={positionMm(point.t, length)} step={.1} precision={12} displayPrecision={4}
              disabled={endpoint} disabledReason={index === 0 ? 'The throat is at z = 0.' : endpoint ? 'The mouth is at Length; change Length to move it.' : undefined}
              validate={endpoint ? undefined : (value) => check(() => setAnchorValue(design, plane, index, 'z', value))}
              onCommit={(value) => edit.propose(`Move ${plane} ${label(index)}`, (current) => setAnchorValue(current, plane, index, 'z', value))} /></td>
            <td><NumberField label={`${plane} ${label(index)} radius`} value={point.r} step={.1} precision={12} displayPrecision={4}
              validate={(value) => check(() => setAnchorValue(design, plane, index, 'r', value))}
              onCommit={(value) => edit.propose(`Set ${plane} ${label(index)} radius`, (current) => setAnchorValue(current, plane, index, 'r', value))} /></td>
            <td><select aria-label={`${plane} ${label(index)} join`} value={point.join ?? 'smooth'}
              onChange={(event) => edit.propose(`Make ${plane} ${label(index)} ${event.target.value}`, (current) => setAnchorJoin(current, plane, index, event.target.value as 'smooth' | 'corner', curves))}>
              <option value="smooth">Smooth</option><option value="corner">Corner</option>
            </select></td>
            <td>{angleCell('in', inField)}</td>
            <td>{angleCell('out', outField)}</td>
            <td>{!endpoint && <button type="button" aria-label={`Delete ${plane} ${label(index)}`}
              onClick={() => edit.propose(`Delete ${plane} ${label(index)}`, (current) => deleteAnchor(current, plane, index))}>−</button>}</td>
          </tr>
          <tr className="anchor-state-row"><td colSpan={6} className="anchor-state">{anchorState(point, index, points.length, anchor, aligned?.spans[index])}</td></tr></Fragment>;
        })}</tbody>
      </table>
    </div>
    {error && <p className="field-error" role="alert">Curve check: {error}</p>}
    {!error && stale && !curves && <p className="section-note">Resolving the curve…</p>}
    <div className="custom-profile-insert" role="group" aria-label={`Insert ${plane} anchor`}>
      <NumberField label={`Insert ${plane} anchor at z`} value={insertZ} step={.1} precision={12} displayPrecision={4} unit="mm" optional placeholder="z"
        onClear={() => setInsertZ(undefined)} onCommit={setInsertZ} />
      <select aria-label={`${plane} insertion mode`} value={insertMode} onChange={(event) => setInsertMode(event.target.value as InsertMode)}>
        <option value="exact">Insert on curve (exact)</option>
        <option value="refit">Refit (releases {plane} handles)</option>
      </select>
      <button type="button" className="btn" disabled={Boolean(insertReason)} aria-describedby={insertReason ? `${id}-insert` : undefined}
        onClick={() => insertZ !== undefined && edit.propose(`${insertMode === 'exact' ? 'Insert on curve' : 'Refit with a new anchor'} at z = ${formatMm(insertZ)} mm (${shared ? 'H' : plane})`, insertAnchorBuilder(plane, insertZ, insertMode))}>Add point</button>
      {insertReason && <span id={`${id}-insert`} className="disabled-reason">{insertReason}</span>}
    </div>
    <ProfileEditPreviewPanel edit={edit} />
  </div>;
}

/** Paste a whole table (one or both planes) as one previewed edit. */
export function CustomProfilePaste() {
  const design = useDesignStore((state) => state.design);
  const currentLength = design.length ?? 120;
  const shared = isSharedProfile(design);
  const edit = useProfileEdit();
  const id = useId();
  const [text, setText] = useState('');
  const [target, setTarget] = useState<Plane | 'both'>('H');
  const [adoptLength, setAdoptLength] = useState(true);
  const [joinChoice, setJoinChoice] = useState<'corner' | 'smooth' | null>(null);
  const parsed = useMemo(() => {
    try { return text.trim() ? parsePointPaste(text) : null; } catch (reason) { return reason instanceof Error ? reason : new Error(String(reason)); }
  }, [text]);
  const valid = parsed && !(parsed instanceof Error) ? parsed : null;
  const bothPlanes = Boolean(valid?.pointsByPlane) && !shared;
  const effectiveTarget = shared ? 'H' : bothPlanes ? 'both' : target;
  const join = joinChoice ?? (valid?.lookupStyle ? 'corner' : 'smooth');
  const targetLabel = shared ? 'the profile (both planes)' : effectiveTarget === 'both' ? 'both profiles' : effectiveTarget === 'H' ? 'the horizontal profile' : 'the vertical profile';
  const targetLength = valid && adoptLength ? valid.importedLength : currentLength;
  const error = parsed instanceof Error ? parsed.message : valid && (targetLength < 20 || targetLength > 1_000) ? 'Imported length must be between 20 and 1000 mm.' : undefined;
  const disabledReason = error ?? (!valid ? 'Paste valid points to import.' : undefined);
  return <div className="point-paste" role="group" aria-label="Import profile points" data-control-reveal-id="control.freeformImport">
    <textarea aria-label="Paste profile points" aria-describedby={`${id}-hint`} rows={4} value={text} onChange={(event) => { setText(event.target.value); setJoinChoice(null); edit.dismissNotice(); }} placeholder={'z r [angle]\n0 12.7\n120 200'} />
    <p className="section-note" id={`${id}-hint`}>{FREEFORM_IMPORT_HINT}</p>
    {!shared && <fieldset className="point-paste-choices">
      <legend>Import into:</legend>
      {([['H', 'Horizontal'], ['V', 'Vertical'], ['both', 'Both']] as const).map(([value, label]) => <label className="ui-check" key={value}>
        <input type="radio" name={`${id}-target`} value={value} checked={effectiveTarget === value} disabled={bothPlanes} onChange={() => setTarget(value)} />{label}
      </label>)}
    </fieldset>}
    {bothPlanes && <p className="section-note">This z;H;V paste contains both planes, so it imports into Both.</p>}
    {shared && valid?.pointsByPlane && <p className="section-note">One profile shapes both planes, so only the H column is used. Turn on Separate vertical profile to import V as well.</p>}
    {valid && <div className="paste-meta">{valid.points.length} rows · pasted length {valid.importedLength} mm · current {currentLength} mm{valid.lookupStyle ? ' · z,r rows' : ''}</div>}
    {valid && <fieldset className="point-paste-choices">
      <legend>Joins</legend>
      <label className="ui-check"><input type="radio" name={`${id}-join`} checked={join === 'corner'} onChange={() => setJoinChoice('corner')} />Corners (straight lines between rows without angles)</label>
      <label className="ui-check"><input type="radio" name={`${id}-join`} checked={join === 'smooth'} onChange={() => setJoinChoice('smooth')} />Smooth (automatic tangents)</label>
    </fieldset>}
    {valid && valid.importedLength !== currentLength && <fieldset className="point-paste-choices">
      <legend>Length</legend>
      <label className="ui-check"><input type="radio" name={`${id}-length`} checked={adoptLength} onChange={() => setAdoptLength(true)} />Use pasted length ({valid.importedLength} mm)</label>
      <label className="ui-check"><input type="radio" name={`${id}-length`} checked={!adoptLength} onChange={() => setAdoptLength(false)} />Keep {currentLength} mm and stretch the pasted points to fit</label>
    </fieldset>}
    {disabledReason && <div id={`${id}-reason`} className={error ? 'field-error' : 'disabled-reason'} role={error ? 'alert' : undefined}>{disabledReason}</div>}
    <button type="button" disabled={Boolean(disabledReason)} aria-describedby={disabledReason ? `${id}-reason` : undefined}
      onClick={() => valid && edit.propose(`Import into ${targetLabel}`, pasteBuilder(valid, { target: effectiveTarget, adoptLength, join }))}>Import into {targetLabel}</button>
    <ProfileEditPreviewPanel edit={edit} />
  </div>;
}

/** Length never changes silently: Stretch profile or Move mouth, each previewed. */
export function CustomProfileLengthField({ field, serverError }: { field: ParameterDefinition; serverError?: string }) {
  const length = useDesignStore((state) => state.design.length ?? 120);
  const expression = useDesignStore((state) => state.design._expressions?.length);
  const edit = useProfileEdit();
  const [pending, setPending] = useState<number | undefined>();
  const generation = useDesignStore((state) => state.loadGeneration);
  useEffect(() => setPending(undefined), [generation]);
  return <div className="custom-profile-length">
    <NumberField label={field.label} description={field.description} value={pending ?? length} expression={pending === undefined ? expression : undefined}
      min={field.min} max={field.max} step={field.step} precision={12} displayPrecision={3} unit="mm" modified={pending !== undefined} invalidMessage={pending === undefined ? serverError : undefined}
      onCommit={(value) => { edit.cancel(); setPending(value === length ? undefined : value); }} />
    {pending !== undefined && <div className="family-switch-choice" role="group" aria-label="Change Length">
      <b>Length {formatMm(length)} → {formatMm(pending)} mm</b>
      <span>Stretch profile scales every anchor, handle and station along z; Move mouth keeps interior positions in mm and moves only the mouth.</span>
      <div>
        <button type="button" onClick={() => edit.propose(`Stretch profile to ${formatMm(pending)} mm`, stretchLengthBuilder(pending))}>Stretch profile</button>
        <button type="button" onClick={() => edit.propose(`Move mouth to ${formatMm(pending)} mm`, moveMouthBuilder(pending))}>Move mouth</button>
        <button type="button" onClick={() => { edit.cancel(); setPending(undefined); }}>Cancel</button>
      </div>
    </div>}
    <ProfileEditPreviewPanel edit={edit} />
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
