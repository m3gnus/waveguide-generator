/**
 * CAD Builder - Parametric B-Rep Horn Geometry via OpenCascade
 *
 * Converts horn profile functions (calculateOSSE, calculateROSSE) into
 * OpenCascade B-Rep geometry (TopoDS_Shape) suitable for STEP export.
 *
 * Architecture:
 *   1. Sample profile at multiple azimuthal angles and axial stations
 *   2. Create closed B-spline wire at each axial station
 *   3. Loft through wires via BRepOffsetAPI_ThruSections
 *   4. Add acoustic source surface at throat
 *   5. Optionally add wall thickness for free-standing models
 */

import { calculateOSSE, calculateROSSE } from '../geometry/hornModels.js';
import { applyMorphing } from '../geometry/morphing.js';
import { evalParam, parseList, parseQuadrants, toRad } from '../geometry/common.js';

/**
 * Build a closed B-spline wire at one axial station.
 * @param {Object} oc - OpenCascade instance
 * @param {number[]} points3D - Array of {x, y, z} points around the horn at this station
 * @param {boolean} closed - Whether to close the wire (always true for horn cross-sections)
 * @returns {TopoDS_Wire}
 */
function makeWireFromPoints(oc, points3D, closed = true) {
    const n = points3D.length;
    // Create point array (1-indexed in OpenCascade)
    const nPts = closed ? n + 1 : n; // duplicate first point if closed
    const ptsArray = new oc.TColgp_Array1OfPnt_2(1, nPts);

    for (let i = 0; i < n; i++) {
        const pt = points3D[i];
        ptsArray.SetValue(i + 1, new oc.gp_Pnt_3(pt.x, pt.y, pt.z));
    }
    if (closed) {
        const pt = points3D[0];
        ptsArray.SetValue(n + 1, new oc.gp_Pnt_3(pt.x, pt.y, pt.z));
    }

    // Fit a B-spline through the points
    const bspline = new oc.GeomAPI_PointsToBSpline_2(ptsArray, 3, 8, oc.GeomAbs_Shape.GeomAbs_C2, 1e-3);
    if (!bspline.IsDone()) {
        throw new Error('[CADBuilder] Failed to create B-spline from points');
    }
    const curve = bspline.Curve();

    // Create edge from B-spline curve
    const edge = new oc.BRepBuilderAPI_MakeEdge_24(curve);
    if (!edge.IsDone()) {
        throw new Error('[CADBuilder] Failed to create edge from B-spline');
    }

    // Create wire from edge
    const wire = new oc.BRepBuilderAPI_MakeWire_2(edge.Edge());
    if (!wire.IsDone()) {
        throw new Error('[CADBuilder] Failed to create wire from edge');
    }

    return wire.Wire();
}

/**
 * Compute the axial slice distribution (z positions along horn).
 * Replicates the logic from meshBuilder.js:buildSliceMap.
 */
function buildSlicePositions(params, numStations) {
    const resT = Number(params.throatResolution);
    const resM = Number(params.mouthResolution);

    if (Number.isFinite(resT) && Number.isFinite(resM) && resT > 0 && resM > 0 && Math.abs(resT - resM) > 0.01) {
        const positions = new Array(numStations + 1);
        const avgRes = 0.5 * (resT + resM);
        for (let j = 0; j <= numStations; j++) {
            const t = j / numStations;
            positions[j] = (resT * t + 0.5 * (resM - resT) * t * t) / avgRes;
        }
        return positions;
    }

    // Uniform distribution
    const positions = new Array(numStations + 1);
    for (let j = 0; j <= numStations; j++) {
        positions[j] = j / numStations;
    }
    return positions;
}

/**
 * Build azimuthal angle list for profile sampling.
 * @param {number} numAngles - Number of angular samples (must be >= 8)
 * @returns {number[]} Array of angles in radians [0, 2*PI)
 */
function buildAngles(numAngles) {
    const angles = [];
    for (let i = 0; i < numAngles; i++) {
        angles.push((i / numAngles) * Math.PI * 2);
    }
    return angles;
}

/**
 * Evaluate horn profile at (t, p) and return 3D point.
 * Coordinate convention: X = r*cos(p), Y = axial, Z = r*sin(p)
 */
