import { useEffect, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import {
  DockviewComponent,
  Orientation,
  type DockviewApi,
  type GroupPanelPartInitParameters,
  type IContentRenderer,
  type SerializedDockview,
} from 'dockview';
import { ParamPanel } from '../design/ParamPanel';
import { AppQueryProvider } from '../queryClient';
import { CadLinkPanel } from './CadLinkPanel';
import { JobsPanel } from './JobsPanel';
import { ResultsPanel } from './ResultsPanel';
import { ViewportPanel } from './ViewportPanel';
import { dockviewPanelVisibility, PanelVisibilityContext } from './panelVisibility';
import { workspaceModeStore, type WorkspaceMode } from '../stores/workspaceMode';
import { namespaceStorage } from '../stores/durableSettings';
import { bindWorkspaceNavigation, type WorkspacePanel } from './workspaceNavigation';
export { workspaceNavigation } from './workspaceNavigation';

export const LEGACY_LAYOUT_KEY = 'wg2.dockview.layout.v1';
export const LAYOUT_KEY = 'wg2.dockview.layout.v4';
/** Which responsive arrangement LAYOUT_KEY was saved for. See restoreDisposition. */
export const LAYOUT_MODE_KEY = 'wg2.dockview.mode.v4';
const layoutStorage = namespaceStorage('dockviewLayout');
const layoutModeStorage = namespaceStorage('dockviewMode');

const components = {
  geometry: () => <ParamPanel tab="geometry" />,
  simulation: () => <ParamPanel tab="simulation" />,
  viewport: ViewportPanel,
  results: ResultsPanel,
  jobs: JobsPanel,
  cadlink: CadLinkPanel,
};

type ComponentName = keyof typeof components;

export class ReactPanelRenderer implements IContentRenderer {
  readonly element = document.createElement('div');
  private root: Root | null = null;
  private generation = 0;
  private teardown = Promise.resolve();

  constructor(private readonly name: ComponentName) {
    this.element.className = `dock-panel-content dock-panel-${name}`;
  }

  init(parameters: GroupPanelPartInitParameters): void {
    const Component = components[this.name];
    // The panel API is the only thing that knows this panel is behind a
    // sibling tab; the element stays laid out and measurable either way.
    const visibility = dockviewPanelVisibility(parameters.api);
    const generation = ++this.generation;
    void this.teardown.then(() => {
      if (generation !== this.generation) return;
      const root = createRoot(this.element);
      this.root = root;
      root.render(<AppQueryProvider><PanelVisibilityContext.Provider value={visibility}><Component /></PanelVisibilityContext.Provider></AppQueryProvider>);
    });
  }

  dispose(): void {
    const root = this.root;
    this.root = null;
    ++this.generation;
    this.teardown = this.teardown.then(() => new Promise<void>((resolve) => {
      queueMicrotask(() => {
        root?.unmount();
        resolve();
      });
    }));
  }
}

/**
 * Wide enough for a parameter name beside the control column.
 *
 * The rail is a label and a --field-w control on one line, so its width is the
 * one number that decides whether "Throat radius (r0)" reads as itself or as
 * "Throat rad...". When the number box grew to the select's width to give the
 * rail a single control edge, this grew by the same 46px, so the labels kept
 * the room they had.
 */
const PARAMETERS_WIDTH = 346;
const JOBS_WIDTH = 320;
const RESULTS_HEIGHT = 340;
const COMPACT_BREAKPOINT = 800;
const MEDIUM_BREAKPOINT = 1100;

export type LayoutMode = 'compact' | 'medium' | 'wide';

/** The responsive arrangement a dock of this width gets. */
export function layoutMode(width: number): LayoutMode {
  return width < COMPACT_BREAKPOINT ? 'compact' : width < MEDIUM_BREAKPOINT ? 'medium' : 'wide';
}

/**
 * Whether a saved layout can be restored into the window that is opening now.
 *
 * The dock rebuilds itself when a live resize crosses a breakpoint, but that
 * only covers a window being dragged. Nothing covered the layout being *saved*
 * in one arrangement and *restored* in another: narrow the window once and the
 * compact single-group arrangement is persisted, and every later session
 * reopened it on a full-width monitor -- five panels stacked into one tab strip
 * with no indication why, and no way back except finding Reset layout.
 *
 * A layout saved before this key existed has no recorded mode. That is treated
 * as restorable, because the alternative is silently discarding the arrangement
 * of every existing install on first upgrade.
 */
export function restoreDisposition(savedMode: string | null, width: number): 'restore' | 'rebuild' {
  if (!savedMode) return 'restore';
  return savedMode === layoutMode(width) ? 'restore' : 'rebuild';
}
const FALLBACK_WIDTH = 1440;
const FALLBACK_HEIGHT = 900;
/** Top bar plus status bar, excluded when guessing the dock size from the window. */
const WORKSPACE_CHROME_HEIGHT = 84;

export function createDefaultLayout(width: number, height: number, mode: WorkspaceMode = 'parametric'): SerializedDockview {
  const layoutWidth = Math.max(1, Math.round(width) || FALLBACK_WIDTH);
  const layoutHeight = Math.max(1, Math.round(height) || FALLBACK_HEIGHT);
  const group = (id: string, views: ComponentName[], activeView: ComponentName = views[0]) => ({ id: `${id}-group`, views, activeView });
  const cadLinkViews: ComponentName[] = mode === 'cad' ? ['cadlink'] : [];
  const panels = {
    geometry: { id: 'geometry', contentComponent: 'geometry', title: 'Geometry' },
    simulation: { id: 'simulation', contentComponent: 'simulation', title: 'Simulation' },
    viewport: { id: 'viewport', contentComponent: 'viewport', title: 'Viewport' },
    results: { id: 'results', contentComponent: 'results', title: 'Results' },
    jobs: { id: 'jobs', contentComponent: 'jobs', title: 'Jobs' },
    ...(mode === 'cad' ? { cadlink: { id: 'cadlink', contentComponent: 'cadlink', title: 'CAD Link' } } : {}),
  };

  if (layoutWidth < COMPACT_BREAKPOINT) {
    return {
      grid: {
        width: layoutWidth,
        height: layoutHeight,
        orientation: Orientation.HORIZONTAL,
        root: {
          type: 'branch',
          size: layoutHeight,
          data: [{ type: 'leaf', size: layoutWidth, data: group('workspace', ['geometry', 'simulation', 'viewport', 'results', 'jobs', ...cadLinkViews], 'viewport') }],
        },
      },
      panels,
      activeGroup: 'viewport',
    };
  }

  if (layoutWidth < MEDIUM_BREAKPOINT) {
    const parametersWidth = 326;
    const contentWidth = layoutWidth - parametersWidth;
    const resultsHeight = Math.min(300, Math.max(220, Math.round(layoutHeight * .38)));
    return {
      grid: {
        width: layoutWidth,
        height: layoutHeight,
        orientation: Orientation.HORIZONTAL,
        root: {
          type: 'branch',
          size: layoutHeight,
          data: [
            { type: 'leaf', size: parametersWidth, data: group('parameters', ['geometry', 'simulation'], 'geometry') },
            {
              type: 'branch',
              size: contentWidth,
              data: [
                { type: 'leaf', size: layoutHeight - resultsHeight, data: group('viewport', ['viewport']) },
                { type: 'leaf', size: resultsHeight, data: group('analysis', ['results', 'jobs', ...cadLinkViews], 'results') },
              ],
            },
          ],
        },
      },
      panels,
      activeGroup: 'viewport',
    };
  }

  const viewportWidth = layoutWidth - PARAMETERS_WIDTH - JOBS_WIDTH;
  const resultsHeight = Math.min(RESULTS_HEIGHT, Math.max(240, Math.round(layoutHeight * .38)));
  const viewportHeight = layoutHeight - resultsHeight;
  return {
    grid: {
      width: layoutWidth,
      height: layoutHeight,
      orientation: Orientation.HORIZONTAL,
      root: {
        type: 'branch',
        size: layoutHeight,
        data: [
          { type: 'leaf', size: PARAMETERS_WIDTH, data: group('parameters', ['geometry', 'simulation'], 'geometry') },
          {
            type: 'branch',
            size: viewportWidth,
            data: [
              { type: 'leaf', size: viewportHeight, data: group('viewport', ['viewport']) },
              { type: 'leaf', size: resultsHeight, data: group('results', ['results']) },
            ],
          },
          { type: 'leaf', size: JOBS_WIDTH, data: group('jobs', ['jobs', ...cadLinkViews], 'jobs') },
        ],
      },
    },
    panels,
    activeGroup: 'viewport',
  };
}

export function measureHost(host: HTMLElement | null, api: DockviewApi | null): [number, number] {
  const bounds = host?.getBoundingClientRect();
  return [
    host?.clientWidth || bounds?.width || api?.width || 0,
    host?.clientHeight || bounds?.height || api?.height || 0,
  ];
}

export function applyDefaultLayout(
  api: DockviewApi,
  width: number,
  height: number,
  mode: WorkspaceMode = workspaceModeStore.getSnapshot().mode,
): void {
  api.fromJSON(createDefaultLayout(width, height, mode));
  // Deserializing a layout leaves dockview believing it is whatever size it
  // last measured, which collapses every panel to its 100px minimum and never
  // recovers, so state the size this layout was built for.
  api.layout(width, height);
}

/** Whether a measurement is big enough to be a real window rather than mid-CSS noise. */
export function isTrustworthySize([width, height]: [number, number]): boolean {
  return width >= 320 && height >= 320;
}

/**
 * Best guess at the dock's eventual size when the host cannot be measured yet.
 *
 * The host really does report 10x100 while the surrounding CSS resolves, and
 * the seed pins the 300px/320px side panels to whatever size it is given —
 * a fixed 1440x900 guess leaves a 300px panel at 533px on a 2560px monitor,
 * because later resizes scale the dock rather than rebuild it. The window is
 * available synchronously and is within the top bar's height of the truth.
 */
export function seedSize(measured: [number, number]): [number, number] {
  if (isTrustworthySize(measured)) return measured;
  const fromWindow: [number, number] = typeof window === 'undefined'
    ? [0, 0]
    : [window.innerWidth, window.innerHeight - WORKSPACE_CHROME_HEIGHT];
  if (isTrustworthySize(fromWindow)) return fromWindow;
  return [FALLBACK_WIDTH, FALLBACK_HEIGHT];
}

export function addDefaultLayout(api: DockviewApi, host: HTMLElement | null): void {
  const [width, height] = seedSize(measureHost(host, api));
  applyDefaultLayout(api, width, height);
}

/** Keep the bidirectional Fusion workflow out of the parametric workspace.
 *
 * A one-way parametric handoff lives in the design and run export menus. CAD
 * Link is mounted only while CAD mode can actually use its return controls.
 * Existing saved layouts may still contain the old always-present tab, so mode
 * changes reconcile the live dock instead of relying only on new defaults.
 */
export function syncCadLinkPanel(api: DockviewApi, mode: WorkspaceMode): void {
  const existing = api.getPanel('cadlink');
  if (mode === 'parametric') {
    if (existing) api.removePanel(existing);
    return;
  }
  if (existing) return;
  const reference = api.getPanel('jobs') ?? api.getPanel('results') ?? api.activePanel;
  api.addPanel({
    id: 'cadlink',
    component: 'cadlink',
    title: 'CAD Link',
    inactive: true,
    ...(reference ? { position: { referencePanel: reference, direction: 'within' as const } } : {}),
  });
}

/** What a newly observed host size means for a dock laid out at `laidOut`.
 *
 * The size read during mount cannot be trusted: the host measures 10x100 while
 * the surrounding CSS is still resolving, which is neither zero (so it cannot
 * be rejected as unmeasured) nor real. Every later usable size is applied to
 * the existing dock. Resizing must never deserialize a layout because doing so
 * destroys every panel renderer, including the viewport's WebGL root.
 */
export function nextLayoutAction(
  current: [number, number],
  laidOut: [number, number],
): 'none' | 'layout' | 'rebuild' {
  const [width, height] = current;
  if (!width || !height) return 'none';
  if (width === laidOut[0] && height === laidOut[1]) return 'none';
  if (layoutMode(width) !== layoutMode(laidOut[0])) return 'rebuild';
  return 'layout';
}

/**
 * Resizes the existing dock, rebuilding only when a responsive layout boundary
 * is crossed. This keeps ordinary drag-resizing cheap while ensuring compact
 * windows never inherit the unusable three-column desktop arrangement.
 */
export function createResizeLayoutHandler(
  api: DockviewApi,
  initialSize: [number, number],
): (current: [number, number]) => void {
  let laidOut = initialSize;
  return (current) => {
    const action = nextLayoutAction(current, laidOut);
    if (action === 'none') return;
    laidOut = current;
    if (action === 'rebuild') applyDefaultLayout(api, current[0], current[1]);
    else api.layout(current[0], current[1]);
  };
}

export function Workspace({ resetKey }: { resetKey: number }) {
  const host = useRef<HTMLDivElement>(null);
  const apiRef = useRef<DockviewApi | null>(null);
  const previousReset = useRef(resetKey);
  const coldStartSeeded = useRef(false);

  useEffect(() => {
    if (!apiRef.current || previousReset.current === resetKey) return;
    previousReset.current = resetKey;
    addDefaultLayout(apiRef.current, host.current);
  }, [resetKey]);

  useEffect(() => {
    if (!host.current) return;
    const dockview = new DockviewComponent(host.current, {
      createComponent: ({ name }) => new ReactPanelRenderer(name as ComponentName),
      disableFloatingGroups: true,
      keyboardNavigation: true,
    });
    apiRef.current = dockview.api;
    const unbindNavigation = bindWorkspaceNavigation((panel: WorkspacePanel) => {
      const target = dockview.api.getPanel(panel);
      if (!target) return false;
      target.api.setActive();
      return true;
    });
    const initialSize = measureHost(host.current, dockview.api);
    const initialLayoutSize = seedSize(initialSize);
    const stored = layoutStorage.getItem(LAYOUT_KEY);
    const storedMode = layoutModeStorage.getItem(LAYOUT_MODE_KEY);
    let restored = false;
    if (stored && restoreDisposition(storedMode, initialLayoutSize[0]) === 'restore') {
      try {
        dockview.api.fromJSON(JSON.parse(stored) as ReturnType<DockviewApi['toJSON']>);
        // Restored layouts see the same transient 10x100 mount measurement as
        // new layouts. Applying it can permanently collapse the Results rows.
        dockview.api.layout(initialLayoutSize[0], initialLayoutSize[1]);
        restored = true;
      } catch {
        layoutStorage.removeItem(LAYOUT_KEY);
        layoutModeStorage.removeItem(LAYOUT_MODE_KEY);
      }
    }
    if (!restored && !coldStartSeeded.current) {
      coldStartSeeded.current = true;
      addDefaultLayout(dockview.api, host.current);
    }
    const syncModePanels = () => syncCadLinkPanel(dockview.api, workspaceModeStore.getSnapshot().mode);
    syncModePanels();
    const unbindModePanels = workspaceModeStore.subscribe(syncModePanels);
    const resizeLayout = createResizeLayoutHandler(dockview.api, initialLayoutSize);
    // The mount-time size is unreliable (see nextLayoutAction), so keep watching
    // and resize the existing dock as soon as the host reports its real size.
    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => {
        resizeLayout(measureHost(host.current, dockview.api));
      });
      observer.observe(host.current);
    }
    // onDidLayoutChange coalesces only to a microtask, so dragging a splitter
    // fires it once per pointermove -- 60-120 Hz on a high-poll mouse. Each
    // firing walked the whole layout tree, stringified it and wrote it to
    // storage synchronously, while dockview was relaying out and the
    // WebGL canvas was being resized. Persisting the layout is not urgent;
    // the trailing timer defers it until the drag stops, and the flush paths
    // below guarantee it still lands. Same shape as stores/autosave.ts.
    let persistTimer: ReturnType<typeof setTimeout> | null = null;
    const persistNow = () => {
      if (persistTimer !== null) {
        clearTimeout(persistTimer);
        persistTimer = null;
      }
      if (!apiRef.current || !dockview.api.totalPanels) return;
      layoutStorage.setItem(LAYOUT_KEY, JSON.stringify(dockview.api.toJSON()));
      // Recorded with the layout so the next session can tell whether this
      // arrangement was built for the window it is about to open in.
      layoutModeStorage.setItem(LAYOUT_MODE_KEY, layoutMode(measureHost(host.current, dockview.api)[0] || initialLayoutSize[0]));
    };
    const schedulePersist = () => {
      if (persistTimer !== null) clearTimeout(persistTimer);
      persistTimer = setTimeout(persistNow, 300);
    };
    const flushOnHide = () => {
      if (document.visibilityState === 'hidden') persistNow();
    };
    const subscription = dockview.api.onDidLayoutChange(schedulePersist);
    window.addEventListener('beforeunload', persistNow);
    document.addEventListener('visibilitychange', flushOnHide);
    return () => {
      window.removeEventListener('beforeunload', persistNow);
      document.removeEventListener('visibilitychange', flushOnHide);
      // Before dispose, while the layout is still readable: a pending drag
      // must not be lost by unmounting.
      persistNow();
      observer?.disconnect();
      subscription.dispose();
      unbindModePanels();
      apiRef.current = null;
      unbindNavigation();
      coldStartSeeded.current = false;
      dockview.dispose();
    };
  }, []);

  return <main className="workspace"><div ref={host} className="dockview-host" /></main>;
}
