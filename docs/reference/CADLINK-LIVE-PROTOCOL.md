# CAD Link live protocol

Status: protocol version 1, **implemented in WG** (`server/cadlink/live/`): endpoint
discovery, registration, sessions and tokens, origin and validation rules, heartbeat over
HTTP, Fusion-bound requests and WG-bound deliveries (sections 2-8). No released WGLink
speaks this protocol yet; until one does, the add-in uses the v3 files.

The live protocol changes how CAD Link operations travel between WG and its Fusion add-in,
not what they mean. The operation contract, digests and states are those of
[CAD operations](../architecture/CAD-OPERATIONS.md).

## 1. Transports and precedence

- **The v3 files stay unchanged and always on.** WG writes `wg-capabilities.json` and every
  Fusion-bound request file and collects v3 solve files; the add-in writes its file
  heartbeat and, when it cannot deliver live, v3 solve files. They are the offline and
  cold-start transport.
- **Live is additive.** With no usable endpoint, or a refusal by protocol, the add-in uses
  the v3 files.
- **One operation, either path.** An operation is identified by its operation ID and
  digest. Transport fields -- tokens, proofs, nonces, the installation header, session IDs,
  attempt and claim IDs, `requestedAt`, file names -- are never digest inputs.
- **Precedence:** heartbeat -- a fresh live heartbeat of a current session, else a fresh
  file heartbeat; Fusion-bound requests -- first claim wins; WG-bound deliveries -- first
  accept wins, the other recovers.

## 2. Endpoint discovery

At startup WG writes `<data dir>/ipc/wglink/wg-endpoint.json`:

```json
{"schemaVersion": 1, "producer": "waveguide-generator", "instanceId": "<32 hex, per start>",
 "pid": 12345, "baseUrl": "http://127.0.0.1:3100", "liveProtocol": 1,
 "startedAt": "2026-09-17T10:00:00Z", "registrationSecret": "<per start>"}
```

- **Written only by a serving start.** The launcher passes the loopback port it reserved to
  `create_app(advertised_port=...)`; without a port (tests, embedders, the OpenAPI
  generator) there is no file. It uses the same atomic writer as the capability file: a
  private temporary file (mode 0600 on POSIX), fsync, replace. On Windows the file relies on
  the per-user profile ACL of the data directory.
- **The secret** is created per start and kept only in WG's memory and this file. It never
  crosses the socket (section 3), is never logged, never returned by any route, and is
  redacted by `scripts/cadlink_evidence.py`.
- **Lifetime.** Replaced by every start; removed at clean shutdown only while it still names
  that start's `instanceId`.
- **Capability.** `wg-capabilities.json` carries the integer `"liveProtocol": 1`, read with
  the same rules as the other capability values (an integer of at least 1, never a boolean).
- **Client go-live rule.** The add-in goes live only if all of these hold, and otherwise uses
  the files:
  1. `wg-capabilities.json` parses, has `schemaVersion` 1, and advertises an integer
     `liveProtocol` of at least 1 (so a WG without live support that left an old endpoint
     file behind stays in file mode);
  2. `wg-endpoint.json` parses, has `schemaVersion` 1, the expected `producer`,
     `liveProtocol` 1, a `baseUrl` of the form `http://127.0.0.1:<1-65535>`, and every field
     present with the right type;
  3. `GET /api/cadlink/live/endpoint` answers 200 with the same schema and the file's
     `instanceId`; any other status, a network error or a parse failure means stale;
  4. the registration's server proof verifies (section 3);
  5. on POSIX, `wg-endpoint.json` is owned by the current user with no group or other
     permission bits, and the `ipc/wglink` folder is not writable by group or other, checked
     without following symlinks (a symlink means stale). A `WG2_DATA_DIR` on a shared or
     multi-user path is not supported for live use and stays in file mode by this rule.

  A stale endpoint is checked again when either file's modification time or size changes,
  or every 30 s.

## 3. Registration and mutual proof

All live routes are under `/api/cadlink/live`. Every live request except
`GET /endpoint` carries the header `X-WGLink-Installation: <installationId>`.

