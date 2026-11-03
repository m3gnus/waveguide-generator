import { useEffect, useRef, useState, type FocusEvent, type KeyboardEvent } from 'react';
import { useDocumentStore } from '../stores/document';

/**
 * Editing the design's name, for every field that offers it.
 *
 * Geometry's Design name field and the File menu's name both rename through
 * this, so they cannot disagree about when a typed name takes effect: Enter
 * and leaving the field commit it, Escape puts the name back. The draft
 * follows the stored name whenever the field is not being edited, so a rename
 * made in one place shows in the other at once.
 */
export function useDesignNameDraft(name: string, onDone?: () => void) {
  const setDesignName = useDocumentStore((state) => state.setDesignName);
  const [draft, setDraft] = useState(name);
  const [editing, setEditing] = useState(false);
  const cancelled = useRef(false);
  useEffect(() => { if (!editing) setDraft(name); }, [name, editing]);
  return {
    value: draft,
    onChange: (event: { target: { value: string } }) => setDraft(event.target.value),
    onFocus: () => { cancelled.current = false; setEditing(true); },
    onBlur: (event: FocusEvent<HTMLInputElement>) => {
      if (cancelled.current) setDraft(name);
      else setDesignName(event.target.value);
      cancelled.current = false;
      setEditing(false);
      onDone?.();
    },
    onKeyDown: (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === 'Enter') event.currentTarget.blur();
      else if (event.key === 'Escape') {
        // The cancel is the field's; it must not also close what holds it.
        event.stopPropagation();
        cancelled.current = true;
        event.currentTarget.blur();
      }
    },
  };
}
