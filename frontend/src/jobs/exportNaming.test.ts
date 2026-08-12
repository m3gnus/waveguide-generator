import { describe, expect, it } from 'vitest';
import { exportStemForJob, exportTitleSlug } from './exportNaming';

describe('job export naming', () => {
  it('always leads with the permanent run number', () => {
    expect(exportStemForJob({ run_number: 123, label: 'asro68', config_summary: {} })).toBe('123_asro68');
  });

  it('uses the current job title so a rename changes future export stems', () => {
    const job = { run_number: 123, label: 'asro68', config_summary: {} };
    expect(exportStemForJob(job)).toBe('123_asro68');
    job.label = 'winner';
    expect(exportStemForJob(job)).toBe('123_winner');
  });

  it('slugifies Unicode titles only at the filesystem boundary', () => {
    const title = 'Ásró horn / prototype';
    expect(exportTitleSlug(title)).toBe('Asro_horn_prototype');
    expect(title).toBe('Ásró horn / prototype');
  });

  it('uses the design family, without a UUID, for historical jobs with no title', () => {
    expect(exportStemForJob({ run_number: 9, label: null, config_summary: { formula_type: 'OSSE' } })).toBe('9_OSSE');
  });
});
