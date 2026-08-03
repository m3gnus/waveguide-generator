import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useDesignStore, type DesignDocument, type DesignFamily, type DesignValue } from '../stores/design';
import { NumberField } from './NumberField';
import {
  PARAMETER_REGISTRY,
  PARAMETER_SECTIONS,
  fieldAppliesToFamily,
  fieldIsVisible,
  fieldMatchesQuery,
  type ParameterDefinition,
  type ParameterSection,
} from './parameterRegistry';
import './paramPanel.css';

interface SectionProps {
  title: ParameterSection;
  summary: string;
  children: ReactNode;
  forceOpen: boolean;
}

const storageKey = (title: string) => `wg-param-section-open:${title}`;

function storedSectionState(title: string): boolean {
  try {
    const value = localStorage.getItem(storageKey(title));
    return value === null ? true : value === 'true';
  } catch {
    return true;
  }
}

function Section({ title, summary, children, forceOpen }: SectionProps) {
  const [open, setOpen] = useState(() => storedSectionState(title));
  const shownOpen = forceOpen || open;
  const toggle = () => {
    const next = !open;
    setOpen(next);
    try { localStorage.setItem(storageKey(title), String(next)); } catch { /* storage is optional */ }
  };
  return (
    <section className={`param-section${shownOpen ? '' : ' closed'}`} data-section={title}>
      <button className="section-head" onClick={toggle} aria-expanded={shownOpen}>
        <span className="chevron">⌄</span><span className="section-name">{title}</span><span className="spacer" />
        <span className="section-summary">{summary}</span>
      </button>
      {shownOpen && <div className="section-body">{children}</div>}
    </section>
  );
}

function getAtPath(design: DesignDocument, path: string | undefined): unknown {
  if (!path) return undefined;
  return path.split('.').reduce<unknown>((value, part) => {
    if (typeof value !== 'object' || value === null) return undefined;
    return (value as Record<string, unknown>)[part];
  }, design);
}

function TextField({ field, value, disabled, onCommit }: {
  field: ParameterDefinition;
  value: string;
  disabled?: boolean;
  onCommit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return <div className={`field-row param-text-row${disabled ? ' field-disabled' : ''}`} title={disabled ? field.disabledReason : field.description}>
    <label className="field-label" htmlFor={`parameter-${field.id}`}>{field.label}</label>
    <input
      id={`parameter-${field.id}`}
      value={draft}
      disabled={disabled}
      spellCheck={false}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => { if (draft !== value) onCommit(draft); }}
      onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
    />
  </div>;
}

function ReadOnlyTable({ field, value }: { field: ParameterDefinition; value: unknown }) {
  const rows = Array.isArray(value) ? value : [];
  return <div className="readonly-parameter-table" aria-label={field.label}>
    <div className="readonly-table-head"><b>{field.label}</b><span>{rows.length} rows</span></div>
    <table><thead><tr><th>#</th><th>Position</th><th>Value / shape</th></tr></thead>
      <tbody>{rows.map((row, index) => {
        const item = row as Record<string, unknown>;
        return <tr key={index}><td>{index + 1}</td><td>{String(item.z ?? item.t ?? '—')}</td><td>{String(item.r ?? item.shape ?? '—')}</td></tr>;
      })}</tbody>
    </table>
    <p>Structured read-only view — the spline-point editor arrives in a later phase.</p>
  </div>;
}

function passthroughStatus(field: ParameterDefinition, design: DesignDocument): string {
  const names = Object.keys(design.extra_blocks);
  if (field.id === 'passthrough.abec') {
    const count = names.filter((name) => name.toLocaleUpperCase().startsWith('ABEC')).length;
    return count ? `${count} block${count === 1 ? '' : 's'} present` : 'No blocks present';
  }
  if (field.id === 'passthrough.report') return names.some((name) => name.toLocaleLowerCase() === 'report') ? 'Block present' : 'No block present';
  if (field.id === 'passthrough.keys') {
    const count = Object.keys(design.extra_keys).length;
    return count ? `${count} key${count === 1 ? '' : 's'} present` : 'No keys present';
  }
  const count = names.filter((name) => !name.toLocaleUpperCase().startsWith('ABEC') && name.toLocaleLowerCase() !== 'report').length;
  return count ? `${count} block${count === 1 ? '' : 's'} present` : 'No blocks present';
}

function validationMessage(field: ParameterDefinition, design: DesignDocument): string | undefined {
  if (field.id === 'icw.hold_end' && (design.hold_end ?? 0) <= (design.hold_start ?? 0)) return 'Must exceed coverage hold start.';
  if (field.id === 'simulation.f2' && design.simulation.f2 <= design.simulation.f1) return 'Must exceed sweep start.';
  return undefined;
}

