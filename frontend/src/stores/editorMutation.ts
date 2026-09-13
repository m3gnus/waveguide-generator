/**
 * An editor-wide mutation token: a number that moves whenever the document on
 * screen changes in any way a file would record.
 *
 * It advances on every design mutation (edit, drag, undo, redo, family, load),
 * on every change to the design's name, and on every change to a solve setting
 * the design file owns. Nothing else. An asynchronous replacement of the
 * document -- opening a CAD-linked design, above all -- captures it when the
 * replacement is decided and refuses to apply once it has moved: whatever the
 * user did in between is newer than the decision, and it stays.
 *
 * The geometry revision cannot answer this on its own: a rename or a solve
 * setting does not move it. Deliberately a bare counter with no imports, so the
 * stores that advance it cannot form an import cycle through it.
 */
let editorMutations = 0;

/** The token now. Capture it when a replacement is decided. */
export function currentEditorMutation(): number {
  return editorMutations;
}

/** Record that the document on screen changed. */
export function advanceEditorMutation(): void {
  editorMutations += 1;
}
