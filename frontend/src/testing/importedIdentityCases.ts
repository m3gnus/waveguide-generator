import { readFileSync } from 'node:fs';
import type { RegisteredMesh } from '../api/meshImport';
import type { ImportedSetup } from '../jobs/importedSetup';
import { bindImportedSetup } from '../jobs/importedSetup';
import { buildImportedSubmission, resolveCadImportedSetup } from '../jobs/importedSubmission';
import { buildCadProjectSetup } from '../shell/cadSetupPublisher';
import { archivedRequest, bundle, record, workspaceFor } from '../shell/cadlink/selection.fixtures';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { restoreCadProjectSettings } from '../stores/cadProjectSettings';
import { openImported, resetImportedModelStore, restoreCadImportedSetup, useImportedModelStore } from '../stores/importedModel';
import { importedSimulationSetup } from '../stores/importedSimulationSetup';
import { fileMeshSetup, fileMeshSubmission, fileSetupControls } from '../stores/meshImport';
import { applyWireOptionsToImportedSolveOptions, resetSolveOptionsStore, seedImportedSolveOptionsForFile, useImportedSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';

/** The same explicit inputs run at the baseline and against the current implementation. */
export async function importedIdentityCases() {
  workspaceModeStore.setImportedInput('cad');
  resetCadReturnStore(); resetSolveOptionsStore(); resetImportedModelStore(); importedMeshStore.clear();
  const cadSaved = workspaceFor().setup.content as unknown as ImportedSetup;
  useCadReturnStore.setState({ selectedBundle: bundle, ingestRecord: record, projectLineageId: 'wgl_project', needsIngest: false });
  restoreCadProjectSettings(cadSaved, record, false, 'wgs_saved');
  restoreCadImportedSetup('wgl_project', record, cadSaved, 'wgs_saved');
  useImportedSolveOptionsStore.getState().setFrequencyMode('range');
  importedSimulationSetup().setSweep({ frequencyStartHz: 125, frequencyEndHz: 8000, frequencyCount: 29 });
  importedSimulationSetup().setDriveVoltage(4);
  const cadState = useCadReturnStore.getState();
  const cadOptions = useImportedSolveOptionsStore.getState();
  const cad = {
    resolved: resolveCadImportedSetup(cadState, cadOptions),
    project: buildCadProjectSetup(cadState, cadOptions, 'wgl_project')?.setup,
    submission: buildImportedSubmission(cadState),
    hasOpenedSetup: useImportedModelStore.getState().setup !== null,
    sweep: [cadState.frequencyStartHz, cadState.frequencyEndHz, cadState.frequencyCount],
  };

  const msh = new TextDecoder().decode(readFileSync('../server/tests/fixtures/mesh_import/tetra.msh'));
  const file: RegisteredMesh = { ingest_id: 'wgi_file', model_id: 'mesh-file:old', producer: 'mesh-file',
    manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact', msh_text: msh,
    source_file: { filename: 'old.msh', transform: { units: 'mm', forward: '+z', up: '+y' }, source_tags: [2] },
    source_tags: { group_2: 2 }, summary: { node_count: 4, triangle_count: 4, dimensions: [1, 1, 1], groups: [] } };
  const fileSaved: ImportedSetup = { geometry: { skipped_source_ids: [],
    drive_channels: [{ id: 'group_2', source_ids: ['group_2'], motion: 'axial' }] },
    options: archivedRequest().options as unknown as ImportedSetup['options'] };
  workspaceModeStore.setImportedInput('file');
  seedImportedSolveOptionsForFile();
  applyWireOptionsToImportedSolveOptions(fileSaved.options);
  await openImported({ kind: 'file', prepared: file, setup: fileSaved });
  importedMeshStore.setFile(createImportedMeshScene('old.msh', parseMSH(msh), 'file', file.ingest_id));
  importedSimulationSetup().setChannelMotion('group_2', 'normal');
  const fileCase = {
    controls: fileSetupControls(), setup: fileMeshSetup(), submission: fileMeshSubmission(),
    original: bindImportedSetup(file, fileSaved),
    hasOpenedSetup: useImportedModelStore.getState().setup !== null,
  };
  return { source_commit: '71fce3e63', cad, file: fileCase };
}
