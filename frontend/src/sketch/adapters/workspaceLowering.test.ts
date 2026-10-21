// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { editorFixtures } from './sourceContourTestSupport';
import { expandedPreset, optionalPresetCorpus, presetCorpus } from './presetTestSupport';
import { presetDefaults, seedPreset, type PresetShape } from './sourcePreset';
import { lower, seed } from './sourceContour';
import golden from './workspaceLowering.golden.json';

// W0 baseline at 77c641c8. Keep literal precision AND JSON property/array order;
// deep equality or sorting the output would hide changes to the source wire bytes.
const cases = [
  ...editorFixtures().map((document, i) => ({ name: `editor ${i}`, state: seed(document) })),
  ...(['flat', 'dome', 'cone'] as PresetShape[]).flatMap(shape =>
    presetCorpus.map((overrides, i) => ({ name: `${shape} ${i}`, state:
      seedPreset(expandedPreset(shape, overrides), shape, { ...presetDefaults[shape], ...overrides }) }))),
  ...(['dome', 'cone'] as const).flatMap(shape => optionalPresetCorpus.map((overrides, i) => ({ name: `${shape} optional ${i}`, state:
    seedPreset(expandedPreset(shape, overrides), shape, { ...presetDefaults[shape], ...overrides }) }))),
];

describe('W0 source lowering wire bytes', () => {
  it('covers every pinned case without stale golden entries', () => {
    expect(cases.map(c => c.name)).toEqual(Object.keys(golden));
  });
  it.each(cases)('$name is byte-identical to 77c641c8', ({ name, state }) => {
    expect(JSON.stringify(lower(state.sketch, state.meta))).toBe(golden[name as keyof typeof golden]);
  });
});
