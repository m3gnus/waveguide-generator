
export class ATHConfigParser {
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
                    result.blocks[currentBlockName] = {};
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
                    result.blocks[currentBlock][key] = value;
                } else {
                    // Flat top-level key — detect OSSE by known flat keys
                    result.params[key] = value;
                }
            }
        }

        // Auto-detect OSSE from flat-key format (no OSSE = { } block)
        if (!result.type) {
            if (result.params['Coverage.Angle'] || result.params['Length'] || result.params['Term.n']) {
                result.type = 'OSSE';
            }
        }

        // Normalize OSSE flat-key names to internal parameter names
        if (result.type === 'OSSE' && !result.params.a) {
            const p = result.params;
            // Map flat ATH keys to the internal names the UI uses
            if (p['Coverage.Angle']) { p.a = p['Coverage.Angle']; }
            if (p['Throat.Angle']) { p.a0 = p['Throat.Angle']; }
            if (p['Throat.Diameter']) { p.r0 = String(parseFloat(p['Throat.Diameter']) / 2); }
            if (p['Length']) { p.L = p['Length']; }
            if (p['Term.s']) { p.s = p['Term.s']; }
            if (p['Term.n']) { p.n = p['Term.n']; }
            if (p['Term.q']) { p.q = p['Term.q']; }
            if (p['OS.h']) { p.h = p['OS.h']; }
            if (p['OS.k']) { p.k = p['OS.k']; }

            // Morph
            if (p['Morph.TargetShape']) { p.morphTarget = p['Morph.TargetShape']; }
            if (p['Morph.TargetWidth']) { p.morphWidth = p['Morph.TargetWidth']; }
            if (p['Morph.TargetHeight']) { p.morphHeight = p['Morph.TargetHeight']; }
            if (p['Morph.CornerRadius']) { p.morphCorner = p['Morph.CornerRadius']; }
            if (p['Morph.Rate']) { p.morphRate = p['Morph.Rate']; }
            if (p['Morph.FixedPart']) { p.morphFixed = p['Morph.FixedPart']; }

            // Mesh
            if (p['Mesh.AngularSegments']) { p.angularSegments = p['Mesh.AngularSegments']; }
            if (p['Mesh.LengthSegments']) { p.lengthSegments = p['Mesh.LengthSegments']; }
            if (p['Mesh.CornerSegments']) { p.cornerSegments = p['Mesh.CornerSegments']; }
            if (p['Mesh.Quadrants']) { p.quadrants = p['Mesh.Quadrants']; }
            if (p['Mesh.WallThickness']) { p.wallThickness = p['Mesh.WallThickness']; }
            if (p['Mesh.RearShape']) { p.RearShape = p['Mesh.RearShape']; }

            // Source & ABEC
            if (p['Source.Shape']) { p.sourceShape = p['Source.Shape']; }
            if (p['Source.Radius']) { p.sourceRadius = p['Source.Radius']; }
            if (p['Source.Velocity']) { p.sourceVelocity = p['Source.Velocity']; }
            if (p['ABEC.SimType']) { p.abecSimType = p['ABEC.SimType']; }
            if (p['ABEC.f1']) { p.abecF1 = p['ABEC.f1']; }
            if (p['ABEC.f2']) { p.abecF2 = p['ABEC.f2']; }
            if (p['ABEC.NumFrequencies']) { p.abecNumFreq = p['ABEC.NumFrequencies']; }
        }

        // Normalize R-OSSE mesh/source/abec params too
        if (result.type === 'R-OSSE') {
            const p = result.params;
            if (p['Mesh.AngularSegments']) { p.angularSegments = p['Mesh.AngularSegments']; }
            if (p['Mesh.LengthSegments']) { p.lengthSegments = p['Mesh.LengthSegments']; }
            if (p['Mesh.WallThickness']) { p.wallThickness = p['Mesh.WallThickness']; }
            if (p['Mesh.Quadrants']) { p.quadrants = p['Mesh.Quadrants']; }
            if (p['Mesh.RearShape']) { p.RearShape = p['Mesh.RearShape']; }
            if (p['ABEC.SimType']) { p.abecSimType = p['ABEC.SimType']; }
            if (p['ABEC.f1']) { p.abecF1 = p['ABEC.f1']; }
            if (p['ABEC.f2']) { p.abecF2 = p['ABEC.f2']; }
            if (p['ABEC.NumFrequencies']) { p.abecNumFreq = p['ABEC.NumFrequencies']; }
        }

        // Parse Mesh.Enclosure block if present
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
