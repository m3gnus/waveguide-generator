import { MWGConfigParser } from '../../src/config/parser.js';
import { generateMWGConfigContent } from '../../src/export/mwgConfig.js';

describe('ATH config round-trip', () => {
    it('preserves advanced ATH keys and blocks in export', () => {
        const input = `
Throat.Profile = 3
Throat.Diameter = 25.4
Throat.Angle = 10
Throat.Ext.Angle = 5
Throat.Ext.Length = 4
Slot.Length = 3
Length = 100
Coverage.Angle = 45
Term.s = 0.7
Term.n = 4
Term.q = 0.99
OS.k = 1
Rot = 5
CircArc.TermAngle = 3
Morph.AllowShrinkage = 1

Mesh.AngularSegments = 80
Mesh.LengthSegments = 20
Mesh.ThroatResolution = 5
Mesh.MouthResolution = 8
Mesh.VerticalOffset = 10
Mesh.SubdomainSlices =
Mesh.InterfaceOffset = 5
Mesh.InterfaceDraw = 0
Mesh.RearResolution = 12
Mesh.RearShape = 1

Mesh.Enclosure = {
Plan = my_plan
Spacing = 10,20,10,20
FrontResolution = 8,8,8,8
BackResolution = 12,12,12,12
}

my_plan = {
point P0 100 0 8
point P1 140 60 12
point PB 0 270 20
cpoint C1 100 60
cpoint C2 0 60
ellipse P0 C1 P1 C1
ellipse P1 C2 PB PB
}

ABEC.SimType = 2
ABEC.SimProfile = 0
ABEC.f1 = 100
ABEC.f2 = 20000
ABEC.NumFrequencies = 40
ABEC.Abscissa = 2
ABEC.MeshFrequency = 1000

Source.Shape = 1
Source.Curv = -1
Source.Velocity = 2

ABEC.Polars:SPL = {
MapAngleRange = 0,180,37
Distance = 2
}

Report = {
Title = "Demo"
Width = 1024
Height = 768
}

GridExport:throat = {
ExportProfiles = 1
}

Output.STL = 1
Output.MSH = 1
Output.ABECProject = 1
`;

        const parsed = MWGConfigParser.parse(input);
        const params = {
            ...parsed.params,
            type: parsed.type || 'OSSE',
            _blocks: parsed.blocks
        };

        const output = generateMWGConfigContent(params);

        expect(output).toContain('Throat.Ext.Angle = 5');
        expect(output).toContain('Throat.Ext.Length = 4');
        expect(output).toContain('Slot.Length = 3');
        expect(output).toContain('CircArc.TermAngle = 3');
        expect(output).toContain('Morph.AllowShrinkage = 1');
        expect(output).toContain('Mesh.ThroatResolution = 5');
        expect(output).toContain('Mesh.MouthResolution = 8');
        expect(output).toContain('Mesh.VerticalOffset = 10');
        expect(output).toContain('Mesh.SubdomainSlices =');
        expect(output).toContain('Mesh.RearResolution = 12');
        expect(output).toContain('Mesh.RearShape = 1');
        expect(output).toContain('Mesh.Enclosure = {');
        expect(output).toContain('Plan = my_plan');
        expect(output).toContain('FrontResolution = 8,8,8,8');
        expect(output).toContain('BackResolution = 12,12,12,12');
        expect(output).toContain('ABEC.Abscissa = 2');
        expect(output).toContain('ABEC.MeshFrequency = 1000');
        expect(output).toContain('ABEC.Polars:SPL = {');
        expect(output).toContain('GridExport:throat = {');
        expect(output).toContain('Report = {');
        expect(output).toContain('Output.MSH = 1');
    });
});
