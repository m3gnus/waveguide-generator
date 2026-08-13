import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import type { ImportedSolveSubmission } from './actions';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { decorateRunName, incrementTrailingDigits, nextRunLabel, nextRunName, runNameDateFor, runNameFromFilename } from './runNaming';
import { projectSubmittedDesign, projectSubmittedImport } from './submittedProjection';

function projection() {
  resetSolveOptionsStore();
  return projectSubmittedDesign(designForFamily('R-OSSE'), useSolveOptionsStore.getState().options());
}

function importedProjection(ingestId: string) {
  return projectSubmittedImport({
    geometry: {
      type: 'imported', ingest_id: ingestId, manifest_sha256: `manifest:${ingestId}`, artifact_sha256: `artifact:${ingestId}`,
      drive_channels: [{ id: 'drive', source_ids: ['source'], motion: 'normal' }],
      mesh: { rigid_size_mm: 5, transition_mm: 5, source_size_mm: { source: 2 } },
      acknowledged_findings: [], skipped_source_ids: [], exterior_only: false,
    },
    options: { ...useSolveOptionsStore.getState().options(), engine: 'metal', symmetry: 'auto' },
  } satisfies ImportedSolveSubmission);
}

describe('design-tracking run names', () => {
  const now = new Date(2026, 7, 12, 12);

  it('increments trailing digits in place and preserves padding', () => {
    expect(incrementTrailingDigits('asro68')).toBe('asro69');
    expect(incrementTrailingDigits('horn_007')).toBe('horn_008');
    expect(incrementTrailingDigits('horn_999')).toBe('horn_1000');
    expect(incrementTrailingDigits('horn', 2)).toBe('horn02');
    expect(incrementTrailingDigits('horn9', 3)).toBe('horn010');
  });

  it('appends 2 when no trailing digits exist', () => {
    expect(incrementTrailingDigits('winner')).toBe('winner2');
  });

  it('keeps the same name for an unchanged submitted design', () => {
    const baseline = projection();
    expect(nextRunName({ outputName: 'asro68', nameSourceProjection: baseline }, structuredClone(baseline)))
      .toBe('asro68');
  });

  it('increments only after the submitted design changes', () => {
    const baseline = projection();
    const changed = structuredClone(baseline);
    changed.design.scale = 2;
    expect(nextRunName({ outputName: 'asro68', nameSourceProjection: baseline }, changed)).toBe('asro69');
  });

  it('can turn design-change numbering off or apply a minimum width', () => {
    const baseline = projection();
    const changed = structuredClone(baseline);
    changed.design.scale = 2;
    expect(nextRunName({
      outputName: 'horn', nameSourceProjection: baseline,
      runNameNumberPosition: 'off', runNameNumberFormat: 'natural',
    }, changed)).toBe('horn');
    expect(nextRunName({
      outputName: 'horn', nameSourceProjection: baseline,
      runNameNumberPosition: 'suffix', runNameNumberFormat: '2-digit',
    }, changed)).toBe('horn02');
  });

  it('uses a file stem, then horn for an untitled document, before a baseline exists', () => {
    const current = projection();
    expect(nextRunName({ outputName: 'stale', nameSourceProjection: null }, current, '/tmp/My horn.cfg')).toBe('My horn');
    expect(nextRunName({ outputName: 'stale', nameSourceProjection: null }, current, '')).toBe('horn');
    expect(runNameFromFilename('horn_007.mwg')).toBe('horn_007');
  });

  it('lets a manual edit reset the baseline for later increments', () => {
    const baseline = projection();
    const manual = { outputName: 'winner', nameSourceProjection: baseline };
    expect(nextRunName(manual, baseline)).toBe('winner');
    const changed = structuredClone(baseline);
    changed.design.scale = 1.1;
    expect(nextRunName(manual, changed)).toBe('winner2');
  });

  it('leaves labels undated by default and supports both date positions', () => {
    expect(decorateRunName('horn', { runNameDatePosition: 'off', runNameDateFormat: 'yymmdd' }, now)).toBe('horn');
    expect(decorateRunName('horn', { runNameDatePosition: 'prefix', runNameDateFormat: 'yymmdd' }, now)).toBe('260812_horn');
    expect(decorateRunName('horn', { runNameDatePosition: 'suffix', runNameDateFormat: 'yymmdd' }, now)).toBe('horn_260812');
    expect(runNameDateFor(now, 'yyyy-mm-dd')).toBe('2026-08-12');
  });

  it('increments the core before adding a date suffix', () => {
    const baseline = projection();
    const changed = structuredClone(baseline);
    changed.design.scale = 2;
    const naming = {
      outputName: 'horn',
      nameSourceProjection: baseline,
      runNameDatePosition: 'suffix' as const,
      runNameDateFormat: 'yymmdd' as const,
    };

    expect(nextRunName(naming, changed)).toBe('horn2');
    expect(nextRunLabel(naming, changed, '', now)).toBe('horn2_260812');
  });

  it('keeps an imported run name for the same ingest and increments for a new ingest', () => {
    resetSolveOptionsStore();
    const baseline = importedProjection('wgi_first');
    const naming = { outputName: 'cad-run', nameSourceProjection: baseline };

    expect(nextRunName(naming, structuredClone(baseline))).toBe('cad-run');
    expect(nextRunName(naming, importedProjection('wgi_second'))).toBe('cad-run2');
  });
});
