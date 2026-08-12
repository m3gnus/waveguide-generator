export type WorkspacePanel = 'geometry' | 'simulation' | 'viewport' | 'results' | 'jobs' | 'cadlink';

let activateWorkspacePanel = (_panel: WorkspacePanel): boolean => false;

export const workspaceNavigation = {
  activate(panel: WorkspacePanel): boolean {
    return activateWorkspacePanel(panel);
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
