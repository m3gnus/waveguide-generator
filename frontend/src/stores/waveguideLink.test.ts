import { describe, expect, it } from 'vitest';
import { waveguideDefinitionApplies } from './waveguideLink';

describe('waveguide definition applicability', () => {
  it('hides only for a project the ingest resolved with no design behind it', () => {
    // How a CAD-authored return resolves: it has a project, but no WG design.
    expect(waveguideDefinitionApplies({ design_id: null })).toBe(false);
    expect(waveguideDefinitionApplies({ design_id: 'wgd_1' })).toBe(true);
  });

  it('keeps the definition wherever nothing has proved the geometry came from CAD', () => {
    // CAD mode before any return: the formula is what you edit before sending
    // the design out, so it must still be there.
    // An older ingest recorded no project at all, and must not lose its
    // inputs either. `_resolve_project` always spells `design_id` out, so a
    // project with the key missing is the same claim as a null one: no design.
    expect(waveguideDefinitionApplies(null)).toBe(true);
    expect(waveguideDefinitionApplies(undefined)).toBe(true);
    expect(waveguideDefinitionApplies({})).toBe(false);
  });
});
