
// --- MATH UTILS ---
// --- MATH UTILS ---

/**
 * Enhanced Expression Parser (Desmos-like)
 * Supports:
 * - Implicit multiplication: 2x, 2sin(p), p cos(p), (a)(b)
 * - Case insensitivity
 * - Aliases: ln -> log, log -> log10
 * - Power operator: ^ -> **
 */
export function parseExpression(expr) {
    if (typeof expr !== 'string') return () => expr || 0;
    if (!expr.trim()) return () => 0;

    try {
        // 1. Normalize
        let clean = expr.toLowerCase();

        // 2. Handle constants
        clean = clean.replace(/\bpi\b/g, 'Math.PI');
        clean = clean.replace(/\be\b/g, 'Math.E');

        // 3. Handle Operators
        clean = clean.replace(/\^/g, '**');

        // 4. Protect known functions from implicit multiplication parsing
        // We temporarily replace them with placeholders starting with @
        // e.g. "2sin(p)" -> "2@sin(p)". This lets us identify "number followed by function"
        const funcs = [
            'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'sinh', 'cosh', 'tanh',
            'abs', 'sqrt', 'cbrt', 'exp', 'floor', 'ceil', 'round', 'sign',
            'max', 'min', 'pow'
        ];

        // Special mapping for logs
        // ln -> Math.log (natural log)
        // log -> Math.log10 (base 10)
        clean = clean.replace(/\bln\b/g, '@log');
        clean = clean.replace(/\blog\b/g, '@log10');

        for (const f of funcs) {
            clean = clean.replace(new RegExp(`\\b${f}\\b`, 'g'), `@${f}`);
        }

        // 5. Insert Implicit Multiplication (*)

        // Case: Digit followed by (Function, Variable, or Open Paren)
        // 2@sin -> 2*@sin, 2p -> 2*p, 2( -> 2*(
        clean = clean.replace(/(\d)\s*([a-z_\(@])/g, '$1*$2');

        // Case: Variable followed by (Function, Variable, or Open Paren)
        // p@sin -> p*@sin, p( -> p*(  <-- BE CAREFUL! "sin(" is valid.
        // But "p(" where p is a variable is implicit mult. 
        // Since we protected functions with @, any normal [a-z] followed by ( is implicit mult
        // UNLESS it's a variable being called as function? (not supported here)
        clean = clean.replace(/([a-z_])\s*([@\(\[])/g, '$1*$2');

        // Case: Variable followed by Variable (e.g. "p p")
        clean = clean.replace(/([a-z_])\s+([a-z_])/g, '$1*$2');

        // Case: Closing Paren followed by (Digit, Variable, Function, Open Paren)
        // )2 -> )*2, )p -> )*p, )@sin -> )*@sin, )( -> )*(
        clean = clean.replace(/\)\s*([\d\.a-z_@\(])/g, ')*$1');

        // 6. Restore Functions with Math. prefix
        clean = clean.replace(/@/g, 'Math.');

        // 7. Final Sanity Checks
        // Remove multiple * if generated (e.g. 2**p is power, don't break it)
        // But 2* *p is bad. 
        // Our regexes shouldn't generate ** unless it was ^ substitution.
        // Just Ensure "Math." isn't preceded by implicit stuff strangely?

        // Create function
        // 'p' is the standard variable for polar angle
        return new Function('p', `return ${clean};`);

    } catch (e) {
        console.warn("Expression parsing error:", expr, e);
        // Fallback to simple parser or return 0
        try {
            // Last ditch attempt with simple substitution
            let simple = expr.toLowerCase()
                .replace(/abs|cos|sin|tan|sqrt/g, m => `Math.${m}`)
                .replace(/\^/g, '**');
            return new Function('p', `return ${simple};`);
        } catch (e2) {
            return () => 0;
        }
    }
}

// Helper for debugging/validation in console
window.testExpressionParser = (expr, pVal = 1) => {
    try {
        const fn = parseExpression(expr);
        console.log(`Expr: "${expr}" -> Result(p=${pVal}):`, fn(pVal));
        return fn(pVal);
    } catch (e) {
        console.error(e);
    }
};
