import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { RegisteredMesh } from '../api/meshImport';
import { expandLegacy, toWire } from '../results/crossoverSpec';
import { latestCombine } from '../results/latestCombine';
import type { CombineMetadata } from '../results/types';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetImportedModelStore, useImportedModelStore } from '../stores/importedModel';
import { importedSimulationSetup, seedFileSimulation } from '../stores/importedSimulationSetup';
import { resetSolveOptionsStore, useImportedSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { solveControlStore } from '../shell/JobsCoordinator';
import { CadCrossover } from './CrossoverSection';

let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  workspaceModeStore.setImportedInput('file');
  resetCadReturnStore(); resetImportedModelStore(); resetSolveOptionsStore(); latestCombine.reset();
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  latestCombine.reset(); resetImportedModelStore(); workspaceModeStore.setImportedInput('cad');
  vi.restoreAllMocks(); host.remove();
});

it('extends the file sweep through the file adapter when re-solving', () => {
  const record = {
    ingest_id: 'wgi_file', manifest_sha256: 'sha256:a', artifact_sha256: 'sha256:b',
    source_tags: { 'source-mf': 1, 'source-hf': 2 },
    sources: [{ id: 'source-mf', role: 'MF' }, { id: 'source-hf', role: 'HF' }],
  } as unknown as RegisteredMesh;
  useImportedModelStore.setState({ input: 'file', prepared: record,
    fileSimulation: { ...seedFileSimulation(record, null), combineEnabled: null } });
  useImportedSolveOptionsStore.getState().setSweep({ startHz: 200, endHz: 20_000, count: 36 });
  const cadStart = useCadReturnStore.getState().frequencyStartHz;
  const solve = vi.fn().mockResolvedValue(undefined);
  vi.spyOn(solveControlStore, 'getSnapshot').mockReturnValue({ solve, disabled: false } as unknown as NonNullable<ReturnType<typeof solveControlStore.getSnapshot>>);
  const wire = toWire(expandLegacy(['drive-mf', 'drive-hf'], [1000]));
  const combine = { ...wire, member_roles: ['MF', 'HF'], crossovers_hz: [1000] } as unknown as CombineMetadata;
  act(() => root.render(<CadCrossover/>));
  act(() => latestCombine.publish({ jobId: 'job-1', channelId: 'combined', combine, canApply: true,
    blockedReason: null, recall: null, onApplied: vi.fn(), solvedBandHz: [200, 20_000] }));
  act(() => importedSimulationSetup().setCombineSpec(expandLegacy(['drive-mf', 'drive-hf'], [100])));
  const offer = [...host.querySelectorAll<HTMLButtonElement>('button')]
    .find(button => button.textContent === 'Re-solve with sweep from 100 Hz');
  expect(offer).toBeDefined();
  act(() => offer!.click());
  expect(useImportedSolveOptionsStore.getState().sweepStartHz).toBe(100);
  expect(useCadReturnStore.getState().frequencyStartHz).toBe(cadStart);
  expect(solve).toHaveBeenCalledTimes(1);
});
