import { expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { modelKindForRun, runProvenanceMarker, showsRetainedMesh } from '../model/currentModel';
import { runSourceForJob } from './exportNaming';
import { runOrigin, runSourceLabel } from './runIdentity';

const assemblyRun = { config_summary: { geometry_type: 'imported', import_origin: 'design-assembly' } } as unknown as JobItem;

it('says a design assembly run did not come from CAD', () => {
  expect(runOrigin(assemblyRun)).toBe('assembly');
  expect(runSourceLabel(assemblyRun)).toBe('Assembly');
  expect(runProvenanceMarker(assemblyRun, 'parametric')).toBe('Assembly');
});

it('keeps the source a run archive records', () => {
  expect(runSourceForJob(assemblyRun)).toBe('cadlink');
});

it('shows an assembly run as a retained mesh', () => {
  expect(showsRetainedMesh(modelKindForRun(assemblyRun))).toBe(true);
  expect(showsRetainedMesh('cad')).toBe(false);
});
