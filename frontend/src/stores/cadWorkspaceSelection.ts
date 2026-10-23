type Listener = () => void;

export interface CadWorkspaceSelectionState {
  /** Bumped every time the CAD workspace folder is chosen or cleared. */
  revision: number;
  /** Whether that choice left a folder selected, as the server reported it. */
  selected: boolean;
}

/**
 * Announces that the CAD workspace folder has been chosen.
 *
 * The CAD Link coordinator suspends its polls while the server says no folder
 * is configured, which leaves it needing to hear about the moment one is. It
 * cannot hear it from the server: nothing pushes, and the returns listing that
 * would eventually notice runs at its slowest idle rate precisely because
 * nothing is configured. Window `focus` covers the native picker -- choosing a
 * folder there takes focus away and gives it back -- but not the manual path
 * field beside it, which never leaves the window at all. A user who types a
 * path would have waited out the idle interval before WG looked again.
 *
 * The shared WG folder API announces successful choices and explicit
 * override removal, so Settings and Send wake the coordinator alike.
 */
class CadWorkspaceSelectionStore {
  private value: CadWorkspaceSelectionState = { revision: 0, selected: false };
  private listeners = new Set<Listener>();

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): CadWorkspaceSelectionState => this.value;

  /** Report what a selection attempt settled on. Fires even when `selected`
   * has not changed: reselecting a different folder is still news to anyone
   * holding a listing from the old one. */
  noteSelection = (selected: boolean): void => {
    this.value = { revision: this.value.revision + 1, selected };
    this.listeners.forEach((listener) => listener());
  };
}

export const cadWorkspaceSelection = new CadWorkspaceSelectionStore();
