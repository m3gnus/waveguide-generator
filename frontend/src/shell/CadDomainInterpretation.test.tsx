/**
 * PLAN.md M1c-auto: the model card states how WG read the model's domain, in
 * one line, and offers Change. No question, no dropdown. The response shapes
 * are the ones `server/cadlink/domain_interpretation.py` records and
 * `GET/PUT /api/cadlink/domain-interpretation` answers.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { DomainInterpretation } from '../api/domainInterpretation';
import { CadDomainInterpretation, domainLine, readingWords } from './CadDomainInterpretation';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function interpretation(overrides: Partial<DomainInterpretation> = {}): DomainInterpretation {
  return {
    contract: 'cad-domain-interpretation-v1',
    manifest_domain: 'automatic',
    reading: 'full',
    planes: [],
    wg_cut_planes: [],
    domain_planes: [],
    looks_cut: [],
    ambiguous: [],
    evidence: { source: null, features: [] },
    choices: [],
    ...overrides,
  };
}

const PROVENANCE_HALF = interpretation({
  reading: 'reduced',
  planes: ['x0'],
  domain_planes: ['x0'],
  evidence: { source: 'cad-provenance', features: [{ plane: 'x0', kind: 'split-body', name: 'Split Body 3' }], applied: true },
  choices: [{ reading: 'as-shown' }],
});

const LOOKS_CUT = interpretation({
  reading: 'as-shown',
  looks_cut: ['x0'],
  choices: [{ reading: 'reduced', planes: ['x0'] }],
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe('the domain line', () => {
  it('says what was solved, and why, in the words of the plan', () => {
    expect(domainLine(PROVENANCE_HALF)).toEqual({ text: 'Half model · cut at x = 0 (Split Body 3)', change: true });
    expect(domainLine(LOOKS_CUT)).toEqual({ text: 'Solved as shown · looks cut at x = 0', change: true });
    // The smallest model: WG's validated second plane on a pre-cut half.
    expect(domainLine({ ...PROVENANCE_HALF, domain_planes: ['x0', 'y0'], wg_cut_planes: ['y0'] }).text)
      .toBe('Quarter model · cut at x = 0 (Split Body 3), mirrored at y = 0');
    expect(domainLine(interpretation({ reading: 'reduced', planes: ['x0', 'y0'], domain_planes: ['x0', 'y0'], evidence: { source: 'user' } })).text)
      .toBe('Quarter model · cut at x = 0 and y = 0 (your choice)');
    expect(domainLine(interpretation({ reading: 'as-shown', ambiguous: ['x0'] })).text).toBe('Solved as shown');
    expect(domainLine(interpretation({ reading: 'as-shown', evidence: { source: 'user' }, choices: [{ reading: 'reduced', planes: ['x0'] }] })).text)
      .toBe('Solved as shown (your choice)');
    expect(domainLine(interpretation({ wg_cut_planes: ['x0', 'y0'] }))).toEqual({ text: 'Full model · WG mirrors it at x = 0 and y = 0', change: false });
    expect(domainLine(interpretation())).toEqual({ text: 'Full model', change: false });
    // A declaration is changed in Fusion, not here.
    expect(domainLine(interpretation({ reading: 'reduced', planes: ['y0'], domain_planes: ['y0'], evidence: { source: 'declaration' } })))
      .toEqual({ text: 'Half model · cut at y = 0 (declared in Fusion)', change: false });
  });

  it('names each reading Change offers', () => {
    expect(readingWords({ reading: 'as-shown' })).toBe('Solve it as shown, unmirrored');
    expect(readingWords({ reading: 'reduced', planes: ['x0'] })).toBe('Half model, cut at x = 0');
    expect(readingWords({ reading: 'reduced', planes: ['x0', 'y0'] })).toBe('Quarter model, cut at x = 0 and y = 0');
  });
});

describe('Change', () => {
  let host: HTMLDivElement;
  let root: Root;
  let puts: unknown[];
  let pendingOnGet: unknown;

  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === 'PUT') {
      const body = JSON.parse(String(init.body));
      puts.push(body);
      const { ingestId: _ingestId, ...reading } = body;
      return new Response(JSON.stringify({ ingestId: 'wgi_1', available: true, pending: reading }), { status: 200 });
    }
    expect(url).toBe('/api/cadlink/domain-interpretation?ingestId=wgi_1');
    return new Response(JSON.stringify({ ingestId: 'wgi_1', available: true, pending: pendingOnGet }), { status: 200 });
  }) as typeof fetch;

  beforeEach(() => {
    puts = [];
    pendingOnGet = null;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  async function render(value: DomainInterpretation) {
    await act(async () => {
      root.render(<CadDomainInterpretation ingestId="wgi_1" interpretation={value} fetcher={fetcher}/>);
    });
  }

  it('is one line with no question and no dropdown until Change is pressed', async () => {
    await render(LOOKS_CUT);
    expect(host.querySelector('.cad-domain-line')!.textContent).toBe('Solved as shown · looks cut at x = 0 · Change');
    expect(host.querySelector('select')).toBeNull();
    expect(host.textContent).not.toContain('?');
    expect(host.querySelector('.cad-domain-choices')).toBeNull();
  });

  it('records the reading chosen and says Solve prepares it again', async () => {
    await render(LOOKS_CUT);
    await act(async () => { (host.querySelector('[data-action="change-domain"]') as HTMLButtonElement).click(); });
    const choices = [...host.querySelectorAll('[data-action="choose-domain"]')].map((button) => button.textContent);
    expect(choices).toEqual(['Half model, cut at x = 0']);
    await act(async () => { (host.querySelector('[data-action="choose-domain"]') as HTMLButtonElement).click(); });
    expect(puts).toEqual([{ ingestId: 'wgi_1', reading: 'reduced', planes: ['x0'] }]);
    expect(host.querySelector('.cad-domain-pending')!.textContent).toBe('Solve prepares it again: half model, cut at x = 0.');
    expect(host.querySelector('.cad-domain-choices')).toBeNull();
  });

  it('does not let the mount read overwrite a newer Change', async () => {
    const get = deferred<Response>();
    const put = deferred<Response>();
    const orderedFetcher = ((_: RequestInfo | URL, init?: RequestInit) => (
      init?.method === 'PUT' ? put.promise : get.promise
    )) as typeof fetch;
    await act(async () => {
      root.render(<CadDomainInterpretation ingestId="wgi_1" interpretation={LOOKS_CUT} fetcher={orderedFetcher}/>);
    });
    await act(async () => { (host.querySelector('[data-action="change-domain"]') as HTMLButtonElement).click(); });
    await act(async () => { (host.querySelector('[data-action="choose-domain"]') as HTMLButtonElement).click(); });
    await act(async () => {
      put.resolve(new Response(JSON.stringify({
        ingestId: 'wgi_1', available: true, pending: { reading: 'reduced', planes: ['x0'] },
      }), { status: 200 }));
      await put.promise;
    });
    expect(host.querySelector('.cad-domain-pending')!.textContent).toContain('half model');

    await act(async () => {
      get.resolve(new Response(JSON.stringify({ ingestId: 'wgi_1', available: true, pending: null }), { status: 200 }));
      await get.promise;
    });
    expect(host.querySelector('.cad-domain-pending')!.textContent).toContain('half model');
  });

  it('does not apply a completed Change after the card switches records', async () => {
    const oldPut = deferred<Response>();
    const switchingFetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PUT') return oldPut.promise;
      const ingestId = new URL(String(input), 'http://wg.test').searchParams.get('ingestId');
      return new Response(JSON.stringify({
        ingestId,
        available: true,
        pending: ingestId === 'wgi_2' ? { reading: 'as-shown' } : null,
      }), { status: 200 });
    }) as typeof fetch;
    await act(async () => {
      root.render(<CadDomainInterpretation ingestId="wgi_1" interpretation={LOOKS_CUT} fetcher={switchingFetcher}/>);
    });
    await act(async () => { (host.querySelector('[data-action="change-domain"]') as HTMLButtonElement).click(); });
    await act(async () => { (host.querySelector('[data-action="choose-domain"]') as HTMLButtonElement).click(); });
    await act(async () => {
      root.render(<CadDomainInterpretation ingestId="wgi_2" interpretation={PROVENANCE_HALF} fetcher={switchingFetcher}/>);
    });
    expect(host.querySelector('.cad-domain-pending')!.textContent).toContain('as shown');

    await act(async () => {
      oldPut.resolve(new Response(JSON.stringify({
        ingestId: 'wgi_1', available: true, pending: { reading: 'reduced', planes: ['x0'] },
      }), { status: 200 }));
      await oldPut.promise;
    });
    expect(host.querySelector('.cad-domain-pending')!.textContent).toContain('as shown');
  });

  it('solves a mirrored model unmirrored on Change, and shows a Change made elsewhere', async () => {
    pendingOnGet = { reading: 'as-shown' };
    await render(PROVENANCE_HALF);
    expect(host.querySelector('.cad-domain-line')!.textContent).toBe('Half model · cut at x = 0 (Split Body 3) · Change');
    expect(host.querySelector('.cad-domain-pending')!.textContent).toBe('Solve prepares it again: as shown, unmirrored.');
    await act(async () => { (host.querySelector('[data-action="change-domain"]') as HTMLButtonElement).click(); });
    await act(async () => { (host.querySelector('[data-action="choose-domain"]') as HTMLButtonElement).click(); });
    expect(puts).toEqual([{ ingestId: 'wgi_1', reading: 'as-shown' }]);
  });

  it('offers no Change for a model WG only cut itself', async () => {
    await render(interpretation({ wg_cut_planes: ['x0'] }));
    expect(host.querySelector('.cad-domain-line')!.textContent).toBe('Full model · WG mirrors it at x = 0');
    expect(host.querySelector('[data-action="change-domain"]')).toBeNull();
  });
});

describe('the Solve card summary', () => {
  it('leaves the domain to its own line once the record states an interpretation', async () => {
    const { modelSummary } = await import('./CadSolveCard');
    const record = {
      scope: { included: [{ name: 'Body1' }] },
      sources: [{ id: 'hf', role: 'HF' }],
      symmetry: { cut_planes: ['x0'], domain_planes: ['x0'] },
    } as never;
    expect(modelSummary(record)).toBe('Body1 · 1 source (HF) · full model, WG mirrors it at x = 0');
    expect(modelSummary({ ...(record as object), domain_interpretation: interpretation({ wg_cut_planes: ['x0'] }) } as never))
      .toBe('Body1 · 1 source (HF)');
  });
});
