export type WorkspacePanel = 'geometry' | 'simulation' | 'viewport' | 'results' | 'jobs' | 'cadlink';

let activateWorkspacePanel = (_panel: WorkspacePanel): boolean => false;

/**
 * How many times the user has deliberately moved somewhere in the workspace.
 *
 * Automatic behaviour -- revealing a finished solve, fronting a request that
 * waits for the user -- may complete an intention the user is still holding,
 * never override one they have since replaced. A command records this number
 * when it is given; the automatic follow-up runs only if it has not moved.
 *
 * It moves on explicit navigation only: a dock tab chosen by pointer or
 * keyboard, a workspace mode switch, a design or project opened, and the
 * controls whose whole job is to take the user to a panel (`navigate`).
 * `activate` is the automatic route and leaves it alone, which is why a CAD
 * return that fronts the CAD Link panel on arrival does not count as the user
 * leaving Results.
 */
let explicitNavigations = 0;

let visiblePanels: ReadonlySet<string> = new Set();
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

export function noteExplicitNavigation(): void {
  explicitNavigations += 1;
  emit();
}

export function navigationGeneration(): number {
  return explicitNavigations;
}

/** The dock reports which of its panels are on screen whenever that may change. */
export function publishVisiblePanels(panels: Iterable<string>): void {
  const next = new Set(panels);
  if (next.size === visiblePanels.size && [...next].every((panel) => visiblePanels.has(panel))) return;
  visiblePanels = next;
  emit();
}

export const workspaceNavigation = {
  /** Front a panel on the application's behalf. Not the user navigating. */
  activate(panel: WorkspacePanel): boolean {
    return activateWorkspacePanel(panel);
  },
  /** Take the user to a panel they asked for: a control whose purpose is that. */
  navigate(panel: WorkspacePanel): boolean {
    noteExplicitNavigation();
    return workspaceNavigation.activate(panel);
  },
  /** Whether the dock shows this panel now: present and not behind a sibling tab. */
  isVisible(panel: WorkspacePanel): boolean {
    return visiblePanels.has(panel);
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  /** A value that changes whenever visibility or the navigation count does. */
  getSnapshot(): string {
    return `${explicitNavigations}:${[...visiblePanels].sort().join(',')}`;
  },
};

export function bindWorkspaceNavigation(
  activate: (panel: WorkspacePanel) => boolean,
): () => void {
  activateWorkspacePanel = activate;
  return () => {
    if (activateWorkspacePanel === activate) activateWorkspacePanel = () => false;
  };
}

/** Tests only. */
export function resetWorkspaceNavigationForTests(): void {
  explicitNavigations = 0;
  visiblePanels = new Set();
  emit();
}
