import { useEffect, useState, type RefObject } from 'react';
import { nextRunLabel, type RunNameDateFormat, type RunNameDatePosition, type RunNameNumberFormat, type RunNameNumberPosition } from '../jobs/runNaming';
import { useRunNameSource } from '../jobs/runNameSource';
import { EXPORT_FORMATS, IMPEDANCE_DISPLAYS, MAP_REFERENCES, MATCH_INTERFACE_THEME, RESULT_PANEL_COUNTS, preferencesStore, usePreferences, type ImpedanceDisplay, type JobSort, type MapReference } from './preferences';
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
    <p className="preferences-section-copy">Chart layout, processing, and automatic export defaults. Files are saved under the Workspace folder, grouped as <code>&lt;design&gt;/&lt;run&gt;</code>, without browser download prompts.</p>
    <div className="preferences-grid">
      <ResultPanelCountControl/>
      <label className="ui-field">Smoothing<select aria-label="Smoothing" value={preferences.smoothing} onChange={(event) => preferencesStore.update({ smoothing: event.target.value as SmoothingMode })}>{SMOOTHING_MODES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
      <label className="ui-field">Map reference<select aria-label="Map reference" value={preferences.mapReference} onChange={(event) => preferencesStore.update({ mapReference: Number(event.target.value) as MapReference })}>{MAP_REFERENCES.map((value) => <option key={value} value={value}>{value} dB</option>)}</select></label>
      <label className="ui-field" title="Horizontal lines across the directivity map every N degrees. 0 turns them off.">Angular guides (°, 0 = off)<input aria-label="Directivity angular guide interval" type="number" min={0} max={180} step={1} value={preferences.directivityGuideInterval} onChange={(event) => { if (Number.isFinite(event.target.valueAsNumber)) preferencesStore.update({ directivityGuideInterval: event.target.valueAsNumber }); }}/></label>
      {/* Re/Im is how horn throat impedance is conventionally read; |Z| and
          phase is how a driver's electrical impedance is. The same chart draws
          both, so which one is useful depends on the run. */}
      <label className="ui-field">Impedance display<select aria-label="Impedance display" value={preferences.impedanceDisplay} onChange={(event) => preferencesStore.update({ impedanceDisplay: event.target.value as ImpedanceDisplay })}>{IMPEDANCE_DISPLAYS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
      {/* "Match interface" is not a server theme: it resolves to console or
          vellum at export time so a figure leaves on the same ground as the
          window it was taken from. The server never sees the sentinel. */}
      <label className="ui-field">Export theme<select aria-label="Chart theme" value={preferences.chartTheme} onChange={(event) => preferencesStore.update({ chartTheme: event.target.value })}><option value={MATCH_INTERFACE_THEME}>Match interface</option>{[...new Set([preferences.chartTheme, ...themes])].filter((theme) => theme !== MATCH_INTERFACE_THEME).map((theme) => <option key={theme}>{theme}</option>)}</select></label>
    </div>
    <div className="preferences-checks">
      {/* On by default because the exported PNG has always drawn the phase:
          with this off, the chart on screen and the chart in the file are not
          the same picture. */}
      <label className="ui-check"><input type="checkbox" checked={preferences.splPhase} onChange={(event) => preferencesStore.update({ splPhase: event.target.checked })}/>Show phase on the SPL chart</label>
      {/* The crossover-sum plot: the sum drawn over the branches it is made of.
          Off leaves the sum alone on the chart, which is the right reading when
          comparing runs rather than tuning a crossover. */}
      <label className="ui-check"><input type="checkbox" checked={preferences.showMembersUnderCombined} onChange={(event) => preferencesStore.update({ showMembersUnderCombined: event.target.checked })}/>Show drivers under Combined</label>
      {/* The bench check on a crossover: the sum with one member inverted must
          cancel deeply if the two really are summing as the filter pair says.
          Off by default — it is a diagnostic, not part of the response. */}
      <label className="ui-check"><input type="checkbox" checked={preferences.showReverseNull} onChange={(event) => preferencesStore.update({ showReverseNull: event.target.checked })}/>Show reverse null</label>
      <label className="ui-check"><input type="checkbox" checked={preferences.archiveRunsOnComplete} onChange={(event) => preferencesStore.update({ archiveRunsOnComplete: event.target.checked })}/>Archive every completed run</label>
      <label className="ui-check"><input type="checkbox" checked={preferences.autoExportOnComplete} onChange={(event) => preferencesStore.update({ autoExportOnComplete: event.target.checked })}/>Auto-export completed jobs</label>
      <label className="ui-check"><input type="checkbox" checked={preferences.autoDownloadMesh} onChange={(event) => preferencesStore.update({ autoDownloadMesh: event.target.checked })}/>Auto-save solve mesh to Workspace</label>
    </div>
    {/* The two retentions must not be left to be discovered: results in the
        run list are pruned, the Workspace folder is not. */}
    <p className="preferences-section-copy">Results stay in the run list for 30 days, or until the run limit is passed — rating a run keeps it. Archived runs are written to the Workspace folder and are kept until you delete them.{!preferences.archiveRunsOnComplete && ' Archiving is off, so a solve you do not export leaves nothing behind.'}</p>
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

function JobsPreferencesContent({ now = new Date() }: { now?: Date }) {
  const preferences = usePreferences();
  // The name itself is not here. It belongs to the document, and is edited in
  // the one Design name field beside the runs it names; duplicating the input
  // in a popover is how a name last set several sessions ago used to end up on
  // today's runs. What is left is how that name is decorated.
  const displayedLabel = nextRunLabel(useRunNameSource().name, preferences, now);
  return <section className="preferences-section">
    <h3 className="preferences-section-title">Jobs</h3>
    <p className="preferences-section-copy">Naming, ordering, and visibility defaults for solve history.</p>
    <div className="job-naming-preferences">
      <label className="ui-field">Number<select aria-label="Run-name number position" value={preferences.runNameNumberPosition} onChange={(event) => preferencesStore.update({ runNameNumberPosition: event.target.value as RunNameNumberPosition })}><option value="off">Off</option><option value="suffix">After name</option></select></label>
      <label className="ui-field">Number format<select aria-label="Run-name number format" value={preferences.runNameNumberFormat} disabled={preferences.runNameNumberPosition === 'off'} onChange={(event) => preferencesStore.update({ runNameNumberFormat: event.target.value as RunNameNumberFormat })}><option value="natural">1, 2, 3</option><option value="2-digit">01, 02, 03</option><option value="3-digit">001, 002, 003</option></select></label>
      <label className="ui-field">Date<select aria-label="Run-name date position" value={preferences.runNameDatePosition} onChange={(event) => preferencesStore.update({ runNameDatePosition: event.target.value as RunNameDatePosition })}><option value="off">Off</option><option value="prefix">Before name</option><option value="suffix">After name</option></select></label>
      <label className="ui-field">Date format<select aria-label="Run-name date format" value={preferences.runNameDateFormat} disabled={preferences.runNameDatePosition === 'off'} onChange={(event) => preferencesStore.update({ runNameDateFormat: event.target.value as RunNameDateFormat })}><option value="yymmdd">YYMMDD</option><option value="yyyy-mm-dd">YYYY-MM-DD</option></select></label>
      <span className="job-name-preview">next · <b>{displayedLabel}</b></span>
    </div>
    <div className="preferences-grid preferences-grid--jobs">
      <label className="ui-field">Default sort<select aria-label="Default task sort" value={preferences.jobSort} onChange={(event) => preferencesStore.update({ jobSort: event.target.value as JobSort })}><option value="completed_desc">Completed, newest</option><option value="created_desc">Created, newest</option><option value="rating_desc">Rating, highest</option><option value="name_asc">Name, A–Z</option></select></label>
      <label className="ui-field">Minimum rating<select aria-label="Minimum rating filter" value={preferences.minRating} onChange={(event) => preferencesStore.update({ minRating: Number(event.target.value) })}>{[0, 1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value === 0 ? 'Any rating' : `${value}+ stars`}</option>)}</select></label>
    </div>
  </section>;
}

export function JobsPreferencesSurface({ expanded = false, popover = false, onClose, anchorRef, now }: PreferencesSurfaceProps & { now?: Date }) {
  if (popover) return <AnchoredPanel anchorRef={anchorRef ?? NO_ANCHOR} onClose={onClose} className="jobs-preferences-popover" label="Job preferences">
    <header><b>Job preferences</b><button type="button" aria-label="Close job preferences" onClick={onClose}><Icon name="close"/></button></header>
    <div className="panel-preferences-scroll"><JobsPreferencesContent now={now}/></div>
  </AnchoredPanel>;
  return <details open={expanded || undefined} className="preferences-surface">
    <summary style={{ color: 'var(--fg2)', cursor: 'pointer', fontSize: 'var(--text-micro)' }}>Job preferences</summary>
    <JobsPreferencesContent now={now}/>
  </details>;
}
