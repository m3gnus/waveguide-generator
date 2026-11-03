import { expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { modelKindForRun, runProvenanceMarker, showsRetainedMesh } from '../model/currentModel';
import { runOrigin, runSourceLabel } from './runIdentity';

const assemblyRun = { config_summary: { geometry_type: 'imported', import_origin: 'design-assembly' } } as unknown as JobItem;

it('says a design assembly run did not come from CAD', () => {
  expect(runOrigin(assemblyRun)).toBe('assembly');
  expect(runSourceLabel(assemblyRun)).toBe('Assembly');
  expect(runProvenanceMarker(assemblyRun, 'parametric')).toBe('Assembly');
});

it.each([
  [{}, 'parametric'],
  [{ geometry_type: 'imported' }, 'cadlink'],
  [{ import_origin: 'mesh-file' }, 'mesh-file'],
  [{ geometry_type: 'imported', import_origin: 'design-assembly' }, 'assembly'],
])('uses the same origin for run display and archives: %j', (config_summary, origin) => {
  expect(runOrigin({ config_summary })).toBe(origin);
});

it('shows an assembly run as a retained mesh', () => {
  expect(showsRetainedMesh(modelKindForRun(assemblyRun))).toBe(true);
  expect(showsRetainedMesh('cad')).toBe(false);
});
