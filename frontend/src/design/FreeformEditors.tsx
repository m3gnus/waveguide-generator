import { useMemo, useState } from 'react';
import { useDesignStore, type CrossSectionStation, type FreeformPoint } from '../stores/design';
import type { ParameterDefinition } from './parameterRegistry';

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
    const values = line.split(compact ? ';' : /[\s,]+/).map((item) => Number(item.trim()));
    if (values.some((value) => !Number.isFinite(value))) throw new Error(`Line ${index + 1} contains a non-numeric value.`);
    if (compact && values.length !== 3) throw new Error(`Line ${index + 1} must contain z, H, and V.`);
    if (!compact && values.length === 4) throw new Error(`Line ${index + 1}: per-anchor strength was removed; tangent speed is now solved automatically.`);
    if (!compact && (values.length < 2 || values.length > 3)) throw new Error(`Line ${index + 1} must contain 2 or 3 columns.`);
    return values;
  });
  const validate = (points: FreeformPoint[]) => {
    points.forEach((point, index) => {
      if (point.z < 0) throw new Error(`Line ${index + 1}: z must be at least 0.`);
      if (!(point.r > 0)) throw new Error(`Line ${index + 1}: radius must be greater than 0.`);
      if (point.angle_deg !== undefined && (point.angle_deg < -90 || point.angle_deg > 90)) throw new Error(`Line ${index + 1}: angle must be between −90 and 90°.`);
    });
    points.sort((left, right) => left.z - right.z);
    if (points.length > 64) throw new Error('A profile supports at most 62 interior points.');
  };
  if (compact) {
    const H = parsed.map(([z, h]) => ({ z: z * 10, r: h * 10 }));
    const V = parsed.map(([z, _h, v]) => ({ z: z * 10, r: v * 10 }));
    validate(H); validate(V);
    return { points: H, pointsByPlane: { H, V }, importedLength: H.at(-1)!.z };
  }
  const points = parsed.map(([z, r, angle]) => ({
    z, r,
    ...(angle === undefined ? {} : { angle_deg: angle }),
  }));
  validate(points);
  return { points, importedLength: points.at(-1)!.z };
}

function normalizedImportedPoints(imported: FreeformPoint[], current: FreeformPoint[], length: number, adoptLength: boolean): FreeformPoint[] {
  const sorted = structuredClone(imported).sort((a, b) => a.z - b.z);
  const targetLength = adoptLength ? sorted.at(-1)!.z : length;
  const throat = sorted[0]?.z === 0 ? sorted.shift()! : structuredClone(current[0]);
  const mouth = sorted.at(-1)?.z === targetLength ? sorted.pop()! : { ...structuredClone(current.at(-1)!), z: targetLength };
  const interior = sorted.filter((point) => point.z > 0 && point.z < targetLength).slice(0, 62);
  return [{ ...throat, z: 0 }, ...interior, { ...mouth, z: targetLength }];
}

