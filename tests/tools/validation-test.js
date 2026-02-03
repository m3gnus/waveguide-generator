/**
 * BEM Validation Test Script
 *
 * Run this to test the validation system against mock solver data.
 * Usage: node tests/tools/validation-test.js
 */

import { createValidationManager } from '../../src/validation/index.js';

// Create validation manager
const validator = createValidationManager();

// Generate mock BEM results (matching improved MockBEMSolver output)
function generateMockResults(config = {}) {
    const {
        numFrequencies = 50,
        freqStart = 100,
        freqEnd = 10000,
        baseSpl = 110,  // Realistic horn sensitivity
        cutoffFreq = 500,
        rhoC = 415,  // Characteristic impedance of air
        addNoise = true,
        introduceError = null  // 'low_spl', 'high_spl', 'nan_values', 'bad_di', 'wild_jumps'
    } = config;

    const frequencies = [];
    for (let i = 0; i < numFrequencies; i++) {
        frequencies.push(freqStart + (freqEnd - freqStart) * (i / (numFrequencies - 1)));
    }

    // Generate physically realistic SPL data
    const splData = frequencies.map((f, i) => {
        let spl;
        if (f < cutoffFreq) {
            // Below cutoff: 12 dB/octave rolloff
            const rolloff = 12 * Math.log2(cutoffFreq / f);
            spl = baseSpl - rolloff;
        } else {
            // Above cutoff: flat with slight HF rolloff
            const hfRolloff = f > 8000 ? 3 * Math.log2(f / 8000) : 0;
            spl = baseSpl - hfRolloff;
        }

        if (addNoise) spl += (Math.random() - 0.5) * 1.0;

        // Introduce specific errors for testing
        if (introduceError === 'low_spl' && i < 5) spl = 20;
        if (introduceError === 'high_spl' && i > 40) spl = 180;
        if (introduceError === 'nan_values' && i === 25) spl = NaN;
        if (introduceError === 'wild_jumps' && i === 20) spl += 25;

        return spl;
    });

    // Generate realistic DI data (6-15 dB range, increasing with frequency)
    const diData = frequencies.map((f, i) => {
        let di;
        if (f < cutoffFreq) {
            di = 3.0 + 3.0 * (f / cutoffFreq);
        } else {
            di = 6.0 + 4.5 * Math.log2(f / cutoffFreq);
        }
        di = Math.max(3.0, Math.min(18.0, di));

        if (addNoise) di += (Math.random() - 0.5) * 0.4;

        if (introduceError === 'bad_di') di = 25 - di; // Inverted trend, out of bounds

        return di;
    });

    // Generate realistic impedance data (approaches ρc at high frequencies)
    const zReal = frequencies.map((f, i) => {
        const fRatio = f / cutoffFreq;
        let z;
        if (fRatio < 1) {
            z = rhoC * (fRatio ** 2) / (1 + fRatio ** 2);
        } else {
            z = rhoC * (1 - 0.1 * Math.exp(-fRatio) * Math.cos(2 * Math.PI * fRatio));
        }
        if (addNoise) z += (Math.random() - 0.5) * 10;
        return z;
    });

    const zImag = frequencies.map((f, i) => {
        const fRatio = f / cutoffFreq;
        let z;
        if (fRatio < 1) {
            z = rhoC * fRatio / (1 + fRatio ** 2);
        } else {
            z = rhoC * 0.1 * Math.exp(-fRatio) * Math.sin(2 * Math.PI * fRatio);
        }
        if (addNoise) z += (Math.random() - 0.5) * 10;
        return z;
    });

    return {
        frequencies,
        spl_on_axis: {
            frequencies,
            spl: splData
        },
        di: {
            frequencies,
            di: diData
        },
        impedance: {
            frequencies,
            real: zReal,
            imaginary: zImag
        },
        directivity: {
            horizontal: [],
            vertical: [],
            diagonal: []
        }
    };
}

// Console colors
const colors = {
    reset: '\x1b[0m',
    green: '\x1b[32m',
    red: '\x1b[31m',
    yellow: '\x1b[33m',
    blue: '\x1b[34m',
    cyan: '\x1b[36m',
    dim: '\x1b[2m'
};

function printHeader(title) {
    console.log('\n' + colors.blue + '═'.repeat(60) + colors.reset);
    console.log(colors.cyan + '  ' + title + colors.reset);
    console.log(colors.blue + '═'.repeat(60) + colors.reset);
}

