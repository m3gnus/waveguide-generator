import { parseDecimalInput } from '../design/decimalInput';
import type { Constraint, Parameter, Sketch } from './model';
import { displayValue, internalValue } from './dimensionValue';

export type Expression = { kind: 'number'; value: number } | { kind: 'ref'; name: string; from: number; to: number }
  | { kind: 'unary'; arg: Expression } | { kind: 'binary'; op: string; left: Expression; right: Expression }
  | { kind: 'call'; name: string; args: Expression[] };
const functions: Record<string, { arity: number | 'many'; run: (...v: number[]) => number }> = {
  sqrt: { arity: 1, run: Math.sqrt }, abs: { arity: 1, run: Math.abs }, round: { arity: 1, run: Math.round },
  min: { arity: 'many', run: Math.min }, max: { arity: 'many', run: Math.max },
  ...Object.fromEntries(['sin', 'cos', 'tan'].map(name => [name, { arity: 1, run: (v: number) => Math[name as 'sin'](v * Math.PI / 180) }])),
  ...Object.fromEntries(['asin', 'acos', 'atan'].map(name => [name, { arity: 1, run: (v: number) => Math[name as 'asin'](v) * 180 / Math.PI }])),
};
function unwrapScale(text: string, op: '*' | '/'): string | undefined {
  if (!text.startsWith('(') || !text.endsWith(`)${op}2`)) return;
  let depth = 0, bracket = false;
  for (let i = 0; i < text.length - 3; i++) {
    const char = text[i];
    if (char === '[') bracket = true;
    else if (char === ']') bracket = false;
    else if (!bracket) {
      if (char === '(') depth++;
      else if (char === ')' && --depth === 0) return;
    }
  }
  if (depth === 1 && !bracket) return text.slice(1, -3);
}
/** Display-unit wrappers do not consume the user's expression budget. */
export function expressionLength(text: string): number {
  const body = unwrapScale(text, '*') ?? unwrapScale(text, '/') ?? text;
  return body.replace(/\((\[[^\]]+\]|[A-Za-z_][A-Za-z_0-9.]*)[*/]2\)/g, '$1').length;
}
export function parseExpression(src: string): { ok: true; ast: Expression } | { ok: false; error: string } {
  let i = 0; const commaDecimals: boolean[] = [];
  const fail = (message: string): never => { throw new Error(`${message} Check the dimension expression.`); };
  const ws = () => { while (/\s/.test(src[i] ?? '') && i < src.length) i++; };
  function atom(): Expression {
    ws(); const from = i;
    if (src[i] === '-') { i++; return { kind: 'unary', arg: binary(3) }; }
    if (src[i] === '+') { i++; return atom(); }
    if (src[i] === '(') { i++; const e = binary(0); ws(); if (src[i++] !== ')') fail('Missing closing parenthesis.'); return e; }
    if (src[i] === '[') { const end = src.indexOf(']', ++i); if (end < 0) fail('Missing closing bracket.'); const name = src.slice(i, end).trim(); i = end + 1; if (!name) fail('Empty dimension reference.'); return { kind: 'ref', name, from, to: i }; }
    const number = src.slice(i).match(commaDecimals.at(-1) === false ? /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/ : /^(?:\d+[.,]?\d*|[.,]\d+)(?:[eE][+-]?\d+)?/);
    if (number) {
      i += number[0].length; const parsed = parseDecimalInput(number[0]);
      if (parsed.kind !== 'number') fail(parsed.kind === 'error' ? parsed.message : 'Invalid number.');
      ws(); const unit = src.slice(i).match(/^(mm|cm|in|deg|m|°)(?![\w])/);
      if (unit) i += unit[0].length;
      return { kind: 'number', value: (parsed as { value: number }).value * ({ mm: 1, cm: 10, m: 1000, in: 25.4, deg: 1, '°': 1 }[unit?.[0] ?? 'mm']!) };
    }
    const word = src.slice(i).match(/^[A-Za-z_][A-Za-z_0-9.]*/)?.[0];
    if (!word) return fail(`Expected a number or dimension at position ${i + 1}.`);
    i += word.length; const to = i; ws();
    if (src[i] === '(') {
      if (!functions[word]) fail(`Unknown function "${word}".`);
      i++;
      let depth = 0, bracket = false, semicolon = false;
      for (let j = i; j < src.length; j++) {
        const char = src[j];
        if (char === '[') bracket = true;
        else if (char === ']') bracket = false;
        else if (!bracket) {
          if (char === '(') depth++;
          else if (char === ')') { if (!depth) break; depth--; }
          else if (char === ';' && !depth) semicolon = true;
        }
      }
      commaDecimals.push(functions[word].arity === 1 || semicolon);
      const args: Expression[] = []; ws();
      if (src[i] !== ')') { args.push(binary(0)); ws(); while (src[i] === ',' || src[i] === ';') { i++; args.push(binary(0)); ws(); } }
      if (src[i++] !== ')') fail('Missing closing function parenthesis.'); commaDecimals.pop();
      const arity = functions[word].arity;
      if (arity === 'many' ? args.length < 1 : args.length !== arity) fail(`Wrong number of arguments for ${word}.`);
      return { kind: 'call', name: word, args };
    }
    return word === 'pi' ? { kind: 'number', value: Math.PI } : { kind: 'ref', name: word, from, to };
  }
  function binary(min: number): Expression {
    let left = atom(); ws();
    while (true) {
      const op = src[i], prec = ({ '+': 1, '-': 1, '*': 2, '/': 2, '^': 3 } as Record<string, number>)[op];
      if (prec === undefined || prec < min) return left;
      i++; const right = binary(op === '^' ? prec : prec + 1); left = { kind: 'binary', op, left, right }; ws();
    }
  }
  try {
    if (typeof src !== 'string' || expressionLength(src) > 200) fail('Expressions must contain at most 200 characters.');
    const ast = binary(0); ws(); if (i !== src.length) fail(`Unexpected "${src[i]}" at position ${i + 1}.`);
    return { ok: true, ast };
  } catch (e) { return { ok: false, error: (e as Error).message }; }
}
function refs(ast: Expression): Extract<Expression, { kind: 'ref' }>[] {
  return ast.kind === 'ref' ? [ast] : ast.kind === 'binary' ? [...refs(ast.left), ...refs(ast.right)] : ast.kind === 'unary' ? refs(ast.arg) : ast.kind === 'call' ? ast.args.flatMap(refs) : [];
}
type ExpressionOwner = (Constraint & { value: number }) | Parameter;
const ownerId = (owner: ExpressionOwner): string => 'name' in owner ? owner.name : owner.id;
const ownerValue = (owner: ExpressionOwner): number => 'name' in owner ? owner.value : displayValue(owner);
function reference(s: Sketch, name: string): ExpressionOwner {
  const parameter = s.parameters?.find(p => p.name === name);
  if (parameter) return parameter;
  const byId = s.constraints.find(c => 'value' in c && c.id === name);
  const matches = byId ? [byId] : s.constraints.filter(c => 'value' in c && c.label?.trim() === name);
  if (matches.length !== 1) throw new Error(`${matches.length ? 'Ambiguous' : 'Unknown'} dimension reference "${name}". Use a unique dimension name or ID.`);
  const c = matches[0];
  if (!('value' in c) || !c.driving) throw new Error(`Dimension reference "${name}" is non-driving. Reference a driving dimension.`);
  return c;
}
export function expressionDependencies(s: Sketch, id: string): string[] {
  const expression = s.parameters?.find(p => p.name === id)?.expression ?? s.constraints.find(c => c.id === id)?.expression;
  if (!expression) return [];
  const parsed = parseExpression(expression); if (!parsed.ok) throw new Error(parsed.error);
  return [...new Set(refs(parsed.ast).map(r => ownerId(reference(s, r.name))))];
}
/** Invalid imported rows remain repairable; they cannot supply a resolved dependency list. */
export function expressionDependents(s: Sketch, id: string): string[] {
  return [...s.constraints, ...(s.parameters ?? [])].filter(c => {
    try { return c.expression && expressionDependencies(s, 'name' in c ? c.name : c.id).includes(id); }
    catch { return false; }
  }).map(c => 'name' in c ? c.name : c.id);
}
/** The only owner of reference syntax, including labels the grammar cannot encode. */
export function formatReference(s: Sketch, id: string): string {
  if (s.parameters?.some(p => p.name === id)) return id === 'pi' ? '[pi]' : id;
  const label = s.constraints.find(c => c.id === id)?.label?.trim();
  const name = label && !label.includes(']') && !s.parameters?.some(p => p.name === label) && !s.constraints.some(c => 'value' in c && c.id !== id && (c.id === label || c.label?.trim() === label)) ? label : id;
  return /^[A-Za-z_][A-Za-z_0-9.]*$/.test(name) && name !== 'pi' ? name : `[${name}]`;
}
function rewriteReferences(s: Sketch, id: string, replace: (ref: Extract<Expression, { kind: 'ref' }>, text: string, source: string) => string | { from: number; to: number; text: string }): Sketch {
  function rewrite<T extends { expression?: string }>(c: T): T {
    if (!c.expression) return c;
    const parsed = parseExpression(c.expression); if (!parsed.ok) throw new Error(parsed.error);
    let expression = c.expression;
    for (const r of refs(parsed.ast).filter(r => ownerId(reference(s, r.name)) === id).reverse()) {
      const replacement = replace(r, expression.slice(r.from, r.to), expression);
      const edit = typeof replacement === 'string' ? { from: r.from, to: r.to, text: replacement } : replacement;
      expression = expression.slice(0, edit.from) + edit.text + expression.slice(edit.to);
    }
    return expression === c.expression ? c : { ...c, expression };
  }
  return { ...s, constraints: s.constraints.map(rewrite), ...(s.parameters?.length ? { parameters: s.parameters.map(rewrite) } : {}) };
}
export function renameParameterReferences(s: Sketch, from: string, to: string): Sketch {
  const renamed = { ...s, parameters: s.parameters?.map(p => p.name === from ? { ...p, name: to } : p) };
  const rewritten = rewriteReferences(s, from, () => formatReference(renamed, to));
  return { ...rewritten, parameters: rewritten.parameters?.map(p => p.name === from ? { ...p, name: to } : p) };
}

