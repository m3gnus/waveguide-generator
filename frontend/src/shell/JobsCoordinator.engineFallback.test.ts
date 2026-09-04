import { describe, expect, it } from 'vitest';
import { engineSubstitutionNotice, solveNotice, solvePlanTitle } from './JobsCoordinator';
import { postSolvePlan, type SolvePlan } from '../jobs/actions';

/**
 * The reported symptom was a Solve button that was grey with no explanation.
 * Two halves fix it: the server substitutes an available engine rather than
 * refusing, and these functions are what put that on screen. A substitution
 * nobody is told about is the same defect with a different shape.
 */

const plan = (over: Partial<SolvePlan> = {}): SolvePlan => ({
  engine: 'bempp',
  formulation: 'full-3d',
  reason: "explicit solver_mode='full_3d'",
  eligibility_reasons: [],
  ...over,
});

const substitution = {
  requested: 'beat',
  resolved: 'bempp',
  reason: 'No Julia executable was found.',
};

describe('engineSubstitutionNotice', () => {
  it('names the swap, the cause, and that the preference survives', () => {
    const text = engineSubstitutionNotice(substitution);

    expect(text).toContain('BEAT is not available on this computer');
    expect(text).toContain('will use BEMPP instead');
    expect(text).toContain('No Julia executable was found.');
    // Without this clause the honest reading is that the app has overwritten a
    // setting, and the user's next move is to go and set it back.
    expect(text).toContain('Your BEAT preference is kept');
  });
});

describe('solveNotice', () => {
  const base = {
    cadGeometryActive: false,
    plan: null as SolvePlan | null,
    pending: false,
    optionsError: null as string | null,
    planError: null as string | null,
  };

  it('reports a substitution as a non-blocking notice', () => {
    const notice = solveNotice({ ...base, plan: plan({ engine_substitution: substitution }) });

    expect(notice?.tone).toBe('substituted');
    expect(notice?.text).toContain('BEMPP instead');
  });

  it('says nothing on an ordinary plan', () => {
    expect(solveNotice({ ...base, plan: plan() })).toBeNull();
  });

  it('treats an explicit null substitution as no substitution', () => {
    expect(solveNotice({ ...base, plan: plan({ engine_substitution: null }) })).toBeNull();
  });

  it('surfaces a refusal that no fallback could rescue', () => {
    const notice = solveNotice({
      ...base,
      planError: "Solve engine 'beat' is unavailable, and no other engine on this host can take its place.",
    });

    expect(notice?.tone).toBe('blocked');
    // This is the case the button title used to hold alone. A disabled control
    // cannot be hovered usefully, so it has to be rendered as text.
    expect(notice?.text).toContain('no other engine on this host');
  });

  it('prefers a solve-options error over a plan error', () => {
    const notice = solveNotice({ ...base, optionsError: 'Polar angles are invalid', planError: 'stale' });

    expect(notice?.text).toBe('Polar angles are invalid');
  });

  it('stays quiet while a plan is in flight', () => {
    // Planning is debounced and re-runs on every parameter keystroke, so a
    // "no plan yet" warning would flash throughout ordinary editing.
    expect(solveNotice({ ...base, pending: true })).toBeNull();
  });

  it('leaves the CAD path to its own Metal availability text', () => {
    const notice = solveNotice({
      ...base,
      cadGeometryActive: true,
      planError: 'Metal is unavailable',
    });

    expect(notice).toBeNull();
  });
});

describe('solvePlanTitle', () => {
  it('explains a substitution rather than calling it a formulation fallback', () => {
    const title = solvePlanTitle(plan({ engine_substitution: substitution }), 'beat');

    expect(title).toContain('BEMPP instead');
    expect(title).not.toContain('full-3D fallback');
  });

  it('still reports the axisymmetric-to-full-3D case as before', () => {
    expect(solvePlanTitle(plan({ engine: 'bempp' }), 'metal')).toBe(
      'Solve current design with BEMPP (requested METAL full-3D fallback)',
    );
  });

  it('is unchanged when the requested engine ran', () => {
    expect(solvePlanTitle(plan(), 'bempp')).toBe('Solve current design with BEMPP');
  });
});

describe('postSolvePlan', () => {
  const respond = (body: unknown) => (async () => new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })) as unknown as typeof fetch;

  it('accepts a plan carrying a substitution', async () => {
    const parsed = await postSolvePlan('{}', respond(plan({ engine_substitution: substitution })));

    expect(parsed.engine_substitution).toEqual(substitution);
  });

  it('accepts a plan with no substitution field at all', async () => {
    const parsed = await postSolvePlan('{}', respond(plan()));

    expect(parsed.engine_substitution).toBeUndefined();
  });

  it('rejects a half-built substitution instead of rendering a blank notice', async () => {
    await expect(
      postSolvePlan('{}', respond(plan({ engine_substitution: { requested: 'beat' } as never }))),
    ).rejects.toThrow('Solve plan response is invalid');
  });
});
