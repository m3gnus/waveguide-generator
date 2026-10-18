import { useRef, useState } from 'react';

/** Single click selects the row; double click or F2 edits only its display name. */
export function SketchNameCell({ value, label, id, maxLength, disabled, onCommit }: {
  value: string; label: string; id: string; maxLength: number; disabled?: boolean; onCommit: (name: string) => void;
}) {
  const [draft, setDraft] = useState<string>();
  const cancel = useRef(false);
  if (draft === undefined) return <button type="button" title={`${id} · Double-click or F2 to rename`} aria-label={`${label}: ${value} (${id})`} disabled={disabled}
    onDoubleClick={() => setDraft(value)} onKeyDown={event => {
      if (event.key === 'F2') { event.preventDefault(); event.stopPropagation(); setDraft(value); }
    }}>{value}</button>;
  return <input aria-label={label} title={id} maxLength={maxLength} value={draft} autoFocus
    onFocus={event => event.currentTarget.select()} onChange={event => setDraft(event.target.value)}
    onBlur={() => {
      if (!cancel.current && draft.trim() !== value) onCommit(draft.trim());
      cancel.current = false; setDraft(undefined);
    }} onKeyDown={event => {
      if (event.nativeEvent.isComposing) return;
      if (event.key === 'Enter' || event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation(); cancel.current = event.key === 'Escape'; event.currentTarget.blur();
      }
    }}/>;
}
