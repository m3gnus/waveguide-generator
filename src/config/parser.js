
export class MWGConfigParser {
    static parse(content) {
        const result = { type: null, params: {}, blocks: {} };
        const lines = content.split('\n').map(line => {
            const commentIdx = line.indexOf(';');
            return (commentIdx !== -1 ? line.substring(0, commentIdx) : line).trim();
        }).filter(line => line.length > 0);

        let currentBlock = null;
        let currentBlockName = null;

        for (const line of lines) {
            // Block start: "Name = {" or "Name:Sub = {"
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
                    result.blocks[currentBlockName] = { _items: {}, _lines: [] };
                }
                continue;
            }

            // Block end
            if (line === '}') {
                currentBlock = null;
                currentBlockName = null;
                continue;
            }

            // Key = Value (split on first = only, to handle expressions with =)
            const eqIdx = line.indexOf('=');
            if (eqIdx > 0) {
                const key = line.substring(0, eqIdx).trim();
                const value = line.substring(eqIdx + 1).trim();

                if (currentBlock === 'R-OSSE' || currentBlock === 'OSSE') {
                    result.params[key] = value;
                } else if (currentBlock && result.blocks[currentBlock]) {
                    result.blocks[currentBlock]._items[key] = value;
                } else {
                    // Flat top-level key — detect OSSE by known flat keys
                    result.params[key] = value;
                }
            } else if (currentBlock && result.blocks[currentBlock]) {
                result.blocks[currentBlock]._lines.push(line);
            }
        }

        // Auto-detect OSSE from flat-key format (no OSSE = { } block)
        if (!result.type) {
            if (result.params['Coverage.Angle'] || result.params['Length'] || result.params['Term.n']) {
                result.type = 'OSSE';
            }
        }

        // Normalize OSSE flat-key names to internal parameter names
        if (result.type === 'OSSE') {
            const p = result.params;
            if (!p.a) {
                if (p['Coverage.Angle']) { p.a = p['Coverage.Angle']; }
                if (p['Throat.Angle']) { p.a0 = p['Throat.Angle']; }
                if (p['Throat.Diameter']) { p.r0 = String(parseFloat(p['Throat.Diameter']) / 2); }
                if (p['Length']) { p.L = p['Length']; }
                if (p['Term.s']) { p.s = p['Term.s']; }
                if (p['Term.n']) { p.n = p['Term.n']; }
                if (p['Term.q']) { p.q = p['Term.q']; }
                if (p['OS.h']) { p.h = p['OS.h']; }
                if (p['OS.k']) { p.k = p['OS.k']; }
            }

            if (p['Throat.Profile']) { p.throatProfile = p['Throat.Profile']; }
            if (p['Throat.Ext.Angle']) { p.throatExtAngle = p['Throat.Ext.Angle']; }
            if (p['Throat.Ext.Length']) { p.throatExtLength = p['Throat.Ext.Length']; }
            if (p['Slot.Length']) { p.slotLength = p['Slot.Length']; }
            if (p['Rot']) { p.rot = p['Rot']; }
            if (p['CircArc.TermAngle']) { p.circArcTermAngle = p['CircArc.TermAngle']; }
            if (p['CircArc.Radius']) { p.circArcRadius = p['CircArc.Radius']; }
            if (p['GCurve.Type']) { p.gcurveType = p['GCurve.Type']; }
            if (p['GCurve.Dist']) { p.gcurveDist = p['GCurve.Dist']; }
            if (p['GCurve.Width']) { p.gcurveWidth = p['GCurve.Width']; }
            if (p['GCurve.AspectRatio']) { p.gcurveAspectRatio = p['GCurve.AspectRatio']; }
            if (p['GCurve.SE.n']) { p.gcurveSeN = p['GCurve.SE.n']; }
            if (p['GCurve.SF']) { p.gcurveSf = p['GCurve.SF']; }
            if (p['GCurve.SF.a']) { p.gcurveSfA = p['GCurve.SF.a']; }
            if (p['GCurve.SF.b']) { p.gcurveSfB = p['GCurve.SF.b']; }
            if (p['GCurve.SF.m1']) { p.gcurveSfM1 = p['GCurve.SF.m1']; }
            if (p['GCurve.SF.m2']) { p.gcurveSfM2 = p['GCurve.SF.m2']; }
            if (p['GCurve.SF.n1']) { p.gcurveSfN1 = p['GCurve.SF.n1']; }
            if (p['GCurve.SF.n2']) { p.gcurveSfN2 = p['GCurve.SF.n2']; }
            if (p['GCurve.SF.n3']) { p.gcurveSfN3 = p['GCurve.SF.n3']; }
            if (p['GCurve.Rot']) { p.gcurveRot = p['GCurve.Rot']; }

            if (p['Morph.TargetShape']) { p.morphTarget = p['Morph.TargetShape']; }
            if (p['Morph.TargetWidth']) { p.morphWidth = p['Morph.TargetWidth']; }
            if (p['Morph.TargetHeight']) { p.morphHeight = p['Morph.TargetHeight']; }
            if (p['Morph.CornerRadius']) { p.morphCorner = p['Morph.CornerRadius']; }
            if (p['Morph.Rate']) { p.morphRate = p['Morph.Rate']; }
            if (p['Morph.FixedPart']) { p.morphFixed = p['Morph.FixedPart']; }
            if (p['Morph.AllowShrinkage'] !== undefined) {
                p.morphAllowShrinkage = p['Morph.AllowShrinkage'] === '1' || p['Morph.AllowShrinkage'] === 1;
            }

            if (p['Mesh.AngularSegments']) { p.angularSegments = p['Mesh.AngularSegments']; }
            if (p['Mesh.LengthSegments']) { p.lengthSegments = p['Mesh.LengthSegments']; }
            if (p['Mesh.CornerSegments']) { p.cornerSegments = p['Mesh.CornerSegments']; }
            if (p['Mesh.ThroatSegments']) { p.throatSegments = p['Mesh.ThroatSegments']; }
            if (p['Mesh.ThroatResolution']) { p.throatResolution = p['Mesh.ThroatResolution']; }
            if (p['Mesh.MouthResolution']) { p.mouthResolution = p['Mesh.MouthResolution']; }
            if (p['Mesh.VerticalOffset']) { p.verticalOffset = p['Mesh.VerticalOffset']; }
            if (p['Mesh.SubdomainSlices'] !== undefined) { p.subdomainSlices = p['Mesh.SubdomainSlices']; }
            if (p['Mesh.InterfaceOffset'] !== undefined) { p.interfaceOffset = p['Mesh.InterfaceOffset']; }
            if (p['Mesh.InterfaceDraw'] !== undefined) { p.interfaceDraw = p['Mesh.InterfaceDraw']; }
            if (p['Mesh.InterfaceResolution'] !== undefined) { p.interfaceResolution = p['Mesh.InterfaceResolution']; }
            if (p['Mesh.Quadrants']) { p.quadrants = p['Mesh.Quadrants']; }
            if (p['Mesh.WallThickness']) { p.wallThickness = p['Mesh.WallThickness']; }
            if (p['Mesh.RearResolution']) { p.rearResolution = p['Mesh.RearResolution']; }

            if (p['Source.Shape']) { p.sourceShape = p['Source.Shape']; }
            if (p['Source.Radius']) { p.sourceRadius = p['Source.Radius']; }
            if (p['Source.Curv']) { p.sourceCurv = p['Source.Curv']; }
            if (p['Source.Velocity']) { p.sourceVelocity = p['Source.Velocity']; }
            if (p['Source.Contours']) { p.sourceContours = p['Source.Contours']; }
            if (p['ABEC.SimType'] !== undefined) { p.abecSimType = p['ABEC.SimType']; }
            if (p['ABEC.SimProfile'] !== undefined) { p.abecSimProfile = p['ABEC.SimProfile']; }
            if (p['ABEC.f1']) { p.abecF1 = p['ABEC.f1']; }
            if (p['ABEC.f2']) { p.abecF2 = p['ABEC.f2']; }
            if (p['ABEC.NumFrequencies']) { p.abecNumFreq = p['ABEC.NumFrequencies']; }
            if (p['ABEC.Abscissa']) { p.abecAbscissa = p['ABEC.Abscissa']; }
            if (p['ABEC.MeshFrequency']) { p.abecMeshFrequency = p['ABEC.MeshFrequency']; }

            if (p['Output.STL'] !== undefined) { p.outputSTL = p['Output.STL']; }
            if (p['Output.MSH'] !== undefined) { p.outputMSH = p['Output.MSH']; }
            if (p['Output.ABECProject'] !== undefined) { p.outputABECProject = p['Output.ABECProject']; }
        }

        // Normalize R-OSSE mesh/source/abec params too
        if (result.type === 'R-OSSE') {
            const p = result.params;
            if (p['Morph.TargetShape']) { p.morphTarget = p['Morph.TargetShape']; }
            if (p['Morph.TargetWidth']) { p.morphWidth = p['Morph.TargetWidth']; }
            if (p['Morph.TargetHeight']) { p.morphHeight = p['Morph.TargetHeight']; }
            if (p['Morph.CornerRadius']) { p.morphCorner = p['Morph.CornerRadius']; }
            if (p['Morph.Rate']) { p.morphRate = p['Morph.Rate']; }
            if (p['Morph.FixedPart']) { p.morphFixed = p['Morph.FixedPart']; }
            if (p['Morph.AllowShrinkage'] !== undefined) {
                p.morphAllowShrinkage = p['Morph.AllowShrinkage'] === '1' || p['Morph.AllowShrinkage'] === 1;
            }
            if (p['Mesh.AngularSegments']) { p.angularSegments = p['Mesh.AngularSegments']; }
            if (p['Mesh.LengthSegments']) { p.lengthSegments = p['Mesh.LengthSegments']; }
            if (p['Mesh.CornerSegments']) { p.cornerSegments = p['Mesh.CornerSegments']; }
            if (p['Mesh.ThroatSegments']) { p.throatSegments = p['Mesh.ThroatSegments']; }
            if (p['Mesh.ThroatResolution']) { p.throatResolution = p['Mesh.ThroatResolution']; }
            if (p['Mesh.MouthResolution']) { p.mouthResolution = p['Mesh.MouthResolution']; }
            if (p['Mesh.VerticalOffset']) { p.verticalOffset = p['Mesh.VerticalOffset']; }
            if (p['Mesh.SubdomainSlices'] !== undefined) { p.subdomainSlices = p['Mesh.SubdomainSlices']; }
            if (p['Mesh.InterfaceOffset'] !== undefined) { p.interfaceOffset = p['Mesh.InterfaceOffset']; }
            if (p['Mesh.InterfaceDraw'] !== undefined) { p.interfaceDraw = p['Mesh.InterfaceDraw']; }
            if (p['Mesh.InterfaceResolution'] !== undefined) { p.interfaceResolution = p['Mesh.InterfaceResolution']; }
            if (p['Mesh.WallThickness']) { p.wallThickness = p['Mesh.WallThickness']; }
            if (p['Mesh.Quadrants']) { p.quadrants = p['Mesh.Quadrants']; }
            if (p['Mesh.RearResolution']) { p.rearResolution = p['Mesh.RearResolution']; }
            if (p['Source.Shape']) { p.sourceShape = p['Source.Shape']; }
            if (p['Source.Radius']) { p.sourceRadius = p['Source.Radius']; }
            if (p['Source.Curv']) { p.sourceCurv = p['Source.Curv']; }
            if (p['Source.Velocity']) { p.sourceVelocity = p['Source.Velocity']; }
            if (p['Source.Contours']) { p.sourceContours = p['Source.Contours']; }
            if (p['ABEC.SimType'] !== undefined) { p.abecSimType = p['ABEC.SimType']; }
            if (p['ABEC.SimProfile'] !== undefined) { p.abecSimProfile = p['ABEC.SimProfile']; }
            if (p['ABEC.f1']) { p.abecF1 = p['ABEC.f1']; }
            if (p['ABEC.f2']) { p.abecF2 = p['ABEC.f2']; }
            if (p['ABEC.NumFrequencies']) { p.abecNumFreq = p['ABEC.NumFrequencies']; }
            if (p['ABEC.Abscissa']) { p.abecAbscissa = p['ABEC.Abscissa']; }
            if (p['ABEC.MeshFrequency']) { p.abecMeshFrequency = p['ABEC.MeshFrequency']; }

            // Output
            if (p['Output.STL'] !== undefined) { p.outputSTL = p['Output.STL']; }
            if (p['Output.MSH'] !== undefined) { p.outputMSH = p['Output.MSH']; }
            if (p['Output.ABECProject'] !== undefined) { p.outputABECProject = p['Output.ABECProject']; }
        }

        // Normalize params (both types, flat keys)
        {
            const p = result.params;
            if (p['Scale'] !== undefined) {
                const scaleNum = Number(p['Scale']);
                p.scale = Number.isFinite(scaleNum) ? scaleNum : p['Scale'];
            }
        }

        // Parse Mesh.Enclosure block if present
        const encBlock = result.blocks['Mesh.Enclosure'];
        if (encBlock && encBlock._items) {
            const p = result.params;
            if (encBlock._items.Depth) { p.encDepth = encBlock._items.Depth; }
            if (encBlock._items.EdgeRadius) { p.encEdge = encBlock._items.EdgeRadius; }
            if (encBlock._items.EdgeType) { p.encEdgeType = encBlock._items.EdgeType; }
            if (encBlock._items.FrontResolution) { p.encFrontResolution = encBlock._items.FrontResolution; }
            if (encBlock._items.BackResolution) { p.encBackResolution = encBlock._items.BackResolution; }
            if (encBlock._items.InterfaceOffset !== undefined) { p.interfaceOffset = encBlock._items.InterfaceOffset; }
            if (encBlock._items.Spacing) {
                const parts = encBlock._items.Spacing.split(',').map(s => s.trim());
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
