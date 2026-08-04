import { useEffect, useState } from 'react';
import { EXPORT_FORMATS, MAP_REFERENCES, RESULT_PANEL_COUNTS, preferencesStore, usePreferences, type JobSort, type MapReference } from './preferences';
import { SMOOTHING_MODES, type SmoothingMode } from '../results/smoothing';

const fieldStyle = { display: 'grid', gap: 3, color: 'var(--fg3)', fontSize: 9 } as const;
const selectStyle = { minWidth: 82, color: 'var(--fg1)', background: 'var(--ctl-grad)', border: '1px solid var(--hair)', borderRadius: 3, fontSize: 10 } as const;

function useThemes(): string[] {
  const [themes, setThemes] = useState(['hornlab']);
  useEffect(() => {
    let live = true;
    void fetch('/api/themes').then(async (response) => {
      if (!response.ok) return;
      const body = await response.json() as { themes?: Array<string | { id?: string; name?: string }> };
      const ids = (body.themes ?? []).map((item) => typeof item === 'string' ? item : item.id ?? item.name ?? '').filter(Boolean);
      if (live && ids.length) setThemes(ids);
    }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  return themes;
}

export function ResultPanelCountControl() {
  const preferences = usePreferences();
  return <label style={fieldStyle}>Results layout<select aria-label="Results layout count" style={selectStyle} value={RESULT_PANEL_COUNTS.includes(preferences.chartTypes.length as never) ? preferences.chartTypes.length : ''} onChange={(event) => preferencesStore.setChartCount(Number(event.target.value))}>
    {!RESULT_PANEL_COUNTS.includes(preferences.chartTypes.length as never) && <option value="">{preferences.chartTypes.length} charts</option>}
    {RESULT_PANEL_COUNTS.map((count) => <option key={count} value={count}>{count} chart{count === 1 ? '' : 's'}</option>)}
  </select></label>;
}

export function ResultsPreferencesSurface({ expanded = false }: { expanded?: boolean }) {
  const preferences = usePreferences();
  const themes = useThemes();
  return <details open={expanded || undefined} className="preferences-surface">
    <summary style={{ color: 'var(--fg2)', cursor: 'pointer', fontSize: 10 }}>Results & export preferences</summary>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, padding: '7px 0' }}>
      <ResultPanelCountControl/>
      <label style={fieldStyle}>Smoothing<select aria-label="Smoothing" style={selectStyle} value={preferences.smoothing} onChange={(event) => preferencesStore.update({ smoothing: event.target.value as SmoothingMode })}>{SMOOTHING_MODES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
      <label style={fieldStyle}>Map ref<select aria-label="Map reference" style={selectStyle} value={preferences.mapReference} onChange={(event) => preferencesStore.update({ mapReference: Number(event.target.value) as MapReference })}>{MAP_REFERENCES.map((value) => <option key={value} value={value}>{value} dB</option>)}</select></label>
      <label style={fieldStyle}>Chart theme<select aria-label="Chart theme" style={selectStyle} value={preferences.chartTheme} onChange={(event) => preferencesStore.update({ chartTheme: event.target.value })}>{[...new Set([preferences.chartTheme, ...themes])].map((theme) => <option key={theme}>{theme}</option>)}</select></label>
      <label style={fieldStyle}>Output name<input aria-label="Output name" style={selectStyle} value={preferences.outputName} onChange={(event) => preferencesStore.update({ outputName: event.target.value })}/></label>
      <label style={fieldStyle}>Counter<input aria-label="Export counter" style={{ ...selectStyle, width: 72 }} type="number" min={1} max={999999} value={preferences.counter} onChange={(event) => preferencesStore.update({ counter: Number(event.target.value) })}/></label>
      <label style={{ ...fieldStyle, display: 'flex', alignItems: 'center', flexDirection: 'row' }}><input type="checkbox" checked={preferences.autoExportOnComplete} onChange={(event) => preferencesStore.update({ autoExportOnComplete: event.target.checked })}/> auto-export on complete</label>
      <label style={{ ...fieldStyle, display: 'flex', alignItems: 'center', flexDirection: 'row' }}><input type="checkbox" checked={preferences.autoDownloadMesh} onChange={(event) => preferencesStore.update({ autoDownloadMesh: event.target.checked })}/> auto-download mesh</label>
    </div>
    <fieldset style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 4, border: 0, padding: '0 0 7px', margin: 0 }}><legend style={{ color: 'var(--fg3)', fontSize: 9 }}>Export formats</legend>{EXPORT_FORMATS.map(({ id, label }) => <label key={id} style={{ color: 'var(--fg2)', fontSize: 9 }}><input type="checkbox" aria-label={label} checked={preferences.exportFormats.includes(id)} onChange={() => preferencesStore.toggleFormat(id)}/> {label}</label>)}</fieldset>
  </details>;
}

export function JobsPreferencesSurface({ expanded = false }: { expanded?: boolean }) {
  const preferences = usePreferences();
  return <details open={expanded || undefined} className="preferences-surface">
    <summary style={{ color: 'var(--fg2)', cursor: 'pointer', fontSize: 10 }}>Job preferences</summary>
    <div style={{ display: 'flex', gap: 8, padding: '7px 0' }}>
      <label style={fieldStyle}>Default sort<select aria-label="Default task sort" style={selectStyle} value={preferences.jobSort} onChange={(event) => preferencesStore.update({ jobSort: event.target.value as JobSort })}><option value="completed_desc">Completed, newest</option><option value="created_desc">Created, newest</option><option value="rating_desc">Rating, highest</option><option value="name_asc">Name, A–Z</option></select></label>
      <label style={fieldStyle}>Minimum rating<select aria-label="Minimum rating filter" style={selectStyle} value={preferences.minRating} onChange={(event) => preferencesStore.update({ minRating: Number(event.target.value) })}>{[0, 1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value === 0 ? 'Any rating' : `${value}+ stars`}</option>)}</select></label>
    </div>
  </details>;
}
