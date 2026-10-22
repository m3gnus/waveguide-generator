import { useEffect, useId, useLayoutEffect, useRef, useState, type ChangeEvent, type CompositionEvent, type KeyboardEvent, type PointerEvent } from 'react';
import type { ExprNumber } from '../stores/design';
import { useHelpTip } from './HelpTip';

/** Compact numeric labels omit trailing zeroes. */
export function formatBound(value: number, precision: number): string {
  return Number(value.toFixed(precision)).toString();
}

interface NumberFieldProps {
  label: string;
  symbol?: string;
  description?: string;
  /** Saved designs store unset optional numbers as null; both mean "no value". */
  value?: number | null;
  unit?: string;
  min?: number;
  /** The minimum itself is not allowed: the lowest accepted value is just above it. */
  exclusiveMin?: boolean;
  max?: number;
  step?: number;
  precision?: number;
  /** Decimals shown; commits keep `precision`, so untouched and typed values are never coarsened. */
  displayPrecision?: number;
  modified?: boolean;
  disabled?: boolean;
  disabledReason?: string;
  invalidMessage?: string;
  validate?: (value: number) => string | undefined;
  formatRangeError?: (value: number) => string | undefined;
  onCommit: (value: number) => void;
  optional?: boolean;
  onClear?: () => void;
  expression?: ExprNumber;
  allowExpression?: boolean;
  onCommitExpression?: (value: ExprNumber) => void;
  onBeginDrag?: () => void;
  onEndDrag?: () => void;
  /** Semantic target consumed by ParamPanel's queued reveal request. */
  revealId?: string;
  /** Shown in an empty optional field instead of "Unset". */
  placeholder?: string;
}

