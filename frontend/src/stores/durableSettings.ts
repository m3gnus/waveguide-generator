/**
 * One durable home for everything WG remembers between sessions.
 *
 * Every setting used to live only in `localStorage`, which is scoped to the
 * exact origin the window was opened from. Losing the lot therefore did not
 * need a bug: the server's port scan moving off 3100, a browser set to clear
 * site data on exit, reaching WG through `localhost` rather than `127.0.0.1`,
 * or simply a different browser than last time each orphaned the whole store
 * at once. The application data directory has none of those failure modes, so
 * it is the authority here and the browser copy is demoted to a cache.
 *
 * The cache is what makes this invisible: it is read synchronously under the
 * original storage keys, so every store still initialises during module
 * evaluation and the first paint is the user's real settings rather than
 * defaults that flip a moment later. The server answer arrives afterwards and
 * only then, if it disagrees, are subscribers asked to re-read.
 *
 * Values are opaque strings -- exactly the strings that used to be stored
 * locally. Each store keeps its own schema, validation, and migrations; this
 * module deliberately understands none of them.
 */

/** Namespace -> the `localStorage` key it has always used. */
export const SETTINGS_NAMESPACES = {
  preferences: 'waveguide-v2-g3-preferences',
  solveOptions: 'waveguide-v2-solve-options',
  viewer: 'wg2.preferences.v1',
  theme: 'wg2.theme',
  dockviewLayout: 'wg2.dockview.layout.v4',
  dockviewMode: 'wg2.dockview.mode.v4',
  paramHelp: 'wg-param-help-visible',
  paramSections: 'wg-param-sections',
  cadSolveProfiles: 'waveguide-v2-g3-cad-solve-profiles',
  cadAcknowledgedFindings: 'waveguide-v2-g3-cad-acknowledged-findings',
  designDraft: 'wg2.autosave.v1',
} as const;

export type SettingsNamespace = keyof typeof SETTINGS_NAMESPACES;

/** The design draft changes on every edit; the rest change when a user acts. */
const WRITE_DELAY_MS: Partial<Record<SettingsNamespace, number>> = { designDraft: 1_500 };
const DEFAULT_WRITE_DELAY_MS = 400;
const LOCAL_NEWER_SUFFIX = '.local-newer';

export interface SettingsEnvelope {
  schemaVersion?: number;
  namespaces?: Record<string, unknown>;
}

type Listener = (raw: string | null) => void;

function defaultStorage(): Storage | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
}

export interface DurableSettingsOptions {
  storage?: Storage | null;
  fetcher?: typeof fetch;
  /** Injected in tests so debounced writes need no timers. */
  writeDelayMs?: (namespace: SettingsNamespace) => number;
}

export class DurableSettings {
  private readonly storage: Storage | null;
  private readonly fetcher: typeof fetch;
  private readonly writeDelay: (namespace: SettingsNamespace) => number;
  private readonly listeners = new Map<SettingsNamespace, Set<Listener>>();
  private readonly timers = new Map<SettingsNamespace, ReturnType<typeof setTimeout>>();
  /** Serialises per namespace so a slow request cannot land after a newer one. */
  private readonly inFlight = new Map<SettingsNamespace, Promise<void>>();
  private readonly memory = new Map<SettingsNamespace, string | null>();
  private readonly memoryLocalNewer = new Set<SettingsNamespace>();
  private hydrated = false;

  constructor({ storage, fetcher, writeDelayMs }: DurableSettingsOptions = {}) {
    this.storage = storage === undefined ? defaultStorage() : storage;
    this.fetcher = fetcher ?? ((...args: Parameters<typeof fetch>) => fetch(...args));
    this.writeDelay = writeDelayMs
      ?? ((namespace) => WRITE_DELAY_MS[namespace] ?? DEFAULT_WRITE_DELAY_MS);
  }

  /** True once the server has answered, whatever it said. */
  get isHydrated(): boolean { return this.hydrated; }

