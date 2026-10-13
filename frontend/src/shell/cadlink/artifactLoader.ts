import { deadlineFetch } from '../../api/deadlineFetch';
import { currentDocumentLoad, isCurrentDocumentLoad } from '../../stores/design';
import type { CadReturnIngestRecord } from '../../api/cadlink';
import { jobsSocket } from '../../api/jobsSocket';
import { displaySymmetry } from '../../api/domainDecision';
import { useCadReturnStore } from '../../stores/cadReturn';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { createImportedMeshScene } from '../../viewport/importedMesh';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { parseMSH } from '../../viewport/mshParser';

interface FetchedMesh {
  ok: boolean;
  status: number;
  text: string;
}

/** Mesh artifact fetches that have not settled yet, keyed by URL.
 *
 * Two independent triggers ask for the CAD scene the moment an ingestion
 * lands — the coordinator, and the viewport's own workspace effect — and a
 * display artifact is tens of megabytes. Sharing the in-flight request means
 * one download instead of two; the entry is dropped as soon as it settles, so
 * this coalesces concurrent callers without ever serving a stale body. */
const inFlightMeshRequests = new Map<string, Promise<FetchedMesh>>();

async function fetchMeshOnce(url: string, fetcher: typeof fetch): Promise<FetchedMesh> {
  const existing = inFlightMeshRequests.get(url);
  if (existing) return existing;
  const request = (async (): Promise<FetchedMesh> => {
    const response = await deadlineFetch(fetcher, url);
    // 202 is "still being built", not a mesh; reading its body would only
    // hand `parseMSH` an empty string to reject.
    const text = response.ok && response.status !== 202 ? await response.text() : '';
    return { ok: response.ok, status: response.status, text };
  })();
  inFlightMeshRequests.set(url, request);
  try {
    return await request;
  } finally {
    inFlightMeshRequests.delete(url);
  }
}

const viewportMeshUrl = (ingestId: string): string =>
  `/api/cadlink/ingest/${encodeURIComponent(ingestId)}/viewport-mesh`;

function cadDisplayScene(record: CadReturnIngestRecord, name: string, meshText: string) {
  // The display tessellation is of the model as it arrived: whole for a model
  // WG cut itself, the kept piece for one cut in CAD -- which is mirrored for
  // display, so a reduced solve always shows the whole speaker.
  const { solvedPlanes, cadCutPlanes } = displaySymmetry(record);
  return createImportedMeshScene(
    name,
    parseMSH(meshText),
    'cad',
    record.ingest_id,
    solvedPlanes,
    {
      fullDomain: true,
      cadCutPlanes,
      solvedTriangleCount: record.mesh?.stats.triangle_count,
      artifactToken: record.viewport_mesh?.content_sha256 ?? `${record.ingest_id}:viewport`,
    },
  );
}

/** Slow recovery checks if the websocket notification was missed during a
 * disconnect. Normal pickup is event-driven. */
const DISPLAY_UPGRADE_FALLBACK_DELAYS_MS = [10_000, 20_000, 30_000, 60_000];

const displayUpgradesInFlight = new Set<string>();

/** Swap the smooth display tessellation in behind an already-visible solve mesh.
 *
 * Deliberately not tied to the caller's intent generation. CAD Link
 * re-publishes an equal record on every Fusion poll and each of those bumps the
 * generation, so a wait that spans several seconds would be cancelled by
 * routine polling and never finish. What actually matters is whether the slot
 * and CAD Link still hold this ingestion, so that is what is checked: a newer
 * selection's viewport load is never cancelled by it. The swap also only
 * takes the viewport if the CAD model view still owns it, leaving a user who
 * switched to the solve-mesh view where they are. */
/** Another model owns CAD Link. Editing or preparing the held record again
 * leaves its immutable artifact displayable until a replacement is applied. */
function anotherSelectionInFlight(ingestId: string): boolean {
  const current = useCadReturnStore.getState();
  return current.ingestRecord
    ? current.ingestRecord.ingest_id !== ingestId
    : current.selectedBundle !== null;
}

