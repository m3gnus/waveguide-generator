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
  // Null means a runtime class value; known blank branches still need a CSS owner.
  function classBranches(value?: Node): Array<string | null> {
    if (!value) return [''];
    if (value.type === 'Literal') return [typeof value.value === 'string' ? value.value : ''];
    if (value.type === 'Identifier' && value.name === 'undefined' || value.type === 'UnaryExpression' && value.operator === 'void') return [''];
    if (value.type === 'ConditionalExpression') return [...classBranches(value.consequent), ...classBranches(value.alternate)];
    if (value.type === 'LogicalExpression') return [...(value.operator === '&&' ? [''] : classBranches(value.left)), ...classBranches(value.right)];
    if (value.type === 'TemplateLiteral') {
      const fixed = value.quasis.map((part: Node) => part.value.cooked).join(' ');
      if (fixed.trim() || value.expressions.length === 0) return [fixed];
      const branches = value.expressions.map((expression: Node) => classBranches(expression));
      return branches.every((items: Array<string | null>) => items.every(item => item === '')) ? [''] : [null];
    }
    return [null];
  }
  function visit(node: Node, parent: HTMLElement) {
    if (node.type === 'JSXElement') {
      const element = opening(node.openingElement, parent, node);
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
  function opening(node: Node, parent: HTMLElement, jsx?: Node): HTMLElement {
    const attr = (name: string) => node.attributes.find((item: Node) => item.name?.name === name)?.value;
    const tag = node.name.name;
    // Attributes can contain entire action trees, including on components whose
    // children are absent (EmptyState) or rendered in a separate row (Notice).
    for (const item of node.attributes) if (item.value?.expression) visit(item.value.expression, parent);
    let element = parent;
    if (tag && /^[a-z]/.test(tag)) {
      element = document.createElement(tag);
      for (const item of node.attributes) if (item.type === 'JSXAttribute' && item.value?.type === 'Literal'
        && typeof item.value.value === 'string' && item.name.name !== 'className') element.setAttribute(item.name.name, item.value.value);
      parent.append(element);
      const style = attr('style')?.expression;
      const styleValue = (key: string) => style?.properties?.find((item: Node) => item.key?.name === key)?.value?.value;
      // Only the real, self-contained emergency boundary owns its literal palette.
      if (file === '../App.tsx' && tag === 'div' && attr('role')?.value === 'alert'
        && styleValue('background') === '#16181d' && styleValue('color') === '#e6e8ec'
        && jsx && source.slice(jsx.start, jsx.end).includes('Waveguide Generator hit a rendering error'))
        element.dataset.guardEmergencyFallback = 'true';
      const emergencyReload = file === '../App.tsx' && tag === 'button'
        && parent.closest('[data-guard-emergency-fallback]') !== null
        && (jsx?.children ?? []).some((child: Node) => child.type === 'JSXText' && child.value.trim() === 'Reload')
        && source.slice(attr('onClick')?.expression?.start, attr('onClick')?.expression?.end).replace(/\s/g, '') === '()=>window.location.reload()'
        && styleValue('border') === '1px solid #3a3f4a' && styleValue('background') === '#242832';
      // TopBar's workspace-mode radio helper is declared outside its actual
      // .theme-toggle.workspace-mode-toggle parent; that parent owns its skin.
      const workspaceRadio = file === '../shell/TopBar.tsx' && attr('role')?.value === 'radio'
        && source.slice(attr('onClick')?.expression?.start, attr('onClick')?.expression?.end).replace(/\s/g, '') === '()=>activateWorkspaceMode(value)'
        && source.slice(attr('aria-checked')?.expression?.start, attr('aria-checked')?.expression?.end).replace(/\s/g, '') === 'mode===value';
      const classes = attr('className');
      const branches = classBranches(classes?.type === 'JSXExpressionContainer' ? classes.expression : classes);
      if (tag === 'button' && !emergencyReload && !workspaceRadio && branches.some(value => {
        element.className = value ?? '';
        return value !== null && !value.trim() && !ownedSelectors.some(selector => {
          try { return element.matches(selector); } catch { return false; }
        });
      })) failures.push(`Unstyled button at ${node.start}`);
      // Descendants retain class candidates for legitimate inherited owners.
      element.className = branches.filter((value): value is string => value !== null).join(' ');
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
        if (!element.closest('[data-guard-emergency-fallback]') || !fallbackColors.includes(colorValue))
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
    '../results/ResultViewSwitch.tsx': 'results-toolbar',
  };
  const consumer = document.createElement('div');
  consumer.className = 'source-editor';
  const host = document.createElement('div');
  if (hosts[file] && file.startsWith('../sketch/')) consumer.append(host);
  host.className = hosts[file] ?? '';
  visit(syntax, host);
  return failures;
}

function appearanceSelectors(css: string): string[] {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].flatMap(match => {
    // Layout, focus and disabled-state rules cannot own a normal button skin.
    if (!/(?:^|;)\s*(?:background(?:-color|-image)?|border(?:-(?:color|style|width|top|right|bottom|left))?|box-shadow)\s*:/.test(match[2])) return [];
    const selectors = match[1].split(/,(?![^()]*\))/).map(selector => selector.trim());
    return selectors.filter(selector => /\.[\w-]+/.test(selector)
      && !/:(?:hover|focus[\w-]*|disabled|enabled|active)\b/.test(selector));
  });
}