`GET /endpoint` answers `{"schemaVersion": 1, "producer": "waveguide-generator",
"instanceId": "...", "liveProtocol": 1, "deliveryVersion": 3}`.

`POST /sessions` carries no `Authorization` header:

```json
{"cadApplication": "fusion360", "liveProtocol": 1, "deliveryVersion": 3,
 "installationId": "<same as the header>", "adapterSessionId": "<heartbeat sessionId>",
 "adapterVersion": "<string>",
 "clientNonce": "<unpadded base64url of 32 random bytes, never reused>",
 "clientProof": "<unpadded base64url HMAC-SHA256, below>",
 "loadedIdentity": {"source": "managed|devSync|unmanaged", "sourceCommit": "<40 hex|null>",
   "addinVersion": "<string|null>", "managedBy": "<string|null>",
   "waveguideGeneratorRoot": "<string|null>", "loadedAt": "<ISO-8601>"}}
```

Every field is required; the nullable ones are sent as `null`. `adapterSessionId` and
`adapterVersion` are non-empty strings of at most 128 characters. Unknown fields are refused
with `400 invalid_request`, so a client that sends fields this WG does not know is not live
with it and stays in file mode.

**Mutual proof.**

```
clientProof = HMAC-SHA256(secret, "wglink-client\n" + clientNonce + "\n" + instanceId + "\n" + installationId)
serverProof = HMAC-SHA256(secret, "wglink-server\n" + clientNonce + "\n" + instanceId + "\n" + installationId)
```

- The HMAC key is the UTF-8 bytes of `registrationSecret` exactly as the endpoint file holds
  it (no decoding, no trimming). The message is the UTF-8 bytes of the labelled string;
  `\n` is a single LF.
- `clientNonce`, `clientProof` and `serverProof` are unpadded base64url; each must decode to
  exactly 32 bytes, in its canonical spelling. WG decodes the proof (a malformed one is
  `401 registration_proof_invalid`) and compares the bytes with `hmac.compare_digest`.
- WG refuses a nonce this start has already accepted (`401 registration_proof_invalid`). A
  nonce is recorded only after its proof verifies, so a bad proof cannot burn a legitimate
  client's nonce.
- The add-in verifies `serverProof` **before** it uses the token or sends any other request.
  The secret never crosses the socket, so a process that binds the port after WG exits
  learns nothing it can replay and cannot produce `serverProof`.

**Who registers.** Only the add-in instance that owns the active IPC lease registers, and it
ends its session (`DELETE /sessions/current`) when it loses the lease or stops.

**installationId.** A UUID the add-in creates once in
`<data dir>/ipc/wglink/.wglink-installation.json`. Grammar `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`.

**loadedIdentity** is what the add-in captured once when it loaded: the managed install
marker, else the developer-sync marker, else `unmanaged`.

**Checks, in order, after the Origin rule (section 5):**

1. installation header missing or malformed → `400 installation_mismatch`;
2. body validation → `400 invalid_request`;
3. header and body `installationId` differ → `400 installation_mismatch`;
4. `liveProtocol` other than 1 → `409 protocol_unsupported` (the add-in uses the files);
5. `deliveryVersion` below 3 → `409 addin_outdated`;
6. nonce or proof invalid, or the nonce reused → `401 registration_proof_invalid`.

**Pin mismatch is reported, not refused.** WG adds `matchesPin` to the identity: `true` only
when `sourceCommit` is not null and equals the WGLink commit this WG pins.

**201 response:**

| Field | Value |
| --- | --- |
| `liveSessionId` | Session ID, stable across refreshes |
| `sessionToken` | Bearer token |
| `expiresAt` | Registration + 15 min (UTC, `...Z`) |
| `refreshAfter` | Registration + 10 min |
| `idleTimeoutSeconds` | 60 |
| `heartbeatIntervalSeconds` | 4 |
| `longPollSeconds` | 25 |
| `instanceId` | This start |
| `serverProof` | Above |
| `liveProtocol` | 1 |
| `capabilities` | Exactly the content of `wg-capabilities.json` |
| `loadedIdentity` | The registered identity with `matchesPin` |

