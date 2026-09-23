import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { jobsSocket } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { CAD_CONTROL_DESCRIPTORS, cadControlIsAvailable, cadControlMatchesQuery } from '../design/cadControlRegistry';
import { DesignFileMenu } from '../design/DesignFileMenu';
import { PARAMETER_REGISTRY, PARAMETER_SECTION_DEFINITIONS, fieldAppliesToFamily, fieldMatchesQuery, parameterSectionIsVisible, type ParameterTab } from '../design/parameterRegistry';
import { PARAMETRIC_CONTROL_DESCRIPTORS, parametricControlMatchesQuery } from '../design/parametricControlRegistry';
import { restoreParametricWorkingDesign } from '../jobs/showJobModel';
import { RESULT_PANEL_COUNTS, preferencesStore, runDisplayName, usePreferences } from '../prefs/preferences';
import { useCadReturnStore } from '../stores/cadReturn';
import { waveguideDefinitionAppliesNow } from '../stores/waveguideLink';
import { useCadOperationsStore } from '../stores/cadOperations';
import type { FusionCadStatus } from '../api/cadlink';
import { operationNeedsUser, solveAttention, useReadyRun } from './solveAttention';
import { noteExplicitNavigation } from './workspaceNavigation';
import { useDesignStore, type DesignDocument, type DesignFamily } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { workspaceModeStore, type WorkspaceMode } from '../stores/workspaceMode';
import { requestParameterReveal } from '../design/ParamPanel';
import { BrandMark, Icon } from './icons';
import { useSolveControl } from './JobsCoordinator';
import { cadLinkCoordinatorBridge } from './CadLinkCoordinator';
import { CommandPalette, type PaletteEntry } from './CommandPalette';
import { ExportDestinationDialog } from './ExportDestinationDialog';
import { ReportDialog } from './ReportDialog';
import { commandShortcutLabel } from './platformKeys';
import { SettingsDialog, type Theme } from './SettingsDialog';
import { subscribeSettingsRequests, type SettingsSection } from './settingsNavigation';
import { namespaceStorage } from '../stores/durableSettings';
import { workspaceNavigation } from './Workspace';
import { UpdateButton, UpdateDialog, useUpdateStatus } from './UpdateControl';
import { WindowControls } from './WindowControls';

const themeStorage = namespaceStorage('theme');

function initialTheme(): Theme {
  const saved = themeStorage.getItem('theme');
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
  workspaceNavigation.navigate(tab);
  // The request waits to be claimed, so it does not need to be timed to land
  // after the panel mounts — and not deferring it means the route still works
  // in a background tab, where animation frames never run.
  requestParameterReveal({ id, tab, query, target: 'parameter' });
}

export function revealCadControlFromPalette(id: string, tab: ParameterTab, query: string, fallbackId?: string): void {
  workspaceModeStore.setMode('cad');
  workspaceNavigation.navigate(tab);
  requestParameterReveal({ id, tab, query, target: 'control', fallbackId });
}

export function revealParametricControlFromPalette(id: string, tab: ParameterTab, query: string): void {
  workspaceModeStore.setMode('parametric');
  workspaceNavigation.navigate(tab);
  requestParameterReveal({ id, tab, query, target: 'control' });
}

export interface ParameterPaletteContext {
  mode?: WorkspaceMode;
  design?: DesignDocument;
  cadReturnReady?: boolean;
  waveguideLinked?: boolean;
}

/** Enter a workspace mode and route first-time CAD users to the workflow that
 * can make that mode usable. A prepared return stays in place because its CAD
 * controls and viewport are already available. Returning to Parametric first
 * restores the parametric working design if a CAD flow replaced it, so the
 * toggle never presents a CAD project's design as the user's own. */
export function activateWorkspaceMode(mode: WorkspaceMode): void {
  // The user's own switch: whatever was waiting to follow them no longer may.
  noteExplicitNavigation();
  if (mode === 'parametric') restoreParametricWorkingDesign();
  workspaceModeStore.setMode(mode);
  if (mode === 'cad' && !useCadReturnStore.getState().ingestRecord) {
    workspaceNavigation.activate('cadlink');
  }
}

