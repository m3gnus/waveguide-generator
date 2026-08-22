import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const appCss = new TextDecoder().decode(readFileSync('src/styles/app.css'));
const tokensCss = new TextDecoder().decode(readFileSync('src/styles/tokens.css'));

function declarations(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)]
    .map(([, name, value]) => [name, value.trim()]));
}

const rootBlock = tokensCss.match(/:root\s*\{([\s\S]*?)\n\}/)?.[1] ?? '';
const lightBlock = tokensCss.match(/\[data-theme="light"\]\s*\{([\s\S]*?)\n\}/)?.[1] ?? '';
const themes = {
  dark: declarations(rootBlock),
  light: { ...declarations(rootBlock), ...declarations(lightBlock) },
};

function resolveToken(tokens: Record<string, string>, name: string, seen = new Set<string>()): string {
  if (seen.has(name)) throw new Error(`Circular token reference: ${name}`);
  seen.add(name);
  const value = tokens[name];
  if (!value) throw new Error(`Missing token: ${name}`);
  const reference = value.match(/^var\((--[\w-]+)\)$/)?.[1];
  return reference ? resolveToken(tokens, reference, seen) : value;
}

function luminance(hex: string): number {
  const channels = hex.match(/[\da-f]{2}/gi)?.map((part) => Number.parseInt(part, 16) / 255);
  if (!channels || channels.length !== 3) throw new Error(`Expected a six-digit hex colour, got ${hex}`);
  const [red, green, blue] = channels.map((value) => (
    value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  ));
  return red * 0.2126 + green * 0.7152 + blue * 0.0722;
}

function contrast(left: string, right: string): number {
  const values = [luminance(left), luminance(right)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

describe('normal text token contrast', () => {
  it('uses the readable foreground token for the run-filter placeholder', () => {
    const rule = appCss.match(/\.jobs-filter input::placeholder\s*\{([^}]*)\}/)?.[1] ?? '';
    expect(rule).toContain('color: var(--fg2)');
  });

  it.each(Object.entries(themes))('%s foreground usages stay at or above 4.5:1', (_theme, tokens) => {
    const everySurface = ['--surface-canvas', '--surface-panel', '--surface-field', '--surface-floating'];
    const usages = ['--fg', '--fg1', '--fg2', '--fg3', '--fg4']
      .flatMap((foreground) => everySurface.map((background) => [foreground, background] as const));
    usages.forEach(([foreground, background]) => {
      const ratio = contrast(resolveToken(tokens, foreground), resolveToken(tokens, background));
      expect(ratio, `${foreground} on ${background}`).toBeGreaterThanOrEqual(4.5);
    });
  });
});
