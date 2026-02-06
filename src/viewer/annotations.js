/**
 * Cross-section visualization utilities for horn profiles.
 * Provides SVG-based 2D cross-sections of horn geometry at any axial position.
 */
import { calculateOSSE, calculateROSSE } from '../geometry/hornModels.js';

import { evalParam } from '../geometry/common.js';

/**
 * Generate SVG cross-section of horn profile at specified axial position
 * @param {Object} params - Horn parameters 
 * @param {number} axialPosition - Position along horn (0 = throat, 1 = mouth)
 * @param {number} radialSteps - Number of radial steps to sample (default: 64)
 * @returns {string} SVG string representing the cross-section
 */
export function generateCrossSectionSVG(params, axialPosition, radialSteps = 64) {
    // Sample the profile at multiple angles
    const points = [];
    let maxRadius = 0;

    // Sample profile at various angles
    for (let i = 0; i <= radialSteps; i++) {
        const p = (i / radialSteps) * Math.PI * 2;
        const L = evalParam(params.L, p);
        const z = axialPosition * L;

        let radius = 0;
        if (params.type === 'OSSE') {
            // Use the OSSE calculation function
            const profile = calculateOSSE(z, p, params);
            radius = profile.y;
        } else if (params.type === 'R-OSSE') {
            // Use the R-OSSE calculation function
            const profile = calculateROSSE(axialPosition, p, params);
            radius = profile.y;
        }

        if (radius > maxRadius) {
            maxRadius = radius;
        }

        points.push({ angle: p, radius: radius });
    }

    // Create SVG with proper scaling
    const svgWidth = 400;
    const svgHeight = 400;
    const centerX = svgWidth / 2;
    const centerY = svgHeight / 2;
    const scale = Math.min(svgWidth, svgHeight) / (maxRadius * 1.2);

    // Build SVG path
    let pathData = '';
    for (let i = 0; i < points.length; i++) {
        const point = points[i];
        const x = centerX + point.radius * Math.cos(point.angle) * scale;
        const y = centerY - point.radius * Math.sin(point.angle) * scale; // Y axis inverted in SVG

        if (i === 0) {
            pathData += `M ${x} ${y} `;
        } else {
            pathData += `L ${x} ${y} `;
        }
    }

    // Close the path
    pathData += 'Z';

    const svg = `
<svg width="${svgWidth}" height="${svgHeight}" viewBox="0 0 ${svgWidth} ${svgHeight}" xmlns="http://www.w3.org/2000/svg">
  <path d="${pathData}" fill="none" stroke="#3498db" stroke-width="2"/>
  <circle cx="${centerX}" cy="${centerY}" r="${maxRadius * scale}" fill="none" stroke="#e74c3c" stroke-width="1" stroke-dasharray="5,5"/>
</svg>`;

    return svg;
}

// Export the function for use in the viewer
