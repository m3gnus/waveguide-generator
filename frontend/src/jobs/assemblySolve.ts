/**
 * The press of a design assembly's Solve: the one model Solve
 * (`POST /api/models/{id}/solve`) on the compiled record of the design on
 * screen, with the setup the shared Simulation sections show. The jobs
 * coordinator submits it, under the same lock and attention as every Solve.
 */
import { assemblyContextRefused, fetchAssemblyContext } from '../api/assembly';
import { placementProblems } from '../sketch/adapters/frontView';
import { designModelCurrent, designModelGeometryCurrent, prepareDesignModel } from '../stores/assemblyBuild';
import { designPreparedRecord, useDesignModelStore } from '../stores/importedModel';
import { assemblyImportedSetup, assemblyRadiatorLabel } from '../stores/assemblySimulation';
import { currentDocumentLoad, serializeDesign, useDesignStore } from '../stores/design';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { useDocumentStore } from '../stores/document';
import type { ImportedSolveSubmission } from './actions';
import { assemblyDriverBlocker, resolveAssemblySetup, type PreparedSubmission } from './importedSubmission';

/** The live document has to own a current prepared mesh before it can be planned. */
export const NEEDS_BUILD = 'NEEDS_BUILD';

export function designAssemblySnapshot(): PreparedSubmission {
  const { design } = useDesignStore.getState();
  if (!design.assembly) throw new Error('This design has no Sources and baffle assembly.');
  const record = designPreparedRecord();
  const slot = useDesignModelStore.getState();
  if (!record || !slot.modelId || !designModelCurrent(design)) throw new Error(NEEDS_BUILD);
  const options = useSolveOptionsStore.getState().options() as ImportedSolveSubmission['options'];
  const { setup, notice } = resolveAssemblySetup(assemblyImportedSetup(), design.assembly, options,
    design.simulation, id => assemblyRadiatorLabel(design, id));
  return {
    modelId: slot.modelId,
    prepared: { ingest_id: record.ingest_id, manifest_sha256: record.manifest_sha256, artifact_sha256: record.artifact_sha256 },
    setup, replay: null, design: serializeDesign(design),
    runName: useDocumentStore.getState().designName, pressPrefix: 'assembly', notice,
  };
}

/** The WG adapter adds only placement and preparation to a ready-mesh submission. */
export async function prepareDesignAssembly(fetcher: typeof fetch = fetch): Promise<PreparedSubmission> {
  const { design, designRevision } = useDesignStore.getState();
  const { designUid } = useDocumentStore.getState();
  const load = currentDocumentLoad();
  if (!design.assembly) throw new Error('This design has no Sources and baffle assembly.');
  const assertCurrent = () => {
    if (useDesignStore.getState().designRevision !== designRevision || currentDocumentLoad() !== load
      || useDocumentStore.getState().designUid !== designUid) {
      throw new Error('The design changed while it was being prepared. Press Solve again.');
    }
  };
  const options = useSolveOptionsStore.getState().options() as ImportedSolveSubmission['options'];
  const driverProblem = assemblyDriverBlocker(assemblyImportedSetup(), options, id => assemblyRadiatorLabel(design, id));
  if (driverProblem) throw new Error(driverProblem);
  if (!designModelGeometryCurrent(design)) {
    const context = await fetchAssemblyContext(serializeDesign(design), fetcher).catch((reason: unknown) => {
      if (assemblyContextRefused(reason)) throw reason;
      return null;
    });
    assertCurrent();
    const [problem] = context ? [...placementProblems(design.assembly, { context, assembly: design.assembly }).values()].flat() : [];
    if (problem) throw new Error(`${problem} Open Sources and baffle to fix it.`);

  }
  const built = await prepareDesignModel(fetcher);
  if (built?.status === 'cancelled') throw new DOMException('Stopped', 'AbortError');
  assertCurrent();
  if (built?.status !== 'published') throw new Error('A newer revision of this design is being built. Solve again when it is ready.');
  return designAssemblySnapshot();
}