export function buildParameterPaletteEntries(family?: DesignFamily, context: ParameterPaletteContext = {}): PaletteEntry[] {
  const mode = context.mode ?? 'parametric';
  const design = context.design ?? useDesignStore.getState().design;
  const cadReturnReady = context.cadReturnReady ?? Boolean(useCadReturnStore.getState().ingestRecord);
  const waveguideLinked = context.waveguideLinked ?? waveguideDefinitionAppliesNow();
  const parameterEntries: PaletteEntry[] = PARAMETER_REGISTRY
    .filter((field) => !family || fieldAppliesToFamily(field, family))
    // Section policy already decides what the rail can render. Reusing it here
    // prevents palette and rail from growing subtly different CAD mode rules.
    .filter((field) => {
      const section = parameterSectionByTitle.get(field.section);
      return !section || parameterSectionIsVisible(section, { mode, design, waveguideLinked });
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

/**
 * Where the CAD model on screen came from, and what WG can honestly say about
 * whether Fusion still matches it.
 *
 * It never claims the displayed model is Fusion's latest unless the add-in's
 * measurement is of the revision Fusion is at (`observationFreshness`
 * `current`, or `unknown`: an add-in older than the revision tokens, whose
 * heartbeat measured what it published -- see server/cadlink/fusion_status.py.
 * `unknown` is not an incompatible add-in). An older snapshot stays solvable on
 * purpose; this line only says so.
 */
export interface CadSourceLine {
  text: string;
  tone: 'info' | 'changed' | 'unverified';
  /** A refresh from Fusion is worth offering. */
  refresh: boolean;
}

export function cadSourceLine(status: FusionCadStatus | null | undefined, modelShown: boolean): CadSourceLine | null {
  if (!modelShown) return null;
  const base = 'Model loaded from Fusion';
  // WGLink with its automatic coordination off reports only when it runs a
  // command: WG holds its last report, and says so -- never "offline", never
  // "matches" -- and Refresh stays, reading Fusion when pressed.
  if (status?.statusObserved === false) {
    const at = status.observedAt ? new Date(status.observedAt) : null;
    const when = at && !Number.isNaN(at.getTime())
      ? ` ${at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
      : '';
    return {
      text: `${base} · Fusion last reported${when}, not observed since`,
      tone: status.fusionChangesAvailable ? 'changed' : 'unverified',
      refresh: true,
    };
  }
  if (!status?.running || status.state !== 'current' && status.state !== 'stale') {
    return { text: base, tone: 'info', refresh: false };
  }
  // Positive evidence of a difference stands whatever the observation's age:
  // over-reporting it costs a redundant refresh, never a wrong solve.
  if (status.fusionChangesAvailable) {
    return { text: `${base} · Newer CAD changes available`, tone: 'changed', refresh: true };
  }
  const freshness = status.observationFreshness ?? null;
  const measured = freshness === 'current' || freshness === 'unknown';
  if (!measured || !status.documentChangeDetectable) {
    return { text: `${base} · Live CAD freshness not verified`, tone: 'unverified', refresh: true };
  }
  return { text: `${base} · Matches Fusion`, tone: 'info', refresh: false };
}

/** The solve actions.
 *
 * Solve has one meaning: it solves the model and settings WG displays now. The
 * connection to Fusion never changes what it does or which button is primary;
 * the button, the shortcut and the palette's Solve are the same command.
 * Bringing newer geometry in from Fusion is its own explicit action on the
 * source line (and "Pull from Fusion & Solve" in the command palette).
 */
export function SolveActions() {
  const solve = useSolveControl();
  const mode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;
  const cadCoordinator = useSyncExternalStore(
    cadLinkCoordinatorBridge.subscribe,
    cadLinkCoordinatorBridge.getSnapshot,
    cadLinkCoordinatorBridge.getSnapshot,
  );
  const modelShown = useCadReturnStore((state) => Boolean(state.ingestRecord));
  const fusion = usePreferences().cadApplication === 'fusion360';
  const source = mode === 'cad' && fusion
    ? cadSourceLine(cadCoordinator.fusionStatus, modelShown)
    : null;

  return <>
    <button
      className="solve-button"
      disabled={solve.disabled}
      title={solve.title}
      aria-busy={solve.submitting}
      // Keep the frequency editor focused until onClick can commit its draft.
      // A normal mouse-down blurs it first and disables this button mid-click.
      onMouseDown={(event) => {
        if (document.activeElement?.matches('[data-crossover-frequency]')) event.preventDefault();
      }}
      onClick={solve.solve}
    ><Icon name="play"/>{solve.label}<kbd>{commandShortcutLabel('↵')}</kbd></button>
    {solve.notice && <span
      className={`solve-notice solve-notice-${solve.notice.tone}`}
      role="status"
      /* Polite, not an alert: the substitution has already been made and the
         solve can proceed, so this must not interrupt what the user is doing. */
      aria-live="polite"
    >{solve.notice.text}</span>}
    {source && <span className={`cad-source-line cad-source-${source.tone}`} role="status" title={source.text}>
      <span>{source.text}</span>
      {source.refresh && <button
        type="button"
        disabled={cadCoordinator.pullingFromFusion}
        aria-busy={cadCoordinator.pullingFromFusion}
        title="Bring Fusion's current geometry into WG without solving it"
        onClick={() => { void cadCoordinator.pullFromFusion().catch(() => undefined); }}
      >{cadCoordinator.pullingFromFusion ? 'Waiting for Fusion…' : 'Refresh'}</button>}
    </span>}
    <AttentionNotices/>
  </>;
}

/**
 * The route to something that finished out of sight.
 *
 * A request waiting for the user is announced wherever they are -- including
 * Parametric mode, where the CAD Link panel is not in the dock at all -- and
 * the notice opens it. Choosing that is the user's explicit act, so it may
 * change the workspace mode; nothing here changes it by itself. A solve whose
 * results arrived after the user navigated elsewhere is only indicated.
 */
export function AttentionNotices() {
  useSyncExternalStore(workspaceNavigation.subscribe, workspaceNavigation.getSnapshot, workspaceNavigation.getSnapshot);
  const mode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;
  const operations = useCadOperationsStore((state) => state.operations);
  const unseenRefusals = useCadOperationsStore((state) => state.unseenRefusals);
  const readyRun = useReadyRun();
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const waiting = Object.values(operations).filter(operationNeedsUser);
  const showWaiting = waiting.length > 0 && !(mode === 'cad' && workspaceNavigation.isVisible('cadlink'));
  const showReady = readyRun !== null && !workspaceNavigation.isVisible('results');
  // A request WG took and refused has no row to wait in; this is its route.
  const showRefused = unseenRefusals > 0 && !(mode === 'cad' && workspaceNavigation.isVisible('cadlink'));
  const readyJob = readyRun ? jobs.find((job) => job.id === readyRun) ?? null : null;
  const first = waiting[0];
  const waitingLabel = !first
    ? ''
    : waiting.length > 1
      ? `${waiting.length} CAD requests need you`
      : first.kind === 'prepare_and_solve' ? 'A solve is waiting for you' : 'A CAD update needs recovery';
  return <>
    {showWaiting && <button
      type="button"
      className="attention-notice attention-waiting"
      title={first?.message ?? waitingLabel}
      onClick={() => {
        if (mode !== 'cad') activateWorkspaceMode('cad');
        workspaceNavigation.navigate('cadlink');
      }}
    ><i/>{waitingLabel} · Show</button>}
    {showRefused && <button
      type="button"
      className="attention-notice attention-refused"
      title="WG took a request from Fusion and could not accept it. The CAD Link panel says why."
      onClick={() => {
        if (mode !== 'cad') activateWorkspaceMode('cad');
        workspaceNavigation.navigate('cadlink');
      }}
    ><i/>{unseenRefusals === 1 ? 'A CAD request was refused' : `${unseenRefusals} CAD requests were refused`} · Show</button>}
    {showReady && <button
      type="button"
      className="attention-notice attention-ready"
      title={`${readyJob ? runDisplayName(readyJob) : 'The run you solved'} finished. It is the selected result.`}
      onClick={() => {
        solveAttention.dismissReady();
        workspaceNavigation.navigate('results');
      }}
    ><i/>Results ready · Show</button>}
  </>;
}

export function WorkspaceModeSwitch() {
  const mode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;

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
    {option('cad', 'CAD Link')}
  </div>;
}

export function workspaceModePaletteEntries(): PaletteEntry[] {
  return [
    { id: 'mode-parametric', kind: 'Commands', label: 'Mode: Parametric', run: () => activateWorkspaceMode('parametric') },
    { id: 'mode-cad-link', kind: 'Commands', label: 'Mode: CAD Link', run: () => activateWorkspaceMode('cad') },
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
  const cadCoordinator = useSyncExternalStore(
    cadLinkCoordinatorBridge.subscribe,
    cadLinkCoordinatorBridge.getSnapshot,
    cadLinkCoordinatorBridge.getSnapshot,
  );
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>();
  const [updateOpen, setUpdateOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const update = useUpdateStatus();
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const temporal = useDesignStore.temporal.getState();
  const canUndo = temporal.pastStates.length > 0 || Boolean(useDesignStore.getState().dragSnapshot);
  const canRedo = temporal.futureStates.length > 0;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    themeStorage.setItem('theme', theme);
  }, [theme]);

  useEffect(() => subscribeSettingsRequests((section) => {
    setSettingsSection(section);
    setSettingsOpen(true);
  }), []);

  const showSettings = useCallback(() => { setSettingsSection(undefined); setSettingsOpen(true); }, []);
  const closeSettings = useCallback(() => { setSettingsOpen(false); setSettingsSection(undefined); }, []);
  const fileAction = (label: 'Open…' | 'Export a copy') => {
    const menu = document.querySelector<HTMLButtonElement>('.file-chip');
    if (menu?.getAttribute('aria-expanded') !== 'true') menu?.click();
    requestAnimationFrame(() => [...document.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .find((button) => button.querySelector('span')?.textContent === label)?.click());
  };
  // Naming a copy renames the design, and the file follows: the name is the one
  // thing WG keeps, and the `.cfg` is derived from it. Typing an extension is
  // therefore not part of the name -- it is stripped rather than stored.
  const exportCopyAs = () => {
    const requested = window.prompt('Export a copy as', useDocumentStore.getState().designName);
    if (!requested?.trim()) return;
    useDocumentStore.getState().setDesignName(requested.trim().replace(/\.(cfg|txt|mwg)$/i, ''));
    requestAnimationFrame(() => fileAction('Export a copy'));
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
      run: () => { compareSelection.setPrimary(job.id); workspaceNavigation.navigate('results'); },
    }));
    const commands: PaletteEntry[] = [
      { id: 'solve', kind: 'Commands', label: solve.label, detail: solve.title, disabled: solve.disabled, run: solve.solve },
      // The pull was reachable only from inside the CAD Link panel.
      {
        id: 'cad-pull-solve',
        kind: 'Commands',
        label: 'Pull from Fusion & Solve',
        detail: 'Request the current Fusion geometry, prepare it, and solve',
        disabled: workspaceMode !== 'cad' || !cadCoordinator.fusionStatus?.running,
        run: () => { void cadLinkCoordinatorBridge.getSnapshot().pullAndSolve().catch(() => undefined); },
      },
      {
        id: 'cad-pull',
        kind: 'Commands',
        label: 'Refresh geometry from Fusion',
        detail: 'Request the current Fusion geometry without solving',
        disabled: workspaceMode !== 'cad' || !cadCoordinator.fusionStatus?.running,
        run: () => { void cadLinkCoordinatorBridge.getSnapshot().pullFromFusion().catch(() => undefined); },
      },
      { id: 'undo', kind: 'Commands', label: 'Undo', disabled: !canUndo, run: undo },
      { id: 'redo', kind: 'Commands', label: 'Redo', disabled: !canRedo, run: redo },
      { id: 'open', kind: 'Commands', label: 'Open', detail: 'Open a design file', run: () => fileAction('Open…') },
      { id: 'save', kind: 'Commands', label: 'Export a copy', detail: 'Write a .cfg copy to the output folder without changing the editor’s saved state', run: () => fileAction('Export a copy') },
      { id: 'save-as', kind: 'Commands', label: 'Export a copy as…', detail: 'Rename the current design and export a copy', run: exportCopyAs },
      { id: 'reset-layout', kind: 'Commands', label: 'Reset layout', run: onResetLayout },
      { id: 'dark-theme', kind: 'Commands', label: 'Dark theme', run: () => setTheme('dark') },
      { id: 'light-theme', kind: 'Commands', label: 'Light theme', run: () => setTheme('light') },
      ...workspaceModePaletteEntries(),
      { id: 'settings', kind: 'Commands', label: 'Settings', run: showSettings },
      { id: 'report-problem', kind: 'Commands', label: 'Report a problem', detail: 'Collect the logs and this machine’s solver status into one file', keywords: 'bug log diagnostics issue feedback suggestion support', run: () => setReportOpen(true) },
      { id: 'application-update', kind: 'Commands', label: update.data?.availability === 'available' ? `Update WG to ${update.data.release?.version ?? 'latest'}` : 'Application update', detail: update.data?.availability === 'available' ? 'Update available' : `Version ${__WG2_VERSION__}`, keywords: 'version release upgrade', run: () => setUpdateOpen(true) },
      ...RESULT_PANEL_COUNTS.map((count) => ({ id: `results-${count}`, kind: 'Commands' as const, label: `Results: ${count} chart${count === 1 ? '' : 's'}`, keywords: `panel count layout`, run: () => preferencesStore.setChartCount(count) })),
    ];
    return [...parameters, ...jobEntries, ...commands];
  }, [cadCoordinator.fusionStatus?.running, cadReturnReady, canRedo, canUndo, design, family, jobs, onResetLayout, redo, showSettings, solve, undo, update.data?.availability, update.data?.release?.version, workspaceMode]);

  return <header className="topbar">
    <WindowControls side="leading"/>
    {/* Everything except the window controls, in one shrinkable box.
      * The controls are the only way to minimize, maximize or close a
      * window whose caption has been removed, so they must never be what
      * a crowded top bar pushes out of the window -- and as the last item
      * of an overflowing flex row that is exactly what they were. This
      * wrapper is `min-width: 0`, so the row shrinks here instead. */}
    <div className="topbar-main">
      <div className="brand"><BrandMark/><div><span className="brand-name">WAVEGUIDE GENERATOR</span><UpdateButton snapshot={update} open={updateOpen} onOpen={() => setUpdateOpen(true)}/></div></div>
      <i className="v-separator" />
      <DesignFileMenu />
      {/* Dropped last, and only at the narrowest window the launcher allows --
        * 1100 px at 150% display scaling is 733 CSS px, and the bar does not
        * fit there even with every `topbar-utility` gone. Undo and redo are
        * the only remaining controls that have both a keyboard shortcut and a
        * palette entry, which is what makes them the ones to go. */}
      <div className="button-group topbar-history">
        <button className="icon-button" disabled={!canUndo} onClick={undo} title="Undo"><Icon name="undo"/></button>
        <button className="icon-button" disabled={!canRedo} onClick={redo} title="Redo"><Icon name="redo"/></button>
      </div>
      <CommandPalette entries={paletteEntries}/>
      <WorkspaceModeSwitch/>
      <SolveActions/>
      <i className="v-separator" />
      {/* `topbar-utility` marks a control the bar may drop when it runs out of
        * room. Every one of them is also a command-palette entry, so dropping
        * it costs a keystroke rather than the feature -- and the alternative
        * is not "keep it", it is "draw it on the same pixels as the window
        * controls", which is how pressing the reset-layout icon came to close
        * the window. `WindowControls.test.tsx` holds each of these to having a
        * palette route out. */}
      <div className="theme-toggle topbar-utility" aria-label="Color theme">
        <button className={theme === 'dark' ? 'on' : ''} onClick={() => setTheme('dark')} aria-label="Dark theme" aria-pressed={theme === 'dark'}><Icon name="moon"/></button>
        <button className={theme === 'light' ? 'on' : ''} onClick={() => setTheme('light')} aria-label="Light theme" aria-pressed={theme === 'light'}><Icon name="sun"/></button>
      </div>
      <button className="icon-button topbar-utility" onClick={() => setReportOpen(true)} title="Report a problem" aria-label="Report a problem"><Icon name="info"/></button>
      <button className="icon-button topbar-utility" onClick={showSettings} title="Settings" aria-label="Settings"><Icon name="settings"/></button>
      <button className="icon-button topbar-utility" onClick={onResetLayout} title="Reset layout" aria-label="Reset layout"><Icon name="layout"/></button>
      <span className="revision-chip" title="Design revision">r{revision}</span>
    </div>
    <WindowControls side="trailing"/>
    <SettingsDialog open={settingsOpen} theme={theme} focusSection={settingsSection} onThemeChange={setTheme} onClose={closeSettings}/>
    <UpdateDialog open={updateOpen} snapshot={update} onRefresh={update.refresh} onClose={() => setUpdateOpen(false)}/>
    <ReportDialog open={reportOpen} jobs={jobs} onClose={() => setReportOpen(false)}/>
    {/* Mounted once, for every export surface in the window: the File menu, the
      * Results panel and each run's export menu all ask this one dialog. */}
    <ExportDestinationDialog/>
  </header>;
}
