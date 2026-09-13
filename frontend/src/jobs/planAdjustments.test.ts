import { describe, expect, it } from 'vitest';
import { postSolvePlan, type PlanAdjustment, type SolvePlan } from './actions';
import { planAdjustmentNotice } from './planAdjustments';

/**
 * BEMPP solves a free-standing horn on a closed body, so the server replaces a
 * missing or zero wall thickness with 5 mm. An explicit 0 is a request the
 * user made; reporting it as "a default was applied" would hide that it was
 * overridden.
 */

const plan = (over: Partial<SolvePlan> = {}): SolvePlan => ({
  engine: 'bempp',
  formulation: 'full-3d',
  reason: "explicit solver_mode='full_3d'",
  eligibility_reasons: [],
  ...over,
});

const wall = (requested: 'omitted' | 'explicit_zero'): PlanAdjustment => ({
  kind: 'bempp_wall_default',
  requested,
  effective_mm: 5,
  reason_code: 'bempp_free_standing_requires_closed_wall',
  policy_version: 1,
});

describe('planAdjustmentNotice', () => {
  it('says an explicit 0 mm wall is overridden', () => {
    const text = planAdjustmentNotice(wall('explicit_zero'));

    expect(text).toContain('0 mm');
    expect(text).toContain('5 mm');
    expect(text).toContain('overrid');
  });

  it('says an unset wall gets the 5 mm default, without calling it an override', () => {
    const text = planAdjustmentNotice(wall('omitted'));

    expect(text).toContain('5 mm');
    expect(text).not.toContain('overrid');
    expect(text).not.toBe(planAdjustmentNotice(wall('explicit_zero')));
  });

  it('has nothing to say about an adjustment kind it does not know', () => {
    expect(planAdjustmentNotice({ kind: 'something_else' } as never)).toBeNull();
  });
});

describe('postSolvePlan adjustments', () => {
  const respond = (body: unknown) => (async () => new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })) as unknown as typeof fetch;

  it('accepts a plan carrying the wall adjustment', async () => {
    const parsed = await postSolvePlan('{}', respond(plan({ adjustments: [wall('explicit_zero')] })));

    expect(parsed.adjustments).toEqual([wall('explicit_zero')]);
  });

  it('accepts a plan from a server that sends no adjustments field', async () => {
    const parsed = await postSolvePlan('{}', respond(plan()));

    expect(parsed.adjustments).toBeUndefined();
  });

  it('rejects a half-built wall adjustment instead of rendering a blank notice', async () => {
    const partial = { kind: 'bempp_wall_default', requested: 'explicit_zero' };

    await expect(
      postSolvePlan('{}', respond(plan({ adjustments: [partial as never] }))),
    ).rejects.toThrow('Solve plan response is invalid');
  });

  it('rejects a wall adjustment whose policy version is not an integer', async () => {
    const fractional = { ...wall('omitted'), policy_version: 1.5 };

    await expect(
      postSolvePlan('{}', respond(plan({ adjustments: [fractional] }))),
    ).rejects.toThrow('Solve plan response is invalid');
  });

  it('rejects adjustments that are not a list', async () => {
    await expect(
      postSolvePlan('{}', respond(plan({ adjustments: wall('omitted') as never }))),
    ).rejects.toThrow('Solve plan response is invalid');
  });
});
