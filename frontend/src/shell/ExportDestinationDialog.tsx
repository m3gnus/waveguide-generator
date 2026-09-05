import { useCallback, useEffect, useRef, useState } from 'react';
import {
  chooseExportDestination,
  getExportDestination,
  type ExportCollision,
  type ExportDestination,
} from '../api/exportDestination';
import { Icon } from './icons';
import { focusableSelector, useModalDialogFocus } from './dialogFocus';
import {
  provideExportDestinationPrompt,
  provideExportReplacementPrompt,
  type ExportDestinationChoice,
  type ExportDestinationRequest,
} from './exportDestinationPrompt';

interface DestinationQuestion {
  kind: 'destination';
  request: ExportDestinationRequest;
  settle: (choice: ExportDestinationChoice | null) => void;
}

interface ReplaceQuestion {
  kind: 'replace';
  collision: ExportCollision;
  settle: (replace: boolean) => void;
}

type Question = DestinationQuestion | ReplaceQuestion;

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The first few, then a count: a folder of forty is not a list to read. */
const NAMED_COLLISIONS = 4;

function fileName(path: string): string {
  return path.split(/[\\/]/).at(-1) ?? path;
}

/**
 * The two questions a manual export asks, in one modal mounted once per window.
 *
 * *Where does this go?* comes first, before anything is built, so cancelling
 * costs nothing. The last folder an export was written to is the default, so
 * the common answer is the button that already has focus. *Choose folder…*
 * opens the platform's own picker on the machine running WG — the same adapter
 * the output folder uses.
 *
 * *Replace these?* comes second and only when it has to: the chosen folder may
 * hold files WG never wrote, and a basename collision there is not something to
 * report after the fact. It is asked once for the whole export and only about
 * files whose bytes would change.
 *
 * Cancelling either resolves to "no", and every caller treats that as "write
 * nothing". Neither touches the workspace: this folder is remembered on its
 * own, and only once an export has actually landed in it.
 */
