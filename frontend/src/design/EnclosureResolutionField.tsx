import { useEffect, useState } from 'react';
import type { DesignDocument, DesignValue } from '../stores/design';
import { HelpTipRow } from './HelpTip';
import type { ParameterDefinition } from './parameterRegistry';

export function parseEnclosureResolution(text: string): number | number[] | undefined {
  const parts = text.split(',');
  if (parts.length > 4 || parts.some((part) => !part.trim() || !Number.isFinite(Number(part)))) return undefined;
  const values = parts.map(Number);
  return values.length === 1 ? values[0] : values;
}

export function EnclosureResolutionField({ field, design, value, onCommit }: {
  field: ParameterDefinition;
  design: DesignDocument;
  value: unknown;
  onCommit: (value: DesignValue) => void;
}) {
  const path = field.path!;
  const values = Array.isArray(value) ? value : [value];
  const displayed = values.map((item, index) => design._expressions?.[Array.isArray(value) ? `${path}.${index}` : path]?.raw ?? item).join(',');
  const [draft, setDraft] = useState(displayed);
  useEffect(() => setDraft(displayed), [displayed]);
  const parsed = parseEnclosureResolution(draft);
  const error = parsed === undefined ? 'Enter one to four finite numbers, separated by commas.' : undefined;
  return <HelpTipRow className="field-row param-text-row" text={field.description}>
    <label htmlFor={`parameter-${field.id}`}>{field.label} (mm)</label>
    <input id={`parameter-${field.id}`} value={draft} aria-invalid={Boolean(error)}
      aria-describedby={error ? `error-${field.id}` : undefined}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => { if (parsed !== undefined && draft !== displayed) onCommit(parsed); }}
      onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }} />
    {error && <small id={`error-${field.id}`} role="alert">{error}</small>}
  </HelpTipRow>;
}
