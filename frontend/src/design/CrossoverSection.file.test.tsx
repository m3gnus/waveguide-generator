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
import { CadSimulationSections } from './ParamPanel';
import { fileSetupControls } from '../stores/meshImport';

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
  const members = importedSimulationSetup().driverGroups.map(group => group.id);
  const wire = toWire(expandLegacy(members, [1000]));
  const combine = { ...wire, member_roles: ['MF', 'HF'], crossovers_hz: [1000],
    member_channels: Object.fromEntries(importedSimulationSetup().driverGroups.map(group => [group.id, group.channelIds])) } as unknown as CombineMetadata;
  act(() => root.render(<CadCrossover/>));
  act(() => latestCombine.publish({ jobId: 'job-1', channelId: 'combined', combine, canApply: true,
    blockedReason: null, recall: null, onApplied: vi.fn(), solvedBandHz: [200, 20_000] }));
  act(() => importedSimulationSetup().setCombineSpec(expandLegacy(members, [100])));
  const offer = [...host.querySelectorAll<HTMLButtonElement>('button')]
    .find(button => button.textContent === 'Re-solve with sweep from 100 Hz');
  expect(offer).toBeDefined();
  act(() => offer!.click());
  expect(useImportedSolveOptionsStore.getState().sweepStartHz).toBe(100);
  expect(useCadReturnStore.getState().frequencyStartHz).toBe(cadStart);
  expect(solve).toHaveBeenCalledTimes(1);
});

it('shows one crossover branch per driver group and excludes the passive opening', () => {
  const record = {
    ingest_id: 'wgi_file', manifest_sha256: 'sha256:a', artifact_sha256: 'sha256:b',
    source_tags: { left: 1, right: 2, high: 3, port: 4 },
    sources: [{ id: 'left', role: 'MF' }, { id: 'right', role: 'MF' },
      { id: 'high', role: 'HF' }, { id: 'port', role: 'PASSIVE_CARDIOID' }],
  } as unknown as RegisteredMesh;
  useImportedModelStore.setState({ input: 'file', prepared: record, fileSimulation: seedFileSimulation(record, null) });
  act(() => importedSimulationSetup().setCombineEnabled(true));
  act(() => root.render(<CadSimulationSections searching matches={section => ['Crossover', 'Passive cardioid'].includes(section)}/>));
  expect(host.querySelector('[data-section="Crossover"]')).not.toBeNull();
  expect(host.querySelector('[data-section="Passive cardioid"]')).not.toBeNull();
  const groups = importedSimulationSetup().driverGroups;
  const wire = fileSetupControls().geometry;
  expect(wire.combine?.members).toEqual(groups.slice(0, 2).map(group => group.id));
  expect(wire.driver_groups?.find(group => group.id === groups[0].id)?.channel_ids).toEqual(['source:left', 'source:right']);
  expect(wire.combine?.members).not.toContain(groups[2].id);
  expect(host.textContent).not.toContain('group:source');
  act(() => importedSimulationSetup().setRoleSharing!('MF', false));
  const separate = fileSetupControls().geometry;
  expect(separate.combine?.members).toHaveLength(3);
  expect(separate.drive_channels.map(channel => channel.id)).toEqual(['source:left', 'source:right', 'source:high', 'source:port']);
});

it('does not apply a crossover edit to a run with different group membership', () => {
  const record = { source_tags: { left: 1, right: 2, high: 3 },
    sources: [{ id: 'left', role: 'MF' }, { id: 'right', role: 'MF' }, { id: 'high', role: 'HF' }] } as unknown as RegisteredMesh;
  useImportedModelStore.setState({ input: 'file', prepared: record, fileSimulation: seedFileSimulation(record, null) });
  act(() => importedSimulationSetup().setCombineEnabled(true));
  const members = importedSimulationSetup().driverGroups.map(group => group.id);
  const combine = { ...toWire(expandLegacy(members, [1000])), member_roles: ['MF', 'HF'], crossovers_hz: [1000],
    member_channels: { [members[0]]: ['source:left'], [members[1]]: ['source:high'] } } as unknown as CombineMetadata;
  act(() => root.render(<CadCrossover/>));
  act(() => latestCombine.publish({ jobId: 'job-1', channelId: 'combined', combine, canApply: true,
    blockedReason: null, recall: null, onApplied: vi.fn(), solvedBandHz: [200, 20_000] }));
  act(() => importedSimulationSetup().setCombineSpec(expandLegacy(members, [1200])));
  expect(host.textContent).toContain('The shown run combines MF + HF');
  expect(host.textContent).not.toContain('group:source');
});