export function renameExpressionReferences(s: Sketch, id: string, name: string): Sketch {
  const renamed = { ...s, constraints: s.constraints.map(c => c.id === id ? { ...c, label: name } : c) };
  return rewriteReferences(s, id, (ref, text) => ref.name === id ? text : formatReference(renamed, id));
}
/** Expressions use display units. Compensate both the changed input and its references atomically. */
export function rescaleDiameterExpressions(s: Sketch, id: string, diameter: boolean): Sketch {
  const rewritten = rewriteReferences(s, id, (ref, text, source) => {
    const from = ref.from - 1, to = ref.to + 3;
    if (from >= 0 && source.slice(from, to) === `(${text}${diameter ? '*2' : '/2'})`)
      return { from, to, text };
    return `(${text}${diameter ? '/2' : '*2'})`;
  });
  return { ...rewritten, constraints: rewritten.constraints.map(c => c.id === id && c.expression
    ? { ...c, expression: unwrapScale(c.expression, diameter ? '/' : '*') ?? `(${c.expression})${diameter ? '*2' : '/2'}` } : c) };
}
/** Preserve the last stored value when topology or consumer cleanup deletes an input dimension. */
export function freezeRemovedExpressionReferences(before: Sketch, after: Sketch): Sketch {
  const deleted = new Set(before.constraints.filter(c => 'value' in c && !after.constraints.some(n => n.id === c.id)).map(c => c.id));
  if (!deleted.size) return after;
  let frozen: Sketch = { ...before, parameters: after.parameters };
  for (const id of deleted) {
    const dimension = before.constraints.find(c => c.id === id)!;
    if ('value' in dimension) frozen = rewriteReferences(frozen, id, () => `(${displayValue(dimension)})`);
  }
  return { ...after, ...(after.parameters?.length ? { parameters: frozen.parameters } : {}), constraints: after.constraints.map(c => {
    const old = before.constraints.find(old => old.id === c.id);
    if (!c.expression || !old?.expression || !expressionDependencies(before, c.id).some(id => deleted.has(id))) return c;
    const { expression: _expression, ...base } = c; return base;
  }) };
}
export function resolveExpressions(sketch: Sketch): { sketch: Sketch; changed: string[]; errors: { id: string; message: string }[] } {
  const changed: string[] = [], errors: { id: string; message: string }[] = [], done = new Map<string, ExpressionOwner>(), stack: string[] = [];
  function evaluate(ast: Expression, unit: Parameter['unit']): number {
    if (ast.kind === 'number') return ast.value;
    if (ast.kind === 'ref') {
      const input = reference(sketch, ast.name);
      if ('name' in input && input.unit !== 'none' && unit !== 'none' && input.unit !== unit)
        throw new Error(`Parameter "${input.name}" uses ${input.unit}, but this expression needs ${unit}. Change the parameter unit or reference a parameter with matching units.`);
      return ownerValue(visit(input));
    }
    if (ast.kind === 'unary') return -evaluate(ast.arg, unit);
    if (ast.kind === 'call') {
      const inputUnit = ['sin', 'cos', 'tan'].includes(ast.name) ? 'deg' : ['asin', 'acos', 'atan'].includes(ast.name) ? 'none' : unit;
      return functions[ast.name].run(...ast.args.map(arg => evaluate(arg, inputUnit)));
    }
    const a = evaluate(ast.left, unit), b = evaluate(ast.right, unit);
    switch (ast.op) { case '+': return a + b; case '-': return a - b; case '*': return a * b; case '/': return a / b; default: return a ** b; }
  }
  function visit(c: ExpressionOwner): ExpressionOwner {
    const id = ownerId(c), cached = done.get(id); if (cached) return cached;
    if (stack.includes(id)) throw new Error(`Expression cycle: ${[...stack.slice(stack.indexOf(id)), id].map(id => sketch.constraints.find(c => c.id === id)?.label || id).join(' → ')}. Remove a cyclic reference.`);
    stack.push(id); let next = c;
    try {
      if (c.expression) {
        const parsed = parseExpression(c.expression); if (!parsed.ok) throw new Error(parsed.error);
        const value = 'name' in c ? evaluate(parsed.ast, c.unit) : internalValue(c, evaluate(parsed.ast, c.kind === 'angle' ? 'deg' : 'mm'));
        if (!Number.isFinite(value) || !('name' in c) && ['radius', 'distance', 'pointLineDistance'].includes(c.kind) && value < 0) throw new Error(`Expression gives an invalid ${'name' in c ? 'parameter' : 'dimension'} value. Enter a finite value in its allowed range.`);
        if (value !== c.value) { changed.push(id); next = { ...c, value }; }
      }
      done.set(id, next); return next;
    } finally { stack.pop(); }
  }
  for (const c of [...sketch.constraints, ...(sketch.parameters ?? [])]) if ('value' in c && c.expression) try { visit(c); } catch (e) { errors.push({ id: ownerId(c), message: (e as Error).message }); }
  return { sketch: { ...sketch, constraints: sketch.constraints.map(c => 'value' in c ? done.get(c.id) as Constraint ?? c : c),
    ...(sketch.parameters?.length ? { parameters: sketch.parameters.map(p => done.get(p.name) as Parameter ?? p) } : {}) }, changed: [...new Set(changed)], errors };
}
