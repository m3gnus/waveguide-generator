
/**
 * Generate ATH Config file content from parameters.
 * @param {Object} params - The parameter object.
 * @returns {string} The formatted config file content.
 */
export function generateATHConfigContent(params) {
    let content = '; Ath config\n';
    content += `; Generated: ${new Date().toISOString().slice(0, 16).replace('T', ' ')}\n`;

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

        if (params.rollback) {
            content += `Rollback = 1\n`;
            content += `Rollback.Angle = ${params.rollbackAngle}\n`;
            content += `Rollback.StartAt = ${params.rollbackStart}\n`;
        }
    } else {
        content += `Coverage.Angle = ${params.a}\n`;
        content += `Length = ${params.L}\n`;
        content += `Term.n = ${params.n}\n`;
        content += `Term.q = ${params.q}\n`;
        content += `Term.s = ${params.s}\n`;
        content += `Throat.Angle = ${params.a0}\n`;
        content += `Throat.Diameter = ${params.r0 * 2}\n`;
        content += `Throat.Profile = 1\n`;
        if (params.h !== 0) content += `OS.h = ${params.h}\n`;

        if (params.morphTarget !== 0) {
            if (params.morphCorner > 0) content += `Morph.CornerRadius = ${params.morphCorner}\n`;
            content += `Morph.FixedPart = ${params.morphFixed}\n`;
            content += `Morph.Rate = ${params.morphRate}\n`;
            content += `Morph.TargetShape = ${params.morphTarget}\n`;
            if (params.morphWidth > 0) content += `Morph.TargetWidth = ${params.morphWidth}\n`;
            if (params.morphHeight > 0) content += `Morph.TargetHeight = ${params.morphHeight}\n`;
        }

        if (params.encDepth > 0 && params.encSpace) {
            content += `Mesh.Enclosure = {\n`;
            content += `Depth = ${params.encDepth}\n`;
            content += `EdgeRadius = ${params.encEdge}\n`;
            content += `EdgeType = ${params.encEdgeType}\n`;
            content += `Spacing = ${params.encSpace.join(',')}\n`;
            content += `}\n`;
        }
    }

    content += `Mesh.AngularSegments = ${params.angularSegments}\n`;
    if (params.morphTarget === 1) content += `Mesh.CornerSegments = ${params.cornerSegments}\n`;
    content += `Mesh.LengthSegments = ${params.lengthSegments}\n`;
    content += `Mesh.Quadrants = ${params.quadrants}\n`;
    if (params.wallThickness > 0) content += `Mesh.WallThickness = ${params.wallThickness}\n`;

    content += `Output.ABECProject = 1\n`;
    content += `Output.STL = 1\n`;

    content += `Source.Shape = ${params.sourceShape}\n`;
    if (params.sourceRadius !== -1) content += `Source.Radius = ${params.sourceRadius}\n`;
    content += `Source.Velocity = ${params.sourceVelocity}\n`;

    content += `ABEC.SimType = ${params.abecSimType}\n`;
    content += `ABEC.f1 = ${params.abecF1}\n`;
    content += `ABEC.f2 = ${params.abecF2}\n`;
    content += `ABEC.NumFrequencies = ${params.abecNumFreq}\n`;

    return content;
}
