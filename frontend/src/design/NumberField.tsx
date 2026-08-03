import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import type { ExprNumber } from '../stores/design';

interface NumberFieldProps {
  label: string;
  symbol?: string;
  value: number;
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
  expression,
  allowExpression = false,
  onCommitExpression,
  onBeginDrag,
  onEndDrag,
}: NumberFieldProps) {
  const id = useId();
  const displayed = expression?.raw ?? value.toFixed(precision);
  const [draft, setDraft] = useState(displayed);
  const [editing, setEditing] = useState(false);
  const [dragDelta, setDragDelta] = useState<number | null>(null);
  const drag = useRef<{ x: number; value: number } | null>(null);
  const cancelBlur = useRef(false);
  const endDragCallback = useRef(onEndDrag);
  endDragCallback.current = onEndDrag;
  const parsed = Number(draft);
  const draftMessage = draft.trim() && Number.isFinite(parsed) ? validate?.(parsed) : undefined;
  const isExpression = allowExpression && draft.trim() !== '' && !Number.isFinite(parsed);
  const invalid = draft.trim() === '' || (!isExpression && (!Number.isFinite(parsed) || parsed < min || parsed > max || Boolean(draftMessage)));

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
    if (invalid) {
      setDraft(displayed);
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
    if (rounded !== value) onCommit(rounded);
  };

  const endDrag = () => {
    if (!drag.current) return;
    drag.current = null;
    setDragDelta(null);
    onEndDrag?.();
  };

  useEffect(() => () => {
    if (!drag.current) return;
    drag.current = null;
    endDragCallback.current?.();
  }, []);

  return (
    <div className={`field-row${modified ? ' modified' : ''}${disabled ? ' field-disabled' : ''}`} title={disabledReason}>
      <i className="modified-dot" />
      <label
        htmlFor={id}
        className="field-label"
        title="Drag horizontally to adjust"
        onPointerDown={onLabelPointerDown}
        onPointerMove={onLabelPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={endDrag}
      >
        {label}{symbol && <span className="field-symbol">{symbol}</span>}
      </label>
      <div className={`number-control${editing ? ' editing' : ''}${invalid ? ' invalid' : ''}${expression?.raw ? ' expression' : ''}`}>
        {dragDelta !== null && (
          <span className="scrub-tip">drag <b>{dragDelta >= 0 ? '+' : ''}{dragDelta.toFixed(precision)}{unit}</b></span>
        )}
        <input
          id={id}
          aria-invalid={invalid}
          aria-describedby={(draftMessage ?? invalidMessage) ? `${id}-error` : undefined}
          inputMode="decimal"
          spellCheck={false}
          disabled={disabled}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onFocus={() => setEditing(true)}
          onBlur={commit}
          onKeyDown={onKeyDown}
        />
        {unit && <span className="unit">{unit}</span>}
        <i className="number-track" style={{ '--fill': `${Math.max(5, Math.min(100, ((value - min) / (max - min)) * 100 || 50))}%` } as React.CSSProperties} />
      </div>
      {expression?.raw && expression.value !== null && <span className="expr-value" title="Evaluated value">= {Number(expression.value.toPrecision(8))}</span>}
      {(draftMessage ?? invalidMessage) && <span id={`${id}-error`} className="sr-only">{draftMessage ?? invalidMessage}</span>}
    </div>
  );
}
