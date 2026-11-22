# Automatic export ownership and recovery

Automatic selected-format exports and run archives share one backend-owned lane.
The frontend queues work before generation, including overlapping jobs updates;
other windows must claim the same lane before fetching results or generating files.
Manual exports keep their existing behavior.

A claim rereads the job and skips completed or blocked formats. It durably reserves
pending formats before generation, with a random token and a 60-second renewable
lease. Tagged HTTP operations retain ownership until their handlers settle, even
when the client cancels. An expired token cannot start another request, and a new
owner cannot replace a handler still doing native work. Gmsh retains its existing
single worker thread.

Destination conflicts and uncertain publication outcomes are blocked, with a reason,
in job metadata. This includes losing a successful response body: files may already
have been written. Reopening does not regenerate blocked formats or interrupted
reservations. Retry is explicit from the run card, after choosing a different
Workspace folder or resolving the existing files. Retry never changes the automatic
`merge_identical` policy. Completed formats and recorded filenames are preserved.
Failures known to occur before publication remain failed rather than conflicted.
An interrupted archive uses the reserved `run_archive` metadata entry; successful
archive completion records that entry and `archived_at` together.

The two never write to the same names. The archive owns the run folder,
`<design>/<run>`, and stamps its files with the run's completion time so a retry is
byte identical; automatic export writes to `<design>/<run>/exports`. Automatic
export publishes one `write-export` request per format, each with that format's
files and any shared sidecar, so a destination conflict or an uncertain publication
blocks only that format; the others are written and recorded complete. The archive
probes with `confirm`: a member whose name an earlier version's export already
holds, with other bytes, is written under `archive/` instead, and `run.json` names
each result and mesh file the archive wrote where it landed (`artifacts.files`, with
`archive/<name>` for one that moved, beside the mesh and impedance entries; `run.json`
itself and the run's CAD document are not listed). Because `run.json` is also written
with `confirm`, re-archiving a run whose `run.json` an older version wrote places the
new record at `archive/run.json` and leaves the older one in place. A release after
`finish`, or by any token that no longer owns the lane, is an idempotent success.

### Files the server already holds

The mesh, the pressure bases and the radiation matrix do not travel through the
browser.

1. The archive reads `GET /api/jobs/{id}/archive-snapshot`. One store transaction
   copies the run's inputs; the server keeps those three kinds of file under a `hold`
   id and the reply lists them by name and byte count.
2. Its first `write-export` request names every held member, after the files built in
   the browser, with `member_source`: one entry per `relative_path`, either `"upload"`
   (the next file part) or `{"hold": …, "member": …}`. The server writes the very bytes
   the snapshot copied, all of them or none.
3. The archive then releases the hold (`DELETE /api/jobs/{id}/archive-holds/{hold}`).

Limits and failures:

- An unreleased hold expires on a timer after 30 minutes; beyond 16 holds or 1 GiB the
  oldest go first, and one snapshot over 1 GiB is refused (422).
- One request may name each held member, and the stored mesh, only once, and copy at
  most 1 GiB of held files (422 otherwise).
- A write naming a hold that is gone writes nothing and answers `409`,
  `code: "server_member_unavailable"`. The archive takes a fresh snapshot and tries once
  more; after a second loss it fails like any archive and is tried again automatically.
- Server members are resolved before the run's folder is claimed, so a refusal leaves
  no trace.

The automatic mesh save names the run's retained mesh (`{"member": "mesh"}`) the same
way. It downloads and sends a mesh only when the server no longer holds one (an empty
stored mesh counts as none), as when it must rebuild it.

### Request forms

`write-export` takes only `multipart/form-data`. The 256 MB cap per request applies to
the bytes actually uploaded; held members are not uploaded and do not count, so an
archive's held members always go in its one request.

Retired: the JSON/base64 `write-export` body (answers `415`) and the base64 `inline`
archive snapshot (`members=inline` answers `422`, asking an outdated window to reload
or update WG). `members=held` is the default and only form.

Capability requests have a ten-second client timeout. Pending qualification, refresh
failure and an interrupted backend connection have distinct notices. The server's
bounded OpenCL qualification and fallback behavior are unchanged. Neither a stale
pending result nor a failed HTTP refresh establishes an OpenCL defect.

The ownership protocol assumes the app's existing single backend owner for a data
directory. Its reservation metadata uses the existing jobs metadata schema; no new
database table or migration is required.
