import { useDesignStore, type DesignDocument } from '../stores/design';
import { commitProfileEdit, currentEditOrigin, ProfileEditRefusal, type EditBuilder } from '../design/customProfileEdit';
import { PlainController, type PlainControllerOptions } from './workspace/plainController';
import { profileCommandBuilder, rebaseProfileDocument, type ProfileCommand, type ProfilePresentation } from './adapters/profile';

interface Options extends PlainControllerOptions<DesignDocument, DesignDocument, ProfileCommand> {
  presentation(): ProfilePresentation;
  propose(label: string, builder: EditBuilder): void;
  drafting(active: boolean): void;
  blocked(): boolean;
}
/** PlainController owns the live drag and W4 history. The profile owner owns
 * the acceptance preview; cancelling live frames before proposing preserves
 * exactly one project Undo entry on Accept, including metadata. */
export class ProfileController extends PlainController<DesignDocument, DesignDocument, ProfileCommand> {
  constructor(private profile: Options) { super(profile); }
  override get current() {
    return rebaseProfileDocument(super.current, useDesignStore.getState().design);
  }
  override command(commands: ProfileCommand | ProfileCommand[] | ((state: DesignDocument) => ProfileCommand | ProfileCommand[])) {
    return this.enqueue(async () => {
      if (this.profile.blocked()) throw new ProfileEditRefusal('Accept or cancel the current profile preview first.');
      const resolved = typeof commands === 'function' ? commands(this.current) : commands;
      const list = Array.isArray(resolved) ? resolved : [resolved];
      if (this.current.formula === 'LOOKUP') {
        const before = this.current;
        let after = before;
        for (const command of list) {
          const result = this.profile.adapter.apply(after, command);
          if ('error' in result) throw new ProfileEditRefusal(result.error);
          after = result;
        }
        commitProfileEdit(currentEditOrigin(), before, after);
        return this.current;
      }
      if (list.some(command => command.kind === 'insert') && (this.profile.presentation().stale || !this.profile.presentation().curves)) {
        throw new ProfileEditRefusal('Wait for the current authored curve before inserting a point.');
      }
      this.profile.propose(list.length === 1 ? `${list[0].kind === 'insert' ? `Insert ${list[0].mode}` : list[0].kind === 'join' ? `Set ${list[0].join}` : list[0].kind === 'angle' ? 'Set angle' : list[0].kind === 'delete' ? 'Delete point' : 'Move point'}` : 'Edit profile points',
        profileCommandBuilder(list, this.profile.presentation().curves));
      return this.current;
    });
  }
  override async beginDrag(ids: string[]) {
    if (this.profile.blocked()) throw new ProfileEditRefusal('Accept or cancel the current profile preview first.');
    await super.beginDrag(ids); this.profile.drafting(true);
  }
  override async endDrag(cancel = false) {
    if (this.current.formula === 'LOOKUP') {
      await super.endDrag(cancel); this.profile.drafting(false); return;
    }
    // Queue after the last frame. External Undo/Open may already have cancelled it.
    const candidate = await this.enqueue(async () => this.dragging && this.dragStart && JSON.stringify(this.current) !== JSON.stringify(this.dragStart)
      ? { before: this.dragStart, after: this.current } : undefined);
    await super.endDrag(true);
    this.profile.drafting(false);
    if (cancel || !candidate || JSON.stringify(this.current) !== JSON.stringify(rebaseProfileDocument(candidate.before, this.current))) return;
    this.profile.propose('Move point', before => ({ after: rebaseProfileDocument(candidate.after, before), notes: ['Point moved in design mm. Pinned handles keep their vectors; automatic spans refit.'] }));
  }
}
