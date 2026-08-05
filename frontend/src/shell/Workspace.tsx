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
import { JobsPanel } from './JobsPanel';
import { ResultsPanel } from './ResultsPanel';
import { ViewportPanel } from './ViewportPanel';

export const LEGACY_LAYOUT_KEY = 'wg2.dockview.layout.v1';
export const LAYOUT_KEY = 'wg2.dockview.layout.v3';

const components = {
  geometry: () => <ParamPanel tab="geometry" />,
  simulation: () => <ParamPanel tab="simulation" />,
  viewport: ViewportPanel,
  results: ResultsPanel,
  jobs: JobsPanel,
};

type ComponentName = keyof typeof components;

type WorkspacePanel = ComponentName;
let activateWorkspacePanel = (_panel: WorkspacePanel): boolean => false;

export const workspaceNavigation = {
  activate(panel: WorkspacePanel): boolean {
    return activateWorkspacePanel(panel);
  },
};

export class ReactPanelRenderer implements IContentRenderer {
  readonly element = document.createElement('div');
  private root: Root | null = null;
  private generation = 0;
  private teardown = Promise.resolve();

  constructor(private readonly name: ComponentName) {
    this.element.className = `dock-panel-content dock-panel-${name}`;
  }

  init(_parameters: GroupPanelPartInitParameters): void {
    const Component = components[this.name];
    const generation = ++this.generation;
    void this.teardown.then(() => {
      if (generation !== this.generation) return;
      const root = createRoot(this.element);
      this.root = root;
      root.render(<AppQueryProvider><Component /></AppQueryProvider>);
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

const PARAMETERS_WIDTH = 300;
const JOBS_WIDTH = 320;
const RESULTS_HEIGHT = 340;
const COMPACT_BREAKPOINT = 800;
const MEDIUM_BREAKPOINT = 1100;
const FALLBACK_WIDTH = 1440;
const FALLBACK_HEIGHT = 900;
/** Top bar plus status bar, excluded when guessing the dock size from the window. */
const WORKSPACE_CHROME_HEIGHT = 84;

export function createDefaultLayout(width: number, height: number): SerializedDockview {
  const layoutWidth = Math.max(1, Math.round(width) || FALLBACK_WIDTH);
  const layoutHeight = Math.max(1, Math.round(height) || FALLBACK_HEIGHT);
  const group = (id: string, views: ComponentName[], activeView: ComponentName = views[0]) => ({ id: `${id}-group`, views, activeView });
  const panels = {
    geometry: { id: 'geometry', contentComponent: 'geometry', title: 'Geometry' },
    simulation: { id: 'simulation', contentComponent: 'simulation', title: 'Simulation' },
    viewport: { id: 'viewport', contentComponent: 'viewport', title: 'Viewport' },
    results: { id: 'results', contentComponent: 'results', title: 'Results' },
    jobs: { id: 'jobs', contentComponent: 'jobs', title: 'Jobs' },
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
          data: [{ type: 'leaf', size: layoutWidth, data: group('workspace', ['geometry', 'simulation', 'viewport', 'results', 'jobs'], 'viewport') }],
        },
      },
      panels,
      activeGroup: 'viewport',
    };
  }

  if (layoutWidth < MEDIUM_BREAKPOINT) {
    const parametersWidth = 280;
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
                { type: 'leaf', size: resultsHeight, data: group('analysis', ['results', 'jobs'], 'results') },
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
          { type: 'leaf', size: JOBS_WIDTH, data: group('jobs', ['jobs']) },
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

export function applyDefaultLayout(api: DockviewApi, width: number, height: number): void {
  api.fromJSON(createDefaultLayout(width, height));
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
  const mode = (value: number) => value < COMPACT_BREAKPOINT ? 'compact' : value < MEDIUM_BREAKPOINT ? 'medium' : 'wide';
  if (mode(width) !== mode(laidOut[0])) return 'rebuild';
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
    activateWorkspacePanel = (panel) => {
      const target = dockview.api.getPanel(panel);
      if (!target) return false;
      target.api.setActive();
      return true;
    };
    const initialSize = measureHost(host.current, dockview.api);
    const initialLayoutSize = seedSize(initialSize);
    const stored = localStorage.getItem(LAYOUT_KEY);
    let restored = false;
    if (stored) {
      try {
        dockview.api.fromJSON(JSON.parse(stored) as ReturnType<DockviewApi['toJSON']>);
        // Restored layouts see the same transient 10x100 mount measurement as
        // new layouts. Applying it can permanently collapse the Results rows.
        dockview.api.layout(initialLayoutSize[0], initialLayoutSize[1]);
        restored = true;
      } catch {
        localStorage.removeItem(LAYOUT_KEY);
      }
    }
    if (!restored && !coldStartSeeded.current) {
      coldStartSeeded.current = true;
      addDefaultLayout(dockview.api, host.current);
    }
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
    const subscription = dockview.api.onDidLayoutChange(() => {
      if (dockview.api.totalPanels) localStorage.setItem(LAYOUT_KEY, JSON.stringify(dockview.api.toJSON()));
    });
    return () => {
      observer?.disconnect();
      subscription.dispose();
      apiRef.current = null;
      activateWorkspacePanel = () => false;
      coldStartSeeded.current = false;
      dockview.dispose();
    };
  }, []);

  return <main className="workspace"><div ref={host} className="dockview-host" /></main>;
}
