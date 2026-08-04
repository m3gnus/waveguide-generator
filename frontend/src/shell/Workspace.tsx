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
import { JobsPanel } from './JobsPanel';
import { ResultsPanel } from './ResultsPanel';
import { ViewportPanel } from './ViewportPanel';

export const LEGACY_LAYOUT_KEY = 'wg2.dockview.layout.v1';
export const LAYOUT_KEY = 'wg2.dockview.layout.v2';

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
    this.element.className = 'dock-panel-content';
  }

  init(_parameters: GroupPanelPartInitParameters): void {
    const Component = components[this.name];
    const generation = ++this.generation;
    void this.teardown.then(() => {
      if (generation !== this.generation) return;
      const root = createRoot(this.element);
      this.root = root;
      root.render(<Component />);
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
const FALLBACK_WIDTH = 1440;
const FALLBACK_HEIGHT = 900;
/** Top bar plus status bar, excluded when guessing the dock size from the window. */
const WORKSPACE_CHROME_HEIGHT = 84;

export function createDefaultLayout(width: number, height: number): SerializedDockview {
  const layoutWidth = Math.max(PARAMETERS_WIDTH + JOBS_WIDTH + 1, Math.round(width) || FALLBACK_WIDTH);
  const layoutHeight = Math.max(RESULTS_HEIGHT + 1, Math.round(height) || FALLBACK_HEIGHT);
  const viewportWidth = layoutWidth - PARAMETERS_WIDTH - JOBS_WIDTH;
  const viewportHeight = layoutHeight - RESULTS_HEIGHT;
  const group = (id: string, views: ComponentName[], activeView: ComponentName = views[0]) => ({ id: `${id}-group`, views, activeView });
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
              { type: 'leaf', size: RESULTS_HEIGHT, data: group('results', ['results']) },
            ],
          },
          { type: 'leaf', size: JOBS_WIDTH, data: group('jobs', ['jobs']) },
        ],
      },
    },
    panels: {
      geometry: { id: 'geometry', contentComponent: 'geometry', title: 'Geometry' },
      simulation: { id: 'simulation', contentComponent: 'simulation', title: 'Simulation' },
      viewport: { id: 'viewport', contentComponent: 'viewport', title: 'Viewport' },
      results: { id: 'results', contentComponent: 'results', title: 'Results' },
      jobs: { id: 'jobs', contentComponent: 'jobs', title: 'Jobs' },
    },
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
  return width >= PARAMETERS_WIDTH + JOBS_WIDTH + 1 && height >= RESULTS_HEIGHT + 1;
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
): 'none' | 'layout' {
  const [width, height] = current;
  if (!width || !height) return 'none';
  if (width === laidOut[0] && height === laidOut[1]) return 'none';
  return 'layout';
}

/**
 * Resizes the existing dock; never rebuilds it.
 *
 * Rebuilding destroys every panel renderer — including the viewport's WebGL
 * root, which is how the dock came to be recreated dozens of times per load
 * and exhaust the browser's contexts. The layout is seeded exactly once, at
 * mount; every measurement after that is a plain `api.layout`.
 */
export function createResizeLayoutHandler(
  api: DockviewApi,
  initialSize: [number, number],
): (current: [number, number]) => void {
  let laidOut = initialSize;
  return (current) => {
    if (nextLayoutAction(current, laidOut) === 'none') return;
    laidOut = current;
    api.layout(current[0], current[1]);
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
    const stored = localStorage.getItem(LAYOUT_KEY);
    let restored = false;
    if (stored) {
      try {
        dockview.api.fromJSON(JSON.parse(stored) as ReturnType<DockviewApi['toJSON']>);
        if (initialSize[0] && initialSize[1]) dockview.api.layout(initialSize[0], initialSize[1]);
        restored = true;
      } catch {
        localStorage.removeItem(LAYOUT_KEY);
      }
    }
    if (!restored && !coldStartSeeded.current) {
      coldStartSeeded.current = true;
      addDefaultLayout(dockview.api, host.current);
    }
    const resizeLayout = createResizeLayoutHandler(dockview.api, initialSize);
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