function PointPaste({ plane, points }: { plane: 'H' | 'V'; points: FreeformPoint[] }) {
  const updateValues = useDesignStore((state) => state.updateValues);
  const other = useDesignStore((state) => plane === 'H' ? state.design.profile_v!.points : state.design.profile_h!.points);
  const [text, setText] = useState('');
  const [applyBoth, setApplyBoth] = useState(false);
  const [adoptLength, setAdoptLength] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const parsed = useMemo(() => {
    try { return text.trim() ? parsePointPaste(text) : null; } catch (reason) { return reason instanceof Error ? reason : new Error(String(reason)); }
  }, [text]);
  const currentLength = points.at(-1)?.z ?? 120;
  const apply = () => {
    if (!parsed || parsed instanceof Error) return;
    const targetLength = adoptLength ? parsed.importedLength : currentLength;
    if (targetLength < 20 || targetLength > 1_000) { setError('Imported length must be between 20 and 1000 mm.'); return; }
    const source = parsed.pointsByPlane?.[plane] ?? parsed.points;
    const updates: Record<string, FreeformPoint[]> = {
      [`profile_${plane.toLowerCase()}.points`]: normalizedImportedPoints(source, points, currentLength, adoptLength),
    };
    if (applyBoth) {
      const otherPlane = plane === 'H' ? 'V' : 'H';
      const otherSource = parsed.pointsByPlane?.[otherPlane] ?? parsed.points;
      updates[`profile_${otherPlane.toLowerCase()}.points`] = normalizedImportedPoints(otherSource, other, currentLength, adoptLength);
    } else if (adoptLength) {
      const otherPlane = plane === 'H' ? 'v' : 'h';
      const resizedOther = structuredClone(other);
      resizedOther[resizedOther.length - 1].z = targetLength;
      updates[`profile_${otherPlane}.points`] = resizedOther;
    }
    updateValues(updates);
    setError(null);
  };
  const parseError = parsed instanceof Error ? parsed.message : null;
  return <div className="point-paste">
    <textarea aria-label={`Paste ${plane} points`} rows={4} value={text} onChange={(event) => setText(event.target.value)} placeholder={'z r [angle]\n# or z_cm;r_h_cm;r_v_cm'} />
    <div className="paste-meta">Imported length <b>{parsed && !(parsed instanceof Error) ? `${parsed.importedLength} mm` : '—'}</b> · current <b>{currentLength} mm</b></div>
    <label><input type="checkbox" checked={applyBoth} onChange={(event) => setApplyBoth(event.target.checked)} /> Apply to both H and V</label>
    <label><input type="radio" checked={adoptLength} onChange={() => setAdoptLength(true)} /> Use imported length</label>
    <label><input type="radio" checked={!adoptLength} onChange={() => setAdoptLength(false)} /> Keep current length</label>
    {(parseError || error) && <div className="field-error" role="alert">{parseError ?? error}</div>}
    <button type="button" disabled={!parsed || parsed instanceof Error} onClick={apply}>Import points</button>
  </div>;
}

export function EditablePointTable({ field, points }: { field: ParameterDefinition; points: FreeformPoint[] }) {
  const updateValue = useDesignStore((state) => state.updateValue);
  const plane = field.id.endsWith('H') ? 'H' : 'V';
  const path = `profile_${plane.toLowerCase()}.points`;
  const interior = points.slice(1, -1);
  const length = points.at(-1)?.z ?? 120;
  const update = (index: number, key: keyof FreeformPoint, raw: string) => {
    const next = structuredClone(points);
    const target = next[index + 1];
    if (raw.trim() === '' && key === 'angle_deg') delete target[key];
    else {
      const value = Number(raw);
      if (!Number.isFinite(value)) return;
      if (key === 'z' && (value < 1 || value > length - 1)) return;
      if (key === 'r' && value <= 0) return;
      if (key === 'angle_deg' && (value <= -90 || value >= 90)) return;
      target[key] = value as never;
    }
    updateValue(path, next);
  };
  const add = () => {
    if (interior.length >= 62) return;
    let gapIndex = 0;
    for (let index = 1; index < points.length - 1; index += 1) {
      if (points[index + 1].z - points[index].z > points[gapIndex + 1].z - points[gapIndex].z) gapIndex = index;
    }
    const left = points[gapIndex]; const right = points[gapIndex + 1];
    const z = (left.z + right.z) / 2;
    const r = left.r + ((z - left.z) / (right.z - left.z || 1)) * (right.r - left.r);
    updateValue(path, [...points.slice(0, gapIndex + 1), { z, r }, ...points.slice(gapIndex + 1)]);
  };
  return <div className="editable-parameter-table" aria-label={field.label}>
    <div className="readonly-table-head"><b>{field.label}</b><span>{interior.length} / 62 interior</span></div>
    <table><thead><tr><th>z mm</th><th>r mm</th><th>angle</th><th /></tr></thead>
      <tbody>{interior.map((point, index) => <tr key={`${index}-${point.z}`}>
        <td><input aria-label={`${plane} point ${index + 1} z`} type="number" min={1} max={length - 1} step={.1} defaultValue={point.z} onBlur={(event) => update(index, 'z', event.target.value)} /></td>
        <td><input aria-label={`${plane} point ${index + 1} radius`} type="number" min={.1} step={.1} defaultValue={point.r} onBlur={(event) => update(index, 'r', event.target.value)} /></td>
        <td><input aria-label={`${plane} point ${index + 1} angle`} type="number" min={-89} max={89} placeholder="auto" defaultValue={point.angle_deg} onBlur={(event) => update(index, 'angle_deg', event.target.value)} /></td>
        <td><button aria-label={`Remove ${plane} point ${index + 1}`} onClick={() => updateValue(path, points.filter((_point, pointIndex) => pointIndex !== index + 1))}>−</button></td>
      </tr>)}</tbody>
    </table>
    <button type="button" disabled={interior.length >= 62} onClick={add}>Add point</button>
    <PointPaste plane={plane} points={points} />
  </div>;
}

