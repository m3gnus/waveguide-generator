import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

// Set by the shell, layout measurements, viewport or sketch at runtime (plan §1.3).
const runtimeNames = new Set([
  '--native-top-inset', '--result-row-min', '--topbar-pad-inline', '--viewport-header-height',
]);
const runtimePrefixes = ['--vp-', '--sketch-'];

it('defines every CSS custom property used by CSS and TSX sources', () => {
  const files = import.meta.glob('../**/*.{css,tsx}');
  const sources = Object.keys(files).map((path) => ({
    path,
    text: new TextDecoder().decode(readFileSync(`src/styles/${path}`)),
  }));
  const definitions = new Set(sources.flatMap(({ text }) =>
    [...text.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1])));
  const undefinedUses = sources.flatMap(({ path, text }) =>
    [...text.matchAll(/var\(\s*(--[\w-]+)/g)]
      .map((match) => match[1])
      .filter((name) => !definitions.has(name) && !runtimeNames.has(name)
        && !runtimePrefixes.some((prefix) => name.startsWith(prefix)))
      .map((name) => `${path}: ${name}`));
  expect([...new Set(undefinedUses)]).toEqual([]);
});
