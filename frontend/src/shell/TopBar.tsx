import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { jobsSocket } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { CAD_CONTROL_DESCRIPTORS, cadControlIsAvailable, cadControlMatchesQuery } from '../design/cadControlRegistry';
import { DesignFileMenu } from '../design/DesignFileMenu';
import { PARAMETER_REGISTRY, PARAMETER_SECTION_DEFINITIONS, fieldAppliesToFamily, fieldMatchesQuery, parameterSectionIsVisible, type ParameterTab } from '../design/parameterRegistry';
import { PARAMETRIC_CONTROL_DESCRIPTORS, parametricControlMatchesQuery } from '../design/parametricControlRegistry';
import { RESULT_PANEL_COUNTS, preferencesStore, runDisplayName, usePreferences, type Preferences } from '../prefs/preferences';
import { useCadReturnStore } from '../stores/cadReturn';
import { useDesignStore, type DesignDocument, type DesignFamily } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { workspaceModeStore, type WorkspaceMode } from '../stores/workspaceMode';
import { requestParameterReveal } from '../design/ParamPanel';
import { BrandMark, Icon } from './icons';
import { useSolveControl } from './JobsCoordinator';
import { CommandPalette, type PaletteEntry } from './CommandPalette';
import { commandShortcutLabel } from './platformKeys';
import { SettingsDialog, type Theme } from './SettingsDialog';
import { subscribeSettingsRequests, type SettingsSection } from './settingsNavigation';
import { workspaceNavigation } from './Workspace';
import { UpdateButton, UpdateDialog, useUpdateStatus } from './UpdateControl';

const THEME_KEY = 'wg2.theme';

function initialTheme(): Theme {
  const saved = localStorage.getItem(THEME_KEY);
  return saved === 'light' ? 'light' : 'dark';
}

const parameterTabBySection = new Map(PARAMETER_SECTION_DEFINITIONS.map((section) => [section.title, section.tab]));
const parameterSectionByTitle = new Map(PARAMETER_SECTION_DEFINITIONS.map((section) => [section.title, section]));

export function revealParameterFromPalette(
  id: string,
  tab: ParameterTab,
  query: string,
  owningMode: WorkspaceMode = 'parametric',
): void {
  // A palette entry can outlive the mode in which its result list was built.
  // Establish the owning workspace before the dock panel is activated or the
  // still-mounted panel could claim a request for controls it is about to hide.
  workspaceModeStore.setMode(owningMode);
  workspaceNavigation.activate(tab);
  // The request waits to be claimed, so it does not need to be timed to land
  // after the panel mounts — and not deferring it means the route still works
  // in a background tab, where animation frames never run.
  requestParameterReveal({ id, tab, query, target: 'parameter' });
}

export function revealCadControlFromPalette(id: string, tab: ParameterTab, query: string, fallbackId?: string): void {
  workspaceModeStore.setMode('cad');
  workspaceNavigation.activate(tab);
  requestParameterReveal({ id, tab, query, target: 'control', fallbackId });
}

export function revealParametricControlFromPalette(id: string, tab: ParameterTab, query: string): void {
  workspaceModeStore.setMode('parametric');
  workspaceNavigation.activate(tab);
  requestParameterReveal({ id, tab, query, target: 'control' });
}

export interface ParameterPaletteContext {
  mode?: WorkspaceMode;
  design?: DesignDocument;
  cadReturnReady?: boolean;
}

/** Enter a workspace mode and route first-time CAD users to the workflow that
 * can make that mode usable. A prepared return stays in place because its CAD
 * controls and viewport are already available. */
export function activateWorkspaceMode(mode: WorkspaceMode): void {
  workspaceModeStore.setMode(mode);
  if (mode === 'cad' && !useCadReturnStore.getState().ingestRecord) {
    workspaceNavigation.activate('cadlink');
  }
}

