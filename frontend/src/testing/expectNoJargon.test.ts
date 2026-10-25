import { describe, expect, it } from 'vitest';
import { expectNoJargon } from './expectNoJargon';

describe('the shared CAD wording guard', () => {
  it.each(['CAD return', '.wgreturn', 'request id', 'entity_token', "options['anchor']",
    'finding-scope-123', 'PASSIVE_CARDIOID', 'UTF-8', 'orthonormal', 'refused',
    '82a49358-0f6d-4c1e-9d55-2b7a1c7e0f11'])('rejects developer copy: %s', word => {
    const element = document.createElement('div');
    element.textContent = `This model needs ${word}.`;
    expect(() => expectNoJargon(element)).toThrow();
  });

  it.each(['title', 'aria-label', 'placeholder', 'alt'])('checks %s as well as visible text', attribute => {
    const element = document.createElement('div');
    element.innerHTML = `<button ${attribute}="Show request id">Show model</button>`;
    expect(() => expectNoJargon(element)).toThrow();
  });

  it('allows diagnostic evidence only in the existing developer disclosure', () => {
    const element = document.createElement('div');
    element.innerHTML = '<p>WG could not prepare this model. Try again.</p>'
      + '<details class="developer-details"><summary>Diagnostics</summary>bundle schema traceback</details>';
    expectNoJargon(element);
    expect(element.textContent).toContain('bundle schema traceback');
  });

  it('allows user names without exempting the rest of the sentence', () => {
    expectNoJargon('Gate horn is ready to solve.', { names: ['Gate horn'] });
    expect(() => expectNoJargon('Gate horn: the bundle is ready.', { names: ['Gate horn'] })).toThrow();
  });
});
