import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import type { ExprNumber } from '../stores/design';
import { useHelpTip } from './HelpTip';

/** Bounds read as limits, not as readings: trailing zeros here are noise. */
function formatBound(value: number, precision: number): string {
  return Number(value.toFixed(precision)).toString();
}

interface NumberFieldProps {
  label: string;
  symbol?: string;
  description?: string;
  value?: number;
  unit?: string;
  min?: number;
  max?: number;
  step?: number;
  precision?: number;
  modified?: boolean;
  disabled?: boolean;
  disabledReason?: string;
  invalidMessage?: string;
  validate?: (value: number) => string | undefined;
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
}

export function NumberField({
  label,
  symbol,
  description,
  value,
  unit,
  min = -Infinity,
  max = Infinity,
  step = 1,
  precision = 1,
  modified = false,
  disabled = false,
  disabledReason,
  invalidMessage,
  validate,
  onCommit,
  optional = false,
  onClear,
  expression,
  allowExpression = false,
  onCommitExpression,
  onBeginDrag,
  onEndDrag,
  revealId,
}: NumberFieldProps) {
  const id = useId();
  const titled = symbol ? `${label} (${symbol})` : label;
  const help = useHelpTip({ title: titled, text: description, hint: 'Drag the label sideways to adjust.' });
  const displayed = expression?.raw ?? (value === undefined ? '' : value.toFixed(precision));
  const [draft, setDraft] = useState(displayed);
  const [editing, setEditing] = useState(false);
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
  const empty = draft.trim() === '';
  const outOfRange = !empty && !isExpression && Number.isFinite(parsed) && (parsed < min || parsed > max);
  /**
   * The bounds are the reason the field went red, so they are the message.
   *
   * Out-of-range input used to set `aria-invalid` and a red border and stop
   * there: no text, no `aria-describedby`, and the allowed range stated nowhere
   * in the interface. The user got a red box, no reason, and -- because an
   * invalid draft is reverted on blur -- their typing silently discarded.
   */
  const rangeMessage = outOfRange
    ? Number.isFinite(min) && Number.isFinite(max)
      ? `Must be between ${formatBound(min, precision)} and ${formatBound(max, precision)}${unit ? ` ${unit}` : ''}.`
      : Number.isFinite(min)
        ? `Must be at least ${formatBound(min, precision)}${unit ? ` ${unit}` : ''}.`
        : `Must be at most ${formatBound(max, precision)}${unit ? ` ${unit}` : ''}.`
    : undefined;
  const emptyMessage = empty && !optional ? 'This value is required.' : undefined;
  const validationMessage = draft.trim() && Number.isFinite(parsed) ? validate?.(parsed) : undefined;
  const draftMessage = rangeMessage ?? emptyMessage ?? validationMessage;
  const draftInvalid = (empty && !optional) || (!empty && !isExpression && (!Number.isFinite(parsed) || outOfRange || Boolean(validationMessage)));
  const invalid = Boolean(invalidMessage) || draftInvalid;

  useEffect(() => {
    if (!editing && !drag.current) setDraft(displayed);
  }, [displayed, editing]);

  const commit = () => {
    setEditing(false);
    if (cancelBlur.current) {
      cancelBlur.current = false;
      setDraft(displayed);
      return;
    }
    if (draft === displayed) return;
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
    setDraft(rounded.toFixed(precision));
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
  const stepBy = (multiplier: number) => {
    if (holdsExpression) return false;
    const base = Number.isFinite(parsed) ? parsed : value;
    if (base === undefined || !Number.isFinite(base)) return false;
    const next = Math.min(max, Math.max(min, base + step * multiplier));
    const rounded = Number(next.toFixed(precision));
    setDraft(rounded.toFixed(precision));
    if (rounded !== value) onCommit(rounded);
    return true;
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.currentTarget.blur();
    } else if (event.key === 'Escape') {
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
    if (holdsExpression) return;
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
    const next = Math.min(max, Math.max(min, drag.current.value + delta * step));
    const rounded = Number(next.toFixed(precision));
    setDraft(rounded.toFixed(precision));
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

  return (
    <div
      className={`field-row${modified ? ' modified' : ''}${disabled ? ' field-disabled' : ''}${holdsExpression ? ' expression-row' : ''}`}
      title={disabledReason}
      data-control-reveal-id={revealId}
    >
      <i className="modified-dot" />
      <label
        htmlFor={id}
        className="field-label"
        // The hover tip carries the full name and symbol as its heading, so a
        // label ellipsized against the value control is still readable. The
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
      {holdsExpression && <span className="expr-badge" aria-hidden="true">fx</span>}
      {showEvaluatedExpression && <span className="expr-value" title="Evaluated expression value">= {Number(expression!.value!.toPrecision(8))}{unit}</span>}
      {/* The formula marker follows `holdsExpression`, not the mere presence of a
          raw string. An imported design carries a raw for every field it names,
          most of them plain numbers ("10", "0.992"), and keying the marker off
          `expression.raw` widened those fields to formula width -- so a loaded
          design showed a rail of boxes at two different sizes with no rule a
          reader could see behind which was which. */}
      <div className={`number-control${editing ? ' editing' : ''}${invalid ? ' invalid' : ''}${holdsExpression ? ' expression' : ''}`}>
        {dragDelta !== null && (
          <span className="scrub-tip">drag <b>{dragDelta >= 0 ? '+' : ''}{dragDelta.toFixed(precision)}{unit}</b></span>
        )}
        <input
          id={id}
          aria-invalid={invalid}
          aria-describedby={(draftMessage ?? invalidMessage) ? `${id}-error` : undefined}
          /* The fill track encodes where this value sits in its range, which is
             information a sighted user gets and a screen-reader user did not.
             spinbutton carries the same fact, and it is also what makes the
             Arrow-key stepping above discoverable rather than a secret. A
             formula field is not a spinbutton; it holds text. */
          role={holdsExpression ? undefined : 'spinbutton'}
          aria-valuemin={holdsExpression || !Number.isFinite(min) ? undefined : min}
          aria-valuemax={holdsExpression || !Number.isFinite(max) ? undefined : max}
          aria-valuenow={holdsExpression || value === undefined ? undefined : value}
          aria-valuetext={holdsExpression || value === undefined ? undefined : `${value.toFixed(precision)}${unit ? ` ${unit}` : ''}`}
          inputMode={holdsExpression ? 'text' : 'decimal'}
          spellCheck={false}
          disabled={disabled}
          placeholder={optional ? 'Unset' : undefined}
          title={holdsExpression ? draft : undefined}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onFocus={() => setEditing(true)}
          onBlur={commit}
          onKeyDown={onKeyDown}
        />
        {unit && !holdsExpression && <span className="unit">{unit}</span>}
        {/* The scrub track reads as a value within a range, which a formula has not got. */}
        {!holdsExpression && value !== undefined && <i className="number-track" style={{ '--fill': `${Math.max(5, Math.min(100, ((value - min) / (max - min)) * 100 || 50))}%` } as React.CSSProperties} />}
      </div>
      {/* Visible, not screen-reader-only. The message was announced and never
          shown, so a sighted user got a red border and no reason for it. */}
      {(draftMessage ?? invalidMessage) && <span id={`${id}-error`} className="field-error" role="alert">{draftMessage ?? invalidMessage}</span>}
    </div>
  );
}
