import { expect, it } from 'vitest';
import { bundle } from './selection.fixtures';
import { resolvePreparationTarget } from './preparationTarget';

const instances = [
  { instanceId: 'live-b' }, { instanceId: 'anchor-a' },
] as NonNullable<typeof bundle.instances>;

it('uses the declared anchor even when an older listing omits the instance inventory', () => {
  expect(resolvePreparationTarget({ ...bundle, solverAnchorInstanceId: 'anchor-a' }, null)).toBe('anchor-a');
});

it('prefers the anchor over a different live instance, which ingest would refuse', () => {
  expect(resolvePreparationTarget({ ...bundle, instances, solverAnchorInstanceId: 'anchor-a' }, 'live-b')).toBe('anchor-a');
});

it.each([undefined, null])('uses the live instance the return contains when the anchor is %s', (solverAnchorInstanceId) => {
  expect(resolvePreparationTarget({ ...bundle, instances, solverAnchorInstanceId }, 'live-b')).toBe('live-b');
});

it.each([undefined, null])('leaves instance inference to ingest for a legacy anchor of %s and no usable live instance', (solverAnchorInstanceId) => {
  expect(resolvePreparationTarget({ ...bundle, instances, solverAnchorInstanceId }, 'elsewhere')).toBeNull();
  expect(resolvePreparationTarget({ ...bundle, solverAnchorInstanceId }, null)).toBeNull();
});
