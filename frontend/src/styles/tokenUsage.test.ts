import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

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

// Composite the actual translucent CSS fill over each ground used by panels
// and dialogs, then apply the WCAG relative-luminance contrast formula.
const tokenCss = new TextDecoder().decode(readFileSync('src/styles/tokens.css'));
const appCss = new TextDecoder().decode(readFileSync('src/styles/app.css'));
const themeTokens = (theme: 'light' | 'dark') => {
  const [dark, light] = tokenCss.split('[data-theme="light"]');
  return Object.fromEntries([...(theme === 'light' ? dark + light : dark)
    .matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((match) => [match[1], match[2].trim()]));
};
function rgb(value: string, tokens: Record<string, string>): number[] {
  const resolved = value.replace(/var\((--[\w-]+)\)/g, (_, name: string) => tokens[name]);
  if (resolved.includes('var(')) return rgb(resolved, tokens);
  if (resolved.startsWith('#')) return [1, 3, 5].map(index => parseInt(resolved.slice(index, index + 2), 16));
  return resolved.replace(/rgb\(|\)/g, '').split(',').map(Number);
}
function contrast(foreground: number[], background: number[]): number {
  const luminance = (color: number[]) => color.map(channel => {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

describe.each(['dark', 'light'] as const)('%s primary button contrast', theme => {
  it.each(['normal', 'hover'])('keeps %s small text at or above 4.5:1 on panel and dialog grounds', state => {
    const tokens = themeTokens(theme);
    const selector = state === 'normal' ? '.btn-primary,' : '.btn-primary:hover:not(:disabled),';
    const rule = appCss.slice(appCss.indexOf(selector)).split('}')[0];
    const foreground = rgb(rule.match(/^\s*color:\s*([^;]+);/m)![1], tokens);
    const alpha = Number(rule.match(/background: rgba\(var\(--acc-rgb\), ([.\d]+)\)/)![1]);
    const accent = rgb(tokens['--acc-rgb'], tokens);
    for (const ground of ['--surface-canvas', '--surface-panel', '--surface-raised', '--surface-floating']) {
      const background = rgb(tokens[ground], tokens).map((channel, index) => channel * (1 - alpha) + accent[index] * alpha);
      expect(contrast(foreground, background), `${theme} ${state} on ${ground}`).toBeGreaterThanOrEqual(4.5);
    }
  });
});
