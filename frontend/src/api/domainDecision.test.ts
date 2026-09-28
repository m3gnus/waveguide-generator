import { describe, expect, it } from 'vitest';
import {
  decisionReading,
  decisionSummaryRows,
  displaySymmetry,
  readDomainDecision,
  recordDomainDecision,
} from './domainDecision';
import {
  decisionFixture,
  OPEN_SHEET,
  PROVENANCE_HALF,
  RECOVERED_HALF_WG_QUARTER,
  RECOVERED_NEGATIVE_HALF,
  REFUSED_CUT,
  REFUSED_OBLIQUE,
  REFUSED_OFF_CENTRE,
  summaryOf,
  UNRESOLVED,
  WG_HALF,
  WG_QUARTER,
} from './domainDecision.fixtures';

describe('reading a domain decision', () => {
  it('reads only the contract it knows', () => {
    expect(readDomainDecision(WG_HALF)).toBe(WG_HALF);
    expect(readDomainDecision({ ...WG_HALF, contract: 'other' })).toBeNull();
    expect(readDomainDecision(null)).toBeNull();
    expect(readDomainDecision({ contract: 'cad-domain-decision-v1' })).toBeNull();
    expect(recordDomainDecision({ domain_decision: WG_QUARTER })).toBe(WG_QUARTER);
    expect(recordDomainDecision({})).toBeNull();
  });
});

describe('the concluded reading, in plain words', () => {
  it('names a full model and the reduction WG solves it as', () => {
    expect(decisionReading(decisionFixture()).text).toBe('Full model');
    expect(decisionReading(WG_HALF).text).toBe('Full model — solved as a half (x = 0)');
    expect(decisionReading(WG_QUARTER).text).toBe('Full model — solved as a quarter (x = 0 and y = 0)');
    expect(decisionReading(WG_HALF).frameNote).toBeNull();
  });

  it('names a model cut in CAD, how it was recovered, and what was reflected', () => {
    expect(decisionReading(RECOVERED_NEGATIVE_HALF).text)
      .toBe('Cut in CAD at x = 0 — recovered by mirroring (the x ≤ 0 side reflected)');
    expect(decisionReading(decisionFixture({ ...RECOVERED_NEGATIVE_HALF, cad_cuts: [{ ...RECOVERED_NEGATIVE_HALF.cad_cuts[0], reflected: false, kept_side: 'positive' }] })).text)
      .toBe('Cut in CAD at x = 0 — recovered by mirroring');
    expect(decisionReading(RECOVERED_HALF_WG_QUARTER).text)
      .toBe('Cut in CAD at x = 0 — recovered by mirroring · solved as a quarter (x = 0 and y = 0)');
    expect(decisionReading(PROVENANCE_HALF).text).toBe('Cut in CAD at x = 0 — mirrored (Split Body 3)');
    expect(decisionReading(decisionFixture({
      ...PROVENANCE_HALF,
      cad_cuts: [{ ...PROVENANCE_HALF.cad_cuts[0], found_by: 'declaration', features: [] }],
    })).text).toBe('Cut in CAD at x = 0 — mirrored (declared in Fusion)');
  });

  it('keeps the as-modelled frame of a CAD cut and says why', () => {
    const reading = decisionReading(RECOVERED_NEGATIVE_HALF);
    expect(reading.frameNote).toMatch(/^Radiates along \+z as modelled: a model cut in CAD is solved only in the frame it was modelled in/);
    // A summary carries no allowed axes: nothing to explain there.
    expect(decisionReading(summaryOf(RECOVERED_NEGATIVE_HALF)).frameNote).toBeNull();
  });

  it('names open and unresolved geometry solved as shown', () => {
    expect(decisionReading(OPEN_SHEET).text).toBe('Open sheet — solved as shown');
    expect(decisionReading(UNRESOLVED).text)
      .toBe('Solved as shown — the geometry alone cannot say whether it is cut at x = 0');
    expect(decisionReading(decisionFixture({ input_reading: 'unresolved' }), { userChoice: true }).text)
      .toBe('Solved as shown (your choice)');
  });

  it('names each refusal in a few words, with the message the engines refuse with', () => {
    const oblique = decisionReading(REFUSED_OBLIQUE);
    expect(oblique).toMatchObject({
      text: 'Refused: cut on an oblique plane — send the uncut model',
      refused: true,
      refusal: REFUSED_OBLIQUE.refusal!.message,
    });
    expect(decisionReading(REFUSED_OFF_CENTRE).text).toBe('Refused: cut off-centre at x = 12.5 mm — send the uncut model');
    const cut = decisionReading(REFUSED_CUT);
    expect(cut.text).toBe('Refused: cut in CAD at x = 0 cannot be recovered — send the uncut model');
    expect(cut.failed).toEqual(['the left and right sources are separate identities']);
    expect(decisionReading(decisionFixture({ refusal: { code: 'imported_frame_improper', message: 'm' } })).text)
      .toBe('Refused: the solver frame is not a proper rotation — send the model again');
  });

  it('names the cut the refusal message names, in the server\'s order', () => {
    // An origin-plane rim and an oblique rim: the server refuses the open rim
    // first (domain_decision.py), so the headline must name it too.
    const both = decisionFixture({
      ...REFUSED_CUT,
      oblique_cuts: [{ rim_edges: 40 }],
      off_centre_cuts: [{ plane_axis: 'y', offset_mm: 3, rim_edges: 12 }],
    });
    expect(decisionReading(both).text).toBe('Refused: cut in CAD at x = 0 cannot be recovered — send the uncut model');
    expect(both.refusal!.message).toContain('open along x = 0');
    // Two refused rims: the one the message names.
    const two = decisionFixture({
      ...REFUSED_CUT,
      cad_cuts: [{ ...REFUSED_CUT.cad_cuts[0] }, { ...REFUSED_CUT.cad_cuts[0], plane: 'y0', solver_plane: 'y0' }],
      refusal: { code: 'imported_open_half_shell', message: 'The model is open along y = 0 (80 rim edges).' },
    });
    expect(decisionReading(two).text).toBe('Refused: cut in CAD at y = 0 cannot be recovered — send the uncut model');
    // Oblique before off-centre.
    expect(decisionReading(decisionFixture({ ...REFUSED_OBLIQUE, off_centre_cuts: [{ plane_axis: 'x', offset_mm: 5 }] })).text)
      .toBe('Refused: cut on an oblique plane — send the uncut model');
  });
});

