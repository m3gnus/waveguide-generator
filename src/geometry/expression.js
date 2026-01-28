
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
        // 1. Normalize to lowercase
        let clean = expr.toLowerCase().trim();

        // 2. Handle power operator first (before any other substitution)
        clean = clean.replace(/\^/g, '**');

        // 3. Replace known functions with Math.FUNC_ placeholder
        //    Using FUNC_ prefix so we can identify them during implicit mult
        const funcMap = {
            'sinh': 'Math.sinh', 'cosh': 'Math.cosh', 'tanh': 'Math.tanh',
            'asin': 'Math.asin', 'acos': 'Math.acos', 'atan': 'Math.atan',
            'sqrt': 'Math.sqrt', 'cbrt': 'Math.cbrt',
            'floor': 'Math.floor', 'ceil': 'Math.ceil', 'round': 'Math.round',
            'sign': 'Math.sign',
            'abs': 'Math.abs',
            'sin': 'Math.sin', 'cos': 'Math.cos', 'tan': 'Math.tan',
            'exp': 'Math.exp',
            'max': 'Math.max', 'min': 'Math.min', 'pow': 'Math.pow',
            'ln': 'Math.log', 'log': 'Math.log10',
        };

        // Sort by length descending so 'asin' is matched before 'sin', 'sinh' before 'sin'
        const funcNames = Object.keys(funcMap).sort((a, b) => b.length - a.length);

        // Replace functions with their Math.* equivalents
        // Use word boundary to avoid partial matches
        for (const name of funcNames) {
            clean = clean.replace(new RegExp(`\\b${name}\\b`, 'g'), funcMap[name]);
        }

        // 4. Handle constants (after functions, so 'exp' is already replaced)
        clean = clean.replace(/\bpi\b/g, 'Math.PI');
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
        clean = clean.replace(/(\d)\s*([a-z_])/g, '$1*$2');
        clean = clean.replace(/(\d)\s*(\()/g, '$1*$2');
        clean = clean.replace(/(\d)\s*(Math\.)/g, '$1*$2');

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

        // Create function with 'p' as the standard polar angle variable
        return new Function('p', `return ${clean};`);

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
window.testExpressionParser = (expr, pVal = 1) => {
    try {
        const fn = parseExpression(expr);
        console.log(`Expr: "${expr}" -> Result(p=${pVal}):`, fn(pVal));
        return fn(pVal);
    } catch (e) {
        console.error(e);
    }
};
