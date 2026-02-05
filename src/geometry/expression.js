
// --- MATH UTILS ---

/**
 * Enhanced Expression Parser (Desmos-like)
 * Supports:
 * - Implicit multiplication: 2x, 2sin(p), p cos(p), (a)(b)
 * - Case insensitivity
 * - JavaScript Math object functions (native support)
 * - Aliases: ln -> log, log -> log10
 * - Power operator: ^ -> **
 * - Constants: pi, e
 * - Helper functions: deg(x), rad(x), fmin/fmax, fmod, etc.
 */
export function parseExpression(expr) {
    if (typeof expr !== 'string') return () => expr || 0;
    if (!expr.trim()) return () => 0;

    try {
        // 1. Normalize to lowercase
        let clean = expr.toLowerCase().trim();

        // 2. Handle power operator first (before any other substitution)
        clean = clean.replace(/\^/g, '**');

        // 3. Replace known functions with Math.FUNC
        // Only using functions that exist natively in JavaScript's Math object
        const funcMap = {
            // Basic trigonometric
            'sin': 'Math.sin', 'cos': 'Math.cos', 'tan': 'Math.tan',
            'asin': 'Math.asin', 'acos': 'Math.acos', 'atan': 'Math.atan',
            'atan2': 'Math.atan2',
            
            // Hyperbolic
            'sinh': 'Math.sinh', 'cosh': 'Math.cosh', 'tanh': 'Math.tanh',
            'asinh': 'Math.asinh', 'acosh': 'Math.acosh', 'atanh': 'Math.atanh',
            
            // Exponential and logarithmic
            'exp': 'Math.exp', 'expm1': 'Math.expm1',
            'ln': 'Math.log', 'log': 'Math.log10', 'log2': 'Math.log2',
            'log10': 'Math.log10', 'log1p': 'Math.log1p',
            'exp2': '__exp2',
            
            // Power and root
            'pow': 'Math.pow', 'sqrt': 'Math.sqrt', 'cbrt': 'Math.cbrt',
            'hypot': 'Math.hypot',
            
            // Rounding
            'ceil': 'Math.ceil', 'floor': 'Math.floor', 'round': 'Math.round',
            'trunc': 'Math.trunc',
            
            // Absolute and sign
            'abs': 'Math.abs', 'sign': 'Math.sign',
            'fabs': 'Math.abs',
            
            // Min/Max (fmin/fmax handled as helpers, not here to avoid conflicts)
            'min': 'Math.min', 'max': 'Math.max',
        };

        // Sort by length descending so 'asin' is matched before 'sin', 'sinh' before 'sin'
        const funcNames = Object.keys(funcMap).sort((a, b) => b.length - a.length);

        // Replace functions with their Math.* equivalents
        // Use word boundary to avoid partial matches
        for (const name of funcNames) {
            const before = clean;
            clean = clean.replace(new RegExp(`\\b${name}\\b`, 'g'), funcMap[name]);
            // Debug: uncomment to trace replacements
            // if (before !== clean && expr.includes('log1p')) console.log(`${name}: ${before} -> ${clean}`);
        }

        // 4. Handle constants (after functions, so 'exp' is already replaced)
        // pi can be used as constant or function pi()
        clean = clean.replace(/\bpi\s*\(\s*\)/g, 'Math.PI'); // pi() -> Math.PI
        clean = clean.replace(/\bpi\b/g, 'Math.PI'); // pi -> Math.PI
        // Only replace standalone 'e', not 'e' inside Math.xxx
        clean = clean.replace(/(?<![a-zA-Z.])e(?![a-zA-Z])/g, 'Math.E');

        // 5. Insert Implicit Multiplication
        //    Strategy: work with the string that has Math.func already substituted.
        //    Math.func( should NEVER get * inserted. We only insert * between:
        //    - number and variable:       2p -> 2*p
        //    - number and Math.func:      2Math.sin -> 2*Math.sin
        //    - number and open paren:     2( -> 2*(
        //    - variable and open paren:   p( -> p*(  (but NOT Math.sin( !)
        //    - variable and Math.func:    pMath.sin -> p*Math.sin
        //    - close paren and anything:  )2 -> )*2, )p -> )*p, )Math -> )*Math, )( -> )*(
        //    - variable and variable:     p p -> p*p (with space)

        // Digit followed by variable, Math, or open paren
        // BUT avoid inserting * for function names like exp2, log2, atan2, log1p, expm1
        // Check if digit is preceded by letters or dots (part of a function name or Math.)
        clean = clean.replace(/(?<![a-zA-Z_.])(\d)\s*([a-z_])/g, '$1*$2');
        clean = clean.replace(/(?<![a-zA-Z_.])(\d)\s*(\()/g, '$1*$2');
        clean = clean.replace(/(?<![a-zA-Z_.])(\d)\s*(Math\.)/g, '$1*$2');

        // Close paren followed by digit, variable, Math, or open paren
        clean = clean.replace(/\)\s*(\d)/g, ')*$1');
        clean = clean.replace(/\)\s*([a-z_])/g, ')*$1');
        clean = clean.replace(/\)\s*(Math\.)/g, ')*$1');
        clean = clean.replace(/\)\s*(\()/g, ')*(');

        // Variable (single letter like p) followed by open paren or Math
        // BUT we must not match the end of Math.sin, Math.abs etc.
        // Only match if the letter is NOT preceded by a dot or other letters
        clean = clean.replace(/(?<![a-zA-Z.])([a-z_])\s*(\()/g, '$1*$2');
        clean = clean.replace(/(?<![a-zA-Z.])([a-z_])\s*(Math\.)/g, '$1*$2');

        // Variable followed by variable with space
        clean = clean.replace(/(?<![a-zA-Z.])([a-z_])\s+(?=[a-z_](?![a-zA-Z.]))/g, '$1*');

        // 6. Add helper functions in scope
        // Create function with helper functions available
        const helperFunctions = `
            const deg = (rad) => rad * 180 / Math.PI;
            const rad = (deg) => deg * Math.PI / 180;
            const fmod = (x, y) => x % y;
            const remainder = (x, y) => x - Math.round(x / y) * y;
            const copysign = (x, y) => Math.sign(y) * Math.abs(x);
            const fdim = (x, y) => Math.max(x - y, 0);
            const fma = (x, y, z) => x * y + z;
            const fmin = Math.min;
            const fmax = Math.max;
            const pi = Math.PI;
            const __exp2 = (x) => Math.pow(2, x);
        `;

        // Create function with 'p' as the standard polar angle variable
        return new Function('p', helperFunctions + `return ${clean};`);

    } catch (e) {
        console.warn("Expression parsing error:", expr, e);
        try {
            let simple = expr.toLowerCase()
                .replace(/\babs\b/g, 'Math.abs')
                .replace(/\bcos\b/g, 'Math.cos')
                .replace(/\bsin\b/g, 'Math.sin')
                .replace(/\btan\b/g, 'Math.tan')
                .replace(/\bsqrt\b/g, 'Math.sqrt')
                .replace(/\^/g, '**');
            return new Function('p', `return ${simple};`);
        } catch (e2) {
            return () => 0;
        }
    }
}

// Helper for debugging/validation in console
if (typeof window !== 'undefined') {
    window.testExpressionParser = (expr, pVal = 1) => {
        try {
            const fn = parseExpression(expr);
            console.log(`Expr: "${expr}" -> Result(p=${pVal}):`, fn(pVal));
            return fn(pVal);
        } catch (e) {
            console.error(e);
        }
    };
}
