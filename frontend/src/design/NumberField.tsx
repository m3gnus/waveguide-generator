import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import type { ExprNumber } from '../stores/design';

interface NumberFieldProps {
  label: string;
  symbol?: string;
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
}

export function NumberField({
  label,
  symbol,
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
}: NumberFieldProps) {
  const id = useId();
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
  const draftMessage = draft.trim() && Number.isFinite(parsed) ? validate?.(parsed) : undefined;
  const isExpression = allowExpression && draft.trim() !== '' && !Number.isFinite(parsed);
  const empty = draft.trim() === '';
  const invalid = (empty && !optional) || (!empty && !isExpression && (!Number.isFinite(parsed) || parsed < min || parsed > max || Boolean(draftMessage)));

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
    if (invalid) {
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

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.currentTarget.blur();
    } else if (event.key === 'Escape') {
      cancelBlur.current = true;
      setDraft(displayed);
      setEditing(false);
      event.currentTarget.blur();
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
    <div className={`field-row${modified ? ' modified' : ''}${disabled ? ' field-disabled' : ''}${holdsExpression ? ' expression-row' : ''}`} title={disabledReason}>
      <i className="modified-dot" />
      <label
        htmlFor={id}
        className="field-label"
        // Long parameter names ellipsize against the value control, so the
        // tooltip always carries the full label as well as the drag hint.
        title={holdsExpression ? `${label} — formula field, edit the expression below` : `${label} — drag horizontally to adjust`}
        onPointerDown={onLabelPointerDown}
        onPointerMove={onLabelPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={endDrag}
      >
        {label}{symbol && <span className="field-symbol">{symbol}</span>}
      </label>
      {holdsExpression && <span className="expr-badge" aria-hidden="true">fx</span>}
      {showEvaluatedExpression && <span className="expr-value" title="Evaluated expression value">= {Number(expression!.value!.toPrecision(8))}{unit}</span>}
      <div className={`number-control${editing ? ' editing' : ''}${invalid ? ' invalid' : ''}${expression?.raw ? ' expression' : ''}`}>
        {dragDelta !== null && (
          <span className="scrub-tip">drag <b>{dragDelta >= 0 ? '+' : ''}{dragDelta.toFixed(precision)}{unit}</b></span>
        )}
        <input
          id={id}
          aria-invalid={invalid}
          aria-describedby={(draftMessage ?? invalidMessage) ? `${id}-error` : undefined}
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
      {(draftMessage ?? invalidMessage) && <span id={`${id}-error`} className="sr-only">{draftMessage ?? invalidMessage}</span>}
    </div>
  );
}
