import { calculateOSGOS } from '../../src/geometry/hornModels.js';
import { parseExpression } from '../../src/geometry/expression.js';

describe('OS-GOS Geometry', () => {
    test('calculateOSGOS returns valid geometry', () => {
        // Test parameters for OS-GOS
        const testParams = {
            L: 150,
            a: parseExpression('48.5 - 5.6*cos(2*p)^5 - 31*sin(p)^12'),
            a0: 15.5,
            r0: 12.7,
            k: 7.0,
            s: parseExpression('0.58 + 0.2*cos(p)^2'),
            n: 4.158,
            q: 0.991,
            h: 0.0,
            gosType: 0,
            gosFactor: 1.0
        };

        const result = calculateOSGOS(testParams.L / 2, 0, testParams);

        expect(result).toBeDefined();
        expect(result.x).toBeDefined();
        expect(result.y).toBeDefined();
        expect(typeof result.x).toBe('number');
        expect(typeof result.y).toBe('number');
        expect(result.y).toBeGreaterThan(0);
        expect(isNaN(result.y)).toBe(false);
    });
});