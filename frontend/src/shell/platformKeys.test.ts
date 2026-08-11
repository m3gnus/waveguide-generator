import { describe, expect, it } from 'vitest';
import { commandShortcutLabel, isApplePlatform } from './platformKeys';

describe('command modifier labels', () => {
  it.each([
    ['MacIntel', '⌘K'],
    ['MacARM', '⌘K'],
    ['iPhone', '⌘K'],
    ['iPad', '⌘K'],
    ['Win32', 'Ctrl+K'],
    ['Windows', 'Ctrl+K'],
    ['Linux x86_64', 'Ctrl+K'],
    ['FreeBSD amd64', 'Ctrl+K'],
  ])('names the modifier %s uses', (platform, label) => {
    expect(commandShortcutLabel('K', platform)).toBe(label);
  });

  it('carries the key through unchanged', () => {
    expect(commandShortcutLabel('↵', 'MacIntel')).toBe('⌘↵');
    expect(commandShortcutLabel('↵', 'Win32')).toBe('Ctrl+↵');
  });

  it('reads a user-agent string, which is the fallback when platform is empty', () => {
    expect(isApplePlatform('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)')).toBe(true);
    expect(isApplePlatform('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')).toBe(false);
  });

  // A host that says nothing is not a Mac, and the shortcut still works there:
  // every handler accepts Meta or Ctrl, so Ctrl is the safe thing to advertise.
  it('falls back to Ctrl when the platform is unknown', () => {
    expect(commandShortcutLabel('K', '')).toBe('Ctrl+K');
  });
});
