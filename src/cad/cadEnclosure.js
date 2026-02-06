/**
 * CAD Enclosure Builder - Create enclosure B-Rep geometry via OpenCascade.
 *
 * Supports:
 *   - Pre-defined rectangular box with rounded/chamfered edges
 *   - User-defined ground plan (extruded profile with lines, arcs, ellipses, bezier)
 *   - Horn mouth opening cut from front baffle
 */

import { evalParam, parseList } from '../geometry/common.js';

/**
 * Build a pre-defined rectangular enclosure with optional edge treatment.
 *
 * @param {Object} oc - OpenCascade instance
 * @param {Object} params - Enclosure parameters
 * @param {Object} mouthExtents - { halfW, halfH } of the horn mouth
 * @returns {TopoDS_Shape}
 */
export function buildPreDefinedEnclosure(oc, params, mouthExtents) {
    const spaceL = Number(params.encSpaceL || 25);
    const spaceT = Number(params.encSpaceT || 25);
    const spaceR = Number(params.encSpaceR || 25);
    const spaceB = Number(params.encSpaceB || 200);
    const depth = Number(params.encDepth || 200);
    const edgeRadius = Number(params.encEdge || 18);
    const edgeType = Number(params.encEdgeType || 1); // 1=rounded, 2=chamfered

    // Compute baffle dimensions from mouth extents + spacing
    const halfW = mouthExtents.halfW;
    const halfH = mouthExtents.halfH;
    const totalW = halfW + spaceL + halfW + spaceR; // left + right
    const totalH = halfH + spaceT + halfH + spaceB; // top + bottom

    // Box origin: front-face center at the mouth plane
    // Box extends backward (negative Y in our coord system)
    const boxW = spaceL + 2 * halfW + spaceR;
    const boxH = spaceT + 2 * halfH + spaceB;
    const boxD = depth;

    // Create box centered on the mouth
    // Origin at front-bottom-left corner, extends in +X, -Y (backward), +Z
    const originX = -(halfW + spaceL);
    const originZ = -(halfH + spaceB);
    const originY = 0; // front face at Y=0 (mouth plane)

    const corner = new oc.gp_Pnt_3(originX, originY - boxD, originZ);
    const box = new oc.BRepPrimAPI_MakeBox_4(
        corner,
        new oc.gp_Pnt_3(originX + boxW, originY, originZ + boxH)
    );

    let encShape = box.Shape();

    // Apply edge treatment
    if (edgeRadius > 0) {
        try {
            if (edgeType === 2) {
                // Chamfered edges
                const chamfer = new oc.BRepFilletAPI_MakeChamfer(encShape);
                const edgeExplorer = new oc.TopExp_Explorer_2(
                    encShape,
                    oc.TopAbs_ShapeEnum.TopAbs_EDGE,
                    oc.TopAbs_ShapeEnum.TopAbs_SHAPE
                );
                while (edgeExplorer.More()) {
                    const edge = oc.TopoDS.Edge_1(edgeExplorer.Current());
                    chamfer.Add_2(edgeRadius, edge);
                    edgeExplorer.Next();
                }
                chamfer.Build(new oc.Message_ProgressRange_1());
                if (chamfer.IsDone()) {
                    encShape = chamfer.Shape();
                }
            } else {
                // Rounded edges (default)
                const fillet = new oc.BRepFilletAPI_MakeFillet(encShape, oc.ChFi3d_FilletShape.ChFi3d_Rational);
                const edgeExplorer = new oc.TopExp_Explorer_2(
                    encShape,
                    oc.TopAbs_ShapeEnum.TopAbs_EDGE,
                    oc.TopAbs_ShapeEnum.TopAbs_SHAPE
                );
                while (edgeExplorer.More()) {
                    const edge = oc.TopoDS.Edge_1(edgeExplorer.Current());
                    fillet.Add_2(edgeRadius, edge);
                    edgeExplorer.Next();
                }
                fillet.Build(new oc.Message_ProgressRange_1());
                if (fillet.IsDone()) {
                    encShape = fillet.Shape();
                }
            }
        } catch (e) {
            console.warn('[CADEnclosure] Edge treatment failed, using sharp edges:', e.message);
        }
    }

    return encShape;
}

/**
 * Cut the horn mouth opening from the enclosure front face.
 *
 * @param {Object} oc - OpenCascade instance
 * @param {TopoDS_Shape} enclosure - The enclosure shape
 * @param {TopoDS_Shape} hornShape - The horn shape (used as cutting tool)
 * @returns {TopoDS_Shape}
 */
export function cutHornOpening(oc, enclosure, hornShape) {
    try {
        const cut = new oc.BRepAlgoAPI_Cut_3(
            enclosure,
            hornShape,
            new oc.Message_ProgressRange_1()
        );
        if (cut.IsDone()) {
            return cut.Shape();
        }
    } catch (e) {
        console.warn('[CADEnclosure] Boolean cut failed:', e.message);
    }
    return enclosure;
}

/**
 * Build a complete enclosure shape from parameters.
 *
 * @param {Object} oc - OpenCascade instance
 * @param {Object} params - Full horn parameters
 * @param {Object} mouthExtents - { halfW, halfH } mouth dimensions
 * @param {TopoDS_Shape} hornShape - The horn B-Rep (for boolean cutting)
 * @returns {TopoDS_Shape|null} The enclosure shape, or null if no enclosure defined
 */
export function buildEnclosureCAD(oc, params, mouthExtents, hornShape) {
    const encDepth = Number(params.encDepth || 0);
    if (encDepth <= 0) return null;

    console.log('[CADEnclosure] Building enclosure, depth:', encDepth);

    let encShape = buildPreDefinedEnclosure(oc, params, mouthExtents);

    // Cut horn opening from front baffle
    if (hornShape) {
        encShape = cutHornOpening(oc, encShape, hornShape);
    }

    console.log('[CADEnclosure] Enclosure build complete');
    return encShape;
}
