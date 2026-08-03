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

function addDefaultLayout(api: DockviewApi, host: HTMLElement | null): void {
  const bounds = host?.getBoundingClientRect();
  const width = host?.clientWidth || bounds?.width || api.width || FALLBACK_WIDTH;
  const height = host?.clientHeight || bounds?.height || api.height || FALLBACK_HEIGHT;
  api.fromJSON(createDefaultLayout(width, height));
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
    const stored = localStorage.getItem(LAYOUT_KEY);
    if (stored) {
      try {
        dockview.api.fromJSON(JSON.parse(stored) as ReturnType<DockviewApi['toJSON']>);
      } catch {
        localStorage.removeItem(LAYOUT_KEY);
        addDefaultLayout(dockview.api, host.current);
      }
    } else {
      addDefaultLayout(dockview.api, host.current);
    }
    const subscription = dockview.api.onDidLayoutChange(() => {
      if (dockview.api.totalPanels) localStorage.setItem(LAYOUT_KEY, JSON.stringify(dockview.api.toJSON()));
    });
    return () => {
      subscription.dispose();
      apiRef.current = null;
      dockview.dispose();
    };
  }, []);

  return <main className="workspace"><div ref={host} className="dockview-host" /></main>;
}
