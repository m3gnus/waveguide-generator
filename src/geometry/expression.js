import { isDevRuntime } from '../config/runtimeMode.js';
import { debugWarn } from '../logging/debug.js';

const FUNCTIONS = Object.freeze({
  sin: Math.sin,
  cos: Math.cos,
  tan: Math.tan,
  asin: Math.asin,
  acos: Math.acos,
  atan: Math.atan,
  atan2: Math.atan2,
  sinh: Math.sinh,
  cosh: Math.cosh,
  tanh: Math.tanh,
  asinh: Math.asinh,
  acosh: Math.acosh,
  atanh: Math.atanh,
  exp: Math.exp,
  expm1: Math.expm1,
  ln: Math.log,
  log: Math.log10,
  log2: Math.log2,
  log10: Math.log10,
  log1p: Math.log1p,
  exp2: (value) => Math.pow(2, value),
  pow: Math.pow,
  sqrt: Math.sqrt,
  cbrt: Math.cbrt,
  hypot: Math.hypot,
  ceil: Math.ceil,
  floor: Math.floor,
  round: Math.round,
  trunc: Math.trunc,
  abs: Math.abs,
  sign: Math.sign,
  fabs: Math.abs,
  min: Math.min,
  max: Math.max,
  deg: (radians) => (radians * 180) / Math.PI,
  rad: (degrees) => (degrees * Math.PI) / 180,
  fmod: (x, y) => x % y,
  remainder: (x, y) => x - Math.round(x / y) * y,
  copysign: (x, y) => Math.sign(y) * Math.abs(x),
  fdim: (x, y) => Math.max(x - y, 0),
  fma: (x, y, z) => x * y + z,
  fmin: Math.min,
  fmax: Math.max,
  pi: () => Math.PI,
});

const CONSTANTS = Object.freeze({
  pi: Math.PI,
  e: Math.E,
});

class ExpressionSyntaxError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ExpressionSyntaxError';
  }
}

function isDigit(character) {
  return character >= '0' && character <= '9';
}

function isIdentifierStart(character) {
  return /[A-Za-z_]/.test(character);
}

function isIdentifierPart(character) {
  return /[A-Za-z0-9_]/.test(character);
}

function syntaxError(message, position) {
  return new ExpressionSyntaxError(`${message} at character ${position + 1}.`);
}

function tokenize(expression) {
  const tokens = [];
  let position = 0;

  while (position < expression.length) {
    const character = expression[position];

    if (/\s/.test(character)) {
      position += 1;
      continue;
    }

    if (isDigit(character) || (character === '.' && isDigit(expression[position + 1] || ''))) {
      const start = position;
      if (character === '.') {
        position += 1;
        while (isDigit(expression[position] || '')) position += 1;
      } else {
        while (isDigit(expression[position] || '')) position += 1;
        if (expression[position] === '.') {
          position += 1;
          while (isDigit(expression[position] || '')) position += 1;
        }
      }

      if (expression[position] === 'e' || expression[position] === 'E') {
        const exponentPosition = position;
        position += 1;
        if (expression[position] === '+' || expression[position] === '-') {
          position += 1;
        }
        const exponentStart = position;
        while (isDigit(expression[position] || '')) position += 1;
        if (position === exponentStart) {
          throw syntaxError('Invalid scientific-notation exponent', exponentPosition);
        }
      }

      const text = expression.slice(start, position);
      const value = Number(text);
      if (!Number.isFinite(value)) {
        throw syntaxError('Invalid number', start);
      }
      tokens.push({ type: 'number', value, position: start });
      continue;
    }

    if (isIdentifierStart(character)) {
      const start = position;
      position += 1;
      while (isIdentifierPart(expression[position] || '')) position += 1;
      tokens.push({
        type: 'identifier',
        value: expression.slice(start, position).toLowerCase(),
        position: start,
      });
      continue;
    }

    if (character === '*' && expression[position + 1] === '*') {
      tokens.push({ type: 'operator', value: '^', position });
      position += 2;
      continue;
    }

    if ('+-*/^(),'.includes(character)) {
      tokens.push({
        type:
          character === '(' || character === ')' || character === ',' ? 'punctuation' : 'operator',
        value: character,
        position,
      });
      position += 1;
      continue;
    }

    throw syntaxError(`Unsupported character "${character}"`, position);
  }

  return insertImplicitMultiplication(tokens);
}

function isFunctionCallToken(token, nextToken) {
  return (
    token?.type === 'identifier' &&
    Object.prototype.hasOwnProperty.call(FUNCTIONS, token.value) &&
    nextToken?.value === '('
  );
}

function isValueIdentifier(token, nextToken) {
  if (token?.type !== 'identifier') return false;
  if (token.value === 'p' || token.value === 'e') return true;
  return token.value === 'pi' && !isFunctionCallToken(token, nextToken);
}

function isPrimaryEnd(token, nextToken) {
  return token?.type === 'number' || token?.value === ')' || isValueIdentifier(token, nextToken);
}

function isPrimaryStart(token) {
  return token?.type === 'number' || token?.type === 'identifier' || token?.value === '(';
}

