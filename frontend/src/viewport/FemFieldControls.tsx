import type { ReactNode } from 'react';
import { FIELD_PLANE_DISPLAY_MODES, fieldPlaneColormap, fieldPlaneWindowForMode, type FieldPlaneDisplayMode } from './fieldPlaneColor';
import { useFieldPlaneStore } from './fieldPlaneStore';
import type { FemFieldRendering } from './FemFieldView';
import type { useFemField } from './useFemField';

export function FemFieldControls({ field, rendering, setRendering, sequential, children }: {
  field: ReturnType<typeof useFemField>; rendering: FemFieldRendering;
  setRendering: (value: FemFieldRendering) => void; sequential: readonly string[]; children?: ReactNode;
}) {
  const mode = useFieldPlaneStore((state) => state.displayMode);
  const animating = useFieldPlaneStore((state) => state.animating);
  const speed = useFieldPlaneStore((state) => state.animationSpeed);
  const hz = field.frequencies[field.frequencyIndex];
  const colormap = fieldPlaneColormap(mode, sequential);
  const window = field.field ? fieldPlaneWindowForMode(mode, field.field.values.real, field.field.values.imag) : null;
  return <div className="field-plane-legend" role="group" aria-label="FEM field controls">
    <div className="field-plane-legend-title"><b>FEM interior field</b></div>
    <p className="field-plane-note" role="note">Pressure per unit acceleration (Pa per m/s²). Interior regions only.</p>
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
      <label>Display<select aria-label="FEM field display mode" value={mode}
        onChange={(event) => useFieldPlaneStore.getState().setDisplayMode(event.target.value as FieldPlaneDisplayMode)}>
        {FIELD_PLANE_DISPLAY_MODES.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <button type="button" aria-label={animating ? 'Pause FEM phase animation' : 'Play FEM phase animation'}
        aria-pressed={animating} onClick={() => useFieldPlaneStore.getState().setAnimating(!animating)}>{animating ? 'Ⅱ' : '▶'}</button>
    </div>
    <div className="field-plane-mode-row"><label>Rendering<select aria-label="FEM field rendering" value={rendering}
      onChange={(event) => setRendering(event.target.value as FemFieldRendering)}>
      <option value="smooth">Smooth</option><option value="element">Element</option>
    </select></label></div>
    {mode === 'instantaneous' && <label className="field-plane-animation-speed">Animation speed
      <input type="range" aria-label="FEM phase animation speed" min={0.1} max={3} step={0.1} value={speed}
        onChange={(event) => useFieldPlaneStore.getState().setAnimationSpeed(Number(event.target.value))}/>
      <output>{speed.toFixed(1)}×</output>
    </label>}
    <i className="field-plane-gradient" style={{ backgroundImage: `linear-gradient(90deg, ${colormap.join(', ')})` }}/>
    {window && <div className="field-plane-window">
      <span>{window.minimum.toFixed(1)}…{window.maximum.toFixed(1)} {mode === 'normalized' ? 'dB re field max' : window.unit}</span>
    </div>}
    {children}
    <div role="status" aria-live="polite">{field.loading ? 'Loading FEM field…' : field.error}
      {field.error && <button type="button" onClick={field.retry}>Retry</button>}
    </div>
  </div>;
}
