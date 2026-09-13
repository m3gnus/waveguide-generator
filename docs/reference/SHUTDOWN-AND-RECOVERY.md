# Shutdown and forced-exit recovery

How a Waveguide Generator server stops, what it guarantees when it cannot stop
cleanly, and what the next start does about it. Source of truth:
`launch/serve.py`, `server/platform/shutdown_backstop.py`,
`server/platform/temp_session.py`, `server/jobs/store.py`
(`recover_on_startup`) and `launchers/statusapp/controller.py`.

## Every stop has a deadline

Every way a server is asked to stop starts one **5 s budget**
(`DEFAULT_SHUTDOWN_BUDGET_SECONDS`), inside the status window's **8 s** grace
(`StatusController.shutdown_timeout`):

| Stop path | Starts the budget |
|---|---|
| Quit in the status window (it writes the stop file) | yes |
| The status window vanished (crash or kill) | yes; on macOS and Linux this is the only deadline an orphaned server has |
| First Ctrl+C, SIGTERM, SIGBREAK | yes |
| Closing the Windows console | yes |
| Second Ctrl+C | ends the process at once |

Within the budget, in this order:

1. **The moment the stop begins**, on a thread of its own, every running job is
   marked *interrupted by Quit* (`JobRuntime.mark_running_interrupted_by_quit`).
   This happens before Uvicorn's connection drain (up to 3 s) and before any
   shutdown handler, so the reason is on disk however early the process ends.
2. Uvicorn stops accepting connections and drains the open ones.
3. The job runtime stops admitting work, requests cancellation of running jobs,
   and waits for their next checkpoint only as long as the budget allows.
4. The gmsh worker drops queued work and stops waiting for a running OCC call
   once the budget says so.
5. The BEMPP worker is closed and BEAT's connection to its host is released.
6. The instance lock is released and the logs are flushed.

If a non-daemon thread is still running when cleanup is done -- a gmsh or
preview worker inside native code, which interpreter exit would join forever --
the process ends at once instead of waiting out the budget. If the budget runs
out first, the watchdog flushes the logs (bounded to 1 s) and ends the process.
The launcher's tree kill after 8 s remains the outer guarantee; nothing here
replaces it.

## What a forced exit skips, and why that is safe

The forced exit is `os._exit` (on Windows `TerminateProcess` on the current
process, which runs no DLL detach code that a thread killed inside OCC or TBB
could deadlock). It skips `atexit` handlers and every Python finalizer. What it
skips is crash-safe by construction:

- **Job state** is SQLite; every commit is atomic.
- **Results are never half-published.** A job's results appear only through
  `complete_job`'s single transaction, the mesh artifact through one
  transaction (`store_mesh_artifact`), field traces through a staged directory
  renamed into place (`server/solver/field_traces_store.py`), and exports and
  CAD bundles through `publish_staging_directory`. A staged directory a kill
  leaves behind is hidden and never referenced.
- **Owned children** (the BEMPP worker, the isolated CAD child) were closed
  earlier in the budget, and each also leaves when its parent does: a process
  group on macOS and Linux, a kill-on-close Job Object on Windows.
- **The instance lock** and the temporary-session lock are OS locks, released
  with the process.

## What the next start does

- **Interrupted jobs.** A job that was running when the process ended reads
  *cancelled*, "Interrupted by Quit", if the stop had begun; only a job whose
  process ended with no stop at all (a crash, a power cut) reads "Simulation
  failed / Server restarted during execution". Interrupted jobs are not
  requeued, because the user quit on purpose. Jobs that were still queued are
  requeued.
- **Temporary files.** Each server process makes WG's own temporary
  directories -- mesh builds, STL exports, imported meshes -- inside one
  directory of its own, `wg2-run-<pid>-<random>` in the system temporary
  directory, and holds an OS lock inside it for as long as it lives. Each start
  removes every such directory whose lock is free -- its owner is gone, however
  it ended -- and never one whose owner is alive, whichever build or checkout
  that is. A clean exit removes its own, unless a thread was still busy when
  cleanup finished; then the next start does. The system temporary directory itself
  is not redirected: BEAT keeps the registry of its persistent host there, and
  the mesher its publish lock, and both must outlive the process. Directories
  with no session to belong to (`wg2-solver-mesh-*`, `wg2-imported-mesh-*`,
  `wg2-imported-viewport-*`, `wg2-field-plane-*`, `wg2-stl-mesh-*` directly in
  the temporary directory, from an earlier release or from the field-plane
  solver) have no owner to ask and are removed once nothing has changed them
  for a day.

## BEAT's persistent host on Windows

BEAT keeps its Julia worker in a *persistent host* process meant to outlive the
application, so the next launch adopts a warm runtime.

- **macOS and Linux:** the host starts its own session (`setsid`) and survives a
  Quit, as designed.
- **Windows, packaged app:** the host does **not** survive a Quit. It is started
  with `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` and without
  `CREATE_BREAKAWAY_FROM_JOB`, so it joins the status window's kill-on-close
  Job Object, and the status window closes that job at the end of every stop.
  The next launch pays one cold BEAT start. A server run directly, outside the
  status window, has no such job and its host survives.

This is recorded rather than changed. Surviving would need both a breakaway
flag in the BEAT package and `JOB_OBJECT_LIMIT_BREAKAWAY_OK` on the launcher's
job, and the cost is start-up time, not a wrong result.
`server/tests/test_beat_host_windows_quit.py` reproduces the mechanism on
Windows with the launcher's real job and the package's exact flags, and fails
if a BEAT pin changes those flags.

## How this is qualified

- **Every CI platform**, against a real `launch/serve.py`
  (`server/tests/test_bounded_server_shutdown.py`): a stop during a blocked mesh
  build and during a blocked preview build exits on its own before the
  launcher's kill; the owned BEMPP child is gone (macOS/Linux); an orphaned
  server and a second Ctrl+C exit on their own deadlines (macOS/Linux); a
  process ended mid-budget still leaves its job interrupted by Quit; the next
  start sweeps what the stopped build left and nothing a live process owns; a
  started server's add-in reconciliation stays in its sandbox.
- **The installed release candidate** (`.github/workflows/rc-build.yml`, all
  three platforms, `scripts/qualify_installed_quit.py`): the packaged server is
  stopped during a blocked mesh build, must exit on its own inside the
  launcher's grace, must leave no child process running and, after the next
  start, no stale temporary directory; the next start must show the job as
  interrupted by Quit; and the dense-solver memory ceiling is reported with the
  probe that measured physical memory.

### Still to check by hand on a packaged machine

The RC gate drives the stop file the status window writes, not the window
itself, and it runs no BEAT solve. Before a stable release, on each platform:

1. Start a solve large enough that meshing takes several seconds, and choose
   **Quit** in the status window while the job shows *Meshing*. The window
   should close within about 5 s, never showing a force-kill.
2. Start the application again. The job reads **Interrupted by Quit**, not
   "Simulation failed". The system temporary directory (`$TMPDIR`, `/tmp` or
   `%TEMP%`) holds no `wg2-run-*` directory other than the running server's.
3. macOS and Linux: during a build, kill the status window's process
   (`kill -9 <pid>`). The server exits by itself within about 5 s and no
   Waveguide Generator process remains.
4. Windows, with BEAT CPU prepared and one BEAT solve done (so its host is
   warm): Quit, and confirm in Task Manager that no `julia.exe` from the
   application remains. That matches the record above; the next BEAT solve
   pays a cold start.
5. Windows: the server log line `Dense-solver memory ceiling: ...` names half of
   the installed memory (Settings > System > About) and the probe
   `GlobalMemoryStatusEx`.
