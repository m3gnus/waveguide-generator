import { useEffect, useState, type RefObject } from 'react';
import { EXPORT_FORMATS, MAP_REFERENCES, RESULT_PANEL_COUNTS, jobBaseName, preferencesStore, usePreferences, type JobSort, type MapReference } from './preferences';
import { SMOOTHING_MODES, type SmoothingMode } from '../results/smoothing';
import { Icon } from '../shell/icons';
import { AnchoredPanel } from './AnchoredPanel';

interface PreferencesSurfaceProps {
  expanded?: boolean;
  popover?: boolean;
  onClose?: () => void;
  /** The gear button the panel hangs off. */
  anchorRef?: RefObject<HTMLElement | null>;
}

/** Without an anchor the panel still opens, pinned to the top-left corner. */
const NO_ANCHOR: RefObject<HTMLElement | null> = { current: null };

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
    <fieldset className="preferences-formats">
      <legend>Preferred manual export formats</legend>
      <p className="preferences-section-copy">Used by the Results toolbar Export button and each run’s primary Export action.</p>
      {EXPORT_FORMATS.map(({ id, label }) => <label key={id} className="ui-check"><input type="checkbox" aria-label={`Manual export: ${label}`} checked={preferences.exportFormats.includes(id)} onChange={() => preferencesStore.toggleFormat(id)}/>{label}</label>)}
    </fieldset>
    <fieldset className="preferences-formats">
      <legend>Automatic export formats</legend>
      <p className="preferences-section-copy">Written only when “Auto-export completed jobs” is enabled.</p>
      {preferences.autoExportOnComplete && !preferences.autoExportFormats.length && <p className="job-warning" role="alert">Choose at least one automatic format. Auto-export is enabled but will not write any files.</p>}
      {EXPORT_FORMATS.map(({ id, label }) => <label key={id} className="ui-check"><input type="checkbox" aria-label={`Automatic export: ${label}`} checked={preferences.autoExportFormats.includes(id)} onChange={() => preferencesStore.toggleAutoExportFormat(id)}/>{label}</label>)}
    </fieldset>
  </section>;
}

export function ResultsPreferencesSurface({ expanded = false, popover = false, onClose, anchorRef }: PreferencesSurfaceProps) {
  if (popover) return <AnchoredPanel anchorRef={anchorRef ?? NO_ANCHOR} onClose={onClose} className="results-preferences-popover" label="Results and export preferences">
    <header><b>Results & export preferences</b><button type="button" aria-label="Close results preferences" onClick={onClose}><Icon name="close"/></button></header>
    <div className="panel-preferences-scroll"><ResultsPreferencesContent/></div>
  </AnchoredPanel>;
  return <details open={expanded || undefined} className="preferences-surface">
    <summary style={{ color: 'var(--fg2)', cursor: 'pointer', fontSize: 'var(--text-micro)' }}>Results & export preferences</summary>
    <ResultsPreferencesContent/>
  </details>;
}

function JobsPreferencesContent() {
  const preferences = usePreferences();
  return <section className="preferences-section">
    <h3 className="preferences-section-title">Jobs</h3>
    <p className="preferences-section-copy">Naming, ordering, and visibility defaults for solve history.</p>
    <div className="job-naming-preferences">
      <label className="ui-field">Design name<input aria-label="Job design name" value={preferences.outputName} onChange={(event) => preferencesStore.update({ outputName: event.target.value })}/></label>
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

export function JobsPreferencesSurface({ expanded = false, popover = false, onClose, anchorRef }: PreferencesSurfaceProps) {
  if (popover) return <AnchoredPanel anchorRef={anchorRef ?? NO_ANCHOR} onClose={onClose} className="jobs-preferences-popover" label="Job preferences">
    <header><b>Job preferences</b><button type="button" aria-label="Close job preferences" onClick={onClose}><Icon name="close"/></button></header>
    <div className="panel-preferences-scroll"><JobsPreferencesContent/></div>
  </AnchoredPanel>;
  return <details open={expanded || undefined} className="preferences-surface">
    <summary style={{ color: 'var(--fg2)', cursor: 'pointer', fontSize: 'var(--text-micro)' }}>Job preferences</summary>
    <JobsPreferencesContent/>
  </details>;
}
