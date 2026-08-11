/**
 * What to call the "command" modifier on the keyboard in front of the user.
 *
 * Every shortcut handler in the shell already accepts Meta OR Ctrl
 * (CommandPalette's ⌘K listener, JobsCoordinator's solve chord), but the hints
 * beside them were literal `⌘K` and `⌘↵`. The repo ships installers/windows and
 * installers/linux, so those builds named a key the keyboard does not have and
 * never mentioned the one that actually works.
 *
 * `navigator.platform` is deprecated and the replacement,
 * `navigator.userAgentData.platform`, is Chromium-only -- absent in the Firefox
 * and Safari/WebKit builds this app is also opened in. So `platform` stays the
 * primary signal, with the user-agent string as a fallback for engines that have
 * already emptied it. Both are strings the host controls, which is why the
 * detection input is a parameter: the tests state the platform instead of
 * inheriting whichever machine happens to run them.
 */
export function hostPlatformSignal(): string {
  if (typeof navigator === 'undefined') return '';
  return navigator.platform || navigator.userAgent || '';
}

export function isApplePlatform(signal: string = hostPlatformSignal()): boolean {
  return /mac|iphone|ipad|ipod/i.test(signal);
}

/**
 * Render a modifier chord for display, e.g. `⌘K` or `Ctrl+K`.
 *
 * Apple keyboards write the chord as bare glyphs; everywhere else spells the
 * modifier out and joins with a plus, which is what those platforms' own menus
 * do.
 */
export function commandShortcutLabel(key: string, signal: string = hostPlatformSignal()): string {
  return isApplePlatform(signal) ? `⌘${key}` : `Ctrl+${key}`;
}
