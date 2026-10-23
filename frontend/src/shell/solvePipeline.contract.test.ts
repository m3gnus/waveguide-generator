import { describe, expect, it } from 'vitest';

const sources = import.meta.glob('../**/*.{ts,tsx}', { eager: true, query: '?raw', import: 'default' }) as Record<string, string>;
const production = Object.entries(sources).filter(([path]) => !/\.(test|fixtures?)\./.test(path));
const submitNames = /\b(submitDesign|solveModel|solveCadAgain|rerunWithPatch)\b/;

describe('one Solve owner', () => {
  it('keeps submission imports and job retries inside JobsCoordinator', () => {
    const violations = production.flatMap(([path, source]) => {
      if (path === './JobsCoordinator.tsx') return [];
      const imports = source.match(/import\s+(?!type\b)[\s\S]*?\sfrom\s*['"][^'"]+['"]/g) ?? [];
      return imports.some((statement) => submitNames.test(statement)) || /\bjobsSocket\s*\.\s*retryJob\b/.test(source) ? [path] : [];
    });
    expect(violations).toEqual([]);
  });

  it('exposes no bypass on the coordinator bridge', () => {
    const source = sources['./JobsCoordinator.tsx'];
    const bridge = source.slice(source.indexOf('interface CoordinatorBridgeSnapshot'), source.indexOf('const bridgeListeners'));
    expect(bridge).not.toMatch(/\b(run|acceptSubmission|solveCurrentCadImport|retry)\s*[:(]/);
    expect(bridge).toContain('continueRun(');
  });
});
