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

Capability requests have a ten-second client timeout. Pending qualification, refresh
failure and an interrupted backend connection have distinct notices. The server's
bounded OpenCL qualification and fallback behavior are unchanged. Neither a stale
pending result nor a failed HTTP refresh establishes an OpenCL defect.

The ownership protocol assumes the app's existing single backend owner for a data
directory. Its reservation metadata uses the existing jobs metadata schema; no new
database table or migration is required.
