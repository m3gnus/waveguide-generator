import { useEffect, useState } from 'react';
import { EXPORT_FORMATS, MAP_REFERENCES, RESULT_PANEL_COUNTS, jobBaseName, preferencesStore, usePreferences, type JobSort, type MapReference } from './preferences';
import { SMOOTHING_MODES, type SmoothingMode } from '../results/smoothing';
import { Icon } from '../shell/icons';

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
  return <label className="ui-field">Results layout<select aria-label="Results layout count" value={RESULT_PANEL_COUNTS.includes(preferences.chartTypes.length as never) ? preferences.chartTypes.length : ''} onChange={(event) => preferencesStore.setChartCount(Number(event.target.value))}>
    {!RESULT_PANEL_COUNTS.includes(preferences.chartTypes.length as never) && <option value="">{preferences.chartTypes.length} charts</option>}
    {RESULT_PANEL_COUNTS.map((count) => <option key={count} value={count}>{count} chart{count === 1 ? '' : 's'}</option>)}
  </select></label>;
}

function ResultsPreferencesContent() {
  const preferences = usePreferences();
  const themes = useThemes();
  return <section className="preferences-section">
    <h3 className="preferences-section-title">Results & export</h3>
    <p className="preferences-section-copy">Chart layout, processing, and automatic export defaults.</p>
    <div className="preferences-grid">
      <ResultPanelCountControl/>
      <label className="ui-field">Smoothing<select aria-label="Smoothing" value={preferences.smoothing} onChange={(event) => preferencesStore.update({ smoothing: event.target.value as SmoothingMode })}>{SMOOTHING_MODES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
      <label className="ui-field">Map reference<select aria-label="Map reference" value={preferences.mapReference} onChange={(event) => preferencesStore.update({ mapReference: Number(event.target.value) as MapReference })}>{MAP_REFERENCES.map((value) => <option key={value} value={value}>{value} dB</option>)}</select></label>
      <label className="ui-field">Export theme<select aria-label="Chart theme" value={preferences.chartTheme} onChange={(event) => preferencesStore.update({ chartTheme: event.target.value })}>{[...new Set([preferences.chartTheme, ...themes])].map((theme) => <option key={theme}>{theme}</option>)}</select></label>
      <label className="ui-field">Export sequence<input aria-label="Export counter" type="number" min={1} max={999999} value={preferences.counter} onChange={(event) => preferencesStore.update({ counter: Number(event.target.value) })}/></label>
    </div>
    <div className="preferences-checks">
      <label className="ui-check"><input type="checkbox" checked={preferences.autoExportOnComplete} onChange={(event) => preferencesStore.update({ autoExportOnComplete: event.target.checked })}/>Auto-export completed jobs</label>
      <label className="ui-check"><input type="checkbox" checked={preferences.autoDownloadMesh} onChange={(event) => preferencesStore.update({ autoDownloadMesh: event.target.checked })}/>Auto-download solve mesh</label>
    </div>
    <fieldset className="preferences-formats"><legend>Export formats</legend>{EXPORT_FORMATS.map(({ id, label }) => <label key={id} className="ui-check"><input type="checkbox" aria-label={label} checked={preferences.exportFormats.includes(id)} onChange={() => preferencesStore.toggleFormat(id)}/>{label}</label>)}</fieldset>
  </section>;
}

export function ResultsPreferencesSurface({ expanded = false, popover = false, onClose }: { expanded?: boolean; popover?: boolean; onClose?: () => void }) {
  if (popover) return <section className="panel-preferences-popover results-preferences-popover" aria-label="Results and export preferences">
    <header><b>Results & export preferences</b><button type="button" aria-label="Close results preferences" onClick={onClose}><Icon name="close"/></button></header>
    <div className="panel-preferences-scroll"><ResultsPreferencesContent/></div>
  </section>;
  return <details open={expanded || undefined} className="preferences-surface">
    <summary style={{ color: 'var(--fg2)', cursor: 'pointer', fontSize: 10 }}>Results & export preferences</summary>
    <ResultsPreferencesContent/>
  </details>;
}

function JobsPreferencesContent() {
  const preferences = usePreferences();
  const [nameDraft, setNameDraft] = useState(preferences.outputName);
  useEffect(() => { setNameDraft(preferences.outputName); }, [preferences.outputName]);
  const commitName = () => preferencesStore.update({ outputName: nameDraft });
  return <section className="preferences-section">
    <h3 className="preferences-section-title">Jobs</h3>
    <p className="preferences-section-copy">Naming, ordering, and visibility defaults for solve history.</p>
    <div className="job-naming-preferences">
      <label className="ui-field">Design name<input aria-label="Job design name" value={nameDraft} onChange={(event) => setNameDraft(event.target.value)} onBlur={commitName} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}/></label>
      <label className="ui-field">Next version<input aria-label="Next job version" type="number" min={1} max={999999} value={preferences.jobVersion} onChange={(event) => preferencesStore.update({ jobVersion: Number(event.target.value) })}/></label>
      <label className="ui-check"><input aria-label="Prefix job name with date" type="checkbox" checked={preferences.datePrefix} onChange={(event) => preferencesStore.update({ datePrefix: event.target.checked })}/>Prefix job name with date</label>
      <span className="job-name-preview">next · <b>{jobBaseName(preferences)}</b></span>
    </div>
    <div className="preferences-grid preferences-grid--jobs">
      <label className="ui-field">Default sort<select aria-label="Default task sort" value={preferences.jobSort} onChange={(event) => preferencesStore.update({ jobSort: event.target.value as JobSort })}><option value="completed_desc">Completed, newest</option><option value="created_desc">Created, newest</option><option value="rating_desc">Rating, highest</option><option value="name_asc">Name, A–Z</option></select></label>
      <label className="ui-field">Minimum rating<select aria-label="Minimum rating filter" value={preferences.minRating} onChange={(event) => preferencesStore.update({ minRating: Number(event.target.value) })}>{[0, 1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value === 0 ? 'Any rating' : `${value}+ stars`}</option>)}</select></label>
    </div>
  </section>;
}

export function JobsPreferencesSurface({ expanded = false, popover = false, onClose }: { expanded?: boolean; popover?: boolean; onClose?: () => void }) {
  if (popover) return <section className="panel-preferences-popover jobs-preferences-popover" aria-label="Job preferences">
    <header><b>Job preferences</b><button type="button" aria-label="Close job preferences" onClick={onClose}><Icon name="close"/></button></header>
    <div className="panel-preferences-scroll"><JobsPreferencesContent/></div>
  </section>;
  return <details open={expanded || undefined} className="preferences-surface">
    <summary style={{ color: 'var(--fg2)', cursor: 'pointer', fontSize: 10 }}>Job preferences</summary>
    <JobsPreferencesContent/>
  </details>;
}
