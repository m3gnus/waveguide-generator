export interface AclRepairRootStatus {
  scope: 'appData' | 'workspace';
  source: 'current' | 'previous';
  repaired: number;
  remaining: number;
  unreadable: number;
  failed: number;
  truncated: boolean;
  administratorMayHelp: number;
}

export interface AclRepairStatus {
  platform: 'windows' | 'other';
  roots: AclRepairRootStatus[];
}

function count(value: unknown): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

export async function getAclRepairStatus(fetcher: typeof fetch = fetch): Promise<AclRepairStatus> {
  const response = await fetcher('/api/acl-repair/status');
  if (!response.ok) throw new Error(`File access status request failed (${response.status})`);
  const payload = await response.json() as { platform?: unknown; roots?: unknown };
  if (payload.platform !== 'windows' || !Array.isArray(payload.roots)) {
    return { platform: 'other', roots: [] };
  }
  const roots = payload.roots.flatMap((raw): AclRepairRootStatus[] => {
    if (!raw || typeof raw !== 'object') return [];
    const root = raw as Record<string, unknown>;
    if ((root.scope !== 'appData' && root.scope !== 'workspace')
      || (root.source !== 'current' && root.source !== 'previous')) return [];
    return [{
      scope: root.scope,
      source: root.source,
      repaired: count(root.repaired),
      remaining: count(root.remaining),
      unreadable: count(root.unreadable),
      failed: count(root.failed),
      truncated: root.truncated === true,
      administratorMayHelp: count(root.administratorMayHelp),
    }];
  });
  return { platform: 'windows', roots };
}