it('guards JSX button ownership and literal inline colors without scanning runtime values', () => {
  // A concrete ancestor class rule owns specialized menu/list/segmented controls;
  // global resets and focus styles never make a new bare action styled.
  const css = Object.keys(import.meta.glob('../**/*.css')).map(path =>
    new TextDecoder().decode(readFileSync(`src/styles/${path}`)).replace(/\/\*[\s\S]*?\*\//g, '')).join('\n');
  const selectors = appearanceSelectors(css);
  const failures = Object.keys(import.meta.glob('../**/*.tsx')).filter(path => !/\.test\.tsx$/.test(path))
    .flatMap(path => presentationViolations(new TextDecoder().decode(readFileSync(`src/styles/${path}`)), selectors, path).map(message => `${path}: ${message}`));
  expect(failures).toEqual([]);
});

it('detects bare controls and colors, accepting multiline classes, variables and dynamic chart colors', () => {
  expect(presentationViolations('<button\n disabled>New action</button>', [])).toHaveLength(1);
  expect(presentationViolations('<button {...props}>New action</button>', [])).toHaveLength(1);
  expect(presentationViolations('<button className={saved ? "" : "primary"}>Save</button>', [])).toHaveLength(2);
  expect(presentationViolations('<button className="" style={{margin: 2}}>Action</button>', [])).toHaveLength(1);
  expect(presentationViolations('<div style={{border: "1px solid var(--hair)", background: "rgba(var(--acc-rgb), .2)"}}/>', [])).toEqual([]);
  expect(presentationViolations('<button className={busy ? "btn" : "btn-primary"} style={{ color: "var(--fg)", backgroundColor: chartColor }}>Action</button>', [])).toEqual([]);
  expect(presentationViolations('<div style={{color: "#ffffff"}}/>', [])).toHaveLength(1);
  expect(presentationViolations('<div style={{backgroundImage: `linear-gradient(#fff, #000)`}}/>', [])).toHaveLength(1);
  expect(presentationViolations('<div className="segments"><button>One</button></div>', ['.segments button'])).toEqual([]);
});


it('guards migrated action props and bounds every legacy exception', () => {
  expect(presentationViolations('<Notice actions={<button style={{color:"#ff0000"}}>Retry</button>}>Oops</Notice>', [])).toHaveLength(2);
  expect(presentationViolations('<EmptyState action={<button>Retry</button>}/>', [])).toHaveLength(1);
  const layout = appearanceSelectors('.settings-theme-options button { display:flex; padding:6px 12px; }');
  expect(layout).toEqual([]);
  expect(presentationViolations('<div className="settings-theme-options"><button>Retry</button></div>', layout)).toHaveLength(1);
  expect(appearanceSelectors('.x button:focus-visible { border:1px solid red; } .x button:disabled { background:red; }')).toEqual([]);
  expect(presentationViolations('<button className={busy ? "" : ""}>Retry</button>', [])).toHaveLength(1);
  expect(presentationViolations('<button className={busy ? "btn" : ""}>Retry</button>', [])).toHaveLength(1);
  expect(presentationViolations('<button className={busy ? "btn" : undefined}>Retry</button>', [])).toHaveLength(1);
  expect(presentationViolations('<button className={`${busy ? "" : ""}`}>Retry</button>', [])).toHaveLength(1);
  expect(presentationViolations('<button style={{margin:2}}>Retry</button>', [], '../App.tsx')).toHaveLength(1);
  expect(presentationViolations('<button style={{background:"#242832"}}>Retry</button>', [], '../App.tsx')).toHaveLength(2);
  expect(presentationViolations('<Notice actions={<button className="btn" style={{color:"var(--fg)"}}>Retry</button>}>Oops</Notice>', [])).toEqual([]);
  expect(presentationViolations('<div className="segments"><button className={busy ? "on" : ""}>One</button></div>', appearanceSelectors('.segments button { background:transparent; border:1px solid var(--hair); }'))).toEqual([]);
});
