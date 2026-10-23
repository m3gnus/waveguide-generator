import { createPortal } from 'react-dom';
import { useModalDialogFocus } from '../shell/dialogFocus';

export type SharedDesignChoice = 'keep' | 'new' | null;
export interface SharedDesignQuestion {
  name: string;
  settle: (choice: SharedDesignChoice) => void;
}

export function SharedDesignIdDialog({ question }: { question: SharedDesignQuestion | null }) {
  const dialog = useModalDialogFocus<HTMLDivElement>({
    open: question !== null,
    onClose: () => question?.settle(null),
  });
  if (!question) return null;
  return createPortal(<div className="modal-backdrop">
    <div ref={dialog} className="update-dialog" role="dialog" aria-modal="true" aria-labelledby="shared-design-title">
      <header><h2 id="shared-design-title">Shared design id</h2></header>
      <div className="update-dialog-body">
        <p>This looks like a copy of {question.name}. Keep it with the original's runs, or start it as a new design?</p>
      </div>
      <footer>
        <button onClick={() => question.settle(null)}>Cancel</button>
        <button onClick={() => question.settle('keep')}>Keep with original</button>
        <button onClick={() => question.settle('new')}>New design</button>
      </footer>
    </div>
  </div>, document.body);
}