export function NumberField({
  label,
  symbol,
  description,
  value: storedValue,
  unit,
  min = -Infinity,
  exclusiveMin = false,
  max = Infinity,
  step = 1,
  precision = 1,
  displayPrecision,
  modified = false,
  disabled = false,
  disabledReason,
  invalidMessage,
  validate,
  formatRangeError,
  onCommit,
  optional = false,
  onClear,
  expression,
  allowExpression = false,
  onCommitExpression,
  onBeginDrag,
  onEndDrag,
  revealId,
  placeholder,
}: NumberFieldProps) {
  const value = storedValue ?? undefined;
  const id = useId();
  const titled = symbol ? `${label} (${symbol})` : label;
  const help = useHelpTip({ title: titled, text: description, hint: 'Drag the label sideways to adjust.' });
  const shown = Math.min(displayPrecision ?? precision, precision);
  const displayed = expression?.raw ?? (value === undefined ? '' : value.toFixed(shown));
  // A field shown coarser than it stores edits its stored value, so typing the
  // rounded number on screen is a real change, not a no-op.
  const exact = expression?.raw ?? (value === undefined ? '' : String(Number(value.toFixed(precision))));
  const editBaseline = useRef<string | null>(null);
  const [draft, setDraft] = useState(displayed);
  const [editing, setEditing] = useState(false);
  const [expandedDraft, setExpandedDraft] = useState(false);
  // A typed value can pass validate() while its rounded form fails it; say why.
  const [roundedError, setRoundedError] = useState<string | undefined>();
  const editor = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const composingRef = useRef(false);
  const pendingSelection = useRef<{ start: number; end: number; direction: 'forward' | 'backward' | 'none' } | null>(null);
  const [dragDelta, setDragDelta] = useState<number | null>(null);
  const drag = useRef<{ x: number; value: number } | null>(null);
  const dragFrame = useRef<number | null>(null);
  const pendingDragValue = useRef<number | null>(null);
  const cancelBlur = useRef(false);
  const commitCallback = useRef(onCommit);
  const endDragCallback = useRef(onEndDrag);
  const currentValue = useRef(value);
  commitCallback.current = onCommit;
  endDragCallback.current = onEndDrag;
  currentValue.current = value;
  const parsed = Number(draft);
  /**
   * ATH designs carry formulas like `48.5 - 7*cos(2*p)^5 - 16*sin(p)^12` in
   * ordinary numeric fields. They cannot share a row with their label at rail
   * width, and right-aligning them hides the start of the formula, so a field
   * holding one switches to its own full-width line.
   */
  const rawExpression = expression?.raw?.trim() ?? '';
  const holdsExpression = Boolean(rawExpression) && !Number.isFinite(Number(rawExpression));
  const showEvaluatedExpression = holdsExpression && expression?.value != null;
  const isExpression = allowExpression && draft.trim() !== '' && !Number.isFinite(parsed);
  // While editing, the switch is driven by expandFor() so it can wait for an IME
  // composition to end; otherwise a non-numeric draft is a formula.
  const formulaLayout = holdsExpression || (editing ? expandedDraft : isExpression);
  const empty = draft.trim() === '';
  const outOfRange = !empty && !isExpression && Number.isFinite(parsed) && (parsed < min || (exclusiveMin && parsed === min) || parsed > max);
  /**
   * The bounds are the reason the field went red, so they are the message.
   *
   * Out-of-range input used to set `aria-invalid` and a red border and stop
   * there: no text, no `aria-describedby`, and the allowed range stated nowhere
   * in the interface. The user got a red box, no reason, and -- because an
   * invalid draft is reverted on blur -- their typing silently discarded.
   */
  const rangeMessage = outOfRange
    ? formatRangeError?.(parsed) ?? (exclusiveMin && Number.isFinite(min)
      ? `Must be greater than ${formatBound(min, precision)}${Number.isFinite(max) ? ` and at most ${formatBound(max, precision)}` : ''}${unit ? ` ${unit}` : ''}.`
      : Number.isFinite(min) && Number.isFinite(max)
      ? `Must be between ${formatBound(min, precision)} and ${formatBound(max, precision)}${unit ? ` ${unit}` : ''}.`
      : Number.isFinite(min)
        ? `Must be at least ${formatBound(min, precision)}${unit ? ` ${unit}` : ''}.`
        : `Must be at most ${formatBound(max, precision)}${unit ? ` ${unit}` : ''}.`)
    : undefined;
  const emptyMessage = empty && !optional ? 'This value is required.' : undefined;
  const numberMessage = !empty && !isExpression && !Number.isFinite(parsed) ? 'Enter a valid number.' : undefined;
  const validationMessage = draft.trim() && Number.isFinite(parsed) ? validate?.(parsed) : undefined;
  const draftMessage = rangeMessage ?? emptyMessage ?? numberMessage ?? validationMessage ?? roundedError;
  const draftInvalid = (empty && !optional) || (!empty && !isExpression && (!Number.isFinite(parsed) || outOfRange || Boolean(validationMessage)));
  const invalid = Boolean(invalidMessage) || draftInvalid || Boolean(roundedError);

  useEffect(() => {
    if (!editing && !drag.current) setDraft(displayed);
  }, [displayed, editing]);
  useEffect(() => setRoundedError(undefined), [displayed]);

  useLayoutEffect(() => {
    const element = editor.current;
    const selection = pendingSelection.current;
    if (element && selection) {
      pendingSelection.current = null;
      element.focus();
      element.setSelectionRange(selection.start, selection.end, selection.direction);
    }
    if (!(element instanceof HTMLTextAreaElement)) return;
    const grow = () => {
      element.style.height = 'auto';
      element.style.height = `${element.scrollHeight}px`;
    };
    grow();
    let width = element.getBoundingClientRect().width;
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(() => {
      const nextWidth = element.getBoundingClientRect().width;
      if (nextWidth !== width) { width = nextWidth; grow(); }
    });
    observer?.observe(element);
    window.addEventListener('resize', grow);
    return () => { observer?.disconnect(); window.removeEventListener('resize', grow); };
  }, [draft, formulaLayout]);

  const changeDraft = (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const element = event.currentTarget;
    const next = element.value.replace(/[\r\n]/g, '');
    const start = element.selectionStart ?? next.length;
    const end = element.selectionEnd ?? start;
    const selection = {
      start: element.value.slice(0, start).replace(/[\r\n]/g, '').length,
      end: element.value.slice(0, end).replace(/[\r\n]/g, '').length,
      direction: element.selectionDirection ?? 'none',
    };
    setRoundedError(undefined);
    if (next !== element.value) {
      element.value = next;
      element.setSelectionRange(selection.start, selection.end, selection.direction);
    }
    // An IME composition lives in this element; swapping it for the textarea
    // mid-composition would drop the text being composed. Expand on
    // compositionend instead.
    const composing = composingRef.current || (event.nativeEvent as InputEvent).isComposing === true;
    if (!composing) expandFor(next, selection);
    setDraft(next);
  };

  const expandFor = (next: string, selection: { start: number; end: number; direction: 'forward' | 'backward' | 'none' }) => {
    const becomesExpression = allowExpression && next.trim() !== '' && !Number.isFinite(Number(next));
    if (!formulaLayout && becomesExpression) pendingSelection.current = selection;
    if (becomesExpression) setExpandedDraft(true);
  };

  const onCompositionStart = () => { composingRef.current = true; };
  const onCompositionEnd = (event: CompositionEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    composingRef.current = false;
    const element = event.currentTarget;
    const next = element.value.replace(/[\r\n]/g, '');
    // Some browsers fire the final input after compositionend; take the value
    // here so the swapped-in editor starts from the composed text.
    setDraft(next);
    const start = element.selectionStart ?? next.length;
    expandFor(next, { start, end: element.selectionEnd ?? start, direction: element.selectionDirection ?? 'none' });
  };

  const commit = () => {
    setEditing(false);
    composingRef.current = false;
    setExpandedDraft(false);
    if (cancelBlur.current) {
      cancelBlur.current = false;
      editBaseline.current = null;
      setDraft(displayed);
      return;
    }
    const baseline = editBaseline.current ?? displayed;
    editBaseline.current = null;
    if (draft === baseline) { setDraft(displayed); return; }
    if (draftInvalid) {
      setDraft(displayed);
      return;
    }
    if (empty) {
      setDraft('');
      onClear?.();
      return;
    }
    if (isExpression) {
      const raw = draft.trim();
      setDraft(raw);
      onCommitExpression?.({ value: null, raw });
      return;
    }
    const rounded = Number(parsed.toFixed(precision));
    const roundedMessage = validate?.(rounded);
    if (roundedMessage) {
      setDraft(displayed);
      setRoundedError(roundedMessage);
      return;
    }
    setRoundedError(undefined);
    setDraft(rounded.toFixed(shown));
    if (rounded !== value) onCommit(rounded);
  };

  /**
   * Arrow keys step the value, which is what every other numeric control on
   * every platform does and what this one did not.
   *
   * The fine-adjust gesture here was a horizontal drag on the label -- mouse
   * only, no key handler -- so nudging a value by one step meant selecting the
   * whole field and retyping it. A formula field is exempt: there is nothing to
   * step, and stepping would overwrite the expression with a plain number.
   */
  // The lowest value an exclusive minimum can step or drag to.
  const floorAt = (candidate: number) => (exclusiveMin && candidate <= min ? min + 10 ** -precision : Math.max(min, candidate));
  const stepBy = (multiplier: number) => {
    if (formulaLayout) return false;
    const base = draft !== displayed && Number.isFinite(parsed) ? parsed : value ?? parsed;
    if (base === undefined || !Number.isFinite(base)) return false;
    const next = Math.min(max, floorAt(base + step * multiplier));
    const rounded = Number(next.toFixed(precision));
    if (validate?.(rounded)) return false;
    setDraft(rounded.toFixed(shown));
    if (rounded !== value) onCommit(rounded);
    return true;
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    // Enter and Escape confirm or cancel an IME candidate while composing; they
    // must not commit or revert the field.
    if (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      event.currentTarget.blur();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancelBlur.current = true;
      setDraft(displayed);
      setEditing(false);
      event.currentTarget.blur();
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      const direction = event.key === 'ArrowUp' ? 1 : -1;
      // Shift is the coarse gesture, matching the browser's own range inputs.
      if (stepBy(direction * (event.shiftKey ? 10 : 1))) event.preventDefault();
    } else if (event.key === 'PageUp' || event.key === 'PageDown') {
      if (stepBy((event.key === 'PageUp' ? 1 : -1) * 10)) event.preventDefault();
    }
  };

  const onLabelPointerDown = (event: PointerEvent<HTMLLabelElement>) => {
    if (disabled) return;
    // Scrubbing commits a plain number, which would silently overwrite the
    // formula this field holds. Expressions are edited by typing only.
    if (formulaLayout) return;
    if (value === undefined) return;
    if (event.button !== 0) return;
    event.preventDefault();
    drag.current = { x: event.clientX, value };
    event.currentTarget.setPointerCapture(event.pointerId);
    onBeginDrag?.();
  };

  const onLabelPointerMove = (event: PointerEvent<HTMLLabelElement>) => {
    if (!drag.current) return;
    const delta = event.clientX - drag.current.x;
    const next = Math.min(max, floorAt(drag.current.value + delta * step));
    const rounded = Number(next.toFixed(precision));
    if (validate?.(rounded)) return;
    setDraft(rounded.toFixed(shown));
    setDragDelta(rounded - drag.current.value);
    pendingDragValue.current = rounded;
    if (dragFrame.current === null) {
      dragFrame.current = requestAnimationFrame(() => {
        dragFrame.current = null;
        const pending = pendingDragValue.current;
        pendingDragValue.current = null;
        if (pending !== null && pending !== currentValue.current) commitCallback.current(pending);
      });
    }
  };

  const endDrag = () => {
    if (!drag.current) return;
    if (dragFrame.current !== null) cancelAnimationFrame(dragFrame.current);
    dragFrame.current = null;
    const pending = pendingDragValue.current;
    pendingDragValue.current = null;
    if (pending !== null && pending !== currentValue.current) commitCallback.current(pending);
    drag.current = null;
    setDragDelta(null);
    onEndDrag?.();
  };

  useEffect(() => () => {
    if (dragFrame.current !== null) cancelAnimationFrame(dragFrame.current);
    dragFrame.current = null;
    if (!drag.current) return;
    const pending = pendingDragValue.current;
    pendingDragValue.current = null;
    if (pending !== null && pending !== currentValue.current) commitCallback.current(pending);
    drag.current = null;
    endDragCallback.current?.();
  }, []);

  const Editor = formulaLayout ? 'textarea' : 'input';

  return (
    <div
      className={`field-row${modified ? ' modified' : ''}${disabled ? ' field-disabled' : ''}${formulaLayout ? ' expression-row' : ''}`}
      title={disabledReason}
      data-control-reveal-id={revealId}
    >
      <i className="modified-dot" />
      <label
        htmlFor={id}
        className="field-label"
        // The hover tip carries the full name and symbol as its heading, so a
        // label clamped against the value control is still readable. The
        // native `title` is only the fallback for a field with no description,
        // because a native tooltip would otherwise pop up over the hover tip.
        title={description ? undefined : `${titled} — drag horizontally to adjust`}
        {...help.triggerProps}
        onPointerDown={(event) => { help.triggerProps.onPointerDown?.(); onLabelPointerDown(event); }}
        onPointerMove={onLabelPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={endDrag}
      >
        <span className="field-name">{label}</span>{symbol && <span className="field-symbol">({symbol})</span>}{help.tip}
      </label>
      {formulaLayout && <span className="expr-badge" aria-hidden="true">fx</span>}
      {showEvaluatedExpression && <span className="expr-value" title="Evaluated expression value">= {Number(expression!.value!.toPrecision(8))}{unit}</span>}
      <div className={`number-control${editing ? ' editing' : ''}${invalid ? ' invalid' : ''}${formulaLayout ? ' expression' : ''}`}>
        {dragDelta !== null && (
          <span className="scrub-tip">drag <b>{dragDelta >= 0 ? '+' : ''}{dragDelta.toFixed(shown)}{unit}</b></span>
        )}
        <Editor
          ref={(element: HTMLInputElement | HTMLTextAreaElement | null) => { editor.current = element; }}
          {...(formulaLayout ? { rows: 1 } : {})}
          id={id}
          aria-invalid={invalid}
          aria-describedby={(draftMessage ?? invalidMessage) ? `${id}-error` : undefined}
          // Formulas hold text; plain numbers keep their spinbutton semantics.
          role={formulaLayout ? undefined : 'spinbutton'}
          aria-valuemin={formulaLayout || !Number.isFinite(min) ? undefined : min}
          aria-valuemax={formulaLayout || !Number.isFinite(max) ? undefined : max}
          aria-valuenow={formulaLayout || value === undefined ? undefined : value}
          aria-valuetext={formulaLayout || value === undefined ? undefined : `${value.toFixed(shown)}${unit ? ` ${unit}` : ''}`}
          inputMode={formulaLayout ? 'text' : 'decimal'}
          spellCheck={false}
          disabled={disabled}
          placeholder={optional ? placeholder ?? 'Unset' : undefined}
          title={formulaLayout ? draft : undefined}
          value={draft}
          onChange={changeDraft}
          onFocus={() => {
            setEditing(true);
            if (shown < precision && !holdsExpression && exact !== displayed) {
              editBaseline.current = exact;
              setDraft(exact);
            }
          }}
          onBlur={commit}
          onKeyDown={onKeyDown}
          onCompositionStart={onCompositionStart}
          onCompositionEnd={onCompositionEnd}
        />
        {unit && !formulaLayout && <span className="unit">{unit}</span>}
      </div>
      {/* Visible, not screen-reader-only. The message was announced and never
          shown, so a sighted user got a red border and no reason for it. */}
      {(draftMessage ?? invalidMessage) && <span id={`${id}-error`} className="field-error" role="alert">{draftMessage ?? invalidMessage}</span>}
    </div>
  );
}
