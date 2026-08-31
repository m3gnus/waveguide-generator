import { describe, expect, it, vi } from 'vitest';
import { exportProfileArtifacts } from './DesignFileMenu';

describe('profile artifact export', () => {
  it('reports partial success with the completed and failed artifact names', async () => {
    const exporter = vi.fn(async (kind: 'profiles' | 'slices') => {
      if (kind === 'slices') throw new Error('disk full');
      return { directory: 'C:/Output/horn' };
    });
    await expect(exportProfileArtifacts(exporter, 7)).rejects.toThrow('Exported profiles CSV; failed slices: disk full');
    expect(exporter).toHaveBeenCalledTimes(2);
  });
});
