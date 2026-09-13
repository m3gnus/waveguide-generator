import { describe, expect, it } from 'vitest';
import type { EngineCapability, EngineSelection } from '../jobs/actions';
import { declaresImportedGeometry, importedSolveEngine } from './backendSupport';

function engine(name: string, overrides: Partial<EngineCapability> = {}): EngineCapability {
  return {
    name,
    available: true,
    reason: null,
    version: 'test',
    fast_paths: [],
    formulations: ['full-3d'],
    geometry_sources: ['parametric'],
    ...overrides,
  };
}

const IMPORTED = ['parametric', 'imported'];

const selection: EngineSelection = {
  default: 'auto',
  resolvedDefault: 'metal',
  full3dOrder: ['metal', 'beat-cuda', 'beat-rocm', 'beat-metal', 'bempp', 'beat-cpu', 'dryrun'],
  axisymmetricRunner: 'axisym',
};

const metal = (overrides: Partial<EngineCapability> = {}) =>
  engine('metal', { label: 'Metal — Apple GPU', geometry_sources: IMPORTED, ...overrides });
const beatCpu = (overrides: Partial<EngineCapability> = {}) =>
  engine('beat-cpu', { label: 'BEAT · CPU — no GPU needed', geometry_sources: IMPORTED, ...overrides });
const bempp = (overrides: Partial<EngineCapability> = {}) =>
  engine('bempp', { label: 'BEMPP — CPU', ...overrides });

describe('importedSolveEngine', () => {
  it('AUTO takes Metal where Metal runs', () => {
    const choice = importedSolveEngine('auto', [metal(), bempp(), beatCpu()], selection);
    expect(choice.engine?.name).toBe('metal');
    expect(choice.reason).toBeNull();
  });

  it('AUTO walks the server order to BEAT-CPU where Metal is unavailable', () => {
    const choice = importedSolveEngine(
      'auto',
      [metal({ available: false, reason: 'needs an Apple GPU' }), bempp(), beatCpu()],
      selection,
    );
    expect(choice.engine?.name).toBe('beat-cpu');
  });

  it('AUTO follows the same order a parametric design does once BEMPP declares imported geometry', () => {
    const choice = importedSolveEngine(
      'auto',
      [metal({ available: false }), bempp({ geometry_sources: IMPORTED }), beatCpu()],
      selection,
    );
    expect(choice.engine?.name).toBe('bempp');
  });

  it('AUTO names every declaring engine that is unavailable', () => {
    const choice = importedSolveEngine(
      'auto',
      [metal({ available: false, reason: 'needs an Apple GPU' }), beatCpu({ available: false, reason: 'runtime is being prepared' })],
      selection,
    );
    expect(choice.engine).toBeNull();
    expect(choice.reason).toContain('Metal — Apple GPU: needs an Apple GPU');
    expect(choice.reason).toContain('BEAT · CPU — no GPU needed: runtime is being prepared');
  });

  it('an explicit engine is honoured as it is', () => {
    expect(importedSolveEngine('beat-cpu', [metal(), beatCpu()], selection).engine?.name).toBe('beat-cpu');
  });

  it('an explicit engine that cannot solve imported geometry is refused with the ones that can', () => {
    const choice = importedSolveEngine('bempp', [metal(), bempp(), beatCpu()], selection);
    expect(choice.engine).toBeNull();
    expect(choice.reason).toBe(
      'BEMPP — CPU does not solve imported CAD geometry. Engines that do: Metal — Apple GPU, BEAT · CPU — no GPU needed.',
    );
  });

  it('an explicit declaring engine that is unavailable says why', () => {
    const choice = importedSolveEngine('beat-cpu', [beatCpu({ available: false, reason: 'no Julia' })], selection);
    expect(choice.engine).toBeNull();
    expect(choice.reason).toBe('BEAT · CPU — no GPU needed is unavailable: no Julia.');
  });

  it('never routes to Axisymmetric', () => {
    const choice = importedSolveEngine('axisym', [metal(), engine('axisym', { formulations: ['axisymmetric'] })], selection);
    expect(choice.engine).toBeNull();
    expect(choice.reason).toContain('full 3-D only');
  });

  it('the legacy BEAT name takes the best declaring BEAT backend', () => {
    const choice = importedSolveEngine(
      'beat',
      [metal(), engine('beat-metal'), beatCpu()],
      selection,
    );
    expect(choice.engine?.name).toBe('beat-cpu');
  });

  it('reads a payload without geometry_sources as the server that sent it behaved', () => {
    expect(declaresImportedGeometry(engine('metal', { geometry_sources: undefined }))).toBe(true);
    expect(declaresImportedGeometry(engine('bempp', { geometry_sources: undefined }))).toBe(false);
  });
});
