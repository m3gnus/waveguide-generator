import { readFileSync } from 'fs';
import { ATHConfigParser } from '../src/config/parser.js';

// TEST 1: OSSE flat-key (tritonia2)
const osse = readFileSync('_resources/example configs/251227tritonia2.txt', 'utf-8');
const osseResult = ATHConfigParser.parse(osse);
console.log('=== OSSE (tritonia2) ===');
console.log('Type:', osseResult.type);
console.log('a (Coverage):', osseResult.params.a ? 'OK' : 'MISSING');
console.log('L:', osseResult.params.L);
console.log('r0:', osseResult.params.r0, '(should be ~15.95)');
console.log('n:', osseResult.params.n);
console.log('morphTarget:', osseResult.params.morphTarget);
console.log('morphCorner:', osseResult.params.morphCorner);
console.log('angularSegments:', osseResult.params.angularSegments);
console.log('quadrants:', osseResult.params.quadrants);
console.log('encDepth:', osseResult.params.encDepth);
console.log('encSpaceL:', osseResult.params.encSpaceL, 'encSpaceT:', osseResult.params.encSpaceT);
console.log('encEdge:', osseResult.params.encEdge);
console.log('abecSimType:', osseResult.params.abecSimType);
console.log('abecF1:', osseResult.params.abecF1, 'abecF2:', osseResult.params.abecF2);

// TEST 2: R-OSSE block (asromain)
const rosse = readFileSync('_resources/example configs/251226asromain.txt', 'utf-8');
const rosseResult = ATHConfigParser.parse(rosse);
console.log('\n=== R-OSSE (asromain) ===');
console.log('Type:', rosseResult.type);
console.log('R:', rosseResult.params.R ? 'OK (expression)' : 'MISSING');
console.log('a:', rosseResult.params.a ? 'OK (expression)' : 'MISSING');
console.log('r0:', rosseResult.params.r0);
console.log('a0:', rosseResult.params.a0);
console.log('k:', rosseResult.params.k);
console.log('q:', rosseResult.params.q);
console.log('lengthSegments:', rosseResult.params.lengthSegments);
console.log('angularSegments:', rosseResult.params.angularSegments);
console.log('wallThickness:', rosseResult.params.wallThickness);
console.log('abecSimType:', rosseResult.params.abecSimType);
console.log('abecF1:', rosseResult.params.abecF1, 'abecF2:', rosseResult.params.abecF2);
console.log('abecNumFreq:', rosseResult.params.abecNumFreq);

// Check for critical fields
let errors = 0;
if (osseResult.type !== 'OSSE') { console.log('FAIL: OSSE type detection'); errors++; }
if (!osseResult.params.a) { console.log('FAIL: OSSE Coverage.Angle normalization'); errors++; }
if (!osseResult.params.encDepth) { console.log('FAIL: OSSE enclosure depth'); errors++; }
if (!osseResult.params.encSpaceL) { console.log('FAIL: OSSE enclosure spacing'); errors++; }
if (rosseResult.type !== 'R-OSSE') { console.log('FAIL: R-OSSE type detection'); errors++; }
if (!rosseResult.params.R) { console.log('FAIL: R-OSSE mouth radius'); errors++; }
if (!rosseResult.params.angularSegments) { console.log('FAIL: R-OSSE angular segments normalization'); errors++; }
if (!rosseResult.params.abecSimType) { console.log('FAIL: R-OSSE ABEC params normalization'); errors++; }

console.log(`\n${errors === 0 ? 'ALL TESTS PASSED' : errors + ' TESTS FAILED'}`);
