
/**
 * Generate MWG Config file content from parameters.
 * @param {Object} params - The parameter object.
 * @returns {string} The formatted config file content.
 */
export function generateMWGConfigContent(params) {
    let content = '; MWG config\n';
    // Use local time format to match system clock
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const hour = String(now.getHours()).padStart(2, '0');
    const minute = String(now.getMinutes()).padStart(2, '0');
    content += `; Generated: ${year}-${month}-${day} ${hour}:${minute}\n`;

    const formatValue = (value) => {
        if (value === undefined || value === null) return '';
        if (Array.isArray(value)) return value.join(',');
        if (typeof value === 'boolean') return value ? '1' : '0';
        return String(value);
    };

    const formatList = (value) => {
        if (value === undefined || value === null) return '';
        if (Array.isArray(value)) return value.join(',');
        return String(value);
    };

    const isNonZero = (value) => {
        if (value === undefined || value === null) return false;
        if (typeof value === 'boolean') return value;
        const num = Number(value);
        if (Number.isFinite(num)) return num !== 0;
        return String(value).trim() !== '' && String(value).trim() !== '0';
    };

    // Helper to get raw value if it's a string expression, or check params
    // Since we receive the already-parsed params object here, we assume 'params' holds the current values.
    // However, the original code used `getRawVal` from DOM to preserve expressions (e.g. "45 + 10").
    // If 'params' contains the evaluated number, we lose the expression.
    // Constraint: The 'params' object passed here should ideally contain the raw strings for expression fields.
    // If the UI updates 'params' with numbers, we might need a separate 'rawParams' object or 
    // we accept that we export the evaluated values.
    // FOR PHASING 0: We will assume 'params' contains values (mixed string/number). 

    // NOTE: The original code fetched directly from DOM to get the string value.
    // In the refactor, we will eventually bindings that update the params object with the raw string.

    const hasScale = params.Scale !== undefined || params.scale !== undefined;
    if (hasScale) {
        const scaleValue = params.scale ?? params.Scale;
        const scaleNum = Number(scaleValue);
        if (!Number.isFinite(scaleNum) || scaleNum !== 1 || params.Scale !== undefined) {
            content += `Scale = ${formatValue(scaleValue)}\n`;
        }
    }

    if (params.type === 'R-OSSE') {
        content += 'R-OSSE = {\n';
        content += `R = ${params.R}\n`;
        content += `a = ${params.a}\n`;
        content += `a0 = ${params.a0}\n`;
        content += `b = ${params.b}\n`;
        content += `k = ${params.k}\n`;
        content += `m = ${params.m}\n`;
        content += `q = ${params.q}\n`;
        content += `r = ${params.r}\n`;
        content += `r0 = ${params.r0}\n`;
        if (params.tmax !== 1.0) content += `tmax = ${params.tmax}\n`;
        content += '}\n';
    } else {
        if (params.throatProfile !== undefined) {
            content += `Throat.Profile = ${formatValue(params.throatProfile)}\n`;
        }
        if (isNonZero(params.throatExtAngle)) content += `Throat.Ext.Angle = ${formatValue(params.throatExtAngle)}\n`;
        if (isNonZero(params.throatExtLength)) content += `Throat.Ext.Length = ${formatValue(params.throatExtLength)}\n`;
        if (isNonZero(params.slotLength)) content += `Slot.Length = ${formatValue(params.slotLength)}\n`;
        content += `Coverage.Angle = ${params.a}\n`;
        content += `Length = ${params.L}\n`;
        content += `Term.n = ${params.n}\n`;
        content += `Term.q = ${params.q}\n`;
        content += `Term.s = ${params.s}\n`;
        content += `Throat.Angle = ${params.a0}\n`;
        content += `Throat.Diameter = ${params.r0 * 2}\n`;
        if (params.throatProfile === undefined) content += `Throat.Profile = 1\n`;
        content += `OS.k = ${params.k}\n`;
        if (params.h !== undefined && params.h !== 0) content += `OS.h = ${params.h}\n`;
        if (isNonZero(params.rot)) content += `Rot = ${formatValue(params.rot)}\n`;

        if (params.gcurveType && Number(params.gcurveType) !== 0) {
            content += `GCurve.Type = ${formatValue(params.gcurveType)}\n`;
            if (isNonZero(params.gcurveDist)) content += `GCurve.Dist = ${formatValue(params.gcurveDist)}\n`;
            if (isNonZero(params.gcurveWidth)) content += `GCurve.Width = ${formatValue(params.gcurveWidth)}\n`;
            if (isNonZero(params.gcurveAspectRatio)) content += `GCurve.AspectRatio = ${formatValue(params.gcurveAspectRatio)}\n`;
            if (isNonZero(params.gcurveSeN)) content += `GCurve.SE.n = ${formatValue(params.gcurveSeN)}\n`;
            if (isNonZero(params.gcurveSf)) content += `GCurve.SF = ${formatValue(params.gcurveSf)}\n`;
            if (isNonZero(params.gcurveSfA)) content += `GCurve.SF.a = ${formatValue(params.gcurveSfA)}\n`;
            if (isNonZero(params.gcurveSfB)) content += `GCurve.SF.b = ${formatValue(params.gcurveSfB)}\n`;
            if (isNonZero(params.gcurveSfM1)) content += `GCurve.SF.m1 = ${formatValue(params.gcurveSfM1)}\n`;
            if (isNonZero(params.gcurveSfM2)) content += `GCurve.SF.m2 = ${formatValue(params.gcurveSfM2)}\n`;
            if (isNonZero(params.gcurveSfN1)) content += `GCurve.SF.n1 = ${formatValue(params.gcurveSfN1)}\n`;
            if (isNonZero(params.gcurveSfN2)) content += `GCurve.SF.n2 = ${formatValue(params.gcurveSfN2)}\n`;
            if (isNonZero(params.gcurveSfN3)) content += `GCurve.SF.n3 = ${formatValue(params.gcurveSfN3)}\n`;
            if (isNonZero(params.gcurveRot)) content += `GCurve.Rot = ${formatValue(params.gcurveRot)}\n`;
        }

        if (isNonZero(params.circArcRadius)) content += `CircArc.Radius = ${formatValue(params.circArcRadius)}\n`;
        if (isNonZero(params.circArcTermAngle)) content += `CircArc.TermAngle = ${formatValue(params.circArcTermAngle)}\n`;

        const morphAllow = params.morphAllowShrinkage !== undefined;
        if ((params.morphTarget !== undefined && params.morphTarget !== 0) || morphAllow) {
            if (params.morphCorner !== undefined && params.morphCorner > 0) {
                content += `Morph.CornerRadius = ${params.morphCorner}\n`;
            }
            if (params.morphFixed !== undefined) content += `Morph.FixedPart = ${params.morphFixed}\n`;
            if (params.morphRate !== undefined) content += `Morph.Rate = ${params.morphRate}\n`;
            if (params.morphTarget !== undefined) content += `Morph.TargetShape = ${params.morphTarget}\n`;
            if (params.morphWidth !== undefined && params.morphWidth > 0) {
                content += `Morph.TargetWidth = ${params.morphWidth}\n`;
            }
            if (params.morphHeight !== undefined && params.morphHeight > 0) {
                content += `Morph.TargetHeight = ${params.morphHeight}\n`;
            }
            if (params.morphAllowShrinkage !== undefined) {
                content += `Morph.AllowShrinkage = ${formatValue(params.morphAllowShrinkage)}\n`;
            }
        }

        // Enclosure plan feature removed - only standard enclosure is supported
        if (params.encDepth > 0) {
            content += `Mesh.Enclosure = {\n`;
            content += `Depth = ${params.encDepth}\n`;
            content += `EdgeRadius = ${params.encEdge}\n`;
            content += `EdgeType = ${params.encEdgeType}\n`;
            content += `Spacing = ${params.encSpaceL || 25},${params.encSpaceT || 25},${params.encSpaceR || 25},${params.encSpaceB || 25}\n`;
            if (isNonZero(params.encFrontResolution)) content += `FrontResolution = ${formatValue(params.encFrontResolution)}\n`;
            if (isNonZero(params.encBackResolution)) content += `BackResolution = ${formatValue(params.encBackResolution)}\n`;
            content += `}\n`;
        }
    }

    content += `Mesh.AngularSegments = ${params.angularSegments}\n`;
    if (params.morphTarget === 1 && params.cornerSegments !== undefined) {
        content += `Mesh.CornerSegments = ${params.cornerSegments}\n`;
    }
    if (isNonZero(params.throatSegments)) content += `Mesh.ThroatSegments = ${formatValue(params.throatSegments)}\n`;
    content += `Mesh.LengthSegments = ${params.lengthSegments}\n`;
    if (isNonZero(params.throatResolution)) content += `Mesh.ThroatResolution = ${formatValue(params.throatResolution)}\n`;
    if (isNonZero(params.mouthResolution)) content += `Mesh.MouthResolution = ${formatValue(params.mouthResolution)}\n`;
    if (isNonZero(params.verticalOffset)) content += `Mesh.VerticalOffset = ${formatValue(params.verticalOffset)}\n`;
    if (params.subdomainSlices !== undefined && params.subdomainSlices !== null) {
        content += `Mesh.SubdomainSlices = ${formatList(params.subdomainSlices)}\n`;
    }
    if (isNonZero(params.interfaceOffset)) content += `Mesh.InterfaceOffset = ${formatList(params.interfaceOffset)}\n`;
    if (isNonZero(params.interfaceDraw)) content += `Mesh.InterfaceDraw = ${formatList(params.interfaceDraw)}\n`;
    if (params.quadrants !== undefined) content += `Mesh.Quadrants = ${params.quadrants}\n`;
    if (params.wallThickness > 0) content += `Mesh.WallThickness = ${params.wallThickness}\n`;
    if (isNonZero(params.rearResolution)) content += `Mesh.RearResolution = ${formatValue(params.rearResolution)}\n`;

    if (params.outputABECProject !== undefined) {
        content += `Output.ABECProject = ${formatValue(params.outputABECProject)}\n`;
    }
    if (params.outputSTL !== undefined) {
        content += `Output.STL = ${formatValue(params.outputSTL)}\n`;
    }
    if (params.outputMSH !== undefined) {
        content += `Output.MSH = ${formatValue(params.outputMSH)}\n`;
    }

    if (params.sourceShape !== undefined) content += `Source.Shape = ${params.sourceShape}\n`;
    if (params.sourceRadius !== undefined && params.sourceRadius !== -1) {
        content += `Source.Radius = ${params.sourceRadius}\n`;
    }
    if (params.sourceCurv !== undefined) content += `Source.Curv = ${formatValue(params.sourceCurv)}\n`;
    if (params.sourceVelocity !== undefined) content += `Source.Velocity = ${params.sourceVelocity}\n`;
    if (params.sourceContours) content += `Source.Contours = ${formatValue(params.sourceContours)}\n`;

    if (params.abecSimType !== undefined) content += `ABEC.SimType = ${params.abecSimType}\n`;
    if (params.abecSimProfile !== undefined) content += `ABEC.SimProfile = ${formatValue(params.abecSimProfile)}\n`;
    if (params.abecF1 !== undefined) content += `ABEC.f1 = ${params.abecF1}\n`;
    if (params.abecF2 !== undefined) content += `ABEC.f2 = ${params.abecF2}\n`;
    if (params.abecNumFreq !== undefined) content += `ABEC.NumFrequencies = ${params.abecNumFreq}\n`;
    if (params.abecAbscissa !== undefined) content += `ABEC.Abscissa = ${formatValue(params.abecAbscissa)}\n`;
    if (params.abecMeshFrequency !== undefined) content += `ABEC.MeshFrequency = ${formatValue(params.abecMeshFrequency)}\n`;

    const blocks = params._blocks || {};
    for (const [blockName, block] of Object.entries(blocks)) {
        if (blockName === 'Mesh.Enclosure') continue;
        if (!block) continue;
        content += `${blockName} = {\n`;
        if (block._lines && block._lines.length > 0) {
            content += `${block._lines.join('\n')}\n`;
        }
        if (block._items) {
            for (const [key, value] of Object.entries(block._items)) {
                content += `${key} = ${value}\n`;
            }
        }
        content += `}\n`;
    }

    return content;
}
