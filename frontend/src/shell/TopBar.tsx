import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { jobsSocket } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { DesignFileMenu } from '../design/DesignFileMenu';
import { PARAMETER_REGISTRY, PARAMETER_SECTION_DEFINITIONS, fieldAppliesToFamily, fieldMatchesQuery, type ParameterTab } from '../design/parameterRegistry';
import { RESULT_PANEL_COUNTS, preferencesStore } from '../prefs/preferences';
import { useDesignStore, type DesignFamily } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { requestParameterReveal } from '../design/ParamPanel';
import { BrandMark, Icon } from './icons';
import { useSolveControl } from './JobsCoordinator';
import { CommandPalette, type PaletteEntry } from './CommandPalette';
import { SettingsDialog, type Theme } from './SettingsDialog';
import { workspaceNavigation } from './Workspace';

const THEME_KEY = 'wg2.theme';

function initialTheme(): Theme {
  const saved = localStorage.getItem(THEME_KEY);
  return saved === 'light' ? 'light' : 'dark';
}

const parameterTabBySection = new Map(PARAMETER_SECTION_DEFINITIONS.map((section) => [section.title, section.tab]));

export function revealParameterFromPalette(id: string, tab: ParameterTab, query: string): void {
  workspaceNavigation.activate(tab);
  // The request waits to be claimed, so it does not need to be timed to land
  // after the panel mounts — and not deferring it means the route still works
  // in a background tab, where animation frames never run.
  requestParameterReveal({ id, tab, query });
}

export function buildParameterPaletteEntries(family?: DesignFamily): PaletteEntry[] {
  return PARAMETER_REGISTRY.filter((field) => !family || fieldAppliesToFamily(field, family)).map((field) => {
    const tab = parameterTabBySection.get(field.section) ?? 'geometry';
    return {
      id: `parameter-${field.id}`,
      kind: 'Parameters',
      label: field.label,
      detail: [field.symbol, field.legacyKey].filter(Boolean).join(' · '),
      keywords: [field.id, field.path, field.symbol, field.legacyKey].filter(Boolean).join(' '),
      matches: (query) => fieldMatchesQuery(field, query) || Boolean(field.symbol?.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())),
      run: () => revealParameterFromPalette(field.id, tab, field.label),
    };
  });
}

