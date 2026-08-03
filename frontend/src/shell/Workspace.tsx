import { useEffect, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { DockviewComponent, type DockviewApi, type GroupPanelPartInitParameters, type IContentRenderer } from 'dockview';
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

function addDefaultLayout(api: DockviewApi): void {
  api.clear();
  const parameters = api.addPanel({
    id: 'parameters', component: 'parameters', title: 'Parameters', initialWidth: 288,
  });
  const viewport = api.addPanel({
    id: 'viewport', component: 'viewport', title: 'Viewport', initialWidth: 720,
    position: { direction: 'right', referencePanel: parameters },
  });
  api.addPanel({
    id: 'jobs', component: 'jobs', title: 'Jobs', initialWidth: 262,
    position: { direction: 'right', referencePanel: viewport },
  });
  api.addPanel({
    id: 'results', component: 'results', title: 'Results', initialHeight: 360,
    position: { direction: 'below', referencePanel: viewport },
  });
  // Dockview does not reliably honor initialWidth on the panels that seed the
  // layout tree (the first group absorbs remaining space), so pin the side
  // columns explicitly once the tree exists; the viewport keeps the flex space.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    // Group-level setSize resizes the branch split; panel-level only affects
    // tabs within a group and is a no-op for our single-panel groups.
    api.getPanel('parameters')?.group.api.setSize({ width: 300 });
    api.getPanel('jobs')?.group.api.setSize({ width: 320 });
    api.getPanel('results')?.group.api.setSize({ height: 340 });
  }));
}

export function Workspace({ resetKey }: { resetKey: number }) {
  const host = useRef<HTMLDivElement>(null);
  const apiRef = useRef<DockviewApi | null>(null);
  const previousReset = useRef(resetKey);

  useEffect(() => {
    if (!apiRef.current || previousReset.current === resetKey) return;
    previousReset.current = resetKey;
    addDefaultLayout(apiRef.current);
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
        addDefaultLayout(dockview.api);
      }
    } else {
      addDefaultLayout(dockview.api);
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
