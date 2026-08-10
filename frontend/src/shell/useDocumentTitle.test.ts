import { describe, expect, it } from 'vitest';
import { APP_NAME, documentTitle } from './useDocumentTitle';

describe('document title', () => {
  // index.html used to hardcode one specific .cfg filename, so every window
  // claimed that document was open whatever was actually on screen.
  it('names the app alone when no document is open', () => {
    expect(documentTitle('', false)).toBe(APP_NAME);
    expect(documentTitle('   ', true)).toBe(APP_NAME);
  });

  it('leads with the document, so it survives a truncated tab', () => {
    expect(documentTitle('tritonia_mk2.cfg', false)).toBe(`tritonia_mk2.cfg — ${APP_NAME}`);
  });

  it('marks unsaved work with the platform bullet', () => {
    expect(documentTitle('tritonia_mk2.cfg', true)).toBe(`• tritonia_mk2.cfg — ${APP_NAME}`);
  });
});