export function buildParameterPaletteEntries(family?: DesignFamily, context: ParameterPaletteContext = {}): PaletteEntry[] {
  const mode = context.mode ?? 'parametric';
  const design = context.design ?? useDesignStore.getState().design;
  const cadReturnReady = context.cadReturnReady ?? Boolean(useCadReturnStore.getState().ingestRecord);
  const parameterEntries: PaletteEntry[] = PARAMETER_REGISTRY
    .filter((field) => !family || fieldAppliesToFamily(field, family))
    // Section policy already decides what the rail can render. Reusing it here
    // prevents palette and rail from growing subtly different CAD mode rules.
    .filter((field) => {
      const section = parameterSectionByTitle.get(field.section);
      return !section || parameterSectionIsVisible(section, mode, design);
    })
    .map((field) => {
      const tab = parameterTabBySection.get(field.section) ?? 'geometry';
      return {
        id: `parameter-${field.id}`,
        kind: 'Parameters',
        label: field.label,
        // The ATH symbol and the legacy config key are usually the same string
        // (R, a, a0, k, m, b, r, q), and joining them unconditionally printed
        // every one of those parameters as "R · R".
        detail: [...new Set([field.symbol, field.legacyKey].filter(Boolean))].join(' · '),
        keywords: [field.id, field.path, field.symbol, field.legacyKey].filter(Boolean).join(' '),
        matches: (query) => fieldMatchesQuery(field, query) || Boolean(field.symbol?.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())),
        // The same formula section can belong to CAD mode. Capture the mode that
        // made this entry visible so executing it never jumps to another owner.
        run: () => revealParameterFromPalette(field.id, tab, field.label, mode),
      };
    });
  if (mode !== 'cad') {
    const parametricControls: PaletteEntry[] = PARAMETRIC_CONTROL_DESCRIPTORS.map((descriptor) => ({
      id: `parametric-control-${descriptor.id}`,
      kind: 'Parameters',
      label: descriptor.label,
      detail: descriptor.section,
      keywords: [descriptor.id, descriptor.section, ...descriptor.keywords].join(' '),
      matches: (query) => parametricControlMatchesQuery(descriptor, query),
      run: () => revealParametricControlFromPalette(descriptor.reveal.id, descriptor.tab, descriptor.label),
    }));
    return [...parameterEntries, ...parametricControls];
  }

  const cadEntries: PaletteEntry[] = CAD_CONTROL_DESCRIPTORS
    .filter((descriptor) => cadControlIsAvailable(descriptor, cadReturnReady))
    .map((descriptor) => ({
      id: `cad-control-${descriptor.id}`,
      kind: 'Parameters',
      label: descriptor.label,
      detail: descriptor.section,
      keywords: [descriptor.id, descriptor.section, ...descriptor.keywords].join(' '),
      matches: (query) => cadControlMatchesQuery(descriptor, query),
      run: () => revealCadControlFromPalette(descriptor.reveal.id, descriptor.tab, descriptor.label, descriptor.reveal.fallbackId),
    }));
  return [...parameterEntries, ...cadEntries];
}

export function WorkspaceModeSwitch() {
  const preferences = usePreferences();
  const mode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;
  const fusion = preferences.cadApplication === 'fusion360';

  const option = (value: WorkspaceMode, label: string) => <button
    type="button"
    role="radio"
    className={mode === value ? 'on' : ''}
    aria-checked={mode === value}
    title={`Use ${label} workspace mode`}
    onClick={() => activateWorkspaceMode(value)}
  >{label}</button>;

  return <div className="theme-toggle workspace-mode-toggle" role="radiogroup" aria-label="Workspace mode">
    {option('parametric', 'Parametric')}
    {option('cad', fusion ? 'Fusion CAD' : 'CAD')}
  </div>;
}

export function workspaceModePaletteEntries(cadApplication: Preferences['cadApplication']): PaletteEntry[] {
  const onshape = cadApplication === 'onshape';
  return [
    { id: 'mode-parametric', kind: 'Commands', label: 'Mode: Parametric', run: () => activateWorkspaceMode('parametric') },
    { id: 'mode-fusion-cad', kind: 'Commands', label: onshape ? 'Mode: CAD' : 'Mode: Fusion CAD', run: () => activateWorkspaceMode('cad') },
  ];
}

