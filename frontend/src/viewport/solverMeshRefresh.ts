/** Hybrid refresh policy for the simulation-mesh viewport.
 *
 * While the mesh view is active every design edit schedules a debounced
 * rebuild, coalesced to at most one build in flight plus one pending; the
 * pending build always starts from the latest design. Auto-rebuild yields to
 * a visible "Mesh out of date" banner instead whenever rebuilding on every
 * edit would hurt: the previous build ran over budget, the previous build
 * failed, or a solve job currently owns the pipeline. A manual refresh is
 * always allowed and is how the banner is cleared.
 */

export const SOLVER_MESH_DEBOUNCE_MS = 500;
export const SOLVER_MESH_AUTO_BUDGET_MS = 1_500;

export type SolverMeshStaleReason = 'build-failed' | 'build-slow' | 'solve-running';

export interface SolverMeshRefreshState {
  /** A build request is currently in flight. */
  building: boolean;
  /** The displayed mesh no longer matches the design and auto-rebuild is
   * withheld; the viewport must offer a manual Refresh. */
  stale: boolean;
  staleReason: SolverMeshStaleReason | null;
  /** The last build failure, held until a later build succeeds. */
  error: string | null;
}

export interface SolverMeshBuildOutcome {
  ok: boolean;
  durationMs: number;
  error?: string;
}

export interface SolverMeshRefreshOptions {
  /** Perform one build for the *current* design (read at call time, so the
   * latest design always wins). Must resolve, never reject: failures come
   * back as `{ok: false}`. `signal` aborts the request on deactivation. */
  runBuild: (signal: AbortSignal) => Promise<SolverMeshBuildOutcome>;
  /** A solve job is currently running or queued. */
  isSolveActive: () => boolean;
  onState: (state: SolverMeshRefreshState) => void;
  debounceMs?: number;
  autoBudgetMs?: number;
}

const FRESH: SolverMeshRefreshState = { building: false, stale: false, staleReason: null, error: null };

export class SolverMeshRefreshController {
  private readonly options: Required<SolverMeshRefreshOptions>;
  private active = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inFlight: AbortController | null = null;
  private pending = false;
  private latestRevision: number | null = null;
  private builtRevision: number | null = null;
  private lastOutcome: SolverMeshBuildOutcome | null = null;
  private state: SolverMeshRefreshState = FRESH;

  constructor(options: SolverMeshRefreshOptions) {
    this.options = {
      debounceMs: SOLVER_MESH_DEBOUNCE_MS,
      autoBudgetMs: SOLVER_MESH_AUTO_BUDGET_MS,
      ...options,
    };
  }

  getState(): SolverMeshRefreshState {
    return this.state;
  }

  /** The mesh view became the active viewport source. Activation is user
   * intent, so an out-of-date or failed mesh rebuilds immediately — only a
   * running solve withholds the build behind the banner. */
  activate(revision: number): void {
    this.active = true;
    this.latestRevision = revision;
    if (this.builtRevision === revision && this.lastOutcome?.ok) {
      this.publish(FRESH);
      return;
    }
    if (this.options.isSolveActive()) {
      this.publish({ building: false, stale: true, staleReason: 'solve-running', error: this.state.error });
      return;
    }
    this.startBuild();
  }

  deactivate(): void {
    this.active = false;
    this.clearTimer();
    this.pending = false;
    this.inFlight?.abort();
    this.inFlight = null;
  }

  /** A design mutation landed. Debounce while auto-rebuild is allowed; raise
   * the banner instead when it is not. */
  designChanged(revision: number): void {
    if (revision === this.latestRevision) return;
    this.latestRevision = revision;
    if (!this.active) return;
    if (this.inFlight) {
      // Coalesce: one in flight plus one pending; the pending build reads the
      // design when it starts, so the latest revision wins.
      this.pending = true;
      return;
    }
    const reason = this.blockedReason();
    if (reason !== null) {
      this.clearTimer();
      this.publish({ building: false, stale: true, staleReason: reason, error: this.state.error });
      return;
    }
    this.clearTimer();
    this.timer = setTimeout(() => {
      this.timer = null;
      if (!this.active || this.inFlight) return;
      this.startBuild();
    }, this.options.debounceMs);
  }

  /** Manual refresh: always allowed, clears the banner path. */
  refresh(): void {
    if (!this.active) return;
    if (this.inFlight) {
      this.pending = true;
      return;
    }
    this.clearTimer();
    this.startBuild();
  }

  /** A solve finished. If the banner is up only because of the solve, resume
   * the automatic path for the missed edits. */
  solveSettled(): void {
    if (!this.active || this.inFlight) return;
    if (this.state.stale && this.state.staleReason === 'solve-running') {
      this.startBuild();
    }
  }

  private blockedReason(): SolverMeshStaleReason | null {
    if (this.options.isSolveActive()) return 'solve-running';
    if (this.lastOutcome && !this.lastOutcome.ok) return 'build-failed';
    if (this.lastOutcome && this.lastOutcome.durationMs > this.options.autoBudgetMs) return 'build-slow';
    return null;
  }

  private startBuild(): void {
    this.clearTimer();
    const controller = new AbortController();
    this.inFlight = controller;
    const revision = this.latestRevision;
    this.publish({ building: true, stale: false, staleReason: null, error: this.state.error });
    void this.options.runBuild(controller.signal).then((outcome) => {
      if (this.inFlight !== controller) return; // deactivated or superseded
      this.inFlight = null;
      this.lastOutcome = outcome;
      if (outcome.ok) this.builtRevision = revision;
      if (!this.active) return;
      const missedEdit = this.pending || this.latestRevision !== revision;
      this.pending = false;
      if (!outcome.ok) {
        this.publish({
          building: false,
          stale: true,
          staleReason: 'build-failed',
          error: outcome.error ?? 'Solver mesh build failed',
        });
        return;
      }
      if (!missedEdit) {
        this.publish(FRESH);
        return;
      }
      const reason = this.blockedReason();
      if (reason !== null) {
        this.publish({ building: false, stale: true, staleReason: reason, error: null });
        return;
      }
      this.publish(FRESH);
      this.startBuild();
    });
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private publish(state: SolverMeshRefreshState): void {
    this.state = state;
    this.options.onState(state);
  }
}