  /**
   * The browser copy is the cache and stays the thing that is read.
   *
   * `memory` is only a fallback for a browser that has no usable storage at
   * all -- private windows, storage disabled, a full quota. Preferring it would
   * make the cache write-only and hide any change made outside this instance.
   */
  get(namespace: SettingsNamespace): string | null {
    try {
      if (this.storage) return this.storage.getItem(SETTINGS_NAMESPACES[namespace]);
    } catch { /* fall through to the in-memory copy */ }
    return this.memory.get(namespace) ?? null;
  }

  /** Record a change locally, then push it to the server in the background. */
  set(namespace: SettingsNamespace, raw: string | null): void {
    this.writeCache(namespace, raw);
    this.scheduleUpload(namespace);
  }

  subscribe(namespace: SettingsNamespace, listener: Listener): () => void {
    const existing = this.listeners.get(namespace) ?? new Set<Listener>();
    existing.add(listener);
    this.listeners.set(namespace, existing);
    return () => { existing.delete(listener); };
  }

  /**
   * Adopt the server's copy, and seed the server from this browser the first
   * time a namespace is seen. The seeding branch is what carries an existing
   * installation's settings across without the user noticing an upgrade.
   */
  async hydrate({ timeoutMs }: { timeoutMs?: number } = {}): Promise<void> {
    // The server is on loopback, so this normally costs a millisecond. The
    // cap bounds how long startup waits, not the request itself: a slow answer
    // is still applied when it lands, so a sluggish backend costs a moment of
    // cached settings rather than a whole session of them.
    const cachedAtRequest = new Map<SettingsNamespace, string | null>();
    for (const namespace of Object.keys(SETTINGS_NAMESPACES) as SettingsNamespace[]) {
      cachedAtRequest.set(namespace, this.get(namespace));
    }
    const request = (async () => {
      try {
        const response = await this.fetcher('/api/settings');
        return response.ok ? await response.json() as SettingsEnvelope : null;
      } catch { return null; /* an unreachable server leaves the cache in charge */ }
    })();
    const applied = request.then((body) => { this.apply(body, cachedAtRequest); });

    if (timeoutMs === undefined) {
      await applied;
    } else {
      await Promise.race([applied, new Promise((resolve) => setTimeout(resolve, timeoutMs))]);
    }
    this.hydrated = true;
  }

  private apply(
    envelope: SettingsEnvelope | null,
    cachedAtRequest: ReadonlyMap<SettingsNamespace, string | null>,
  ): void {
    const stored = envelope?.namespaces ?? {};
    for (const namespace of Object.keys(SETTINGS_NAMESPACES) as SettingsNamespace[]) {
      const local = this.get(namespace);
      // A prior publish failed or has not completed. This marker is stored next
      // to the cache so it survives a restart: an older server answer may not
      // replace the only newer copy, and hydration is the retry opportunity.
      if (this.isLocalNewer(namespace)) {
        this.scheduleUpload(namespace, 0);
        continue;
      }
      if (!envelope) continue;
      // The request describes the server state from before any edits made
      // while it was in flight. A diverged cache is therefore newer and must
      // neither be overwritten nor reported to subscribers as server state.
      if (local !== cachedAtRequest.get(namespace)) continue;
      const remote = stored[namespace];
      if (typeof remote === 'string') {
        if (remote === local) continue;
        this.writeCache(namespace, remote);
        this.notify(namespace, remote);
        continue;
      }
      // Absent upstream: this browser is the only copy, so publish it once.
      if (local !== null) this.scheduleUpload(namespace, 0);
    }
  }

  /** Push every pending change before the page goes away. */
  flush(): void {
    for (const namespace of [...this.timers.keys()]) this.scheduleUpload(namespace, 0, true);
  }

  private writeCache(namespace: SettingsNamespace, raw: string | null): void {
    this.memory.set(namespace, raw);
    try {
      if (raw === null) this.storage?.removeItem(SETTINGS_NAMESPACES[namespace]);
      else this.storage?.setItem(SETTINGS_NAMESPACES[namespace], raw);
    } catch { /* the cache is best effort; the server copy is the durable one */ }
  }

  private notify(namespace: SettingsNamespace, raw: string | null): void {
    for (const listener of this.listeners.get(namespace) ?? []) {
      try { listener(raw); } catch { /* one bad subscriber must not stop the rest */ }
    }
  }

