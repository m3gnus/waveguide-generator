import { readFileSync } from 'node:fs';
import { parseAst } from 'rolldown/parseAst';
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
    const selector = state === 'normal' ? '.btn-primary {' : '.btn-primary:hover:not(:disabled) {';
    const rule = appCss.slice(appCss.indexOf('\n' + selector)).split('}')[0];
    const foreground = rgb(rule.match(/^\s*color:\s*([^;]+);/m)![1], tokens);
    const alpha = Number(rule.match(/background: rgba\(var\(--acc-rgb\), ([.\d]+)\)/)![1]);
    const accent = rgb(tokens['--acc-rgb'], tokens);
    for (const ground of ['--surface-canvas', '--surface-panel', '--surface-raised', '--surface-floating']) {
      const background = rgb(tokens[ground], tokens).map((channel, index) => channel * (1 - alpha) + accent[index] * alpha);
      expect(contrast(foreground, background), `${theme} ${state} on ${ground}`).toBeGreaterThanOrEqual(4.5);
    }
  });
});

// Vite's existing parser handles JSX syntax, including multiline attributes and
// runtime expressions. TypeScript 7's native checker has no JS parser API.
type Node = { type: string; start: number; end: number; [key: string]: any };
function presentationViolations(source: string, ownedSelectors: string[], file = ''): string[] {
  const failures: string[] = [];
  const syntax = parseAst(source, { lang: 'tsx' }) as unknown as Node;
  function visit(node: Node, parent: HTMLElement) {
    if (node.type === 'JSXElement') {
      const element = opening(node.openingElement, parent);
      for (const child of node.children) visit(child, element);
      return;
    }
    if (node.type === 'JSXOpeningElement') { opening(node, parent); return; }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) {
        for (const child of value) if (child?.type) visit(child, parent);
      } else if (value && typeof value === 'object' && 'type' in value) visit(value as Node, parent);
    }
  }
  function opening(node: Node, parent: HTMLElement): HTMLElement {
    const attr = (name: string) => node.attributes.find((item: Node) => item.name?.name === name)?.value;
    const tag = node.name.name;
    let element = parent;
    if (tag && /^[a-z]/.test(tag)) {
      element = document.createElement(tag);
      const classes = attr('className');
      if (classes?.type === 'Literal') element.className = classes.value;
      else if (classes) element.className = [...source.slice(classes.expression.start, classes.expression.end)
        .matchAll(/['"`]([\w -]+)['"`]/g)].map(match => match[1]).join(' ');
      parent.append(element);
      const hasClass = classes?.type === 'Literal' ? Boolean(classes.value.trim()) : Boolean(classes);
      if (tag === 'button' && !hasClass && !(file === '../App.tsx' && attr('style')) && !ownedSelectors.some(selector => {
        try { return element.matches(selector); } catch { return false; }
      })) failures.push(`Unstyled button at ${node.start}`);
    }
    // The retired primary alias is semantic only; dynamic branches must also
    // name its shared appearance owner.
    const classValue = attr('className');
    const classSource = classValue ? source.slice(classValue.start, classValue.end) : '';
    if (tag === 'button' && /(?:^|["\'\s])primary(?:["\'\s]|$)/.test(classSource) && !/\bbtn-primary\b/.test(classSource))
      failures.push(`Unowned primary button at ${node.start}`);
    const style = attr('style')?.expression;
    if (style?.type === 'ObjectExpression') for (const item of style.properties) {
      const colorValue = item.value?.type === 'Literal' ? item.value.value
        : item.value?.type === 'TemplateLiteral' && item.value.expressions.length === 0
          ? item.value.quasis.map((part: Node) => part.value.cooked).join('') : undefined;
      if (/^(?:color|background(?:Color|Image)?|border(?:Top|Right|Bottom|Left)?(?:Color)?|outline(?:Color)?|accentColor|caretColor|textDecorationColor|fill|stroke|boxShadow|textShadow)$/.test(item.key?.name ?? item.key?.value ?? '')
        && typeof colorValue === 'string'
        && !/^(?:transparent|currentColor|inherit|none)$/.test(colorValue)
        && !(colorValue.includes('var(') && !/#|(?:rgb|hsl)a?\(\s*[\d.#]/i.test(colorValue.replace(/var\([^)]*\)/g, '')))) {
        // The last-resort error boundary must render even if application CSS fails.
        const fallbackColors = ['#16181d', '#e6e8ec', '#a9adb6', '1px solid #3a3f4a', '#242832', '#8ab4f8'];
        if (file !== '../App.tsx' || !fallbackColors.includes(colorValue))
          failures.push(`Literal style color at ${item.start}: ${colorValue}`);
      }
    }
    return element;
  }
  // These reusable controls are rendered in a styled host supplied by their
  // consumer, outside this file's JSX ancestry. Keep the exact owning hosts.
  const hosts: Record<string, string> = {
    '../sketch/SelectionProperties.tsx': 'sketch-context',
    '../sketch/kit/SketchTools.tsx': 'sketch-toolbar',
  };
  const host = document.createElement('div');
  host.className = hosts[file] ?? '';
  visit(syntax, host);
  return failures;
}

it('guards JSX button ownership and literal inline colors without scanning runtime values', () => {
  // A concrete ancestor class rule owns specialized menu/list/segmented controls;
  // global resets and focus styles never make a new bare action styled.
  const css = Object.keys(import.meta.glob('../**/*.css')).map(path =>
    new TextDecoder().decode(readFileSync(`src/styles/${path}`)).replace(/\/\*[\s\S]*?\*\//g, '')).join('\n');
  const selectors = [...css.matchAll(/([^{}]+)\{[^{}]*\}/g)].flatMap(match => match[1].split(','))
    .map(selector => selector.trim()).filter(selector => /\.[\w-]+/.test(selector) && /\bbutton\b/.test(selector));
  const failures = Object.keys(import.meta.glob('../**/*.tsx')).filter(path => !/\.test\.tsx$/.test(path))
    .flatMap(path => presentationViolations(new TextDecoder().decode(readFileSync(`src/styles/${path}`)), selectors, path).map(message => `${path}: ${message}`));
  expect(failures).toEqual([]);
});

it('detects bare controls and colors, accepting multiline classes, variables and dynamic chart colors', () => {
  expect(presentationViolations('<button\n disabled>New action</button>', [])).toHaveLength(1);
  expect(presentationViolations('<button {...props}>New action</button>', [])).toHaveLength(1);
  expect(presentationViolations('<button className={saved ? "" : "primary"}>Save</button>', [])).toHaveLength(1);
  expect(presentationViolations('<button className="" style={{margin: 2}}>Action</button>', [])).toHaveLength(1);
  expect(presentationViolations('<div style={{border: "1px solid var(--hair)", background: "rgba(var(--acc-rgb), .2)"}}/>', [])).toEqual([]);
  expect(presentationViolations('<button className={busy ? "btn" : "btn-primary"} style={{ color: "var(--fg)", backgroundColor: chartColor }}>Action</button>', [])).toEqual([]);
  expect(presentationViolations('<div style={{color: "#ffffff"}}/>', [])).toHaveLength(1);
  expect(presentationViolations('<div style={{backgroundImage: `linear-gradient(#fff, #000)`}}/>', [])).toHaveLength(1);
  expect(presentationViolations('<div className="segments"><button>One</button></div>', ['.segments button'])).toEqual([]);
});