function printCheck(check) {
    const icon = check.passed
        ? colors.green + '✓'
        : (check.severity === 'error' ? colors.red + '✗' : colors.yellow + '⚠');
    console.log(`  ${icon} ${colors.reset}${check.name}: ${colors.dim}${check.message}${colors.reset}`);
}

function printResult(result) {
    const status = result.passed
        ? colors.green + 'PASSED'
        : (result.severity === 'error' ? colors.red + 'FAILED' : colors.yellow + 'WARNING');
    console.log(`\n  Status: ${status}${colors.reset}`);
    console.log(`  Message: ${result.message}`);

    if (result.checks) {
        console.log('\n  Checks:');
        result.checks.forEach(printCheck);
    }
}

// Run tests
console.log('\n' + colors.cyan + '╔════════════════════════════════════════════════════════════╗' + colors.reset);
console.log(colors.cyan + '║         BEM VALIDATION TEST SUITE                          ║' + colors.reset);
console.log(colors.cyan + '╚════════════════════════════════════════════════════════════╝' + colors.reset);

// Test 1: Normal mock data (should pass)
printHeader('Test 1: Normal Mock Data (Expected: PASS)');
const normalResults = generateMockResults({ addNoise: true });
const normalValidation = validator.validatePhysicalBehavior(normalResults);
printResult(normalValidation);

// Test 2: Data with low SPL values (should warn/fail)
printHeader('Test 2: Low SPL Values (Expected: WARNING/FAIL)');
const lowSplResults = generateMockResults({ introduceError: 'low_spl' });
const lowSplValidation = validator.validatePhysicalBehavior(lowSplResults);
printResult(lowSplValidation);

// Test 3: Data with high SPL values (should warn/fail)
printHeader('Test 3: High SPL Values (Expected: WARNING/FAIL)');
const highSplResults = generateMockResults({ introduceError: 'high_spl' });
const highSplValidation = validator.validatePhysicalBehavior(highSplResults);
printResult(highSplValidation);

// Test 4: Data with NaN values (should fail)
printHeader('Test 4: NaN Values (Expected: FAIL)');
const nanResults = generateMockResults({ introduceError: 'nan_values' });
const nanValidation = validator.validatePhysicalBehavior(nanResults);
printResult(nanValidation);

// Test 5: Data with wild jumps (should warn)
printHeader('Test 5: Wild Jumps in SPL (Expected: WARNING)');
const jumpResults = generateMockResults({ introduceError: 'wild_jumps' });
const jumpValidation = validator.validatePhysicalBehavior(jumpResults);
printResult(jumpValidation);

// Test 6: Data with inverted DI trend (should warn)
printHeader('Test 6: Inverted DI Trend (Expected: WARNING)');
const badDiResults = generateMockResults({ introduceError: 'bad_di' });
const badDiValidation = validator.validatePhysicalBehavior(badDiResults);
printResult(badDiValidation);

// Test 7: Reference comparison
printHeader('Test 7: Reference Comparison (Exponential Horn)');
const refResults = generateMockResults({ baseSpl: 110 }); // Standard horn sensitivity
const refValidation = validator.validateAgainstReference(refResults, 'exponential_1inch');
printResult(refValidation);

// Test 8: Full validation report
printHeader('Test 8: Full Validation Report');
const fullReport = validator.runFullValidation(normalResults, {
    cutoffFrequency: 500,
    referenceHorn: 'exponential_1inch'
});
console.log('\n  Overall:', fullReport.overallPassed ? colors.green + 'PASSED' : colors.red + 'FAILED', colors.reset);
console.log('\n  Summary:');
console.log(colors.dim + fullReport.summary + colors.reset);

// Print test summary
printHeader('TEST SUMMARY');
const tests = [
    { name: 'Normal Mock Data', expected: true, actual: normalValidation.passed },
    { name: 'Low SPL Detection', expected: false, actual: lowSplValidation.passed },
    { name: 'High SPL Detection', expected: false, actual: highSplValidation.passed },
    { name: 'NaN Detection', expected: false, actual: nanValidation.passed },
    { name: 'Jump Detection', expected: false, actual: jumpValidation.passed },
    { name: 'Bad DI Detection', expected: false, actual: badDiValidation.passed },
];

let passed = 0;
let failed = 0;

tests.forEach(test => {
    const success = test.expected === test.actual;
    if (success) passed++; else failed++;
    const icon = success ? colors.green + '✓' : colors.red + '✗';
    console.log(`  ${icon}${colors.reset} ${test.name}: ${success ? 'Correct' : 'INCORRECT'}`);
});

console.log(`\n  Results: ${colors.green}${passed} passed${colors.reset}, ${colors.red}${failed} failed${colors.reset}`);
console.log('\n');