## 4. Sessions and tokens

- **One registry per data directory, per start.** WG keeps sessions, used nonces and the
  loaded identity in memory, in a registry tagged with the start's `instanceId`. Startup
  replaces any registry for that data directory; shutdown removes it only if it is still its
  own. Two WGs on different data directories share nothing, and a stopped WG has no live
  state. Nothing is persisted: a restart forgets every session and nonce, and the old secret
  no longer verifies.
- **Token use.** `Authorization: Bearer <sessionToken>`, together with an
  `X-WGLink-Installation` header equal to the session's `installationId`.
- **Token storage.** WG keeps only `sha256(token)`, finds the session by that digest, and
  confirms it with `hmac.compare_digest`.
- **One session per installation.** A new registration supersedes the installation's
  previous session.
- **Validity.** A token authenticates while its session is current, not expired
  (15 minutes after registration or its last refresh), and not idle: any authenticated
  request is activity, and more than 60 s without one ends the session.
- **Refresh.** `POST /sessions/refresh` answers 200
  `{liveSessionId, sessionToken, expiresAt, refreshAfter}`: a new token for the same session
  with a new 15-minute lifetime. The replaced token stays valid for 30 s (never past its own
  expiry) for other requests, but cannot refresh (`401 token_expired`); a later refresh ends
  that grace at once.
- **Clocks.** Lifetime, grace and idle are measured on WG's monotonic clock, so a change of
  the system clock neither ends nor extends a session; `expiresAt` and `refreshAfter` are the
  corresponding wall-clock times, for information.
- **End.** `DELETE /sessions/current` answers 204 and ends the session at once.
- **401 recovery.** Apply the go-live rule again and register again. Operations, claims and
  outbox items are bound to the installation and attempt generation, never to a session.
- **Never** is a token, proof, nonce or the secret in a digest, bundle, CAD attribute, query
  string, log line, database or file, or in any response other than `POST /sessions` and
  `POST /sessions/refresh` (the secret in none).

## 5. Loopback, origin, validation and ordering

- The global Host/Origin guard is unchanged: the Host must be loopback on the bound port, and
  a non-local `Origin` (including `null`) is refused there with 403.
- **Live routes refuse any `Origin` header**, including the application's own origin, with
  `403 origin_not_allowed`, `GET /endpoint` included. Browsers send `Origin` on cross-origin
  and non-GET requests and WG's own UI never calls these routes; a client that sends no
  `Origin`, the installation header and a valid token (or a valid registration proof) is the
  add-in.
- **Validation errors** on live routes answer `400 invalid_request` with the location and
  type of each error only, never the input values; an unknown key is reported at its parent
  object, since its name is input too. Every other route keeps FastAPI's 422.