  private localNewerKey(namespace: SettingsNamespace): string {
    return `${SETTINGS_NAMESPACES[namespace]}${LOCAL_NEWER_SUFFIX}`;
  }

  private isLocalNewer(namespace: SettingsNamespace): boolean {
    try {
      if (this.storage?.getItem(this.localNewerKey(namespace)) === '1') return true;
    } catch { /* fall through to the in-memory marker */ }
    return this.memoryLocalNewer.has(namespace);
  }

  private markLocalNewer(namespace: SettingsNamespace): void {
    this.memoryLocalNewer.add(namespace);
    try { this.storage?.setItem(this.localNewerKey(namespace), '1'); } catch { /* best effort */ }
  }

  private clearLocalNewer(namespace: SettingsNamespace): void {
    this.memoryLocalNewer.delete(namespace);
    try { this.storage?.removeItem(this.localNewerKey(namespace)); } catch { /* best effort */ }
  }

  private scheduleUpload(namespace: SettingsNamespace, delayMs?: number, keepalive = false): void {
    this.markLocalNewer(namespace);
    const timer = this.timers.get(namespace);
    if (timer !== undefined) clearTimeout(timer);
    const delay = delayMs ?? this.writeDelay(namespace);
    const run = () => {
      this.timers.delete(namespace);
      void this.upload(namespace, keepalive);
    };
    if (delay <= 0) { run(); return; }
    this.timers.set(namespace, setTimeout(run, delay));
  }

  private upload(namespace: SettingsNamespace, keepalive: boolean): Promise<void> {
    // An unload flush must reach the network now. Waiting behind an in-flight
    // request would mean it is never issued, and the newer value would then be
    // overwritten by the server's older copy on the next start. Both requests
    // read the value at send time, so racing them cannot publish a stale one.
    const previous = keepalive ? Promise.resolve() : this.inFlight.get(namespace) ?? Promise.resolve();
    const next = previous.then(async () => {
      // Read at send time, not at schedule time, so the newest value wins even
      // when several changes collapsed into this one request.
      const raw = this.get(namespace);
      try {
        const response = await this.fetcher(`/api/settings/${namespace}`, {
          method: raw === null ? 'DELETE' : 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: raw === null ? undefined : JSON.stringify(raw),
          keepalive,
        });
        // Non-OK responses are failed writes too. Clear only when the server
        // accepted the value that is still current; a later local edit keeps
        // its own marker and queued upload.
        if (response.ok && this.get(namespace) === raw) {
          this.clearLocalNewer(namespace);
        }
      } catch { /* the cache still holds it; the next change or hydration retries */ }
    }).finally(() => {
      if (this.inFlight.get(namespace) === next) this.inFlight.delete(namespace);
    });
    this.inFlight.set(namespace, next);
    return next;
  }
}

export const durableSettings = new DurableSettings();

/**
 * A `Storage`-shaped view of one namespace.
 *
 * Lets a store that already reads and writes `localStorage` directly move onto
 * durable settings without restructuring how it loads.
 */
export function namespaceStorage(
  namespace: SettingsNamespace,
  settings: DurableSettings = durableSettings,
): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> {
  return {
    getItem: () => settings.get(namespace),
    setItem: (_key: string, value: string) => settings.set(namespace, value),
    removeItem: () => settings.set(namespace, null),
  };
}

/**
 * Make sure a pending change survives the window closing.
 *
 * Writes are debounced so typing in a field is not one request per keystroke,
 * which leaves a window in which the newest value exists only in this tab.
 * Both events are registered because neither fires reliably alone: `pagehide`
 * covers the back/forward cache, `beforeunload` covers the rest.
 */
export function startDurableSettings(
  settings: DurableSettings = durableSettings,
  windowTarget: Window | null = typeof window === 'undefined' ? null : window,
): () => void {
  const flush = () => settings.flush();
  windowTarget?.addEventListener('beforeunload', flush);
  windowTarget?.addEventListener('pagehide', flush);
  return () => {
    windowTarget?.removeEventListener('beforeunload', flush);
    windowTarget?.removeEventListener('pagehide', flush);
  };
}
