import { describe, expect, it } from 'vitest';
import type { ImportedSetup } from './importedSetup';
import { applySetupEdits, bindImportedSetup, importedSetupOf } from './importedSetup';
import { modelSolvePress, type PreparedSubmission } from './importedSubmission';

describe('saved physics wire before P6', () => {
  it.each(['assembly', 'cad', 'file'] as const)('preserves the saved %s setup and model press byte for byte', origin => {
    const saved = '{"geometry":{"drive_channels":[{"id":"hf","source_ids":["hf"],"motion":"normal"}],'
      + (origin === 'assembly' ? '"required_features":["wg-design-assembly-v1"],' : '')
      + '"skipped_source_ids":[],"exterior_only":false},"options":{"engine":"beat-cpu","frequencies_hz":[100,300,500]}}';
    const setup = JSON.parse(saved) as ImportedSetup;
    const prepared = { ingest_id: 'wgi_saved', manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact' };
    const bound = bindImportedSetup(prepared, setup);
    expect(JSON.stringify(importedSetupOf(bound))).toBe(saved);
    expect(JSON.stringify(applySetupEdits(setup, setup, structuredClone(setup)))).toBe(saved);
    const snapshot: PreparedSubmission = { modelId: 'saved', prepared, setup,
      replay: { setup: { schema_version: 1, ...setup, options: { ...setup.options } } }, runName: 'Saved',
      pressPrefix: origin === 'assembly' ? 'assembly' : 'mesh', notice: null };
    expect(JSON.stringify(modelSolvePress(snapshot, 'Saved', 'retry'))).toBe(
      `{"prepared_id":"wgi_saved","client_request_id":"${snapshot.pressPrefix}-retry","label":"Saved","setup":{"schema_version":1,${saved.slice(1)}}`,
    );
  });
});
