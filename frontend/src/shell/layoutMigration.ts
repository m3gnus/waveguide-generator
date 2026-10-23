import type { SerializedDockview } from 'dockview';

/** Read old layouts without changing any run, design or input identity. */
export function migrateWorkspaceLayout(saved: SerializedDockview): SerializedDockview {
  const layout = structuredClone(saved);
  delete layout.panels.cadlink;
  const groups = new Set<string>();
  type Node = SerializedDockview['grid']['root'];
  type Group = Exclude<Node['data'], Node[]>;
  const migrateGroup = (group: Group): boolean => {
    group.views = group.views.filter(id => id !== 'cadlink');
    if (!group.views.length) return false;
    if (!group.activeView || !group.views.includes(group.activeView)) {
      group.activeView = group.views.includes('jobs') ? 'jobs' : group.views[0];
    }
    groups.add(group.id);
    return true;
  };
  const migrateNode = (node: Node): boolean => {
    if (!Array.isArray(node.data)) return migrateGroup(node.data);
    node.data = node.data.filter(migrateNode);
    return node.data.length > 0;
  };
  migrateNode(layout.grid.root);
  const migrateWindow = (entry: Pick<NonNullable<SerializedDockview['popoutGroups']>[number], 'data' | 'grid'>) => entry.grid
    ? migrateNode(entry.grid.root) : entry.data ? migrateGroup(entry.data) : false;
  if (layout.floatingGroups) layout.floatingGroups = layout.floatingGroups.filter(migrateWindow);
  if (layout.popoutGroups) layout.popoutGroups = layout.popoutGroups.filter(migrateWindow);
  for (const position of ['top', 'bottom', 'left', 'right'] as const) {
    const group = layout.edgeGroups?.[position]?.group as Group | undefined;
    if (group && !migrateGroup(group)) delete layout.edgeGroups?.[position];
  }
  if (!groups.size) throw new Error('Saved layout has no panels');
  if (!layout.activeGroup || !groups.has(layout.activeGroup)) layout.activeGroup = groups.values().next().value;
  return layout;
}
