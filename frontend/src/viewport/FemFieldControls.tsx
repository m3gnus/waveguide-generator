import { useMemo, type ReactNode } from 'react';
import { FIELD_PLANE_DISPLAY_MODES, fieldPlaneColormap, fieldPlaneWindowForMode, type FieldPlaneValueWindow } from './fieldPlaneColor';
import { useFieldPlaneStore } from './fieldPlaneStore';
import type { FemFieldRendering, FemDisplayMode } from './FemFieldView';
import type { useFemField } from './useFemField';
import { femInteriorDisplayValues } from './FemFieldScene';
import { FEM_PLANE_MASK_REASONS } from '../api/femField';

export function FemFieldControls({ field, rendering, setRendering, sequential, children, planeResult, scope, setScope, quantity, setQuantity, displayMode, setDisplayMode, manualWindow, setManualWindow, interiorValues }: {
  interiorValues?: ReturnType<typeof femInteriorDisplayValues> | null;
  field: ReturnType<typeof useFemField>; rendering: FemFieldRendering;
  setRendering: (value: FemFieldRendering) => void; sequential: readonly string[]; children?: ReactNode;
  planeResult: ReturnType<typeof import('./useFemField').useFemPlane>;
  scope: import('../api/femField').FemPlaneScope; setScope: (value: import('../api/femField').FemPlaneScope) => void;
  quantity: import('../api/femField').FemPlaneQuantity; setQuantity: (value: import('../api/femField').FemPlaneQuantity) => void;
  displayMode: FemDisplayMode; setDisplayMode: (value: FemDisplayMode) => void;
  manualWindow: FieldPlaneValueWindow | null; setManualWindow: (value: FieldPlaneValueWindow | null) => void;
}) {
  const mode = displayMode === 'real' || displayMode === 'imaginary' ? 'instantaneous' : displayMode;
  const animating = useFieldPlaneStore((state) => state.animating);
  const speed = useFieldPlaneStore((state) => state.animationSpeed);
  const hz = field.frequencies[field.frequencyIndex];
  const colormap = fieldPlaneColormap(mode, sequential);
  const plane = useFieldPlaneStore((state) => state.plane);
  const interior = useMemo(() => interiorValues !== undefined ? interiorValues : field.field ? femInteriorDisplayValues(field.field, plane, quantity) : null,
    [interiorValues, field.field, plane, quantity]);
  const shown = useMemo(() => {
    if (!planeResult.field) return interior;
    if (scope !== 'combined' || !interior) return planeResult.field;
    const join = (a: Float32Array, b: Float32Array) => {
      const values = new Float32Array(a.length + b.length); values.set(a); values.set(b, a.length); return values;
    };
    return { real: join(interior.real, planeResult.field.real), imag: join(interior.imag, planeResult.field.imag) };
  }, [interior, planeResult.field, scope]);
  const window = manualWindow ?? (shown ? fieldPlaneWindowForMode(mode, shown.real, shown.imag) : null);
  const unit = quantity === 'velocity_normal' && window?.unit === 'Pa' ? 'm/s' : window?.unit;
  const scopes = field.field?.payload.plane_scopes ?? ['interior'];
  return <div className="field-plane-legend" role="group" aria-label="FEM field controls">
    <div className="field-plane-legend-title"><b>{scope === 'interior' ? 'FEM interior field' : scope === 'exterior' ? 'Coupled exterior field' : 'Combined acoustic field'}</b></div>
    <p className="field-plane-note" role="note">{field.field?.payload.drive_normalization === 'voltage_rms'
      ? `RMS ${quantity === 'pressure' ? 'pressure' : 'velocity'} at ${field.field.payload.transducer_reference_voltage_v} V RMS (${quantity === 'pressure' ? 'Pa' : 'm/s'}). `
      : `${quantity === 'pressure' ? 'Pressure' : 'Velocity'} per unit acceleration (${quantity === 'pressure' ? 'Pa' : '(m/s)'} per m/s²). `}</p>
    {field.field?.payload.plane_unavailable_reason && <p role="note">{field.field.payload.plane_unavailable_reason}</p>}
    {quantity === 'velocity_normal' && <p role="note">Signed particle velocity normal to the plane (m/s), constant within each P1 element. Exterior velocity uses a centred potential difference. Clearance requires kh ≥ 0.001 with h ≤ 5% of surface distance (about 0.0032 wavelengths), plus coordinate precision; it decreases as frequency rises.</p>}
    {FEM_PLANE_MASK_REASONS.slice(1).map((reason, i) => {
      const count = planeResult.field?.maskReasons.filter((code) => code === i + 1).length ?? 0;
      return count > 0 && <p key={reason} role="note" title={reason}>{count} masked samples: {reason}.</p>;
    })}
    <div className="field-plane-mode-row"><label>Plane region<select aria-label="FEM plane region" value={scope}
      onChange={(e) => setScope(e.target.value as typeof scope)}>
      {(['interior', 'exterior', 'combined'] as const).map((value) => <option key={value} value={value} disabled={!scopes.includes(value)}>{value}</option>)}
    </select></label></div>
    <div className="field-plane-mode-row"><label>Quantity<select aria-label="FEM field quantity" value={quantity}
      onChange={(e) => setQuantity(e.target.value as typeof quantity)}>
      <option value="pressure">Pressure</option><option value="velocity_normal">Normal particle velocity</option>
    </select></label></div>
    {field.frequencies.length > 0 && <label className="field-plane-frequency">
      Frequency <output>{hz === undefined ? '—' : `${hz.toLocaleString()} Hz`}</output>
      <input type="range" aria-label="FEM field frequency" aria-valuetext={`${hz ?? 0} Hz`}
        min={0} max={field.frequencies.length - 1} step={1} value={field.frequencyIndex}
        onChange={(event) => field.setFrequencyIndex(Number(event.target.value))}/>
    </label>}
    {field.channels.length > 0 && <div className="field-plane-mode-row"><label>Channel
      <select aria-label="FEM field channel" value={field.channel} onChange={(event) => field.setChannel(event.target.value)}>
        {field.channels.map((channel) => <option key={channel} value={channel}>{channel}</option>)}
      </select>
    </label></div>}
    <div className="field-plane-mode-row">
      <label>Display<select aria-label="FEM field display mode" value={displayMode}
        onChange={(event) => setDisplayMode(event.target.value as FemDisplayMode)}>
        {FIELD_PLANE_DISPLAY_MODES.map(({ value, label }) => <option key={value} value={value} disabled={quantity !== 'pressure' && value === 'spl'}>{label}</option>)}
        <option value="real">Real</option><option value="imaginary">Imaginary</option>
      </select></label>
      <button type="button" aria-label={animating ? 'Pause FEM phase animation' : 'Play FEM phase animation'}
        aria-pressed={animating} onClick={() => { if (!animating) setDisplayMode('instantaneous'); useFieldPlaneStore.getState().setAnimating(!animating); }}>{animating ? 'Ⅱ' : '▶'}</button>
    </div>
    <div className="field-plane-mode-row"><label>Rendering<select aria-label="FEM field rendering" value={rendering} disabled={quantity !== 'pressure' || scope === 'exterior'}
      onChange={(event) => setRendering(event.target.value as FemFieldRendering)}>
      <option value="smooth">Smooth</option><option value="element">Element</option>
    </select></label></div>
    {displayMode === 'instantaneous' && <label className="field-plane-animation-speed">Animation speed
      <input type="range" aria-label="FEM phase animation speed" min={0.1} max={3} step={0.1} value={speed}
        onChange={(event) => useFieldPlaneStore.getState().setAnimationSpeed(Number(event.target.value))}/>
      <output>{speed.toFixed(1)}×</output>
    </label>}
    <i className="field-plane-gradient" style={{ backgroundImage: `linear-gradient(90deg, ${colormap.join(', ')})` }}/>
    {window && <div className="field-plane-window">
      <span>{window.minimum.toFixed(1)}…{window.maximum.toFixed(1)} {mode === 'normalized' ? 'dB re field max' : unit}</span>
    </div>}
    {window && <div className="field-plane-mode-row">
      <label>Manual colour limits<input type="checkbox" aria-label="FEM manual colour limits" checked={manualWindow !== null}
        onChange={(e) => setManualWindow(e.target.checked ? window : null)}/></label>
      {manualWindow && <>
        <label>Min<input type="number" aria-label="FEM colour minimum" value={manualWindow.minimum} step="any"
          onChange={(e) => { const minimum = Number(e.target.value); if (Number.isFinite(minimum) && minimum < manualWindow.maximum) setManualWindow({ ...manualWindow, minimum }); }}/></label>
        <label>Max<input type="number" aria-label="FEM colour maximum" value={manualWindow.maximum} step="any"
          onChange={(e) => { const maximum = Number(e.target.value); if (Number.isFinite(maximum) && maximum > manualWindow.minimum) setManualWindow({ ...manualWindow, maximum }); }}/></label>
      </>}
    </div>}
    {children}
    <div role="status" aria-live="polite">{field.loading || planeResult.loading ? 'Loading FEM field…' : planeResult.error ?? field.error}
      {(field.error || planeResult.error) && <button type="button" className="btn" onClick={planeResult.error ? planeResult.retry : field.retry}>Retry</button>}
    </div>
  </div>;
}