export function TopBar({ onResetLayout }: { onResetLayout: () => void }) {
  const solve = useSolveControl();
  const undo = useDesignStore((state) => state.undo);
  const redo = useDesignStore((state) => state.redo);
  const revision = useDesignStore((state) => state.designRevision);
  const design = useDesignStore((state) => state.design);
  const family = design.formula;
  const workspaceMode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;
  const cadReturnReady = useCadReturnStore((state) => Boolean(state.ingestRecord));
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>();
  const [updateOpen, setUpdateOpen] = useState(false);
  const update = useUpdateStatus();
  const preferences = usePreferences();
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const temporal = useDesignStore.temporal.getState();
  const canUndo = temporal.pastStates.length > 0 || Boolean(useDesignStore.getState().dragSnapshot);
  const canRedo = temporal.futureStates.length > 0;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => subscribeSettingsRequests((section) => {
    setSettingsSection(section);
    setSettingsOpen(true);
  }), []);

  const showSettings = useCallback(() => { setSettingsSection(undefined); setSettingsOpen(true); }, []);
  const closeSettings = useCallback(() => { setSettingsOpen(false); setSettingsSection(undefined); }, []);
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
    const parameters = buildParameterPaletteEntries(family, { mode: workspaceMode, design, cadReturnReady });
    const jobEntries: PaletteEntry[] = jobs.map((job) => ({
      id: `job-${job.id}`,
      kind: 'Jobs',
      label: runDisplayName(job),
      detail: job.has_results ? 'Show in Results' : `${job.status} · no results`,
      keywords: `${job.id} ${job.status}`,
      disabled: !job.has_results,
      run: () => { compareSelection.setPrimary(job.id); workspaceNavigation.activate('results'); },
    }));
    const commands: PaletteEntry[] = [
      { id: 'solve', kind: 'Commands', label: solve.label, detail: solve.title, disabled: solve.disabled, run: solve.solve },
      { id: 'undo', kind: 'Commands', label: 'Undo', disabled: !canUndo, run: undo },
      { id: 'redo', kind: 'Commands', label: 'Redo', disabled: !canRedo, run: redo },
      { id: 'open', kind: 'Commands', label: 'Open', detail: 'Open a design file', run: () => fileAction('Open…') },
      { id: 'save', kind: 'Commands', label: 'Save', detail: 'Download the current design', run: () => fileAction('Save') },
      { id: 'save-as', kind: 'Commands', label: 'Save As', detail: 'Name and download a new copy', run: saveAs },
      { id: 'reset-layout', kind: 'Commands', label: 'Reset layout', run: onResetLayout },
      { id: 'dark-theme', kind: 'Commands', label: 'Dark theme', run: () => setTheme('dark') },
      { id: 'light-theme', kind: 'Commands', label: 'Light theme', run: () => setTheme('light') },
      ...workspaceModePaletteEntries(preferences.cadApplication),
      { id: 'settings', kind: 'Commands', label: 'Settings', run: showSettings },
      { id: 'application-update', kind: 'Commands', label: update.data?.availability === 'available' ? `Update WG to ${update.data.release?.version ?? 'latest'}` : 'Application update', detail: update.data?.availability === 'available' ? 'Update available' : `Version ${__WG2_VERSION__}`, keywords: 'version release upgrade', run: () => setUpdateOpen(true) },
      ...RESULT_PANEL_COUNTS.map((count) => ({ id: `results-${count}`, kind: 'Commands' as const, label: `Results: ${count} chart${count === 1 ? '' : 's'}`, keywords: `panel count layout`, run: () => preferencesStore.setChartCount(count) })),
    ];
    return [...parameters, ...jobEntries, ...commands];
  }, [cadReturnReady, canRedo, canUndo, design, family, jobs, onResetLayout, preferences.cadApplication, redo, showSettings, solve, undo, update.data?.availability, update.data?.release?.version, workspaceMode]);

  return <header className="topbar">
    <div className="brand"><BrandMark/><div><span className="brand-name">WAVEGUIDE GENERATOR</span><UpdateButton snapshot={update} open={updateOpen} onOpen={() => setUpdateOpen(true)}/></div></div>
    <i className="v-separator" />
    <DesignFileMenu />
    <div className="button-group">
      <button className="icon-button" disabled={!canUndo} onClick={undo} title="Undo"><Icon name="undo"/></button>
      <button className="icon-button" disabled={!canRedo} onClick={redo} title="Redo"><Icon name="redo"/></button>
    </div>
    <CommandPalette entries={paletteEntries}/>
    <WorkspaceModeSwitch/>
    <button className="solve-button" disabled={solve.disabled} title={solve.title} aria-busy={solve.submitting} onClick={solve.solve}><Icon name="play"/>{solve.label}<kbd>{commandShortcutLabel('↵')}</kbd></button>
    <i className="v-separator" />
    <div className="theme-toggle" aria-label="Color theme">
      <button className={theme === 'dark' ? 'on' : ''} onClick={() => setTheme('dark')} aria-label="Dark theme" aria-pressed={theme === 'dark'}><Icon name="moon"/></button>
      <button className={theme === 'light' ? 'on' : ''} onClick={() => setTheme('light')} aria-label="Light theme" aria-pressed={theme === 'light'}><Icon name="sun"/></button>
    </div>
    <button className="icon-button" onClick={showSettings} title="Settings" aria-label="Settings"><Icon name="settings"/></button>
    <button className="icon-button" onClick={onResetLayout} title="Reset layout" aria-label="Reset layout"><Icon name="layout"/></button>
    <span className="revision-chip" title="Design revision">r{revision}</span>
    <SettingsDialog open={settingsOpen} theme={theme} focusSection={settingsSection} onThemeChange={setTheme} onClose={closeSettings}/>
    <UpdateDialog open={updateOpen} snapshot={update} onRefresh={update.refresh} onClose={() => setUpdateOpen(false)}/>
  </header>;
}
