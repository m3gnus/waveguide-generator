import { afterAll, expect, it } from 'vitest';
import { writeFileSync } from 'node:fs';
import routes from '../../../server/tests/fixtures/error_routes_round3.json';
import { failureWords, PLAIN_REQUEST_FAILURE, refusalText } from './httpError';
import { queryFreeformCurves } from './freeformProfile';
import { designForFamily } from '../stores/design';
import { solveModel, submitDesign, type ModelSolvePress } from '../jobs/actions';
import '../design/parameterRegistry';

const finalWords: { route: string; words: string }[] = [];
it.each(routes)('keeps the final public remedy from $route', async row => {
  const body = row.body as Record<string, unknown>;
  const fetcher = async () => Response.json(body, { status: row.status });
  let words: string;
  if (row.route.includes('/freeform/curves')) {
    let reason: unknown;
    try { await queryFreeformCurves(designForFamily('FREEFORM'), 'review', undefined, fetcher); } catch (error) { reason = error; }
    words = failureWords(reason);
    expect(words).toContain('Cross-section stations');
  } else if (row.route.endsWith(':frequencies')) {
    let reason: unknown;
    try { await submitDesign(designForFamily('OSSE'), undefined, fetcher); } catch (error) { reason = error; }
    words = failureWords(reason);
    expect(words).toContain('Enter each frequency once');
    const imported = await solveModel('review', {} as ModelSolvePress, fetcher).catch((error: unknown) => error);
    expect(failureWords(imported)).toBe(words);
  } else words = failureWords(refusalText(body.detail ?? body.refusal, row.route));
  expect(words).not.toBe(PLAIN_REQUEST_FAILURE);
  expect(words).not.toContain('has recorded the problem');
  expect(words).not.toMatch(/Value error,|design\.FREEFORM|Solve request body is invalid/);
  finalWords.push({ route: row.route, words });
});
it('uses solve validation rows even when the top-level detail is generic', async () => {
  const body = { detail: 'Solve request body is invalid', error: { code: 'invalid_request', details: { validation_errors: [{ loc: ['body', 'options'], msg: 'Value error, Solver frequencies in Hz must increase without duplicates. Enter each frequency once, from lowest to highest, in Solve options.' }] } } };
  const fetcher = async () => Response.json(body, { status: 422 });
  await expect(submitDesign(designForFamily('OSSE'), undefined, fetcher)).rejects.toThrow('Enter each frequency once');
  await expect(solveModel('review', {} as ModelSolvePress, fetcher)).rejects.toThrow('Enter each frequency once');
});
afterAll(() => {
  const output = (globalThis as { process?: { env: Record<string, string> } }).process?.env.REFUSAL_FINAL_WORDS;
  if (output) writeFileSync(output, finalWords.map(row => JSON.stringify(row)).join('\n') + '\n');
});
