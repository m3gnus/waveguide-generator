import { calculateOSSE } from '../../src/geometry/hornModels.js';

describe('ATH geometry extensions', () => {
    it('applies throat extension and slot length for OSSE profile', () => {
        const params = {
            type: 'OSSE',
            L: 100,
            a: 30,
            a0: 10,
            r0: 10,
            k: 1,
            s: 0,
            n: 4,
            q: 1,
            throatExtLength: 10,
            throatExtAngle: 5,
            slotLength: 5,
            throatProfile: 1
        };

        const p = 0;
        const extAngleRad = (5 * Math.PI) / 180;
        const r0Main = params.r0 + params.throatExtLength * Math.tan(extAngleRad);

        const atThroat = calculateOSSE(0, p, params);
        const atExtMid = calculateOSSE(5, p, params);
        const atExtEnd = calculateOSSE(10, p, params);
        const atSlotMid = calculateOSSE(12, p, params);
        const atSlotEnd = calculateOSSE(15, p, params);

        expect(atThroat.y).toBeCloseTo(params.r0, 6);
        expect(atExtMid.y).toBeCloseTo(params.r0 + 5 * Math.tan(extAngleRad), 6);
        expect(atExtEnd.y).toBeCloseTo(r0Main, 6);
        expect(atSlotMid.y).toBeCloseTo(r0Main, 6);
        expect(atSlotEnd.y).toBeCloseTo(r0Main, 6);
    });

    it('supports circular-arc profile endpoints', () => {
        const params = {
            type: 'OSSE',
            L: 100,
            a: 30,
            a0: 10,
            r0: 10,
            k: 1,
            s: 0,
            n: 4,
            q: 1,
            throatProfile: 3,
            circArcRadius: 200,
            throatExtLength: 0,
            throatExtAngle: 0,
            slotLength: 0
        };

        const p = 0;
        const mouthR = params.r0 + params.L * Math.tan((params.a * Math.PI) / 180);

        const start = calculateOSSE(0, p, params);
        const end = calculateOSSE(params.L, p, params);

        expect(start.y).toBeCloseTo(params.r0, 6);
        expect(end.y).toBeCloseTo(mouthR, 6);
    });
});
