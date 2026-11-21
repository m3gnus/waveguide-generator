import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import * as chartImage from './chartImage';

/**
 * `scripts/smoke_chart_clipboard.mjs` is the native WebKit check of the
 * clipboard rule `copyChartPng` depends on. It is run by hand, so nothing else
 * notices when it calls an API that has gone: this does.
 */
describe('the WebKit clipboard smoke script', () => {
  const script = readFileSync('../scripts/smoke_chart_clipboard.mjs', 'utf8');
  const called = [...new Set([...script.matchAll(/chartImage\.(\w+)\(/g)].map(([, name]) => name))];

  it('calls only chartImage functions that exist', () => {
    expect(called.length).toBeGreaterThan(0);
    for (const name of called) expect(typeof (chartImage as Record<string, unknown>)[name], name).toBe('function');
  });

  it('uses the current signatures: copy takes a draw function, save takes a pending blob', () => {
    expect(script).toMatch(/chartImage\.copyChartPng\(\(\) =>/);
    expect(script).toMatch(/chartImage\.saveChartPng\(Promise\.resolve\(/);
    expect(chartImage.copyChartPng.length).toBe(1);
  });
});