- **Body limit.** A live request body over 64 KiB answers `413 request_too_large` (a route
  may set its own limit, as the heartbeat's 256 KiB does).
- **Order of checks:** Host guard → body size limit → Origin refusal → installation header →
  authentication → body validation → restart latch (routes that start work check it
  themselves, answering `409 update_restart_pending`) → handler. The checks before the body
  run before the body is read, so an unauthenticated request answers 401 whatever its body,
  even one that is not JSON. Live routes are not in the application-wide restart latch.
- A WG that has not finished starting, or is stopping, answers live routes with
  `503 store_busy` (retryable).
- **Client:** only `baseUrl`, proxies bypassed, no redirects followed.

## 6. Heartbeat over HTTP

`POST /api/cadlink/live/heartbeat` with a session token, carrying exactly the object the
add-in writes to `.fusion-status.json`; `204` with no body when recorded. Body limit
256 KiB, the file reader's own limit (`413 request_too_large`).

- **Validation is the file heartbeat's**, one implementation for both transports
  (`fusion_status.heartbeat_problem`): `schemaVersion` 1, `cadApplication` `"fusion360"`,
  an `updatedAt` with a time zone no more than 20 s old and no more than 1 min in the
  future. Unknown fields are kept, as the file reader keeps them.
- **Checks, in order, after the session checks (section 5):**
  1. a body that is not a JSON object, or an unusable `schemaVersion`, `cadApplication` or
     `updatedAt` → `400 invalid_request` (field and type only, never a value);
  2. `deliveryVersion` missing, not an integer, or below 3 → `409 addin_outdated`;
  3. `updatedAt` already outside the freshness window → `409 heartbeat_stale`;
  4. `sessionId` other than the session's registered `adapterSessionId` →
     `409 session_mismatch`.
  Nothing is recorded on a refusal. A recorded heartbeat replaces the previous one, unless
  that one has a later `updatedAt`: a request that arrives late is ignored and still answers
  `204`, so an older heartbeat never replaces a newer one.
- **Kept in memory only**, in this data directory's live registry, bound to the session that
  posted it. Ending the session, a registration that supersedes it, and its expiry drop the
  heartbeat at once; a WG restart forgets it. Every authenticated request, a heartbeat
  included, is activity for the 60 s idle timeout.
- **The add-in keeps writing `.fusion-status.json` while live**, so a WG that restarts, or
  a reader without a live session, still has the file.
- **Selection** (`fusion_status.select_heartbeat`), used by every heartbeat reader in the WG
  server:
  the live heartbeat if the session that posted it is current in this data directory's
  registry and the heartbeat is fresh; else a fresh file heartbeat; else none. The same
  freshness window applies to both. A WG that has not started or has stopped has no
  registry, so only the file is read. The evidence collector (`scripts/cadlink_evidence.py`)
  still collects only the file heartbeat -- the live one is kept in this in-memory registry
  and never persisted, so a read-only, out-of-process, offline collector cannot see it; its
  `manifest.json` carries a `notes` entry saying so and pointing at this section's
  `heartbeatTransport` field as the place that does know.
- **One selection per status answer.** `POST /api/cadlink/fusion-status` reads the clock
  once, selects once, and gives that same heartbeat and instant to operation settlement and
  to the status it reports, so the two cannot disagree when the file changes, or the
  heartbeat ages out, in between. Settlement uses the selection only
  at `deliveryVersion` 3 or later, whichever transport it came by; a `recovery_required`
  mark recorded from a live heartbeat is durable exactly as one from the file.
- **The status reports `heartbeatTransport`**: `"live"`, `"file"`, or `null` when no
  heartbeat was selected. For the same heartbeat object the status is otherwise identical
  on either transport.
- **The status reports `observationFreshness`**, for the selected link, from the two
  revision tokens each link carries beside its measured state (`geometryRevisionToken`,
  `measuredRevisionToken`; the add-in's `WGLink/README.md` owns their semantics). The
  heartbeat inspects no geometry, so `localBodyState`, `bodyFingerprintHash`,
  `documentSignatureHash`, `documentBodyCount` and `sourceStateHash` are a cached
  measurement, and only the tokens say which revision it was taken at:
  `"current"` (tokens equal), `"stale"` (unequal, or a null `geometryRevisionToken`),
  `"none"` (empty `documentSignatureHash` with `localBodyState: "unknown"` -- read from
  the hashes, never from the tokens), `"unknown"` (an add-in that publishes neither
  token), or `null` when no link is selected. Anything but `"current"` or `"unknown"`
  makes `documentChangeDetectable` false and forbids `state: "current"`: a cached hash
  that equals the returned one is not a comparison against the document as it stands.
  Evidence of a difference is not withdrawn, only the absence of one. Not because a
  difference cannot stop being one -- an undo back to the returned state leaves an
  observation reporting a difference the document no longer has -- but because
  over-reporting a change is the conservative direction, and the add-in re-measures
  inline before any guarded mutation.

## 7. Fusion-bound requests

WG still records every Fusion-bound operation (`request_return`, `insert_link`,
`update_link`) and publishes its v3 request file ([CAD operations](../architecture/CAD-OPERATIONS.md),
"WG-produced Fusion requests"). The add-in takes a request either by renaming that file
(the file transport, unchanged) or through the routes below. **The visible file is the
mutual-exclusion token**: whoever renames it first has the request, whichever transport
it uses. Every route here is authenticated (section 5); none is held by the restart latch,
because a claim starts no work in WG and a file claim cannot be held either.

Operations, claims, progress and outcomes are bound to the **installation** and the
**attempt generation**, never to a session: after a token refresh or a new registration
of the same installation the attempt continues.

### 7.1 Long poll

`GET /requests?waitSeconds=<0-25>` (default 25; anything else `400 invalid_request`) →
`200 {"requests": [{"operationId", "kind", "attemptGeneration", "request"}]}`.

- **Offered:** a Fusion-bound operation in `received` whose request file is visible under
  its own name and is a valid v3 request of that operation; a return request only when
  its `sessionId` is the session's registered `adapterSessionId`. `request` is the file's
  JSON exactly. Ordered by `deliverySequence`, then operation ID. An offer changes
  nothing.
- **Waiting.** With nothing to offer WG waits up to `waitSeconds` and answers as soon as
  a request is published, else `{"requests": []}` at the bound. A missed wake-up costs
  at most one rescan: WG reads the store again every 2 s while it waits. The wait holds
  no database transaction and no lock.
- **Ends early** with `401` (`session_superseded`, `token_expired`, `session_unknown`)
  when the session stops authenticating while it waits, or `503 store_busy` when WG
  stops. A wait is not activity for the idle timeout; the heartbeat is.

### 7.2 Claim

`POST /requests/{operationId}/claim` `{"attemptGeneration": g, "claimId": "<id>"}`.
`claimId` matches `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` (a UUID fits); the add-in journals it
before it sends the claim. Under WG's publisher lock:

0. An operation that does not exist or is not Fusion-bound → `404 operation_unknown`.
   **A claim already recorded answers first:** the same `claimId`, installation and
   generation (`g+1` recorded for `g`) → `200` with exactly the original answer, taken
   from the store, even after the file is gone, after the operation finished and after a
   WG restart; any other claim → `409 already_claimed`.
1. A return request for another `adapterSessionId` → `409 session_mismatch`, nothing
   renamed. WG renames the visible file to `.<operationId>.json.live-<claimId>.tmp`. Gone
   → `409 claimed_elsewhere` (the add-in, or another claim, took it); another rename
   failure (a Windows sharing violation) → `503 store_busy`.
2. WG claims the operation in the store, conditional on generation `g`, state `received`
   and no recorded claim: generation `g+1`, `processing`, stage `adapter-received`, and
   the claim recorded (installation, live session and claim IDs, the generation, the time
   and the request itself; never a token). If that claim is refused, WG **deletes the
   file of an operation that finished or was dismissed** (terminal, or
   `cancel_requested`) -- it is never resurrected -- and puts any other back for the
   file transport, then answers `409 stale_attempt`. A busy store restores the file and answers `503 store_busy`.
3. WG deletes the hidden file and answers `200 {"attemptGeneration": g+1, "request": <the v3
   request>}`. A deletion that fails is left to startup recovery.

**Interrupted claims.** Startup recovery handles the hidden name like any staged request
file. Interrupted between steps 1 and 2, the operation is still `received`: the visible
file is restored and offered again at `g` (and the file transport can take it). If the
operation was cancelled meanwhile, the file is deleted. Interrupted between steps 2 and
3, the operation is `processing`: the hidden file is deleted, no request is visible, and
the claim replays by its `claimId`. While a live claim holds a file hidden, heartbeat
settlement treats the request as still being delivered and does not claim it.

### 7.3 Progress

`POST /requests/{operationId}/progress` `{"attemptGeneration": g, "stage":
"queuedForFusion"|"executing"}` → `200 {"operation": <operation summary>}`.

- Stored as the operation's stage: `adapter-received` (set by the claim) →
  `queued-for-fusion` → `executing`, **one step at a time**. The same stage again →
  `200 {"operation", "alreadyRecorded": true}` with no write; a skipped or earlier stage →
  `409 stage_out_of_order`.
- Fenced: the operation must have been claimed live by this installation
  (else `409 claimed_elsewhere`, which is also the answer for a file-claimed operation),
  be at generation `g` and still running (`processing`, or `cancel_requested`), else
  `409 stale_attempt`.

### 7.4 Completion

`POST /requests/{operationId}/complete` `{"attemptGeneration": g, "outcome": O,
"message"?: "<1-2000 chars>", "evidence"?: {"operationId", "exportId"}}` →
`200 {"operation": <operation summary>}`, fenced as progress.

The mapping is the heartbeat's (`fusion_outcomes.adapter_outcome`, one implementation
for both transports):

| `outcome` | `request_return` | `insert_link` / `update_link` |
| --- | --- | --- |
| `applied` | `accepted` | reconciled `accepted`, only with `evidence` equal to the operation's own ID and export ID |
| `reconciled` | `400 invalid_request` | reconciled `accepted`, evidence as above |
| `refused` | `rejected` / `adapter_refused` | same |
| `superseded` | `cancelled` / `superseded` | same |
| `discarded` | `cancelled` / `adapter_not_started` | same |
| `recoveryRequired` | `400 invalid_request` | `recovery_required` |
| `failed` | `rejected` / `adapter_failed` | `recovery_required` if the stage was `executing`, else `rejected` / `adapter_failed` |

- Missing or mismatched evidence for a mutation, or evidence on a return request →
  `400 invalid_request`; nothing is recorded. `failed` has no heartbeat equivalent (the
  heartbeat leaves such an operation `processing`); over HTTP it is recorded as above.
- **Repeats.** The same outcome again → `200 {"operation", "alreadyRecorded": true}`;
  a different one → `409 outcome_conflict`. This holds whichever transport recorded the
  first: a heartbeat that later reports the same outcome settles nothing again, and a
  live completion of an outcome the heartbeat already recorded is `alreadyRecorded`.
- **Dismissal stands.** From `cancel_requested` the outcome recorded is `cancelled`, and
  any completion of that attempt afterwards is `alreadyRecorded`. From
  `recovery_required` only a reconciled `accepted` settles; the same outcome again is
  `alreadyRecorded`, anything else `409 outcome_conflict`.
- An obsolete attempt -- one WG took over, or whose operation finished at a later
  generation -- gets `409 stale_attempt` and changes nothing.

Every recorded claim, stage and outcome is published to WG's UI as a `cadOperation`
event after it is committed; the event carries the operation summary, never the claim.

## 8. WG-bound deliveries and the outbox

`POST /api/cadlink/live/deliveries` with a session token:

```json
{"operationId": "<the item's id>", "kind": "prepare_and_solve|receive_snapshot",
 "returnId": "<solve only>", "bundlePath": "wgreturn/<name>.wgreturn",
 "manifestSha256": "sha256:<hex>", "requestedAt": "<ISO-8601>"}
```

- **Fields.** `operationId`, `bundlePath`, `manifestSha256` and `requestedAt` are non-empty
  strings. `returnId` is a string, required for `prepare_and_solve` and refused for
  `receive_snapshot`. Any other field -- a token, an attempt or claim ID -- is
  `400 invalid_request`. The digest is the operation's
  ([CAD operations](../architecture/CAD-OPERATIONS.md), "Identity"): `requestedAt`, the
  token, the headers and the transport never change it, so the same item delivered as a
  v3 solve file and over HTTP is one operation.
- **By reference.** The bundle stays in the WGLink folder; WG retains it into its own
  storage before it answers 200.
- **Checks, in order, after the session checks and body validation (section 5):**
  1. an approved update restart → `409 update_restart_pending` (retryable);
  2. WG's request consumer is not running (`WG2_CAD_DELIVERY=0`) →
     `409 delivery_consumer_disabled` (retryable): nothing would ever prepare what it
     accepted, so it accepts nothing;
  3. no WGLink folder selected → `409 wglink_folder_not_selected` (retryable);
  4. the operation is held (below), accepted or recovered, and its snapshot retained:
     - the ID already names a different request or kind, finished or not →
       `409 operation_conflict`; the stored operation is untouched;
     - the store is locked or busy → `503 store_busy`, `Retry-After: 1` (any other store
       error is a 500);
     - the return cannot be read now (not in the folder yet, a file another process holds,
       a drive not mounted) → `503 snapshot_not_readable`, `Retry-After: 1`;
     - otherwise `200 {"result": "created"|"recovered", "operation": <operation summary>}`:
       the snapshot is retained, or can never be retained as named (malformed, changed,
       outside the folder), which preparation then refuses. A redelivered finished
       operation is `recovered` with its outcome.
  Nothing is accepted on a refusal before step 3.
- **The 30 s bound.** The first `503 snapshot_not_readable` for an operation sets a
  deadline 30 s later. A retry before it keeps that deadline; a retry at or after it is
  answered 200 and ends the hold: a solve then waits for its return
  (`preparation_failed`), as a v3 file does after its passes, and a snapshot is left to
  settlement. WG remembers the deadline for five minutes past it; a retry later than that
  starts a new 30 s bound.
- **In-flight hold.** WG records a hold on the operation before it accepts it. A 200 ends
  it. A `503 snapshot_not_readable` keeps it until the deadline; after that the operation
  is no longer held, even before the retry. A 409, a busy store or any error leaves the
  hold as it was before that delivery, so a conflicting delivery under the same ID never
  ends another delivery's hold. The delivery pass and snapshot settlement list operations
  first and read the holds after, and skip what is held, so neither starts or settles an
  operation whose delivery is still being accepted or retained. Holds are in memory; a WG
  restart forgets them.
- **`receive_snapshot`** is retained, then recorded `accepted`; one never retainable as
  named is `rejected` with `snapshot_invalid`; one not readable now is neither claimed nor
  given an outcome, and is answered 503. No ingest, mesh, solve or import intent. It is
  never accepted before WG holds its bytes, and the route never answers 200 for one still
  unsettled except at the 30 s bound.
  **Unsettled snapshots** -- `received` (WG stopped between acceptance and outcome, or the
  return was not readable within the bound) or `processing` (claimed, then WG stopped or
  the store refused the outcome) -- are settled the same way at startup, at the start of
  each delivery pass with a WGLink folder, and by a retry of the delivery. Every one is
  reached however many stay unreadable; held ones are skipped. Settling claims the
  operation again, so a late outcome from the earlier claim is refused.
  **Bounded wait:** a snapshot whose bundle has stayed unreadable for 24 hours, measured
  from the first unreadable attempt and stored with the operation (a restart does not
  reset it), is `rejected` with `snapshot_unavailable`. Sending it again is a new
  operation ID.
- **Client retry.** `503` and the retryable `409`s are retried over HTTP with backoff
  (honour `Retry-After`) until WG answers 200 -- for `snapshot_not_readable` it does
  within 30 s. A 401 is answered by registering again (section 4) and retrying the same
  item. Only a network failure (refused, reset, timeout) or a 401 that one new
  registration does not cure makes a solve fall back to the v3 solve file with the same
  operation ID; the digest is the same, so whichever arrives first is accepted and the
  other recovers it.
- **The add-in's outbox** keeps each item under a fixed operation ID, created after the
  bundle and before any send. A solve with no healthy session is written as the v3 solve
  file (cold start); a `receive_snapshot` waits in
  `<data dir>/ipc/wglink/.wglink-outbox/<operationId>.json`, which WG never reads, and is
  deleted on 200 or `409 operation_conflict`. Mixed file and live delivery of one operation
  is accepted once, in any order or at once.

## 9. Exactly-once recovery

| Event | Rule |
| --- | --- |
| WG restarts | New registry, instance and secret; old tokens and the old secret are refused; the client applies the go-live rule again and registers. Operations persist and are offered, recovered or settled again. |
| Token refresh | New token only, 30 s grace; no operation or digest changes. |
| A response is lost | Claim replay by `claimId` (from the store, even after the file is gone or a WG restart), the same progress stage (`alreadyRecorded`), the same outcome (`alreadyRecorded`), delivery recovery by digest. |
| WG stops between hiding a request and claiming it | Startup recovery restores the visible request if the operation is still `received`, and deletes it if the operation was cancelled meanwhile. |
| WG stops between the claim and deleting the file | Startup recovery deletes the hidden file; the operation stays processing, and the claim replays. |
| Fusion restarts mid-request | The claim journal is settled read-only; the operation is never re-run. |
| Another process binds the port after WG exits | It cannot prove the secret; the client stays in file mode. |
| WG stops between accepting a delivery and answering it | A solve stays `received` and the next delivery pass prepares it; a `receive_snapshot` left `received` or `processing` is settled at startup (a `processing` one taken over by a new claim). The add-in's retry is `recovered` by digest. |
| The store refuses a snapshot's outcome after its claim | The retry answers `503 store_busy`; the next pass or retry takes it over and settles it. |
| Outbox item made while WG was down | Delivered at the next session (a solve also by its v3 file), accepted once. |
| Missed wake-up | The waiting long poll rescans every 2 s; otherwise the next poll, or the operations listing. |

## 10. Error codes

Every refusal is an error envelope with `stage: "cadlink-live"`.

| HTTP | Code | Retry | Status |
| --- | --- | --- | --- |
| 400 | `invalid_request` (no input echo), `installation_mismatch` (registration header) | no | implemented |
| 401 | `registration_proof_invalid` (bad proof or reused nonce) | read the endpoint file again | implemented |
| 401 | `session_unknown`, `token_expired`, `session_superseded`, `installation_mismatch` | register again | implemented |
| 403 | `origin_not_allowed` (plus the global Host/Origin 403) | no | implemented |
| 409 | `addin_outdated`, `protocol_unsupported` | no (file mode) | implemented |
| 409 | `session_mismatch`, `heartbeat_stale` (heartbeat) | no | implemented |
| 413 | `request_too_large` (64 KiB; the heartbeat 256 KiB) | no | implemented |
| 503 | `store_busy` | yes | implemented (WG not started or stopping, or its store busy) |
| 409 | `operation_conflict` (deliveries) | no | implemented |
| 409 | `wglink_folder_not_selected`, `update_restart_pending`, `delivery_consumer_disabled` (deliveries) | yes | implemented |
| 503 | `snapshot_not_readable`, `store_busy` (deliveries; `Retry-After: 1`) | yes | implemented |
| — | outcome `rejected` / `snapshot_unavailable`: a `receive_snapshot` unreadable for 24 h (a 200 answer, not an HTTP error) | send again as a new operation | implemented |
| 404 | `operation_unknown` (Fusion-bound requests) | no | implemented |
| 409 | `session_mismatch` (a return request for another adapter session) | no | implemented |
| 409 | `claimed_elsewhere`, `already_claimed`, `stale_attempt`, `stage_out_of_order`, `outcome_conflict` | no | implemented |
| 503 | `store_busy` (Fusion-bound requests: the store is busy, a request file could not be renamed, or WG stopped during a long poll; `Retry-After: 1`) | yes | implemented |

## 11. Security boundary

- Only the live routes are authenticated. The rest of WG's local API stays reachable by any
  local process, as before; the live protocol does not change that.
- The trust anchor is the data directory's file permissions: whoever can read
  `wg-endpoint.json` can register. That is why the client refuses a group- or
  other-accessible file or folder on POSIX, and why a shared data directory is unsupported
  for live use.
- Loopback only: WG binds `127.0.0.1`, the Host guard refuses other authorities, and the
  client bypasses proxies.

## Platform evidence still owed

Unit tests do not prove: the Windows ACL on the endpoint file; a live claim's rename of a request
file another process holds open on Windows; loopback and proxy bypass from
inside Fusion's Python; custom-event dispatch under long polling; reconnection across a real
WG restart and a token refresh; an outbox item delivered once on macOS and Windows.
