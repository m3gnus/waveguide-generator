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

const LAYOUT_KEY = 'wg2.dockview.layout.v1';

const components = {
  parameters: ParamPanel,
  viewport: ViewportPanel,
  results: ResultsPanel,
  jobs: JobsPanel,
};

type ComponentName = keyof typeof components;

class ReactPanelRenderer implements IContentRenderer {
  readonly element = document.createElement('div');
  private root: Root | null = null;

  constructor(private readonly name: ComponentName) {
    this.element.className = 'dock-panel-content';
  }

  init(_parameters: GroupPanelPartInitParameters): void {
    const Component = components[this.name];
    this.root = createRoot(this.element);
    this.root.render(<Component />);
  }

  dispose(): void {
    queueMicrotask(() => this.root?.unmount());
  }
}

const PARAMETERS_WIDTH = 300;
const JOBS_WIDTH = 320;
const RESULTS_HEIGHT = 340;
const FALLBACK_WIDTH = 1440;
const FALLBACK_HEIGHT = 900;

export function createDefaultLayout(width: number, height: number): SerializedDockview {
  const layoutWidth = Math.max(PARAMETERS_WIDTH + JOBS_WIDTH + 1, Math.round(width) || FALLBACK_WIDTH);
  const layoutHeight = Math.max(RESULTS_HEIGHT + 1, Math.round(height) || FALLBACK_HEIGHT);
  const viewportWidth = layoutWidth - PARAMETERS_WIDTH - JOBS_WIDTH;
  const viewportHeight = layoutHeight - RESULTS_HEIGHT;
  const group = (id: ComponentName) => ({ id: `${id}-group`, views: [id], activeView: id });
  return {
    grid: {
      width: layoutWidth,
      height: layoutHeight,
      orientation: Orientation.HORIZONTAL,
      root: {
        type: 'branch',
        size: layoutHeight,
        data: [
          { type: 'leaf', size: PARAMETERS_WIDTH, data: group('parameters') },
          {
            type: 'branch',
            size: viewportWidth,
            data: [
              { type: 'leaf', size: viewportHeight, data: group('viewport') },
              { type: 'leaf', size: RESULTS_HEIGHT, data: group('results') },
            ],
          },
          { type: 'leaf', size: JOBS_WIDTH, data: group('jobs') },
        ],
      },
    },
    panels: {
      parameters: { id: 'parameters', contentComponent: 'parameters', title: 'Parameters' },
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

export function addDefaultLayout(api: DockviewApi, host: HTMLElement | null): void {
  const [measured, measuredHeight] = measureHost(host, api);
  applyDefaultLayout(api, measured || FALLBACK_WIDTH, measuredHeight || FALLBACK_HEIGHT);
}

/** What a newly observed host size means for a dock laid out at `laidOut`.
 *
 * The size read during mount cannot be trusted: the host measures 10x100 while
 * the surrounding CSS is still resolving, which is neither zero (so it cannot
 * be rejected as unmeasured) nor real. The dock must therefore be corrected
 * whenever the observed size actually changes. A default layout is rebuilt so
 * its pinned proportions apply at the true width; a layout the user has
 * arranged is only re-laid-out, never rebuilt.
 */
export function nextLayoutAction(
  current: [number, number],
  laidOut: [number, number],
  hasStoredLayout: boolean,
): 'none' | 'layout' | 'reseed' {
  const [width, height] = current;
  if (!width || !height) return 'none';
  if (width === laidOut[0] && height === laidOut[1]) return 'none';
  return hasStoredLayout ? 'layout' : 'reseed';
}

export function Workspace({ resetKey }: { resetKey: number }) {
  const host = useRef<HTMLDivElement>(null);
  const apiRef = useRef<DockviewApi | null>(null);
  const previousReset = useRef(resetKey);

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
    let laidOut: [number, number] = measureHost(host.current, dockview.api);
    const stored = localStorage.getItem(LAYOUT_KEY);
    let restored = false;
    if (stored) {
      try {
        dockview.api.fromJSON(JSON.parse(stored) as ReturnType<DockviewApi['toJSON']>);
        if (laidOut[0] && laidOut[1]) dockview.api.layout(laidOut[0], laidOut[1]);
        restored = true;
      } catch {
        localStorage.removeItem(LAYOUT_KEY);
      }
    }
    if (!restored) applyDefaultLayout(dockview.api, laidOut[0] || FALLBACK_WIDTH, laidOut[1] || FALLBACK_HEIGHT);
    // The mount-time size is unreliable (see nextLayoutAction), so keep watching
    // and correct the dock as soon as the host reports its real size.
    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => {
        const current = measureHost(host.current, dockview.api);
        const action = nextLayoutAction(current, laidOut, Boolean(localStorage.getItem(LAYOUT_KEY)));
        if (action === 'none') return;
        laidOut = current;
        if (action === 'layout') dockview.api.layout(current[0], current[1]);
        else applyDefaultLayout(dockview.api, current[0], current[1]);
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
      dockview.dispose();
    };
  }, []);

  return <main className="workspace"><div ref={host} className="dockview-host" /></main>;
}
