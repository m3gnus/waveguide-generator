import type { ChangeEvent } from 'react';
import { DEFAULT_VIEWER_PREFERENCES, type CameraProjection, type ViewerPreferences, viewerPreferences } from './viewerPreferences';

function RangePreference({ label, field, value, min, max, step }: {
  label: string;
  field: keyof Pick<ViewerPreferences, 'rotateSpeed' | 'zoomSpeed' | 'panSpeed' | 'dampingFactor'>;
  value: number;
  min: number;
  max: number;
  step: number;
}) {
  const update = (event: ChangeEvent<HTMLInputElement>) => viewerPreferences.update({ [field]: Number(event.target.value) });
  return <label className="viewer-pref-range">
    <span>{label}<output>{value.toFixed(field === 'dampingFactor' ? 2 : 1)}</output></span>
    <input type="range" min={min} max={max} step={step} value={value} onChange={update} />
  </label>;
}

function TogglePreference({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className="viewer-pref-toggle"><span>{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /></label>;
}

export function ViewerPreferencesPanel({ preferences, onClose }: { preferences: ViewerPreferences; onClose: () => void }) {
  const setProjection = (startupCameraMode: CameraProjection) => viewerPreferences.update({ startupCameraMode });
  return <section className="viewer-preferences" aria-label="Viewer preferences">
    <header><b>Viewer preferences</b><button type="button" aria-label="Close viewer preferences" onClick={onClose}>×</button></header>
    <div className="viewer-pref-scroll">
      <h3>Orbit controls</h3>
      <RangePreference label="Rotate speed" field="rotateSpeed" value={preferences.rotateSpeed} min={0.1} max={5} step={0.1} />
      <RangePreference label="Zoom speed" field="zoomSpeed" value={preferences.zoomSpeed} min={0.1} max={5} step={0.1} />
      <RangePreference label="Pan speed" field="panSpeed" value={preferences.panSpeed} min={0.1} max={5} step={0.1} />
      <TogglePreference label="Enable damping" checked={preferences.dampingEnabled} onChange={(dampingEnabled) => viewerPreferences.update({ dampingEnabled })} />
      {preferences.dampingEnabled && <RangePreference label="Damping factor" field="dampingFactor" value={preferences.dampingFactor} min={0.01} max={0.5} step={0.01} />}

      <h3>Input</h3>
      <TogglePreference label="Invert scroll zoom" checked={preferences.invertWheelZoom} onChange={(invertWheelZoom) => viewerPreferences.update({ invertWheelZoom })} />
      <TogglePreference label="Keyboard pan" checked={preferences.keyboardPanEnabled} onChange={(keyboardPanEnabled) => viewerPreferences.update({ keyboardPanEnabled })} />
      <TogglePreference label="Live updates" checked={preferences.liveUpdate} onChange={(liveUpdate) => viewerPreferences.update({ liveUpdate })} />

      <fieldset>
        <legend>Startup camera</legend>
        <label><input type="radio" name="startup-camera" checked={preferences.startupCameraMode === 'perspective'} onChange={() => setProjection('perspective')} />Perspective</label>
        <label><input type="radio" name="startup-camera" checked={preferences.startupCameraMode === 'orthographic'} onChange={() => setProjection('orthographic')} />Orthographic</label>
      </fieldset>
    </div>
    <footer><button type="button" onClick={() => viewerPreferences.update({ ...DEFAULT_VIEWER_PREFERENCES })}>Reset defaults</button></footer>
  </section>;
}