function insertImplicitMultiplication(tokens) {
  const normalized = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    const previous = normalized[normalized.length - 1];

    if (
      previous &&
      isPrimaryEnd(previous, token) &&
      isPrimaryStart(token) &&
      !isFunctionCallToken(previous, token)
    ) {
      normalized.push({ type: 'operator', value: '*', position: token.position });
    }

    normalized.push(token);
  }
  return normalized;
}

function compileExpression(expression) {
  const tokens = tokenize(expression);
  let index = 0;

  const current = () => tokens[index] || null;
  const consume = (value) => {
    if (current()?.value !== value) return false;
    index += 1;
    return true;
  };
  const requireToken = (value, message) => {
    if (!consume(value)) {
      throw syntaxError(message, current()?.position ?? expression.length);
    }
  };

  const parsePrimary = () => {
    const token = current();
    if (!token) {
      throw syntaxError('Expected a value', expression.length);
    }

    if (token.type === 'number') {
      index += 1;
      return () => token.value;
    }

    if (consume('(')) {
      const inner = parseAdditive();
      requireToken(')', 'Expected a closing parenthesis');
      return inner;
    }

    if (token.type !== 'identifier') {
      throw syntaxError('Expected a value', token.position);
    }

    index += 1;
    const name = token.value;
    const isCall = current()?.value === '(';
    if (isCall) {
      if (!Object.prototype.hasOwnProperty.call(FUNCTIONS, name)) {
        throw syntaxError(`Unknown function "${name}"`, token.position);
      }
      consume('(');
      const argumentsList = [];
      if (!consume(')')) {
        do {
          argumentsList.push(parseAdditive());
        } while (consume(','));
        requireToken(')', 'Expected a closing parenthesis');
      }
      if (name === 'pi' && argumentsList.length !== 0) {
        throw syntaxError('pi does not accept arguments', token.position);
      }
      const fn = FUNCTIONS[name];
      return (p) => fn(...argumentsList.map((argument) => argument(p)));
    }

    if (name === 'p') {
      return (p) => p;
    }
    if (Object.prototype.hasOwnProperty.call(CONSTANTS, name)) {
      const value = CONSTANTS[name];
      return () => value;
    }
    if (Object.prototype.hasOwnProperty.call(FUNCTIONS, name)) {
      throw syntaxError(`Function "${name}" requires parentheses`, token.position);
    }
    throw syntaxError(`Unknown identifier "${name}"`, token.position);
  };

  const parsePower = () => {
    const base = parsePrimary();
    if (!consume('^')) return base;
    const exponent = parseUnary();
    return (p) => Math.pow(base(p), exponent(p));
  };

  const parseUnary = () => {
    if (consume('+')) {
      const operand = parseUnary();
      return (p) => +operand(p);
    }
    if (consume('-')) {
      const operand = parseUnary();
      return (p) => -operand(p);
    }
    return parsePower();
  };

  const parseMultiplicative = () => {
    let value = parseUnary();
    while (true) {
      if (consume('*')) {
        const left = value;
        const right = parseUnary();
        value = (p) => left(p) * right(p);
        continue;
      }
      if (consume('/')) {
        const left = value;
        const right = parseUnary();
        value = (p) => left(p) / right(p);
        continue;
      }
      return value;
    }
  };

  const parseAdditive = () => {
    let value = parseMultiplicative();
    while (true) {
      if (consume('+')) {
        const left = value;
        const right = parseMultiplicative();
        value = (p) => left(p) + right(p);
        continue;
      }
      if (consume('-')) {
        const left = value;
        const right = parseMultiplicative();
        value = (p) => left(p) - right(p);
        continue;
      }
      return value;
    }
  };

  if (tokens.length === 0) {
    return () => 0;
  }

  const evaluator = parseAdditive();
  if (current()) {
    throw syntaxError(`Unexpected token "${current().value}"`, current().position);
  }
  return evaluator;
}

/**
 * Check whether a formula is accepted by the geometry expression grammar.
 * @param {unknown} expression
 * @returns {{valid: boolean, error?: string}}
 */
export function validateExpression(expression) {
  if (typeof expression !== 'string' || !expression.trim()) {
    return { valid: false, error: 'Expression must not be empty.' };
  }

  try {
    compileExpression(expression.trim());
    return { valid: true };
  } catch (error) {
    return {
      valid: false,
      error: error instanceof Error ? error.message : 'Invalid expression.',
    };
  }
}

/**
 * Parse a supported ATH-style formula without evaluating arbitrary JavaScript.
 * Supports documented math functions, constants, implicit multiplication, and
 * scientific-notation number literals.
 */
export function parseExpression(expr) {
  if (typeof expr !== 'string') return () => expr || 0;
  if (!expr.trim()) return () => 0;

  try {
    const evaluator = compileExpression(expr.trim());
    evaluator._rawExpr = expr;
    return evaluator;
  } catch (error) {
    debugWarn('Expression parsing error:', expr, error);
    return () => 0;
  }
}

// Helper for debugging/validation in console (dev/local runtime only)
if (typeof window !== 'undefined' && isDevRuntime()) {
  window.testExpressionParser = (expr, pVal = 1) => {
    try {
      const fn = parseExpression(expr);
      console.log(`Expr: "${expr}" -> Result(p=${pVal}):`, fn(pVal));
      return fn(pVal);
    } catch (error) {
      console.error(error);
    }
  };
}
