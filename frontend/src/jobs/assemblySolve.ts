/**
 * Solve a design assembly: the one model Solve (`POST /api/models/{id}/solve`)
 * on the compiled record of the design on screen, with the setup the shared
 * Simulation sections show.
 */
import { assemblyBuildCurrent, requestAssemblyBuild, useAssemblyBuildStore } from '../stores/assemblyBuild';
import { assemblyImportedSetup } from '../stores/assemblySimulation';
import { useDesignStore } from '../stores/design';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { solveModel, type ImportedSolveSubmission } from './actions';
import { resolveAssemblySetup } from './importedSubmission';

export interface AssemblySolveResult { jobId: string; notice: string | null }

export async function solveAssembly(fetcher: typeof fetch = fetch,
  newPressId: () => string = () => globalThis.crypto.randomUUID()): Promise<AssemblySolveResult> {
  const { design } = useDesignStore.getState();
  if (!design.assembly) throw new Error('This design has no Sources and baffle assembly.');
  if (!assemblyBuildCurrent(useAssemblyBuildStore.getState(), design)) {
    const built = await requestAssemblyBuild(fetcher);
    if (built?.status !== 'published') throw new Error('A newer revision of this design is being built. Solve again when it is ready.');
  }
  const published = useAssemblyBuildStore.getState().published;
  if (!published || !assemblyBuildCurrent(useAssemblyBuildStore.getState(), useDesignStore.getState().design)) {
    throw new Error('The design changed while its assembly was built. Solve again.');
  }
  const { setup, notice } = resolveAssemblySetup(
    assemblyImportedSetup(), design.assembly,
    useSolveOptionsStore.getState().options() as ImportedSolveSubmission['options'], design.simulation,
  );
  const jobId = await solveModel(published.model.model_id, {
    prepared_id: published.record.ingest_id,
    client_request_id: `assembly-${newPressId()}`,
    setup: { schema_version: 1, ...setup } as unknown as Parameters<typeof solveModel>[1]['setup'],
  }, fetcher);
  return { jobId, notice };
}