function upgradeToDisplayMesh(
  record: CadReturnIngestRecord,
  name: string,
  fetcher: typeof fetch,
): void {
  const ingestId = record.ingest_id;
  const documentLoad = currentDocumentLoad();
  if (displayUpgradesInFlight.has(ingestId)) return;
  displayUpgradesInFlight.add(ingestId);
  void (async () => {
    let signalled = false;
    let wake: (() => void) | null = null;
    const unsubscribe = jobsSocket.subscribeCadViewportReady((readyIngestId) => {
      if (readyIngestId !== ingestId) return;
      signalled = true;
      wake?.();
    });
    try {
      for (const [attempt, delay] of DISPLAY_UPGRADE_FALLBACK_DELAYS_MS.entries()) {
        // The first read immediately after subscribing closes the race where
        // completion landed between the endpoint's 202 and this subscription.
        if (attempt === 0) {
          signalled = false;
        } else if (signalled) {
          signalled = false;
        } else {
          await new Promise<void>((resolve) => {
            const timer = window.setTimeout(() => {
              wake = null;
              resolve();
            }, delay);
            wake = () => {
              window.clearTimeout(timer);
              resolve();
            };
          });
          wake = null;
          signalled = false;
        }
        if (importedMeshStore.getSnapshot().cad?.ingestId !== ingestId
          || !isCurrentDocumentLoad(documentLoad)) return;
        let result: FetchedMesh;
        try {
          result = await fetchMeshOnce(viewportMeshUrl(ingestId), fetcher);
        } catch {
          continue;
        }
        if (result.status === 202) continue;
        if (!result.ok) return;
        if (importedMeshStore.getSnapshot().cad?.ingestId !== ingestId
          || !isCurrentDocumentLoad(documentLoad)) return;
        // The slot keeps this ingestion until a newer selection's own scene
        // lands; taking the intent meanwhile would cancel that load.
        if (anotherSelectionInFlight(ingestId)) return;
        try {
          importedMeshStore.replaceCadArtifact(
            ingestId,
            cadDisplayScene(record, name, result.text),
            workspaceModeStore.getSnapshot().mode === 'cad'
              && importedMeshStore.getSnapshot().showing === 'cad',
          );
        } catch {
          // The solve mesh already on screen is the same geometry.
        }
        return;
      }
    } finally {
      unsubscribe();
      displayUpgradesInFlight.delete(ingestId);
    }
  })();
}

/** Prefer the independently tessellated full CAD display artifact. Older
 * records and advisory display failures fall back to the exact solver mesh.
 *
 * A record may also publish before its display tessellation exists: that
 * tessellation is two thirds of a cold ingestion's wall clock, so waiting for
 * it would leave the viewport empty for several seconds after WG already knows
 * the geometry. The solve mesh is complete at that point and is the same
 * model, so it is shown immediately and quietly replaced when the smoother
 * artifact lands. */
