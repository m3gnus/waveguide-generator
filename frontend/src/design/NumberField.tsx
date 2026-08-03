import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';

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
  invalidMessage?: string;
  onCommit: (value: number) => void;
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
  invalidMessage,
  onCommit,
  onBeginDrag,
  onEndDrag,
}: NumberFieldProps) {
  const id = useId();
  const [draft, setDraft] = useState(value.toFixed(precision));
  const [editing, setEditing] = useState(false);
  const [dragDelta, setDragDelta] = useState<number | null>(null);
  const drag = useRef<{ x: number; value: number } | null>(null);
  const parsed = Number(draft);
  const invalid = !Number.isFinite(parsed) || parsed < min || parsed > max;

  useEffect(() => {
    if (!editing && !drag.current) setDraft(value.toFixed(precision));
  }, [editing, precision, value]);

  const commit = () => {
    setEditing(false);
    if (invalid) return;
    const rounded = Number(parsed.toFixed(precision));
    setDraft(rounded.toFixed(precision));
    if (rounded !== value) onCommit(rounded);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.currentTarget.blur();
    } else if (event.key === 'Escape') {
      setDraft(value.toFixed(precision));
      setEditing(false);
      event.currentTarget.blur();
    }
  };

  const onLabelPointerDown = (event: PointerEvent<HTMLLabelElement>) => {
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

  return (
    <div className={`field-row${modified ? ' modified' : ''}`}>
      <i className="modified-dot" />
      <label
        htmlFor={id}
        className="field-label"
        title="Drag horizontally to adjust"
        onPointerDown={onLabelPointerDown}
        onPointerMove={onLabelPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        {label}{symbol && <span className="field-symbol">{symbol}</span>}
      </label>
      <div className={`number-control${editing ? ' editing' : ''}${invalid ? ' invalid' : ''}`}>
        {dragDelta !== null && (
          <span className="scrub-tip">drag <b>{dragDelta >= 0 ? '+' : ''}{dragDelta.toFixed(precision)}{unit}</b></span>
        )}
        <input
          id={id}
          aria-invalid={invalid}
          aria-describedby={invalidMessage ? `${id}-error` : undefined}
          inputMode="decimal"
          spellCheck={false}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onFocus={() => setEditing(true)}
          onBlur={commit}
          onKeyDown={onKeyDown}
        />
        {unit && <span className="unit">{unit}</span>}
        <i className="number-track" style={{ '--fill': `${Math.max(5, Math.min(100, ((value - min) / (max - min)) * 100 || 50))}%` } as React.CSSProperties} />
      </div>
      {invalidMessage && <span id={`${id}-error`} className="sr-only">{invalidMessage}</span>}
    </div>
  );
}
