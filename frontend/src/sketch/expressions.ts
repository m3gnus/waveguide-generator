import { parseDecimalInput } from '../design/decimalInput';
import type { Constraint, Sketch } from './model';
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
function reference(s: Sketch, name: string): Constraint & { value: number; driving: boolean } {
  const byId = s.constraints.find(c => 'value' in c && c.id === name);
  const matches = byId ? [byId] : s.constraints.filter(c => 'value' in c && c.label?.trim() === name);
  if (matches.length !== 1) throw new Error(`${matches.length ? 'Ambiguous' : 'Unknown'} dimension reference "${name}". Use a unique dimension name or ID.`);
  const c = matches[0];
  if (!('value' in c) || !c.driving) throw new Error(`Dimension reference "${name}" is non-driving. Reference a driving dimension.`);
  return c;
}
export function expressionDependencies(s: Sketch, id: string): string[] {
  const expression = s.constraints.find(c => c.id === id)?.expression;
  if (!expression) return [];
  const parsed = parseExpression(expression); if (!parsed.ok) throw new Error(parsed.error);
  return [...new Set(refs(parsed.ast).map(r => reference(s, r.name).id))];
}
/** Invalid imported rows remain repairable; they cannot supply a resolved dependency list. */
export function expressionDependents(s: Sketch, id: string): string[] {
  return s.constraints.filter(c => {
    try { return c.expression && expressionDependencies(s, c.id).includes(id); }
    catch { return false; }
  }).map(c => c.id);
}
/** The only owner of reference syntax, including labels the grammar cannot encode. */
export function formatReference(s: Sketch, id: string): string {
  const label = s.constraints.find(c => c.id === id)?.label?.trim();
  const name = label && !label.includes(']') && !s.constraints.some(c => 'value' in c && c.id !== id && (c.id === label || c.label?.trim() === label)) ? label : id;
  return /^[A-Za-z_][A-Za-z_0-9.]*$/.test(name) && name !== 'pi' ? name : `[${name}]`;
}
function rewriteReferences(s: Sketch, id: string, replace: (ref: Extract<Expression, { kind: 'ref' }>, text: string, source: string) => string | { from: number; to: number; text: string }): Sketch {
  return { ...s, constraints: s.constraints.map(c => {
    if (!c.expression) return c;
    const parsed = parseExpression(c.expression); if (!parsed.ok) throw new Error(parsed.error);
    let expression = c.expression;
    for (const r of refs(parsed.ast).filter(r => reference(s, r.name).id === id).reverse()) {
      const replacement = replace(r, expression.slice(r.from, r.to), expression);
      const edit = typeof replacement === 'string' ? { from: r.from, to: r.to, text: replacement } : replacement;
      expression = expression.slice(0, edit.from) + edit.text + expression.slice(edit.to);
    }
    return expression === c.expression ? c : { ...c, expression };
  }) };
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
  const deleted = new Set(before.constraints.filter(c => !after.constraints.some(n => n.id === c.id)).map(c => c.id));
  if (!deleted.size) return after;
  return { ...after, constraints: after.constraints.map(c => {
    const old = before.constraints.find(old => old.id === c.id);
    if (!c.expression || !old?.expression || !expressionDependencies(before, c.id).some(id => deleted.has(id))) return c;
    const { expression: _expression, ...base } = c; return base;
  }) };
}
export function resolveExpressions(sketch: Sketch): { sketch: Sketch; changed: string[]; errors: { id: string; message: string }[] } {
  const changed: string[] = [], errors: { id: string; message: string }[] = [], done = new Map<string, Constraint & { value: number }>(), stack: string[] = [];
  function evaluate(ast: Expression): number {
    if (ast.kind === 'number') return ast.value;
    if (ast.kind === 'ref') return displayValue(visit(reference(sketch, ast.name)));
    if (ast.kind === 'unary') return -evaluate(ast.arg);
    if (ast.kind === 'call') return functions[ast.name].run(...ast.args.map(evaluate));
    const a = evaluate(ast.left), b = evaluate(ast.right);
    switch (ast.op) { case '+': return a + b; case '-': return a - b; case '*': return a * b; case '/': return a / b; default: return a ** b; }
  }
  function visit(c: Constraint & { value: number }): Constraint & { value: number } {
    const cached = done.get(c.id); if (cached) return cached;
    if (stack.includes(c.id)) throw new Error(`Expression cycle: ${[...stack.slice(stack.indexOf(c.id)), c.id].map(id => sketch.constraints.find(c => c.id === id)?.label || id).join(' → ')}. Remove a cyclic reference.`);
    stack.push(c.id); let next = c;
    try {
      if (c.expression) {
        const parsed = parseExpression(c.expression); if (!parsed.ok) throw new Error(parsed.error);
        const value = internalValue(c, evaluate(parsed.ast));
        if (!Number.isFinite(value) || ['radius', 'distance', 'pointLineDistance'].includes(c.kind) && value < 0) throw new Error('Expression gives an invalid dimension value. Enter a finite value in its allowed range.');
        if (value !== c.value) { changed.push(c.id); next = { ...c, value }; }
      }
      done.set(c.id, next); return next;
    } finally { stack.pop(); }
  }
  for (const c of sketch.constraints) if ('value' in c && c.expression) try { visit(c); } catch (e) { errors.push({ id: c.id, message: (e as Error).message }); }
  return { sketch: { ...sketch, constraints: sketch.constraints.map(c => done.get(c.id) ?? c) }, changed: [...new Set(changed)], errors };
}