export async function showIngestedMeshInViewport(
  record: CadReturnIngestRecord,
  name: string,
  onNotice?: (notice: string) => void,
  fetcher: typeof fetch = fetch,
  generation = importedMeshStore.beginIntent(),
): Promise<void> {
  const selection = useCadReturnStore.getState().captureSelectionIntent();
  const documentLoad = currentDocumentLoad();
  const current = () => importedMeshStore.isCurrentGeneration(generation)
    && useCadReturnStore.getState().isCurrentSelectionIntent(selection)
    && !anotherSelectionInFlight(record.ingest_id)
    && isCurrentDocumentLoad(documentLoad);
  if (!current()) return;
  const ingestId = record.ingest_id;
  const available = importedMeshStore.getSnapshot().cad;
  if (available?.ingestId === ingestId) {
    if (workspaceModeStore.getSnapshot().mode === 'cad') importedMeshStore.showCad(generation);
    return;
  }
  let displayPending = false;
  try {
    const result = await fetchMeshOnce(viewportMeshUrl(ingestId), fetcher);
    if (!current()) return;
    if (result.status === 202) {
      displayPending = true;
    } else if (result.ok) {
      importedMeshStore.setCad(
        cadDisplayScene(record, name, result.text),
        generation,
        workspaceModeStore.getSnapshot().mode === 'cad',
      );
      return;
    } else if (result.status === 409) {
      onNotice?.('The independent CAD viewport artifact failed verification. Showing the exact solver mesh instead.');
    }
  } catch {
    // The independent display artifact is advisory; try the solver artifact.
  }
  if (!current()) return;
  try {
    const result = await fetchMeshOnce(
      `/api/cadlink/ingest/${encodeURIComponent(ingestId)}/mesh`,
      fetcher,
    );
    if (result.ok && current()) {
      importedMeshStore.setCad(createImportedMeshScene(
        name,
        parseMSH(result.text),
        'cad',
        ingestId,
        // Every plane the solver mirrors -- WG's own cuts and CAD cuts alike.
        displaySymmetry(record).solvedPlanes,
        {
          solvedTriangleCount: record.mesh?.stats.triangle_count,
          artifactToken: record.mesh_content_sha256 ?? `${ingestId}:solver`,
        },
      ), generation, workspaceModeStore.getSnapshot().mode === 'cad');
      if (displayPending) upgradeToDisplayMesh(record, name, fetcher);
      return;
    }
  } catch {
    // Neither artifact could be displayed; fall through to drop the old one.
  }
  // A scene left over from an earlier ingestion would keep claiming to be the
  // geometry on screen — misleading on its own, and enough to refuse every
  // later solve at the viewport-mismatch gate. An empty slot is honest.
  if (current()
    && importedMeshStore.getSnapshot().cad !== null
    && importedMeshStore.getSnapshot().cad?.ingestId !== ingestId) {
    importedMeshStore.clear('cad');
  }
}

/** Load the exact ingested solve mesh a CAD return will be solved on.
 *
 * The counterpart to `showIngestedMeshInViewport`: that one prefers the
 * independently tessellated display artifact, this one asks for the solver
 * artifact itself, so the mesh view shows the triangles the solver assembles
 * rather than a smooth stand-in. The record is immutable, so the fetched
 * scene is cached in its own slot and re-serves every later activation.
 */
export async function showIngestedSolverMeshInViewport(
  record: CadReturnIngestRecord,
  name: string,
  fetcher: typeof fetch = fetch,
  generation = importedMeshStore.beginIntent(),
): Promise<string | null> {
  const selection = useCadReturnStore.getState().captureSelectionIntent();
  const documentLoad = currentDocumentLoad();
  const current = () => importedMeshStore.isCurrentGeneration(generation)
    && useCadReturnStore.getState().isCurrentSelectionIntent(selection)
    && !anotherSelectionInFlight(record.ingest_id)
    && isCurrentDocumentLoad(documentLoad);
  if (!current()) return null;
  const ingestId = record.ingest_id;
  const available = importedMeshStore.getSnapshot().cadSolver;
  if (available?.ingestId === ingestId) {
    if (workspaceModeStore.getSnapshot().mode === 'cad') importedMeshStore.showCadSolver(generation);
    return null;
  }
  let response: Response;
  try {
    response = await deadlineFetch(fetcher, `/api/cadlink/ingest/${encodeURIComponent(ingestId)}/mesh`);
  } catch (error) {
    return current() ? (error instanceof Error ? error.message : String(error)) : null;
  }
  if (!current()) return null;
  if (!response.ok) {
    return response.status === 409
      ? 'The ingested solve mesh failed verification and cannot be displayed.'
      : `Could not read the ingested solve mesh (${response.status}).`;
  }
  try {
    const meshText = await response.text();
    if (!current()) return null;
    importedMeshStore.setCadSolver(createImportedMeshScene(
      name,
      parseMSH(meshText),
      'cad',
      ingestId,
      displaySymmetry(record).solvedPlanes,
      {
        solvedTriangleCount: record.mesh?.stats.triangle_count,
        artifactToken: record.mesh_content_sha256 ?? `${ingestId}:solver`,
      },
    ), generation, importedMeshStore.getSnapshot().showing === 'cadSolver');
    return null;
  } catch (error) {
    return current() ? (error instanceof Error ? error.message : String(error)) : null;
  }
}
