/**
 * The press of a design assembly's Solve: the one model Solve
 * (`POST /api/models/{id}/solve`) on the compiled record of the design on
 * screen, with the setup the shared Simulation sections show. The jobs
 * coordinator submits it, under the same lock and attention as every Solve.
 */
import { assemblyBuildCurrent, requestAssemblyBuild, useAssemblyBuildStore } from '../stores/assemblyBuild';
import { assemblyImportedSetup } from '../stores/assemblySimulation';
import { useDesignStore } from '../stores/design';
import { useSolveOptionsStore } from '../stores/solveOptions';
import type { ImportedSolveSubmission, ModelSolvePress } from './actions';
import { resolveAssemblySetup } from './importedSubmission';

export interface AssemblySolvePress { modelId: string; press: ModelSolvePress; notice: string | null }

/** Build the design's assembly if it is not current, then the press for its record. */
export async function prepareAssemblySolve(fetcher: typeof fetch = fetch,
  newPressId: () => string = () => globalThis.crypto.randomUUID()): Promise<AssemblySolvePress> {
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
  return {
    modelId: published.model.model_id, notice,
    press: {
      prepared_id: published.record.ingest_id,
      client_request_id: `assembly-${newPressId()}`,
      setup: { schema_version: 1, ...setup } as unknown as ModelSolvePress['setup'],
    },
  };
}