function evaluateProfile(t, p, params, morphTargetInfo) {
    let profile;
    if (params.type === 'R-OSSE') {
        const tmax = params.tmax === undefined ? 1.0 : evalParam(params.tmax, p);
        profile = calculateROSSE(t * tmax, p, params);
    } else {
        const L = evalParam(params.L, p);
        const extLen = Math.max(0, evalParam(params.throatExtLength || 0, p));
        const slotLen = Math.max(0, evalParam(params.slotLength || 0, p));
        const totalLength = L + extLen + slotLen;
        profile = calculateOSSE(t * totalLength, p, params);
        const h = params.h === undefined ? 0 : evalParam(params.h, p);
        if (h > 0) {
            profile.y += h * Math.sin(t * Math.PI);
        }
    }

    let r = profile.y;
    r = applyMorphing(r, t, p, params, morphTargetInfo);

    return {
        x: r * Math.cos(p),
        y: profile.x,  // axial position
        z: r * Math.sin(p)
    };
}

/**
 * Build morph target info for OSSE (needed for morphing without explicit dimensions).
 */
function computeMorphTargets(params, numStations, angles, slicePositions) {
    const morphTarget = Number(params.morphTarget || 0);
    if (morphTarget === 0) return null;
    if (params.morphWidth > 0 && params.morphHeight > 0) return null;
    if (params.type !== 'OSSE') return null;

    const targets = new Array(numStations + 1);
    for (let j = 0; j <= numStations; j++) {
        const t = slicePositions[j];
        let maxX = 0, maxZ = 0;
        for (const p of angles) {
            let profile;
            const L = evalParam(params.L, p);
            const extLen = Math.max(0, evalParam(params.throatExtLength || 0, p));
            const slotLen = Math.max(0, evalParam(params.slotLength || 0, p));
            const totalLength = L + extLen + slotLen;
            profile = calculateOSSE(t * totalLength, p, params);
            const r = profile.y;
            const x = Math.abs(r * Math.cos(p));
            const z = Math.abs(r * Math.sin(p));
            if (x > maxX) maxX = x;
            if (z > maxZ) maxZ = z;
        }
        targets[j] = { halfW: maxX, halfH: maxZ };
    }
    return targets;
}

/**
 * Create a flat disc face at the throat for the acoustic source.
 * @param {Object} oc - OpenCascade instance
 * @param {Object} params - Horn parameters
 * @returns {TopoDS_Face}
 */
function makeThroatSourceDisc(oc, params) {
    const r0 = evalParam(params.r0, 0);
    // Create a circular edge at z=0 (throat plane)
    const center = new oc.gp_Pnt_3(0, 0, 0);
    const dir = new oc.gp_Dir_4(0, 1, 0); // Y-axis = axial direction
    const axis = new oc.gp_Ax2_3(center, dir);
    const circle = new oc.gp_Circ_2(axis, r0);

    const edge = new oc.BRepBuilderAPI_MakeEdge_8(circle);
    const wire = new oc.BRepBuilderAPI_MakeWire_2(edge.Edge());
    const face = new oc.BRepBuilderAPI_MakeFace_15(wire.Wire(), true);

    return face.Face();
}

/**
 * Create a spherical cap face at the throat for the acoustic source.
 * @param {Object} oc - OpenCascade instance
 * @param {Object} params - Horn parameters
 * @returns {TopoDS_Face}
 */
function makeThroatSourceSphericalCap(oc, params) {
    const r0 = evalParam(params.r0, 0);
    const a0Deg = evalParam(params.a0, 0);
    const a0Rad = toRad(a0Deg);

    let sourceRadius = params.sourceRadius !== undefined ? Number(params.sourceRadius) : -1;
    if (sourceRadius <= 0) {
        // Auto-calculate: radius of curvature matching throat angle
        sourceRadius = r0 / Math.sin(Math.max(a0Rad, 0.01));
    }

    const curv = Number(params.sourceCurv || 0);
    // Place sphere center behind the throat
    const sign = curv === 1 ? 1 : curv === -1 ? -1 : (a0Deg > 0 ? -1 : 1);
    const centerY = sign * Math.sqrt(Math.max(0, sourceRadius * sourceRadius - r0 * r0));

    const center = new oc.gp_Pnt_3(0, centerY, 0);
    const sphere = new oc.BRepPrimAPI_MakeSphere_1(center, sourceRadius);

    // Cut the sphere with a cylinder to keep only the cap within throat radius
    const cylAxis = new oc.gp_Ax2_3(
        new oc.gp_Pnt_3(0, centerY - sourceRadius * 2, 0),
        new oc.gp_Dir_4(0, 1, 0)
    );
    const cylinder = new oc.BRepPrimAPI_MakeCylinder_1(cylAxis, r0, sourceRadius * 4);
    const common = new oc.BRepAlgoAPI_Common_3(sphere.Shape(), cylinder.Shape(), new oc.Message_ProgressRange_1());

    return common.Shape();
}