export function TopBar({ onResetLayout }: { onResetLayout: () => void }) {
  const solve = useSolveControl();
  const undo = useDesignStore((state) => state.undo);
  const redo = useDesignStore((state) => state.redo);
  const revision = useDesignStore((state) => state.designRevision);
  const family = useDesignStore((state) => state.design.formula);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const temporal = useDesignStore.temporal.getState();
  const canUndo = temporal.pastStates.length > 0 || Boolean(useDesignStore.getState().dragSnapshot);
  const canRedo = temporal.futureStates.length > 0;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  const closeSettings = useCallback(() => setSettingsOpen(false), []);
  const fileAction = (label: 'Open…' | 'Save') => {
    const menu = document.querySelector<HTMLButtonElement>('.file-chip');
    if (menu?.getAttribute('aria-expanded') !== 'true') menu?.click();
    requestAnimationFrame(() => [...document.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .find((button) => button.querySelector('span')?.textContent === label)?.click());
  };
  const saveAs = () => {
    const current = useDocumentStore.getState().filename;
    const requested = window.prompt('Save design as', current);
    if (!requested?.trim()) return;
    const filename = requested.trim().toLocaleLowerCase().endsWith('.cfg') ? requested.trim() : `${requested.trim()}.cfg`;
    useDocumentStore.getState().setFilename(filename);
    requestAnimationFrame(() => fileAction('Save'));
  };
  const paletteEntries = useMemo<PaletteEntry[]>(() => {
    const parameters = buildParameterPaletteEntries(family);
    const jobEntries: PaletteEntry[] = jobs.map((job) => ({
      id: `job-${job.id}`,
      kind: 'Jobs',
      label: job.label || `${String(job.config_summary.formula_type ?? 'job').toLowerCase()} ${job.id.slice(0, 6)}`,
      detail: job.has_results ? 'Show in Results' : `${job.status} · no results`,
      keywords: `${job.id} ${job.status}`,
      disabled: !job.has_results,
      run: () => { compareSelection.setPrimary(job.id); workspaceNavigation.activate('results'); },
    }));
    const commands: PaletteEntry[] = [
      { id: 'solve', kind: 'Commands', label: 'Solve', detail: solve.title, disabled: solve.disabled, run: solve.solve },
      { id: 'undo', kind: 'Commands', label: 'Undo', disabled: !canUndo, run: undo },
      { id: 'redo', kind: 'Commands', label: 'Redo', disabled: !canRedo, run: redo },
      { id: 'open', kind: 'Commands', label: 'Open', detail: 'Open a design file', run: () => fileAction('Open…') },
      { id: 'save', kind: 'Commands', label: 'Save', detail: 'Download the current design', run: () => fileAction('Save') },
      { id: 'save-as', kind: 'Commands', label: 'Save As', detail: 'Name and download a new copy', run: saveAs },
      { id: 'reset-layout', kind: 'Commands', label: 'Reset layout', run: onResetLayout },
      { id: 'dark-theme', kind: 'Commands', label: 'Dark theme', run: () => setTheme('dark') },
      { id: 'light-theme', kind: 'Commands', label: 'Light theme', run: () => setTheme('light') },
      { id: 'settings', kind: 'Commands', label: 'Settings', run: () => setSettingsOpen(true) },
      ...RESULT_PANEL_COUNTS.map((count) => ({ id: `results-${count}`, kind: 'Commands' as const, label: `Results: ${count} chart${count === 1 ? '' : 's'}`, keywords: `panel count layout`, run: () => preferencesStore.setChartCount(count) })),
    ];
    return [...parameters, ...jobEntries, ...commands];
  }, [canRedo, canUndo, family, jobs, onResetLayout, redo, solve, undo]);

  return <header className="topbar">
    <div className="brand"><BrandMark/><div><span className="brand-name">WAVEGUIDE GENERATOR</span><span className="brand-version">{__WG2_VERSION__} · local</span></div></div>
    <i className="v-separator" />
    <DesignFileMenu />
    <div className="button-group">
      <button className="icon-button" disabled={!canUndo} onClick={undo} title="Undo"><Icon name="undo"/></button>
      <button className="icon-button" disabled={!canRedo} onClick={redo} title="Redo"><Icon name="redo"/></button>
    </div>
    <CommandPalette entries={paletteEntries}/>
    <button className="solve-button" disabled={solve.disabled} title={solve.title} aria-busy={solve.submitting} onClick={solve.solve}><Icon name="play"/>Solve<kbd>⌘↵</kbd></button>
    <i className="v-separator" />
    <div className="theme-toggle" aria-label="Color theme">
      <button className={theme === 'dark' ? 'on' : ''} onClick={() => setTheme('dark')} aria-label="Dark theme" aria-pressed={theme === 'dark'}><Icon name="moon"/></button>
      <button className={theme === 'light' ? 'on' : ''} onClick={() => setTheme('light')} aria-label="Light theme" aria-pressed={theme === 'light'}><Icon name="sun"/></button>
    </div>
    <button className="icon-button" onClick={() => setSettingsOpen(true)} title="Settings" aria-label="Settings"><Icon name="settings"/></button>
    <button className="icon-button" onClick={onResetLayout} title="Reset layout" aria-label="Reset layout"><Icon name="layout"/></button>
    <span className="revision-chip" title="Design revision">r{revision}</span>
    <SettingsDialog open={settingsOpen} theme={theme} onThemeChange={setTheme} onClose={closeSettings}/>
  </header>;
}
