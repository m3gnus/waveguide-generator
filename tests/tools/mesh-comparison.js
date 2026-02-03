/**
 * Mesh Comparison Test Script
 *
 * Compare mesh output from the app vs ATH reference mesh for 251227tritonia4
 * Usage: node tests/tools/mesh-comparison.js
 */

import * as fs from 'fs';
import * as path from 'path';

// ============= Inline dependencies (avoiding browser-specific code) =============

// Expression parser (from expression.js, without window reference)
function parseExpression(expr) {
    if (typeof expr !== 'string') return () => expr || 0;
    if (!expr.trim()) return () => 0;

    try {
        let clean = expr.toLowerCase().trim();
        clean = clean.replace(/\^/g, '**');

        const funcMap = {
            'sinh': 'Math.sinh', 'cosh': 'Math.cosh', 'tanh': 'Math.tanh',
            'asin': 'Math.asin', 'acos': 'Math.acos', 'atan': 'Math.atan',
            'sqrt': 'Math.sqrt', 'cbrt': 'Math.cbrt',
            'floor': 'Math.floor', 'ceil': 'Math.ceil', 'round': 'Math.round',
            'sign': 'Math.sign', 'abs': 'Math.abs',
            'sin': 'Math.sin', 'cos': 'Math.cos', 'tan': 'Math.tan',
            'exp': 'Math.exp',
            'max': 'Math.max', 'min': 'Math.min', 'pow': 'Math.pow',
            'ln': 'Math.log', 'log': 'Math.log10',
        };

        const funcNames = Object.keys(funcMap).sort((a, b) => b.length - a.length);
        for (const name of funcNames) {
            clean = clean.replace(new RegExp(`\\b${name}\\b`, 'g'), funcMap[name]);
        }

        clean = clean.replace(/\bpi\b/g, 'Math.PI');
        clean = clean.replace(/(?<![a-zA-Z.])e(?![a-zA-Z])/g, 'Math.E');

        clean = clean.replace(/(\d)\s*([a-z_])/g, '$1*$2');
        clean = clean.replace(/(\d)\s*(\()/g, '$1*$2');
        clean = clean.replace(/(\d)\s*(Math\.)/g, '$1*$2');
        clean = clean.replace(/\)\s*(\d)/g, ')*$1');
        clean = clean.replace(/\)\s*([a-z_])/g, ')*$1');
        clean = clean.replace(/\)\s*(Math\.)/g, ')*$1');
        clean = clean.replace(/\)\s*(\()/g, ')*(');
        clean = clean.replace(/(?<![a-zA-Z.])([a-z_])\s*(\()/g, '$1*$2');
        clean = clean.replace(/(?<![a-zA-Z.])([a-z_])\s*(Math\.)/g, '$1*$2');
        clean = clean.replace(/(?<![a-zA-Z.])([a-z_])\s+(?=[a-z_](?![a-zA-Z.]))/g, '$1*');

        return new Function('p', `return ${clean};`);
    } catch (e) {
        console.warn("Expression parsing error:", expr, e);
        return () => 0;
    }
}

