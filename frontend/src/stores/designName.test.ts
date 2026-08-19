import { describe, expect, it } from 'vitest';
import {
  blocksWithDesignTitle,
  designFilename,
  designNameForOpenedFile,
  designNameFromFilename,
  designNameSlug,
  designTitleFromBlocks,
  normalizeDesignName,
} from './designName';

describe('the one design name', () => {
  it('strips what a filename and a quoted config value cannot carry', () => {
    expect(normalizeDesignName('  ATH "Tritonia-M"  ')).toBe('ATH Tritonia-M');
    expect(normalizeDesignName('a; b {c}')).toBe('a b c');
    expect(normalizeDesignName(null)).toBe('');
    expect(normalizeDesignName('x'.repeat(200))).toHaveLength(120);
  });

  it('derives one filename from the name, without doubling the extension', () => {
    expect(designFilename('ATH Tritonia-M')).toBe('ATH_Tritonia-M.cfg');
    expect(designFilename('')).toBe('untitled.cfg');
    expect(designFilename('horn.cfg')).toBe('horn.cfg');
    // Combining marks are folded away; a letter with no ASCII decomposition
    // (thorn) is not transliterated, it is separated like any other symbol.
    expect(designNameSlug('Þröstur – horn')).toBe('rostur_horn');
  });

  it('reads a name back out of a file it saved', () => {
    expect(designNameFromFilename('/tmp/260701_horn_v13.mwg')).toBe('260701_horn_v13');
    // The richer spelling wins when it is the same name: the space in
    // "ATH Tritonia-M" survives a save-and-reopen through ATH_Tritonia-M.cfg.
    const blocks = { Report: { items: { Title: '"ATH Tritonia-M"' }, lines: [] } };
    expect(designTitleFromBlocks(blocks)).toBe('ATH Tritonia-M');
    expect(designNameForOpenedFile('ATH_Tritonia-M.cfg', blocks)).toBe('ATH Tritonia-M');
  });

  it('lets the filename override a stale imported title', () => {
    // The exact reported bug: a file renamed on disk still carried the name it
    // had in ATH. The file the user opened is the name they manage.
    const blocks = { Report: { items: { Title: '"ATH Tritonia-M"' }, lines: [] } };
    expect(designNameForOpenedFile('tritonia_mk2.cfg', blocks)).toBe('tritonia_mk2');
    expect(designNameForOpenedFile('anything.cfg', undefined)).toBe('anything');
  });

  it('states the name first in Report, rewriting the verbatim row too', () => {
    const blocks = {
      Report: {
        items: { PolarData: 'SPL_H', Title: '"old"' },
        lines: [],
        comments: ['; kept'],
        entries: ['; kept', 'Title = "old"', 'PolarData = SPL_H'],
      },
      'ABEC.Polars:SPL_H': { items: { Distance: '2' }, lines: [] },
    };
    const named = blocksWithDesignTitle(blocks, 'Tritonia-M');

    // Verbatim replay is what a parsed block serializes from, so the raw row
    // has to move or the old name would survive byte-for-byte.
    expect(named.Report.entries).toEqual(['; kept', 'Title = "Tritonia-M"', 'PolarData = SPL_H']);
    expect(named.Report.items).toEqual({ Title: '"Tritonia-M"', PolarData: 'SPL_H' });
    expect(Object.keys(named.Report.items)[0]).toBe('Title');
    expect(Object.keys(named)[0]).toBe('Report');
    // Every other block is untouched.
    expect(named['ABEC.Polars:SPL_H']).toBe(blocks['ABEC.Polars:SPL_H']);
  });

  it('creates a Report block for a design that never had one', () => {
    const named = blocksWithDesignTitle(undefined, 'Fresh Horn');
    expect(named).toEqual({
      Report: { items: { Title: '"Fresh Horn"' }, lines: [], comments: [], entries: [] },
    });
  });

  it('writes no title for an untitled design rather than inventing one', () => {
    expect(blocksWithDesignTitle({}, '')).toEqual({});
  });
});