describe('what the viewport mirrors for display', () => {
  const symmetry = { planes: {}, cut_planes: [] as string[] };

  it('mirrors a WG-cut solve mesh across the planes it cut; the display was never cut', () => {
    expect(displaySymmetry({ domain_decision: WG_HALF, symmetry })).toEqual({ solvedPlanes: ['x0'], cadCutPlanes: [] });
    expect(displaySymmetry({ domain_decision: WG_QUARTER, symmetry })).toEqual({ solvedPlanes: ['x0', 'y0'], cadCutPlanes: [] });
  });

  it('mirrors a CAD-cut model on both views, whichever side it kept', () => {
    expect(displaySymmetry({ domain_decision: RECOVERED_NEGATIVE_HALF, symmetry }))
      .toEqual({ solvedPlanes: ['x0'], cadCutPlanes: ['x0'] });
    expect(displaySymmetry({ domain_decision: RECOVERED_HALF_WG_QUARTER, symmetry }))
      .toEqual({ solvedPlanes: ['x0', 'y0'], cadCutPlanes: ['x0'] });
    expect(displaySymmetry({ domain_decision: PROVENANCE_HALF, symmetry }))
      .toEqual({ solvedPlanes: ['x0'], cadCutPlanes: ['x0'] });
  });

  it('mirrors nothing for any refused decision, even one whose planes WG cut', () => {
    const improper = decisionFixture({
      ...WG_HALF,
      frame: { ...WG_HALF.frame, proper: false, determinant: -1 },
      confidence: 'refused',
      refusal: { code: 'imported_frame_improper', message: 'The solver frame this model was meshed in is not a proper rotation.' },
    });
    expect(displaySymmetry({ domain_decision: improper, symmetry: { cut_planes: ['x0'], domain_planes: ['x0'] } }))
      .toEqual({ solvedPlanes: [], cadCutPlanes: [] });
  });

  it('mirrors nothing it does not solve reduced', () => {
    for (const decision of [REFUSED_CUT, REFUSED_OBLIQUE, UNRESOLVED, OPEN_SHEET]) {
      expect(displaySymmetry({ domain_decision: decision, symmetry })).toEqual({ solvedPlanes: [], cadCutPlanes: [] });
    }
  });

  it('reads an earlier build\'s record from its symmetry planes', () => {
    expect(displaySymmetry({ symmetry: { cut_planes: ['y0'], domain_planes: ['x0', 'y0'] } }))
      .toEqual({ solvedPlanes: ['x0', 'y0'], cadCutPlanes: ['x0'] });
    expect(displaySymmetry({ symmetry: { cut_planes: ['x0'] } })).toEqual({ solvedPlanes: ['x0'], cadCutPlanes: [] });
  });
});

describe('the run details of a decision', () => {
  it('states the reading, the domain, the reflection, the frame and the decision', () => {
    expect(decisionSummaryRows(summaryOf(RECOVERED_NEGATIVE_HALF))).toEqual([
      { label: 'Reading', value: 'Cut in CAD at x = 0 — recovered by mirroring (the x ≤ 0 side reflected)', title: undefined },
      { label: 'Solved as', value: 'half (mirrored at x = 0)' },
      {
        label: 'Reflection',
        value: 'the x ≤ 0 side reflected',
        title: 'The model was cut in CAD keeping the negative side; its mesh was reflected onto the side the solver mirrors.',
      },
      { label: 'Frame', value: '+z forward, +y up' },
      { label: 'Decision', value: 'aaaaaaaaaaaa', title: `sha256:${'a'.repeat(64)}` },
    ]);
    expect(decisionSummaryRows(summaryOf(decisionFixture())).map((row) => [row.label, row.value])).toEqual([
      ['Reading', 'Full model'],
      ['Solved as', 'the whole model as shown'],
      ['Frame', '+z forward, +y up'],
      ['Decision', 'aaaaaaaaaaaa'],
    ]);
  });
});