export function ExportDestinationDialog() {
  const [pending, setPending] = useState<Question | null>(null);
  const [destination, setDestination] = useState<ExportDestination | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [typedPath, setTypedPath] = useState('');
  const generation = useRef(0);
  // The question is also held outside React state: answering it settles a
  // promise, and a state updater that settles one is not a pure function of the
  // previous state -- which is exactly what StrictMode calls twice.
  const asked = useRef<Question | null>(null);

  const clear = useCallback(() => {
    generation.current += 1;
    const current = asked.current;
    asked.current = null;
    setPending(null);
    setDestination(null);
    setBusy(false);
    setError(undefined);
    setTypedPath('');
    return current;
  }, []);

  const answerDestination = useCallback((choice: ExportDestinationChoice | null) => {
    const current = clear();
    if (current?.kind === 'destination') current.settle(choice);
    else if (current?.kind === 'replace') current.settle(false);
  }, [clear]);

  const answerReplace = useCallback((replace: boolean) => {
    const current = clear();
    if (current?.kind === 'replace') current.settle(replace);
    else if (current?.kind === 'destination') current.settle(null);
  }, [clear]);

  const cancel = useCallback(() => answerDestination(null), [answerDestination]);

  useEffect(() => {
    const ask = <T,>(question: (settle: (value: T) => void) => Question, refused: T) => (
      new Promise<T>((resolve) => {
        // One question at a time: a second export arriving while this one is
        // open is refused rather than replacing the question on screen.
        if (asked.current) { resolve(refused); return; }
        const entry = question(resolve);
        asked.current = entry;
        setPending(entry);
      })
    );
    provideExportDestinationPrompt((request) => ask<ExportDestinationChoice | null>(
      (settle) => ({ kind: 'destination', request, settle }), null,
    ));
    provideExportReplacementPrompt((collision) => ask<boolean>(
      (settle) => ({ kind: 'replace', collision, settle }), false,
    ));
    return () => {
      provideExportDestinationPrompt(null);
      provideExportReplacementPrompt(null);
      // An unmount with a question on screen must not leave the export that
      // asked it awaiting a promise nobody can settle. It is answered the way
      // the closed dialog means: nothing chosen, nothing replaced.
      const outstanding = asked.current;
      asked.current = null;
      if (outstanding?.kind === 'destination') outstanding.settle(null);
      if (outstanding?.kind === 'replace') outstanding.settle(false);
    };
  }, []);

  useEffect(() => {
    if (pending?.kind !== 'destination') return;
    const request = ++generation.current;
    setBusy(true);
    void getExportDestination().then(
      (value) => { if (request === generation.current) { setDestination(value); setBusy(false); } },
      (reason: unknown) => {
        if (request !== generation.current) return;
        setError(message(reason));
        setBusy(false);
      },
    );
  }, [pending]);

  const initialFocus = useCallback((node: HTMLDivElement) => (
    node.querySelector<HTMLElement>('[data-autofocus]:not([disabled])')
      ?? node.querySelector<HTMLElement>(focusableSelector)
  ), []);
  const dialog = useModalDialogFocus<HTMLDivElement>({
    open: pending !== null,
    onClose: () => (pending?.kind === 'replace' ? answerReplace(false) : cancel()),
    initialFocus,
  });

  if (!pending) return null;

  if (pending.kind === 'replace') {
    const { paths, directory } = pending.collision;
    const named = paths.slice(0, NAMED_COLLISIONS).map(fileName);
    const rest = paths.length - named.length;
    return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) answerReplace(false); }}>
      <div
        ref={dialog}
        className="update-dialog export-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-replace-title"
      >
        <header>
          <div>
            <h2 id="export-replace-title">Replace {paths.length} existing file{paths.length === 1 ? '' : 's'}?</h2>
            <p>This folder already holds {paths.length === 1 ? 'a file of that name' : 'files of those names'} with different contents.</p>
          </div>
          <button className="dialog-close" aria-label="Cancel this export" onClick={() => answerReplace(false)}><Icon name="close"/></button>
        </header>

        <div className="update-dialog-body">
          <p className="export-destination-path" title={directory}>{directory}</p>
          <ul className="export-replace-list">
            {named.map((name) => <li key={name}>{name}</li>)}
            {rest > 0 && <li className="export-replace-more">and {rest} more</li>}
          </ul>
          <p className="cad-settings-note">Nothing has been written yet. Cancelling leaves this folder exactly as it is.</p>
        </div>

        <footer>
          <span className="spacer"/>
          <button onClick={() => answerReplace(false)}>Cancel</button>
          <button className="primary" data-autofocus onClick={() => answerReplace(true)}>Replace</button>
        </footer>
      </div>
    </div>;
  }

  const choose = async (path?: string) => {
    const request = ++generation.current;
    setBusy(true);
    setError(undefined);
    try {
      const chosen = await chooseExportDestination(path);
      if (request !== generation.current) return;
      // Only a folder that was actually chosen replaces the one on screen. A
      // cancelled picker is not an answer, and must not drop a folder the user
      // picked a moment ago back to the remembered default.
      if (chosen.selected) { setDestination(chosen); setTypedPath(''); }
    } catch (reason) {
      if (request === generation.current) setError(message(reason));
    } finally {
      if (request === generation.current) setBusy(false);
    }
  };

  const confirm = () => {
    if (!destination?.token || !destination.path) return;
    answerDestination({ token: destination.token, directory: destination.path });
  };

  const folder = destination?.path ?? null;
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) cancel(); }}>
    <div
      ref={dialog}
      className="update-dialog export-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="export-destination-title"
      aria-busy={busy}
    >
      <header>
        <div>
          <h2 id="export-destination-title">{pending.request.title}</h2>
          <p>{pending.request.detail
            ? `${pending.request.detail} Choose where to write it.`
            : 'Choose where to write this export.'}</p>
        </div>
        <button className="dialog-close" aria-label="Cancel this export" onClick={cancel}><Icon name="close"/></button>
      </header>

      <div className="update-dialog-body">
        <p className="export-destination-label" id="export-destination-folder-label">Destination folder</p>
        <p
          className={`export-destination-path${folder ? '' : ' not-selected'}`}
          title={folder ?? undefined}
          aria-describedby="export-destination-folder-label"
        >{folder ?? (busy ? 'Reading the last folder used…' : 'No folder available')}</p>
        <p className="cad-settings-note">{destination?.remembered
          ? 'Where your last export went.'
          : 'WG’s output folder, until an export goes somewhere else.'}</p>
        <div className="settings-theme-options">
          <button disabled={busy} onClick={() => void choose()}><Icon name="folder"/>Choose folder…</button>
        </div>
        <details className="cad-folder-manual">
          <summary>Enter a folder path instead</summary>
          <p className="cad-settings-note">The folder picker opens on the machine running WG. Type a path when that machine has no desktop session to open it on — a tunnelled or headless install.</p>
          <label>Export folder path<input
            value={typedPath}
            onChange={(event) => setTypedPath(event.target.value)}
            placeholder="/path/to/folder"
          /></label>
          <button
            disabled={!typedPath.trim() || busy}
            onClick={() => void choose(typedPath.trim())}
          >Use this path</button>
        </details>
        {error && <p className="workspace-settings-error" role="status">{error}</p>}
      </div>

      <footer>
        <span className="spacer"/>
        <button onClick={cancel}>Cancel</button>
        <button
          className="primary"
          data-autofocus
          disabled={busy || !destination?.token}
          onClick={confirm}
        >Export here</button>
      </footer>
    </div>
  </div>;
}