/**
 * Main entry point: Build complete horn B-Rep geometry.
 *
 * @param {Object} oc - Initialized OpenCascade instance
 * @param {Object} params - Horn parameters (same format as buildHornMesh)
 * @param {Object} options - Build options
 * @param {number} options.numStations - Number of axial cross-sections for lofting (default: 30)
 * @param {number} options.numAngles - Number of angular samples per cross-section (default: 64)
 * @param {boolean} options.includeSource - Include acoustic source surface (default: true)
 * @param {boolean} options.includeWallThickness - Add wall thickness for free-standing (default: false)
 * @returns {{ shape: TopoDS_Shape, sourceShape: TopoDS_Shape|null }}
 */
export function buildHornCAD(oc, params, options = {}) {
    const numStations = options.numStations || 30;
    const numAngles = options.numAngles || 64;
    const includeSource = options.includeSource !== false;

    console.log(`[CADBuilder] Building horn: ${numStations} stations, ${numAngles} angles, type=${params.type}`);

    const angles = buildAngles(numAngles);
    const slicePositions = buildSlicePositions(params, numStations);
    const morphTargets = computeMorphTargets(params, numStations, angles, slicePositions);

    // Build cross-section wires at each axial station
    const wires = [];
    for (let j = 0; j <= numStations; j++) {
        const t = slicePositions[j];
        const morphTargetInfo = morphTargets ? morphTargets[j] : null;

        const points = [];
        for (let i = 0; i < numAngles; i++) {
            const p = angles[i];
            const pt = evaluateProfile(t, p, params, morphTargetInfo);
            points.push(pt);
        }

        const wire = makeWireFromPoints(oc, points, true);
        wires.push(wire);
    }

    // Loft through all cross-section wires
    const loft = new oc.BRepOffsetAPI_ThruSections(false, false, 1e-6);
    for (const wire of wires) {
        loft.AddWire(wire);
    }
    loft.Build(new oc.Message_ProgressRange_1());

    if (!loft.IsDone()) {
        throw new Error('[CADBuilder] Loft (ThruSections) failed');
    }

    let hornShape = loft.Shape();
    console.log('[CADBuilder] Horn loft created successfully');

    // Build acoustic source surface
    let sourceShape = null;
    if (includeSource) {
        const sourceShapeType = Number(params.sourceShape || 1);
        if (sourceShapeType === 2) {
            sourceShape = makeThroatSourceDisc(oc, params);
        } else {
            sourceShape = makeThroatSourceSphericalCap(oc, params);
        }
        console.log('[CADBuilder] Acoustic source surface created');
    }

    // Combine horn and source into a compound
    const builder = new oc.BRep_Builder();
    const compound = new oc.TopoDS_Compound();
    builder.MakeCompound(compound);
    builder.Add(compound, hornShape);

    if (sourceShape) {
        builder.Add(compound, sourceShape);
    }

    console.log('[CADBuilder] Horn CAD build complete');

    return {
        shape: compound,
        hornShape,
        sourceShape
    };
}

/**
 * Build horn with wall thickness (for free-standing horns).
 * Creates a solid shell from the inner surface.
 *
 * @param {Object} oc - OpenCascade instance
 * @param {TopoDS_Shape} innerShape - The inner horn surface
 * @param {number} wallThickness - Wall thickness in mm
 * @returns {TopoDS_Shape} Solid horn shell
 */
export function addWallThickness(oc, innerShape, wallThickness) {
    if (!wallThickness || wallThickness <= 0) return innerShape;

    try {
        // Use BRepOffsetAPI_MakeThickSolid to thicken the shell
        const thickSolid = new oc.BRepOffsetAPI_MakeThickSolid();
        const facesToRemove = new oc.TopTools_ListOfShape_1();

        // We want to thicken outward - pass the faces we want to keep open (mouth, throat)
        // For now, thicken the entire surface
        thickSolid.MakeThickSolidByJoin(
            innerShape,
            facesToRemove,
            wallThickness,
            1e-3,
            oc.BRepOffset_Mode.BRepOffset_Skin,
            false,
            false,
            oc.GeomAbs_JoinType.GeomAbs_Arc,
            false,
            new oc.Message_ProgressRange_1()
        );

        if (thickSolid.IsDone()) {
            return thickSolid.Shape();
        }
    } catch (e) {
        console.warn('[CADBuilder] Wall thickness operation failed, returning inner surface:', e.message);
    }

    return innerShape;
}