export function EditableStationTable({ field, stations }: { field: ParameterDefinition; stations: CrossSectionStation[] }) {
  const updateValue = useDesignStore((state) => state.updateValue);
  const update = (index: number, update: Partial<CrossSectionStation>) => {
    if (update.t !== undefined && (!Number.isFinite(update.t) || update.t <= 0 || update.t >= 1 || stations.some((station, item) => item !== index && Math.abs(station.t - update.t!) < 1e-9))) return;
    if (update.exponent !== undefined && (!Number.isFinite(update.exponent) || update.exponent < 2 || update.exponent > 16)) return;
    if (update.corner_radius_mm !== undefined && (!Number.isFinite(update.corner_radius_mm) || update.corner_radius_mm < 1)) return;
    const next = stations.map((station, item) => item === index ? { ...station, ...update } : station).sort((a, b) => a.t - b.t);
    updateValue('cross_sections', next);
  };
  const add = () => {
    if (stations.length >= 32) return;
    let gap = 0;
    for (let index = 1; index < stations.length - 1; index += 1) if (stations[index + 1].t - stations[index].t > stations[gap + 1].t - stations[gap].t) gap = index;
    const station = { t: (stations[gap].t + stations[gap + 1].t) / 2, shape: 'ellipse' as const };
    updateValue('cross_sections', [...stations, station].sort((a, b) => a.t - b.t));
  };
  return <div className="editable-parameter-table" aria-label={field.label}>
    <div className="readonly-table-head"><b>{field.label}</b><span>{stations.length} / 32 stations</span></div>
    <table><thead><tr><th>t</th><th>shape</th><th>n / radius</th><th /></tr></thead><tbody>
      {stations.map((station, index) => <tr key={`${index}-${station.t}`}>
        <td><input aria-label={`Station ${index + 1} position`} type="number" min={0} max={1} step={.01} disabled={index === 0 || index === stations.length - 1} defaultValue={station.t} onBlur={(event) => update(index, { t: Number(event.target.value) })} /></td>
        <td><select aria-label={`Station ${index + 1} shape`} value={station.shape} disabled={index === 0} onChange={(event) => update(index, { shape: event.target.value as CrossSectionStation['shape'] })}><option value="ellipse">Ellipse</option><option value="superellipse">Superellipse</option><option value="rounded_rectangle">Rounded rectangle</option></select></td>
        <td>{station.shape === 'superellipse' ? <input aria-label={`Station ${index + 1} exponent`} type="number" min={2} max={16} step={.1} defaultValue={station.exponent ?? 4} onBlur={(event) => update(index, { exponent: Number(event.target.value) })} /> : station.shape === 'rounded_rectangle' ? <input aria-label={`Station ${index + 1} corner radius`} type="number" min={1} step={1} defaultValue={station.corner_radius_mm ?? 10} onBlur={(event) => update(index, { corner_radius_mm: Number(event.target.value) })} /> : '—'}</td>
        <td><button aria-label={`Remove station ${index + 1}`} disabled={stations.length <= 2 || index === 0 || index === stations.length - 1} onClick={() => updateValue('cross_sections', stations.filter((_station, item) => item !== index))}>−</button></td>
      </tr>)}
    </tbody></table>
    <button type="button" disabled={stations.length >= 32} onClick={add}>Add station</button>
  </div>;
}