// Config parser (from parser.js)
class MWGConfigParser {
    static parse(content) {
        const result = { type: null, params: {}, blocks: {} };
        const lines = content.split('\n').map(line => {
            const commentIdx = line.indexOf(';');
            return (commentIdx !== -1 ? line.substring(0, commentIdx) : line).trim();
        }).filter(line => line.length > 0);

        let currentBlock = null;
        let currentBlockName = null;

        for (const line of lines) {
            const blockStartMatch = line.match(/^([\w.:-]+)\s*=\s*\{/);
            if (blockStartMatch) {
                currentBlockName = blockStartMatch[1];
                if (currentBlockName === 'R-OSSE') {
                    result.type = 'R-OSSE';
                    currentBlock = 'R-OSSE';
                } else if (currentBlockName === 'OSSE') {
                    result.type = 'OSSE';
                    currentBlock = 'OSSE';
                } else {
                    currentBlock = currentBlockName;
                    result.blocks[currentBlockName] = {};
                }
                continue;
            }

            if (line === '}') {
                currentBlock = null;
                currentBlockName = null;
                continue;
            }

            const eqIdx = line.indexOf('=');
            if (eqIdx > 0) {
                const key = line.substring(0, eqIdx).trim();
                const value = line.substring(eqIdx + 1).trim();

                if (currentBlock === 'R-OSSE' || currentBlock === 'OSSE') {
                    result.params[key] = value;
                } else if (currentBlock && result.blocks[currentBlock]) {
                    result.blocks[currentBlock][key] = value;
                } else {
                    result.params[key] = value;
                }
            }
        }

        if (!result.type) {
            if (result.params['Coverage.Angle'] || result.params['Length'] || result.params['Term.n']) {
                result.type = 'OSSE';
            }
        }

        if (result.type === 'OSSE' && !result.params.a) {
            const p = result.params;
            if (p['Coverage.Angle']) { p.a = p['Coverage.Angle']; }
            if (p['Throat.Angle']) { p.a0 = p['Throat.Angle']; }
            if (p['Throat.Diameter']) { p.r0 = String(parseFloat(p['Throat.Diameter']) / 2); }
            if (p['Length']) { p.L = p['Length']; }
            if (p['Term.s']) { p.s = p['Term.s']; }
            if (p['Term.n']) { p.n = p['Term.n']; }
            if (p['Term.q']) { p.q = p['Term.q']; }
            if (p['OS.h']) { p.h = p['OS.h']; }
            if (p['OS.k']) { p.k = p['OS.k']; }
            if (p['Morph.TargetShape']) { p.morphTarget = p['Morph.TargetShape']; }
            if (p['Morph.TargetWidth']) { p.morphWidth = p['Morph.TargetWidth']; }
            if (p['Morph.TargetHeight']) { p.morphHeight = p['Morph.TargetHeight']; }
            if (p['Morph.CornerRadius']) { p.morphCorner = p['Morph.CornerRadius']; }
            if (p['Morph.Rate']) { p.morphRate = p['Morph.Rate']; }
            if (p['Morph.FixedPart']) { p.morphFixed = p['Morph.FixedPart']; }
            if (p['Mesh.AngularSegments']) { p.angularSegments = p['Mesh.AngularSegments']; }
            if (p['Mesh.LengthSegments']) { p.lengthSegments = p['Mesh.LengthSegments']; }
            if (p['Mesh.CornerSegments']) { p.cornerSegments = p['Mesh.CornerSegments']; }
            if (p['Mesh.Quadrants']) { p.quadrants = p['Mesh.Quadrants']; }
            if (p['Mesh.VerticalOffset']) { p.verticalOffset = p['Mesh.VerticalOffset']; }
        }

        // Parse Mesh.Enclosure block
        const encBlock = result.blocks['Mesh.Enclosure'];
        if (encBlock) {
            const p = result.params;
            if (encBlock.Depth) { p.encDepth = encBlock.Depth; }
            if (encBlock.EdgeRadius) { p.encEdge = encBlock.EdgeRadius; }
            if (encBlock.EdgeType) { p.encEdgeType = encBlock.EdgeType; }
            if (encBlock.Spacing) {
                const parts = encBlock.Spacing.split(',').map(s => s.trim());
                if (parts.length >= 4) {
                    p.encSpaceL = parts[0];
                    p.encSpaceT = parts[1];
                    p.encSpaceR = parts[2];
                    p.encSpaceB = parts[3];
                }
            }
        }

        return result;
    }
}

// OSSE horn model (corrected - matches hornModels.js)
function calculateOSSE(z, p, params) {
    const val = (v) => (typeof v === 'function' ? v(p) : v);

    const L = parseFloat(params.L) || 130;
    const a = (val(params.a) * Math.PI) / 180;  // Coverage angle in radians
    const s = params.s ? val(params.s) : 0;     // Termination parameter s

    const a0 = (parseFloat(params.a0) || 10) * Math.PI / 180;  // Throat angle in radians
    const r0 = parseFloat(params.r0) || 12.7;
    const k = parseFloat(params.k) || 1;   // ATH default is 1
    const n = parseFloat(params.n) || 4;
    const q = parseFloat(params.q) || 1;

    // rGOS - the geometric oblate spheroid formula
    const rGOS = Math.sqrt(
        Math.pow(k * r0, 2) +
        2 * k * r0 * z * Math.tan(a0) +
        Math.pow(z, 2) * Math.pow(Math.tan(a), 2)
    ) + r0 * (1 - k);

    // rTERM - the superelliptical termination
    let rTERM = 0;
    if (z > 0 && n > 0 && q > 0) {
        const zNorm = q * z / L;
        if (zNorm <= 1.0) {
            rTERM = (s * L / q) * (1 - Math.pow(1 - Math.pow(zNorm, n), 1 / n));
        } else {
            rTERM = (s * L / q);
        }
    }

    return { x: z, y: rGOS + rTERM };
}

// Morphing (from morphing.js) - Updated to compute circumscribed rectangle
function applyMorphing(r, t, p, params) {
    const target = parseFloat(params.morphTarget) || 0;
    if (target === 0) return r;

    const rate = parseFloat(params.morphRate) || 3;
    const fixed = parseFloat(params.morphFixed) || 0;
    const cornerRadius = parseFloat(params.morphCorner) || 0;

    if (t <= fixed) return r;

    const tNorm = (t - fixed) / (1 - fixed);
    const morph = Math.pow(tNorm, rate);

    if (target === 1) {
        // Rectangle morph
        // If width/height not specified, use circumscribed rectangle of raw profile
        let halfW = parseFloat(params.morphWidth) / 2;
        let halfH = parseFloat(params.morphHeight) / 2;

        // If not specified, use the pre-computed mouth bounds
        if (isNaN(halfW) || halfW <= 0) {
            halfW = params._mouthMaxX || r;
        }
        if (isNaN(halfH) || halfH <= 0) {
            halfH = params._mouthMaxZ || r;
        }

        const cr = Math.min(cornerRadius, halfW - 0.1, halfH - 0.1);

        const cosP = Math.cos(p);
        const sinP = Math.sin(p);
        const absCos = Math.abs(cosP);
        const absSin = Math.abs(sinP);

        let targetR;

        // For rounded rectangle, we need to compute distance from origin to the outline
        // This is more complex when cornerRadius > 0
        if (cr > 0.1) {
            // Rounded rectangle polar distance
            const innerW = halfW - cr;
            const innerH = halfH - cr;

            // Determine which region we're in
            const px = halfW * absCos;
            const pz = halfH * absSin;

            if (absCos < 0.0001) {
                // On vertical axis
                targetR = halfH;
            } else if (absSin < 0.0001) {
                // On horizontal axis
                targetR = halfW;
            } else {
                // General case - check if we're in corner region
                const rW = halfW / absCos;
                const rH = halfH / absSin;
                const rInnerW = innerW / absCos;
                const rInnerH = innerH / absSin;

                if (rW <= rInnerW || rH <= rInnerH) {
                    // In the flat region (not corner)
                    targetR = Math.min(rW, rH);
                } else {
                    // In corner region - need to find intersection with arc
                    // Corner center is at (innerW, innerH) for first quadrant
                    const cornerCx = innerW;
                    const cornerCz = innerH;

                    // Line from origin at angle p intersects circle of radius cr centered at (cornerCx, cornerCz)
                    // Parametric: (t*cos(p), t*sin(p))
                    // Distance to corner center: sqrt((t*cos(p) - cornerCx)^2 + (t*sin(p) - cornerCz)^2) = cr
                    // Solve for t
                    const A = 1;  // cos^2 + sin^2
                    const B = -2 * (cornerCx * absCos + cornerCz * absSin);
                    const C = cornerCx * cornerCx + cornerCz * cornerCz - cr * cr;

                    const discriminant = B * B - 4 * A * C;
                    if (discriminant >= 0) {
                        targetR = (-B + Math.sqrt(discriminant)) / (2 * A);
                    } else {
                        targetR = Math.min(rW, rH);
                    }
                }
            }
        } else {
            // Sharp rectangle (no corner radius)
            if (absCos < 0.0001) {
                targetR = halfH;
            } else if (absSin < 0.0001) {
                targetR = halfW;
            } else {
                const rW = halfW / absCos;
                const rH = halfH / absSin;
                targetR = Math.min(rW, rH);
            }
        }

        return r + (targetR - r) * morph;
    } else if (target === 2) {
        // Circle
        const targetRadius = parseFloat(params.morphWidth) || r;
        return r + (targetRadius - r) * morph;
    }

    return r;
}

// Quadrant parsing
function parseQuadrants(quadrants) {
    const q = String(quadrants || '1234');

    if (q === '1234' || q === '') {
        return { startAngle: 0, endAngle: Math.PI * 2, fullCircle: true };
    }

    if (q === '14') {
        return { startAngle: -Math.PI / 2, endAngle: Math.PI / 2, fullCircle: false };
    }

    if (q === '12') {
        return { startAngle: 0, endAngle: Math.PI, fullCircle: false };
    }

    if (q === '1') {
        return { startAngle: 0, endAngle: Math.PI / 2, fullCircle: false };
    }

    return { startAngle: 0, endAngle: Math.PI * 2, fullCircle: true };
}

// Enclosure builder (updated to match ATH architecture - front at MOUTH Y, not throat)
function addEnclosureGeometry(vertices, indices, params, verticalOffset, quadrantInfo) {
    const radialSteps = params.angularSegments;
    const lastRowStart = params.lengthSegments * (radialSteps + 1);

    // Get mouth Y position (where front baffle inner edge connects)
    const mouthY = vertices[lastRowStart * 3 + 1];  // Y of first mouth vertex

    const sL = parseFloat(params.encSpaceL) || 25;
    const sT = parseFloat(params.encSpaceT) || 25;
    const sR = parseFloat(params.encSpaceR) || 25;
    const sB = parseFloat(params.encSpaceB) || 25;
    const depth = parseFloat(params.encDepth);
    const edgeR = parseFloat(params.encEdge) || 0;

    const isRightHalf = quadrantInfo && (params.quadrants === '14' || params.quadrants === 14);

    // Find bounding box at MOUTH ring
    let maxX = -Infinity, minX = Infinity, maxZ = -Infinity, minZ = Infinity;
    for (let i = 0; i <= radialSteps; i++) {
        const idx = lastRowStart + i;
        const mx = vertices[idx * 3];
        const mz = vertices[idx * 3 + 2];
        if (mx > maxX) maxX = mx;
        if (mx < minX) minX = mx;
        if (mz > maxZ) maxZ = mz;
        if (mz < minZ) minZ = mz;
    }

    // Enclosure outer bounds based on MOUTH dimensions + spacing
    let boxRight, boxLeft, boxTop, boxBot;

    if (isRightHalf) {
        boxRight = maxX + sR;
        boxLeft = 0;
        boxTop = maxZ + sT;
        boxBot = minZ - sB;
    } else {
        boxRight = maxX + sR;
        boxLeft = minX - sL;
        boxTop = maxZ + sT;
        boxBot = minZ - sB;
    }

    const startIdx = vertices.length / 3;

    const halfW = (boxRight - boxLeft) / 2;
    const halfH = (boxTop - boxBot) / 2;
    const cx = (boxRight + boxLeft) / 2;
    const cz = (boxTop + boxBot) / 2;
    const cr = Math.min(edgeR, halfW - 0.1, halfH - 0.1);

    const cornerSegs = Math.max(4, params.cornerSegments || 4);

    const outline = [];

    if (isRightHalf) {
        const addCorner = (cornerCx, cornerCz, startAngle) => {
            for (let i = 0; i <= cornerSegs; i++) {
                const a = startAngle + (i / cornerSegs) * (Math.PI / 2);
                const x = cornerCx + cr * Math.cos(a);
                const z = cornerCz + cr * Math.sin(a);
                if (x >= -0.001) {
                    outline.push({ x: Math.max(0, x), z });
                }
            }
        };

        addCorner(cx + halfW - cr, cz - halfH + cr, -Math.PI / 2);
        addCorner(cx + halfW - cr, cz + halfH - cr, 0);
        outline.push({ x: 0, z: cz + halfH });
        outline.push({ x: 0, z: cz - halfH });
    } else {
        const addCorner = (cornerCx, cornerCz, startAngle) => {
            for (let i = 0; i <= cornerSegs; i++) {
                const a = startAngle + (i / cornerSegs) * (Math.PI / 2);
                outline.push({ x: cornerCx + cr * Math.cos(a), z: cornerCz + cr * Math.sin(a) });
            }
        };

        addCorner(cx + halfW - cr, cz - halfH + cr, -Math.PI / 2);
        addCorner(cx + halfW - cr, cz + halfH - cr, 0);
        addCorner(cx - halfW + cr, cz + halfH - cr, Math.PI / 2);
        addCorner(cx - halfW + cr, cz - halfH + cr, Math.PI);
    }

    const totalPts = outline.length;

    // Key positions (ATH architecture):
    // - Front baffle inner edge at mouthY
    // - Front baffle outer edge extends forward by edgeR
    // - Back baffle inner at mouthY - depth
    // - Back baffle outer extends backward by edgeR

    const frontInnerY = mouthY;
    const frontOuterY = mouthY + edgeR;  // Extends FORWARD
    const backInnerY = mouthY - depth;
    const backOuterY = mouthY - depth - edgeR;  // Extends BACKWARD

    // 1. Front Inner Ring (at mouth Y - connects to horn mouth)
    for (let i = 0; i < totalPts; i++) {
        vertices.push(outline[i].x, frontInnerY, outline[i].z);
    }

    // 2. Front Outer Ring (at mouth Y + edgeR - the front edge)
    // Shrink outline inward for the rounded front edge
    const frontOutline = [];
    for (let i = 0; i < totalPts; i++) {
        const pt = outline[i];
        const dx = cx - pt.x;
        const dz = cz - pt.z;
        const dist = Math.sqrt(dx * dx + dz * dz);
        if (dist > 0.001) {
            const nx = dx / dist;
            const nz = dz / dist;
            frontOutline.push({
                x: pt.x + nx * edgeR,
                z: pt.z + nz * edgeR
            });
        } else {
            frontOutline.push({ x: pt.x, z: pt.z });
        }
    }

    for (let i = 0; i < totalPts; i++) {
        vertices.push(frontOutline[i].x, frontOuterY, frontOutline[i].z);
    }

    // 3. Back Inner Ring (at mouth Y - depth)
    for (let i = 0; i < totalPts; i++) {
        vertices.push(outline[i].x, backInnerY, outline[i].z);
    }

    // 4. Back Outer Ring (at mouth Y - depth - edgeR)
    const backOutline = [];
    for (let i = 0; i < totalPts; i++) {
        const pt = outline[i];
        const dx = cx - pt.x;
        const dz = cz - pt.z;
        const dist = Math.sqrt(dx * dx + dz * dz);
        if (dist > 0.001) {
            const nx = dx / dist;
            const nz = dz / dist;
            backOutline.push({
                x: pt.x + nx * edgeR,
                z: pt.z + nz * edgeR
            });
        } else {
            backOutline.push({ x: pt.x, z: pt.z });
        }
    }

    for (let i = 0; i < totalPts; i++) {
        vertices.push(backOutline[i].x, backOuterY, backOutline[i].z);
    }

    const frontInnerStart = startIdx;
    const frontOuterStart = startIdx + totalPts;
    const backInnerStart = startIdx + totalPts * 2;
    const backOuterStart = startIdx + totalPts * 3;

    const sideLoopEnd = isRightHalf ? totalPts - 1 : totalPts;

    // Front edge: connect front inner to front outer (rounded front edge)
    for (let i = 0; i < sideLoopEnd; i++) {
        const i2 = (i + 1) % totalPts;
        indices.push(frontInnerStart + i, frontInnerStart + i2, frontOuterStart + i2);
        indices.push(frontInnerStart + i, frontOuterStart + i2, frontOuterStart + i);
    }

    // Side walls: connect front inner ring to back inner ring
    for (let i = 0; i < sideLoopEnd; i++) {
        const i2 = (i + 1) % totalPts;
        indices.push(frontInnerStart + i, backInnerStart + i, backInnerStart + i2);
        indices.push(frontInnerStart + i, backInnerStart + i2, frontInnerStart + i2);
    }

    // Back edge: connect back inner to back outer (beveled back edge)
    for (let i = 0; i < sideLoopEnd; i++) {
        const i2 = (i + 1) % totalPts;
        indices.push(backInnerStart + i, backInnerStart + i2, backOuterStart + i2);
        indices.push(backInnerStart + i, backOuterStart + i2, backOuterStart + i);
    }

    // Front baffle: connect MOUTH ring to enclosure front inner ring
    for (let i = 0; i < radialSteps; i++) {
        const angleRange = quadrantInfo ?
            (quadrantInfo.endAngle - quadrantInfo.startAngle) : (Math.PI * 2);
        const startAngle = quadrantInfo ? quadrantInfo.startAngle : 0;

        const p = startAngle + (i / radialSteps) * angleRange;
        const p2 = startAngle + ((i + 1) / radialSteps) * angleRange;

        let ei, ei2;
        if (isRightHalf) {
            const normalizedAngle1 = (p + Math.PI / 2) / Math.PI;
            const normalizedAngle2 = (p2 + Math.PI / 2) / Math.PI;
            const rightSidePts = 2 * (cornerSegs + 1);
            ei = Math.round(normalizedAngle1 * (rightSidePts - 1));
            ei2 = Math.round(normalizedAngle2 * (rightSidePts - 1));
            ei = Math.min(ei, rightSidePts - 1);
            ei2 = Math.min(ei2, rightSidePts - 1);
        } else {
            ei = Math.round((p / (2 * Math.PI)) * totalPts) % totalPts;
            ei2 = Math.round((p2 / (2 * Math.PI)) * totalPts) % totalPts;
        }

        const mi = lastRowStart + i;
        const mi2 = lastRowStart + i + 1;
        indices.push(mi, mi2, frontInnerStart + ei2);
        indices.push(mi, frontInnerStart + ei2, frontInnerStart + ei);
    }

    // Back cap: fan from center to back outer ring
    const backCenterIdx = vertices.length / 3;
    vertices.push(cx, backOuterY, cz);

    for (let i = 0; i < sideLoopEnd; i++) {
        const i2 = (i + 1) % totalPts;
        indices.push(backOuterStart + i, backOuterStart + i2, backCenterIdx);
    }

    // Symmetry plane closure
    if (isRightHalf) {
        const topFrontInner = frontInnerStart + totalPts - 2;
        const botFrontInner = frontInnerStart + totalPts - 1;
        const topFrontOuter = frontOuterStart + totalPts - 2;
        const botFrontOuter = frontOuterStart + totalPts - 1;
        const topBackInner = backInnerStart + totalPts - 2;
        const botBackInner = backInnerStart + totalPts - 1;
        const topBackOuter = backOuterStart + totalPts - 2;
        const botBackOuter = backOuterStart + totalPts - 1;

        // Front edge along symmetry plane
        indices.push(topFrontInner, botFrontInner, botFrontOuter);
        indices.push(topFrontInner, botFrontOuter, topFrontOuter);

        // Side wall along symmetry plane
        indices.push(topFrontInner, topBackInner, botBackInner);
        indices.push(topFrontInner, botBackInner, botFrontInner);

        // Back edge along symmetry plane
        indices.push(topBackInner, botBackInner, botBackOuter);
        indices.push(topBackInner, botBackOuter, topBackOuter);
    }
}

// Mesh builder (updated with vertical offset, quadrant support, and circumscribed morph)
function buildHornMesh(params) {
    const radialSteps = parseInt(params.angularSegments) || 80;
    const lengthSteps = parseInt(params.lengthSegments) || 20;

    const vertices = [];
    const indices = [];

    const verticalOffset = parseFloat(params.verticalOffset) || 0;
    const quadrantInfo = parseQuadrants(params.quadrants);
    const angleRange = quadrantInfo.endAngle - quadrantInfo.startAngle;

    // Pre-compute mouth bounds for circumscribed rectangle morph
    // (if morphWidth/Height not specified)
    const morphTarget = parseFloat(params.morphTarget) || 0;
    if (morphTarget === 1 && (isNaN(parseFloat(params.morphWidth)) || isNaN(parseFloat(params.morphHeight)))) {
        // Compute raw OSSE profile at mouth (t=1) to find bounding box
        let maxX = 0, maxZ = 0, minZ = 0;
        const L = parseFloat(params.L) || 120;

        // Sample many angles to find the actual bounds (use full circle for bounds calc)
        for (let i = 0; i < 360; i++) {
            const p = (i / 360) * Math.PI * 2 - Math.PI;  // -π to π
            const profile = calculateOSSE(L, p, params);
            const r = profile.y;
            const x = r * Math.cos(p);
            const z = r * Math.sin(p);

            if (x > maxX) maxX = x;
            if (z > maxZ) maxZ = z;
            if (z < minZ) minZ = z;
        }

        // Store computed bounds for morphing
        // For rectangular morph, we use max extents
        params._mouthMaxX = maxX;
        params._mouthMaxZ = Math.max(maxZ, -minZ);  // Use symmetric Z for rectangle

        console.log(`  Pre-computed mouth bounds for morph: maxX=${maxX.toFixed(2)}, maxZ=${Math.max(maxZ, -minZ).toFixed(2)}`);
    }

    for (let j = 0; j <= lengthSteps; j++) {
        const t = j / lengthSteps;

        for (let i = 0; i <= radialSteps; i++) {
            const p = quadrantInfo.startAngle + (i / radialSteps) * angleRange;

            let profile;
            if (params.type === 'OSSE') {
                const L = parseFloat(params.L) || 120;
                profile = calculateOSSE(t * L, p, params);
                const h = parseFloat(params.h) || 0;
                if (h > 0) {
                    profile.y += h * Math.sin(t * Math.PI);
                }
            } else {
                profile = { x: t * 100, y: 12.7 + t * 50 };
            }

            let x = profile.x;
            let r = profile.y;

            r = applyMorphing(r, t, p, params);

            const vx = r * Math.cos(p);
            const vy = x + verticalOffset;
            const vz = r * Math.sin(p);

            vertices.push(vx, vy, vz);
        }
    }

    if (params.type === 'OSSE' && parseFloat(params.encDepth) > 0) {
        addEnclosureGeometry(vertices, indices, params, verticalOffset, quadrantInfo);
    }

    const indexRadialSteps = quadrantInfo.fullCircle ? radialSteps : radialSteps;
    for (let j = 0; j < lengthSteps; j++) {
        for (let i = 0; i < indexRadialSteps; i++) {
            const row1 = j * (radialSteps + 1);
            const row2 = (j + 1) * (radialSteps + 1);
            indices.push(row1 + i, row1 + i + 1, row2 + i + 1);
            indices.push(row1 + i, row2 + i + 1, row2 + i);
        }
    }

    return { vertices, indices };
}

// ============= Test functions =============

function parseATHMesh(content) {
    const lines = content.split('\n');
    const nodes = [];
    const elements = [];
    let section = null;
    let nodeCount = 0;
    let elemCount = 0;

    for (const line of lines) {
        const trimmed = line.trim();

        if (trimmed === '$Nodes') {
            section = 'nodes';
            continue;
        } else if (trimmed === '$EndNodes') {
            section = null;
            continue;
        } else if (trimmed === '$Elements') {
            section = 'elements';
            continue;
        } else if (trimmed === '$EndElements') {
            section = null;
            continue;
        }

        if (section === 'nodes') {
            const parts = trimmed.split(/\s+/);
            if (parts.length === 1 && !nodeCount) {
                nodeCount = parseInt(parts[0]);
            } else if (parts.length >= 4) {
                nodes.push({
                    id: parseInt(parts[0]),
                    x: parseFloat(parts[1]),
                    y: parseFloat(parts[2]),
                    z: parseFloat(parts[3])
                });
            }
        } else if (section === 'elements') {
            const parts = trimmed.split(/\s+/);
            if (parts.length === 1 && !elemCount) {
                elemCount = parseInt(parts[0]);
            } else if (parts.length >= 8) {
                elements.push({
                    id: parseInt(parts[0]),
                    type: parseInt(parts[1]),
                    physicalTag: parseInt(parts[4]),
                    entityId: parseInt(parts[5]),
                    nodes: [parseInt(parts[6]), parseInt(parts[7]), parseInt(parts[8])]
                });
            }
        }
    }

    return { nodes, elements, nodeCount, elemCount };
}

function analyzeMeshBounds(nodes) {
    const bounds = {
        minX: Infinity, maxX: -Infinity,
        minY: Infinity, maxY: -Infinity,
        minZ: Infinity, maxZ: -Infinity
    };

    for (const node of nodes) {
        bounds.minX = Math.min(bounds.minX, node.x);
        bounds.maxX = Math.max(bounds.maxX, node.x);
        bounds.minY = Math.min(bounds.minY, node.y);
        bounds.maxY = Math.max(bounds.maxY, node.y);
        bounds.minZ = Math.min(bounds.minZ, node.z);
        bounds.maxZ = Math.max(bounds.maxZ, node.z);
    }

    return bounds;
}

function findEnclosureNodes(nodes) {
    const enclosureNodes = nodes.filter(n =>
        Math.abs(n.x) > 150 || Math.abs(n.y) > 200 || Math.abs(n.z) > 100
    );
    return enclosureNodes;
}

// Main test function
async function runTest() {
    console.log('\n╔════════════════════════════════════════════════════════════╗');
    console.log('║         MESH COMPARISON TEST (UPDATED)                     ║');
    console.log('╚════════════════════════════════════════════════════════════╝\n');

    const configPath = '../../ATHequivalent/251227tritonia4.txt';
    const meshPath = '../../ATHequivalent/251227tritonia4/ABEC_FreeStanding/251227tritonia4.msh';

    console.log('Loading ATH config from:', configPath);
    const configContent = fs.readFileSync(configPath, 'utf-8');
    const parsed = MWGConfigParser.parse(configContent);

    console.log('\n--- Parsed Config ---');
    console.log('Type:', parsed.type);
    console.log('Key parameters:');
    console.log('  Length:', parsed.params.L);
    console.log('  Throat Radius:', parsed.params.r0);
    console.log('  Coverage Angle:', (parsed.params.a || '').substring(0, 40) + '...');
    console.log('  Enclosure Depth:', parsed.params.encDepth);
    console.log('  Enclosure Spacing:', [parsed.params.encSpaceL, parsed.params.encSpaceT, parsed.params.encSpaceR, parsed.params.encSpaceB].join(', '));
    console.log('  Angular Segments:', parsed.params.angularSegments);
    console.log('  Length Segments:', parsed.params.lengthSegments);
    console.log('  Vertical Offset:', parsed.params.verticalOffset);
    console.log('  Quadrants:', parsed.params.quadrants);
    console.log('\nMorph parameters:');
    console.log('  morphTarget:', parsed.params.morphTarget);
    console.log('  morphWidth:', parsed.params.morphWidth);
    console.log('  morphHeight:', parsed.params.morphHeight);
    console.log('  morphCorner:', parsed.params.morphCorner);
    console.log('  morphRate:', parsed.params.morphRate);
    console.log('  morphFixed:', parsed.params.morphFixed);

    console.log('\nLoading ATH mesh from:', meshPath);
    const athMeshContent = fs.readFileSync(meshPath, 'utf-8');
    const athMesh = parseATHMesh(athMeshContent);

    console.log('\n--- ATH Reference Mesh ---');
    console.log('Nodes:', athMesh.nodes.length);
    console.log('Elements:', athMesh.elements.length);

    const athBounds = analyzeMeshBounds(athMesh.nodes);
    console.log('\nATH Mesh Bounds:');
    console.log('  X:', athBounds.minX.toFixed(2), 'to', athBounds.maxX.toFixed(2));
    console.log('  Y:', athBounds.minY.toFixed(2), 'to', athBounds.maxY.toFixed(2));
    console.log('  Z:', athBounds.minZ.toFixed(2), 'to', athBounds.maxZ.toFixed(2));

    const athEnclosureNodes = findEnclosureNodes(athMesh.nodes);
    console.log('\nATH Enclosure Corner Nodes:', athEnclosureNodes.length);
    console.log('Sample enclosure nodes:');
    athEnclosureNodes.slice(0, 10).forEach(n => {
        console.log(`  Node ${n.id}: (${n.x.toFixed(1)}, ${n.y.toFixed(1)}, ${n.z.toFixed(1)})`);
    });

    // Debug: Calculate raw OSSE profile at mouth (z=L) to see what dimensions we should have
    console.log('\n--- Debug: Raw OSSE profile at mouth ---');
    const L = parseFloat(parsed.params.L) || 130.69;
    const debugParams = {
        L: L,
        a: parseExpression(parsed.params.a),
        a0: parseFloat(parsed.params.a0) || 10,
        r0: parseFloat(parsed.params.r0) || 15.95,
        s: parseExpression(parsed.params.s),
        n: parseFloat(parsed.params.n) || 4.1276,
        q: parseFloat(parsed.params.q) || 0.9901,
        k: parseFloat(parsed.params.k) || 1  // ATH default is 1
    };

    // Calculate mouth radius at various angles
    const testAngles = [0, Math.PI/4, Math.PI/2, -Math.PI/4, -Math.PI/2];
    let maxMouthR = 0;
    for (const angle of testAngles) {
        const profile = calculateOSSE(L, angle, debugParams);
        const x = profile.y * Math.cos(angle);
        const z = profile.y * Math.sin(angle);
        console.log(`  Angle ${(angle * 180 / Math.PI).toFixed(0)}°: r=${profile.y.toFixed(2)}, x=${x.toFixed(2)}, z=${z.toFixed(2)}`);
        if (profile.y > maxMouthR) maxMouthR = profile.y;
    }
    console.log(`  Max mouth radius: ${maxMouthR.toFixed(2)}`);

    console.log('\n--- Building Our Mesh (with fixes) ---');
    const preparedParams = { ...parsed.params };

    for (const key of Object.keys(preparedParams)) {
        const val = preparedParams[key];
        if (typeof val === 'string') {
            const num = parseFloat(val);
            if (!isNaN(num) && String(num) === val.trim()) {
                preparedParams[key] = num;
            } else if (val.includes('sin') || val.includes('cos') || val.includes('p')) {
                preparedParams[key] = parseExpression(val);
            }
        }
    }

    preparedParams.type = parsed.type;

    const { vertices, indices } = buildHornMesh(preparedParams);

    console.log('Our mesh:');
    console.log('  Vertices:', vertices.length / 3);
    console.log('  Triangles:', indices.length / 3);

    const ourNodes = [];
    for (let i = 0; i < vertices.length; i += 3) {
        ourNodes.push({
            id: i / 3 + 1,
            x: vertices[i],
            y: vertices[i + 1],
            z: vertices[i + 2]
        });
    }

    const ourBounds = analyzeMeshBounds(ourNodes);
    console.log('\nOur Mesh Bounds:');
    console.log('  X:', ourBounds.minX.toFixed(2), 'to', ourBounds.maxX.toFixed(2));
    console.log('  Y:', ourBounds.minY.toFixed(2), 'to', ourBounds.maxY.toFixed(2));
    console.log('  Z:', ourBounds.minZ.toFixed(2), 'to', ourBounds.maxZ.toFixed(2));

    const ourEnclosureNodes = findEnclosureNodes(ourNodes);
    console.log('\nOur Enclosure Corner Nodes:', ourEnclosureNodes.length);
    if (ourEnclosureNodes.length > 0) {
        console.log('Sample enclosure nodes:');
        ourEnclosureNodes.slice(0, 10).forEach(n => {
            console.log(`  Node ${n.id}: (${n.x.toFixed(1)}, ${n.y.toFixed(1)}, ${n.z.toFixed(1)})`);
        });
    }

    console.log('\n═══════════════════════════════════════════════════════════');
    console.log('COMPARISON ANALYSIS');
    console.log('═══════════════════════════════════════════════════════════');

    const issues = [];
    const passed = [];

    const nodeRatio = ourNodes.length / athMesh.nodes.length;
    console.log(`\nNode count: ATH=${athMesh.nodes.length}, Ours=${ourNodes.length} (ratio: ${nodeRatio.toFixed(2)})`);

    console.log('\nBounds comparison:');
    const xRange = { ath: athBounds.maxX - athBounds.minX, our: ourBounds.maxX - ourBounds.minX };
    const yRange = { ath: athBounds.maxY - athBounds.minY, our: ourBounds.maxY - ourBounds.minY };
    const zRange = { ath: athBounds.maxZ - athBounds.minZ, our: ourBounds.maxZ - ourBounds.minZ };

    console.log(`  X range: ATH=${xRange.ath.toFixed(1)}, Ours=${xRange.our.toFixed(1)}`);
    console.log(`  Y range: ATH=${yRange.ath.toFixed(1)}, Ours=${yRange.our.toFixed(1)}`);
    console.log(`  Z range: ATH=${zRange.ath.toFixed(1)}, Ours=${zRange.our.toFixed(1)}`);

    const hasNegativeX = ourNodes.some(n => n.x < -0.1);
    console.log(`\nSymmetry check: Has negative X nodes = ${hasNegativeX}`);

    if (!hasNegativeX) {
        passed.push('Quadrant symmetry (x >= 0) correctly implemented');
    } else {
        issues.push('Our mesh still has negative X coordinates');
    }

    console.log('\nVertical offset check:');
    console.log(`  Config Vertical Offset: ${preparedParams.verticalOffset}`);
    console.log(`  ATH Y max (front): ${athBounds.maxY.toFixed(1)}`);
    console.log(`  Our Y max (front): ${ourBounds.maxY.toFixed(1)}`);

    // Check if vertical offset is applied - our max should be close to mouth position + offset
    if (ourBounds.maxY > 100 && ourBounds.maxY < 300) {
        passed.push('Vertical offset applied correctly');
    } else {
        issues.push(`Vertical offset may not be applied correctly (Y max: ${ourBounds.maxY.toFixed(1)})`);
    }

    console.log('\nEnclosure depth check:');
    console.log(`  ATH Y range: ${yRange.ath.toFixed(1)}`);
    console.log(`  Our Y range: ${yRange.our.toFixed(1)}`);

    if (Math.abs(yRange.ath - yRange.our) < 100) {
        passed.push('Y range approximately matches');
    } else {
        issues.push(`Y range mismatch: ATH=${yRange.ath.toFixed(1)}, Ours=${yRange.our.toFixed(1)}`);
    }

    console.log('\n═══════════════════════════════════════════════════════════');
    console.log('RESULTS');
    console.log('═══════════════════════════════════════════════════════════');

    if (passed.length > 0) {
        console.log('\n✓ PASSED:');
        passed.forEach((p, i) => console.log(`  ${i + 1}. ${p}`));
    }

    if (issues.length > 0) {
        console.log('\n✗ ISSUES:');
        issues.forEach((issue, i) => console.log(`  ${i + 1}. ${issue}`));
    }

    if (issues.length === 0) {
        console.log('\n✓ All major issues resolved!');
    }

    console.log('\n');
}

runTest().catch(console.error);