function FieldControl({ field, design }: { field: ParameterDefinition; design: DesignDocument }) {
  const updateValue = useDesignStore((state) => state.updateValue);
  const updateValues = useDesignStore((state) => state.updateValues);
  const beginDrag = useDesignStore((state) => state.beginDrag);
  const endDrag = useDesignStore((state) => state.endDrag);
  const value = getAtPath(design, field.path);
  const modeReason = field.disabledWhen?.(design);
  const disabledReason = field.disabledReason ?? modeReason;
  const disabled = Boolean(disabledReason);
  const commit = (next: DesignValue) => {
    if (!field.path || disabled) return;
    if (field.mirrorPaths?.length) {
      updateValues(Object.fromEntries([field.path, ...field.mirrorPaths].map((path) => [path, next])));
    } else {
      updateValue(field.path, next);
    }
  };

  if (field.kind === 'indicator') {
    return <div className="passthrough-row"><span>{field.label}</span><b>{passthroughStatus(field, design)}</b></div>;
  }
  if (field.kind === 'table') return <ReadOnlyTable field={field} value={value} />;
  if (field.disabledReason && !field.path) {
    return <div className="schema-gap" title={field.disabledReason}>
      <span>{field.label}</span><button disabled>{field.kind === 'toggle' ? 'Off' : 'Unavailable'}</button><small>{field.disabledReason}</small>
    </div>;
  }
  if (field.kind === 'select' || field.kind === 'toggle') {
    return <div className={`select-row${disabled ? ' field-disabled' : ''}`} title={disabledReason ?? field.description}>
      <label htmlFor={`parameter-${field.id}`}>{field.label}</label>
      <select id={`parameter-${field.id}`} value={String(value ?? '')} disabled={disabled} onChange={(event) => {
        const option = field.options?.find((item) => String(item.value) === event.target.value);
        commit(option?.value ?? event.target.value);
      }}>
        {(field.options ?? []).map((option) => <option key={String(option.value)} value={String(option.value)}>{option.label}</option>)}
      </select>
    </div>;
  }
  if (field.kind === 'text') return <TextField field={field} value={String(value ?? '')} disabled={disabled} onCommit={commit} />;
  const error = validationMessage(field, design);
  return <>
    <NumberField
      label={field.label}
      symbol={field.symbol}
      value={typeof value === 'number' ? value : 0}
      unit={field.unit}
      min={field.min}
      max={field.max}
      step={field.step}
      precision={field.precision}
      disabled={disabled}
      disabledReason={disabledReason}
      invalidMessage={error}
      onCommit={(next) => commit(next)}
      onBeginDrag={beginDrag}
      onEndDrag={endDrag}
    />
    {error && <div className="field-error">△ {error}</div>}
    {field.id === 'mesh.mouth_resolution' && <div className={`lambda-hint${Number(value) > 2.86 ? ' warning' : ''}`}>λ/6 at 20 kHz ≈ 2.86 mm</div>}
    {disabledReason && <div className="disabled-reason">{disabledReason}</div>}
  </>;
}

function QuadrantControl({ design }: { design: DesignDocument }) {
  const setQuadrants = useDesignStore((state) => state.setQuadrants);
  return <div className="quadrant-wrap"><div className="quadrants">
    {[2, 1, 3, 4].map((quadrant) => <button key={quadrant} className={design.quadrants.includes(quadrant) ? 'on' : ''} onClick={() => {
      const next = design.quadrants.includes(quadrant) ? design.quadrants.filter((item) => item !== quadrant) : [...design.quadrants, quadrant];
      if (next.length) setQuadrants(next);
    }}>Q{quadrant}</button>)}
  </div><div className="quadrant-meta"><b>{design.quadrants.length === 4 ? 'Full domain' : design.quadrants.length === 1 ? 'Quarter domain' : 'Reduced domain'}</b><span>{design.quadrants.length} of 4 quadrants</span><em>schema mask {design.mesh.quadrants}</em></div></div>;
}

export function ParamPanel() {
  const design = useDesignStore((state) => state.design);
  const setFamily = useDesignStore((state) => state.setFamily);
  const [query, setQuery] = useState('');
  const fieldsBySection = useMemo(() => new Map(PARAMETER_SECTIONS.map((section) => {
    const fields = PARAMETER_REGISTRY.filter((field) => field.section === section)
      .filter((field) => query.trim() ? fieldAppliesToFamily(field, design.formula) : fieldIsVisible(field, design))
      .filter((field) => fieldMatchesQuery(field, query));
    return [section, fields] as const;
  })), [design, query]);

  return (
    <div className="param-panel panel-scroll">
      <div className="panel-meta"><span className="pill accent">{design.formula}</span><span>complete design inventory</span></div>
      <div className="parameter-search">
        <label className="sr-only" htmlFor="parameter-filter">Filter parameters</label>
        <input id="parameter-filter" type="search" value={query} placeholder="Filter labels or keys…" onChange={(event) => setQuery(event.target.value)} />
        {query && <button aria-label="Clear parameter filter" onClick={() => setQuery('')}>×</button>}
      </div>
      {PARAMETER_SECTIONS.map((section) => {
        const fields = fieldsBySection.get(section) ?? [];
        if (fields.length === 0) return null;
        return <Section key={section} title={section} summary={`${fields.length} ${fields.length === 1 ? 'field' : 'fields'}`} forceOpen={Boolean(query.trim())}>
          {section === 'Profile' && <div className="select-row family-row">
            <label htmlFor="family">Family</label>
            <select id="family" value={design.formula} onChange={(event) => setFamily(event.target.value as DesignFamily)}>
              <option>OSSE</option><option>R-OSSE</option><option>ICW</option><option>FREEFORM</option>
            </select>
          </div>}
          {fields.map((field) => <div className="parameter-entry" data-parameter-id={field.id} data-parameter-key={field.legacyKey} key={field.id}>
            {field.id === 'mesh.quadrants' ? <QuadrantControl design={design} /> : <FieldControl field={field} design={design} />}
          </div>)}
          {query.trim() && fields.some((field) => !fieldIsVisible(field, design)) && <p className="filter-note">Some matches are normally hidden by the active mode; they are shown here for discoverability.</p>}
        </Section>;
      })}
      {query.trim() && [...fieldsBySection.values()].every((fields) => fields.length === 0) && <div className="parameter-empty">No parameter labels or keys match “{query}”.</div>}
    </div>
  );
}
