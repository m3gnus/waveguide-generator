import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import {
  getDriver,
  searchDrivers,
  type DriverDetail,
  type DriverHit,
  type DriverKind,
} from '../api/drivers';
import {
  DRIVER_FIELD_KEYS,
  driverEditedKeys,
  driverMissingGroups,
  driverValues,
  useCadReturnStore,
  type CadDriveChannel,
  type ChannelDriverForm,
  type DriverFieldKey,
  type DriverPreset,
} from '../stores/cadReturn';
import {
  driverLibraryHasFiles,
  savedDriverMatches,
  savedDriverPreset,
  useDriverLibraryStore,
  type SavedDriver,
} from '../stores/driverLibrary';
import { trapDialogFocus } from '../shell/SettingsDialog';
import { Icon } from '../shell/icons';
import {
  CAD_CONTROLS,
  CAD_DRIVER_FIELD_CONTROLS,
  CAD_DRIVER_SHEET_FIELDS,
} from './cadControlRegistry';
import { driverDerivedValues, driverValuesDisagree } from './driverDerived';

const FIELD_LABELS = new Map(CAD_DRIVER_FIELD_CONTROLS.map((control) => [control.driverKey, control.label]));

function fieldLabel(key: DriverFieldKey): string {
  return FIELD_LABELS.get(key) ?? key;
}

/** "Sd, Bl, Re, one of Mms/Mmd, one of Cms/Vas/Fs" in the user's words. */
function missingText(form: ChannelDriverForm | undefined): string {
  return driverMissingGroups(form)
    .map((group) => (group.length === 1
      ? fieldLabel(group[0])
      : `one of ${group.map(fieldLabel).join('/')}`))
    .join(', ');
}

/**
 * Which half of the library a channel's search starts in.
 *
 * A tweeter channel is far more often a compression driver than a woofer, and
 * getting this wrong costs one click, so it reads the channel's own words
 * rather than asking. Anything that is not recognisably a high-frequency
 * channel starts on the cone half.
 */
export function defaultDriverKind(...hints: Array<string | undefined | null>): DriverKind {
  const text = hints.filter(Boolean).join(' ').toLocaleLowerCase();
  return /\b(hf|uhf|vhf|treble|tweeter)\b|tweeter|compression|horn|cd\b/.test(text) ? 'cd' : 'lf';
}

function formatNumber(value: number, digits = 3): string {
  return String(Number(value.toPrecision(digits)));
}

function impedanceText(z: number | null | undefined): string | null {
  return typeof z === 'number' && Number.isFinite(z) ? `${formatNumber(z, 3)} Ω` : null;
}

export function driverHitLabel(hit: Pick<DriverHit, 'brand' | 'model'>): string {
  return `${hit.brand} ${hit.model}`.trim();
}

function presetFromHit(hit: DriverHit): DriverPreset {
  const base: Partial<Record<DriverFieldKey, number>> = {};
  for (const key of DRIVER_FIELD_KEYS) {
    const value = hit.spec[key];
    if (typeof value === 'number' && Number.isFinite(value)) base[key] = value;
  }
  return {
    id: hit.id,
    label: driverHitLabel(hit),
    source: 'database',
    kind: hit.kind,
    z_ohm: typeof hit.z_ohm === 'number' ? hit.z_ohm : null,
    xo_min_hz: typeof hit.xo_min_hz === 'number' && Number.isFinite(hit.xo_min_hz) ? hit.xo_min_hz : null,
    base,
  };
}

/** What the library could not tell us about a driver the user picked. */
function shortfallText(form: ChannelDriverForm): string | null {
  const missing = missingText(form);
  return missing ? `Needs ${missing}` : null;
}

function KindToggle({ kind, onChange, channelId }: {
  kind: DriverKind;
  onChange: (kind: DriverKind) => void;
  channelId: string;
}) {
  return <div className="driver-kind-toggle" role="group" aria-label={`Driver type for ${channelId}`}>
    {(['lf', 'cd'] as const).map((option) => <button
      key={option}
      type="button"
      className={kind === option ? 'on' : ''}
      aria-pressed={kind === option}
      onClick={() => onChange(option)}
    >{option === 'lf' ? 'Cone' : 'Compression'}</button>)}
  </div>;
}

/** Hornresp-unit T/S entry for one drive channel. Plain inputs are required:
 * an empty field means "not provided", which NumberField cannot represent. */
export function DriverFields({ channel, form, onField }: {
  channel: CadDriveChannel;
  form: ChannelDriverForm | undefined;
  onField: (field: DriverFieldKey, value: number | null) => void;
}) {
  const values = driverValues(form);
  const missing = missingText(form);
  return <div className="cad-driver-grid">
    {CAD_DRIVER_FIELD_CONTROLS.map(({ driverKey, label, unit, step, reveal }) => <label key={driverKey} className="cad-driver-field" data-control-reveal-id={reveal.id}>
      <span>{label}{unit ? ` (${unit})` : ''}</span>
      <input
        type="number"
        min={0}
        step={step}
        value={values[driverKey] ?? ''}
        aria-label={`${label} for ${channel.id}`}
        onChange={(event) => onField(driverKey, event.target.value === '' ? null : Number(event.target.value))}
      />
    </label>)}
    {missing && <p className="cad-driver-hint">Required: {missing}. The channel solves as a unit-drive basis until they are set.</p>}
  </div>;
}

function DriverResultRow({ hit, active, onPick, onHover, id }: {
  hit: DriverHit;
  active: boolean;
  id: string;
  onPick: () => void;
  onHover: () => void;
}) {
  const z = impedanceText(hit.z_ohm);
  const facts = [
    hit.size,
    z,
    hit.display.fs_hz ? `Fs ${formatNumber(hit.display.fs_hz, 3)} Hz` : null,
    hit.display.sd_cm2 ? `Sd ${formatNumber(hit.display.sd_cm2, 3)} cm²` : null,
  ].filter(Boolean).join(' · ');
  return <button
    type="button"
    id={id}
    role="option"
    aria-selected={active}
    className={`driver-result${active ? ' active' : ''}`}
    onMouseMove={onHover}
    onMouseDown={(event) => event.preventDefault()}
    onClick={onPick}
  >
    <span className="driver-result-name">{driverHitLabel(hit)}</span>
    <span className="driver-result-facts">{facts}</span>
    {hit.completeness !== 'full' && <span className="driver-chip warn">{hit.completeness === 'catalogue' ? 'catalogue only' : 'partial'}</span>}
  </button>;
}

function SavedResultRow({ driver, active, onPick, onHover, id }: {
  driver: SavedDriver;
  active: boolean;
  id: string;
  onPick: () => void;
  onHover: () => void;
}) {
  return <button
    type="button"
    id={id}
    role="option"
    aria-selected={active}
    className={`driver-result${active ? ' active' : ''}`}
    onMouseMove={onHover}
    onMouseDown={(event) => event.preventDefault()}
    onClick={onPick}
  >
    <span className="driver-result-name">{driver.label}</span>
    <span className="driver-result-facts">{impedanceText(driver.z_ohm) ?? ''}</span>
    <span className="driver-chip">mine</span>
  </button>;
}

interface Candidate {
  key: string;
  preset: DriverPreset;
  render: (props: { active: boolean; id: string; onPick: () => void; onHover: () => void }) => ReactElement;
}

function DriverSearch({ channel, roleHint, onPick }: {
  channel: CadDriveChannel;
  roleHint: string | undefined;
  onPick: (preset: DriverPreset) => void;
}) {
  const saved = useDriverLibraryStore((state) => state.saved);
  const [kind, setKind] = useState<DriverKind>(() => defaultDriverKind(channel.id, roleHint));
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [hits, setHits] = useState<DriverHit[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);

  useEffect(() => {
    if (!open) return;
    const request = ++generation.current;
    void searchDrivers({ q: query, kind, limit: 20 }).then(
      (items) => { if (request === generation.current) { setHits(items); setError(null); } },
      (reason: unknown) => {
        if (request !== generation.current) return;
        setHits([]);
        setError(reason instanceof Error ? reason.message : String(reason));
      },
    );
    return () => { generation.current += 1; };
  }, [kind, open, query]);

  const candidates = useMemo<Candidate[]>(() => {
    const fromLibrary = hits.map((hit) => ({
      key: `db:${hit.id}`,
      preset: presetFromHit(hit),
      render: ({ active, id, onPick: pick, onHover }: { active: boolean; id: string; onPick: () => void; onHover: () => void }) =>
        <DriverResultRow key={hit.id} hit={hit} active={active} id={id} onPick={pick} onHover={onHover}/>,
    }));
    // Saved drivers sit under the database hits: they are the user's own
    // corrections, and burying them would defeat the point of saving one, but
    // leading with them would hide the library the query was aimed at.
    const fromSaved = saved
      .filter((driver) => savedDriverMatches(driver, query) && (driver.kind === kind || driver.kind === 'unknown'))
      .map((driver) => ({
        key: `mine:${driver.id}`,
        preset: savedDriverPreset(driver),
        render: ({ active, id, onPick: pick, onHover }: { active: boolean; id: string; onPick: () => void; onHover: () => void }) =>
          <SavedResultRow key={driver.id} driver={driver} active={active} id={id} onPick={pick} onHover={onHover}/>,
      }));
    return [...fromLibrary, ...fromSaved];
  }, [hits, kind, query, saved]);

  useEffect(() => setActiveIndex(0), [query, kind]);

  const pick = (candidate: Candidate | undefined) => {
    if (!candidate) return;
    setOpen(false);
    setQuery('');
    onPick(candidate.preset);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') {
      // Esc closes the list and keeps whatever driver the channel already has.
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => (candidates.length ? (index + 1) % candidates.length : 0));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => (candidates.length ? (index - 1 + candidates.length) % candidates.length : 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      pick(candidates[activeIndex]);
    }
  };

  const listId = `driver-results-${channel.id}`;
  const activeId = candidates[activeIndex] ? `${listId}-${activeIndex}` : undefined;
  return <div className="driver-search" data-control-reveal-id={CAD_CONTROLS.driverSearch.reveal.id}>
    <div className="driver-search-row">
      <input
        type="search"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={activeId}
        aria-label={`${CAD_CONTROLS.driverSearch.label} for ${channel.id}`}
        placeholder="Search drivers…"
        value={query}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onChange={(event) => { setQuery(event.target.value); setOpen(true); }}
        onKeyDown={onKeyDown}
      />
      <KindToggle kind={kind} channelId={channel.id} onChange={setKind}/>
    </div>
    {open && <div id={listId} className="driver-results" role="listbox" aria-label={`Driver matches for ${channel.id}`}>
      {error && <p className="cad-driver-hint" role="status">{error}</p>}
      {!error && !candidates.length && <p className="cad-driver-hint" role="status">No driver matches that search.</p>}
      {candidates.map((candidate, index) => candidate.render({
        active: index === activeIndex,
        id: `${listId}-${index}`,
        onPick: () => pick(candidate),
        onHover: () => setActiveIndex(index),
      }))}
    </div>}
  </div>;
}

function DerivedRow({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return <div className="driver-derived-row"><span>{label}</span><b>{value}{unit ? ` ${unit}` : ''}</b></div>;
}

/**
 * The T/S sheet: the picked driver's numbers, what the user changed, and what
 * those numbers imply.
 *
 * Every input writes an override rather than the value itself, so *Reset to
 * database values* is always available and an edit is always visible as one.
 */
function DriverSheet({ channel, form, onClose }: {
  channel: CadDriveChannel;
  form: ChannelDriverForm;
  onClose: () => void;
}) {
  const state = useCadReturnStore();
  const saveMine = useDriverLibraryStore((store) => store.save);
  const dialog = useRef<HTMLDivElement>(null);
  const [detail, setDetail] = useState<DriverDetail | null>(null);
  const [saved, setSaved] = useState(false);
  const preset = form.preset;

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return; }
      trapDialogFocus(dialog, event);
    };
    document.addEventListener('keydown', keydown);
    const frame = requestAnimationFrame(() => dialog.current?.querySelector<HTMLElement>('input')?.focus());
    return () => {
      document.removeEventListener('keydown', keydown);
      cancelAnimationFrame(frame);
    };
  }, [onClose]);

  const presetId = preset?.source === 'database' ? preset.id : null;
  useEffect(() => {
    if (!presetId) { setDetail(null); return; }
    let current = true;
    void getDriver(presetId).then(
      (value) => { if (current) setDetail(value); },
      () => { if (current) setDetail(null); },
    );
    return () => { current = false; };
  }, [presetId]);

  const values = driverValues(form);
  const edited = driverEditedKeys(form);
  const derived = driverDerivedValues(values);
  const disagrees = driverValuesDisagree(derived);
  const variants = detail?.variants ?? [];

  const chooseVariant = useCallback(async (variantId: string) => {
    if (!preset || variantId === preset.id) return;
    const next = await getDriver(variantId).catch(() => null);
    if (!next) return;
    // The edits are still the user's own, so a variant reloads only the base.
    state.setChannelDriverPreset(channel.id, presetFromHit(next), true);
    setDetail(next);
  }, [channel.id, preset, state]);

  const saveToMine = () => {
    if (!preset) return;
    saveMine({
      id: `mine:${preset.id}`,
      label: preset.label,
      based_on: preset.source === 'manual' ? 'manual' : preset.id,
      base: { ...preset.base },
      overrides: Object.fromEntries(edited.map((key) => [key, form.fields[key]!])),
      kind: preset.kind,
      z_ohm: preset.z_ohm,
      xo_min_hz: preset.xo_min_hz,
    });
    setSaved(true);
  };

  const provenance = preset?.source === 'mine'
    ? 'My drivers'
    : detail?.source.file ?? (preset?.source === 'manual' ? 'Typed by hand' : null);

  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={dialog} className="settings-dialog driver-sheet" role="dialog" aria-modal="true" aria-labelledby={`driver-sheet-title-${channel.id}`}>
      <header>
        <div>
          <h2 id={`driver-sheet-title-${channel.id}`}>{preset?.label ?? `Driver · ${channel.id}`}</h2>
          <p>{channel.id}{impedanceText(preset?.z_ohm) ? ` · ${impedanceText(preset?.z_ohm)}` : ''}</p>
        </div>
        <div className="driver-sheet-chips">
          {provenance && <span className="driver-chip">{provenance}</span>}
          {edited.length > 0 && <span className="driver-chip accent">{edited.length} edited</span>}
        </div>
        <button className="dialog-close" aria-label="Close driver sheet" onClick={onClose}><Icon name="close"/></button>
      </header>
      <div className="settings-scroll">
        {variants.length > 1 && <div className="driver-variants" role="group" aria-label="Impedance variant">
          {variants.map((variant) => <button
            key={variant.id}
            type="button"
            className={variant.id === preset?.id ? 'on' : ''}
            aria-pressed={variant.id === preset?.id}
            onClick={() => void chooseVariant(variant.id)}
          >{impedanceText(variant.z_ohm) ?? 'unknown Ω'}</button>)}
        </div>}
        <div className="cad-driver-grid driver-sheet-grid" data-control-reveal-id={CAD_CONTROLS.driverEdit.reveal.id}>
          {CAD_DRIVER_SHEET_FIELDS.map(({ driverKey, label, unit, step, reveal }) => <label
            key={driverKey}
            className={`cad-driver-field${edited.includes(driverKey) ? ' edited' : ''}`}
            data-control-reveal-id={reveal.id}
          >
            <span>{label}{unit ? ` (${unit})` : ''}</span>
            <input
              type="number"
              min={0}
              step={step}
              value={values[driverKey] ?? ''}
              aria-label={`${label} for ${channel.id}`}
              onChange={(event) => state.setChannelDriverField(
                channel.id,
                driverKey,
                event.target.value === '' ? null : Number(event.target.value),
              )}
            />
          </label>)}
        </div>
        <div className="driver-derived" aria-label="Derived values">
          <DerivedRow label="Cms" value={derived.cmsMPerN === undefined ? '—' : derived.cmsMPerN.toExponential(3)} unit="m/N"/>
          <DerivedRow label="Qes" value={derived.qes === undefined ? '—' : formatNumber(derived.qes, 3)}/>
          <DerivedRow label="Qts" value={derived.qts === undefined ? '—' : formatNumber(derived.qts, 3)}/>
          <DerivedRow label="Sensitivity" value={derived.sensitivityDb === undefined ? '—' : derived.sensitivityDb.toFixed(1)} unit="dB 1 W/1 m"/>
        </div>
        {disagrees && <p className="field-warning" role="status">
          Fs, Mms and Vas disagree by {(100 * (derived.fsMismatch ?? 0)).toFixed(0)}%. One of the three is wrong; the solve uses them as typed.
        </p>}
        {missingText(form) && <p className="cad-driver-hint">Still needed: {missingText(form)}.</p>}
      </div>
      <footer className="driver-sheet-actions">
        <button
          type="button"
          disabled={!preset || edited.length === 0}
          onClick={() => state.clearChannelDriverOverrides(channel.id)}
        >Reset to database values</button>
        <button type="button" disabled={!preset || saved} onClick={saveToMine}>{saved ? 'Saved' : 'Save to My drivers'}</button>
        <button type="button" className="primary" onClick={onClose}>Done</button>
      </footer>
    </div>
  </div>;
}

/** The picked driver, as the channel card shows it. */
function DriverSummary({ channel, form, onEdit, onClear }: {
  channel: CadDriveChannel;
  form: ChannelDriverForm;
  onEdit: () => void;
  onClear: () => void;
}) {
  const values = driverValues(form);
  const preset = form.preset!;
  const z = impedanceText(preset.z_ohm);
  const shortfall = shortfallText(form);
  const count = values.count ?? 1;
  return <div className="driver-summary">
    <div className="driver-summary-head">
      <span className="driver-chip name">{preset.label}{z ? ` · ${z}` : ''}</span>
      {preset.source === 'mine' && <span className="driver-chip">mine</span>}
      {count > 1 && <span className="driver-chip">×{count}</span>}
      <button
        type="button"
        className="driver-edit-link"
        data-control-reveal-id={CAD_CONTROLS.driverEdit.reveal.id}
        onClick={onEdit}
      >Edit T/S…</button>
      <button type="button" className="driver-clear-link" aria-label={`Clear driver for ${channel.id}`} onClick={onClear}>Clear</button>
    </div>
    <div className="driver-summary-facts">
      {([
        ['sd_cm2', 'Sd', 'cm²'],
        ['bl_t_m', 'Bl', 'T·m'],
        ['fs_hz', 'Fs', 'Hz'],
        ['xmax_mm', 'Xmax', 'mm'],
      ] as const).map(([key, label, unit]) => values[key] === undefined
        ? null
        : <span className="driver-chip" key={key}>{label} {formatNumber(values[key]!, 3)} {unit}</span>)}
      {shortfall && <span className="driver-chip warn">{shortfall}</span>}
    </div>
  </div>;
}

/**
 * One channel's driver: search, the picked driver, and the T/S sheet.
 *
 * With no library folder there is nothing to search, so the card falls back to
 * the manual grid it has always shown rather than offering a field that can
 * only ever answer "no matches".
 */
export function ChannelDriverPicker({ channel, form, roleHint }: {
  channel: CadDriveChannel;
  form: ChannelDriverForm | undefined;
  roleHint?: string;
}) {
  const state = useCadReturnStore();
  const status = useDriverLibraryStore((store) => store.status);
  const info = useDriverLibraryStore((store) => store.info);
  const load = useDriverLibraryStore((store) => store.load);
  const [sheetOpen, setSheetOpen] = useState(false);

  useEffect(() => { void load(); }, [load]);

  const hasLibrary = driverLibraryHasFiles({ status, info });
  const preset = form?.preset ?? null;

  if (!hasLibrary) {
    return <>
      <p className="cad-driver-hint">
        {status === 'loading' || status === 'idle'
          ? 'Looking for a driver library…'
          : 'No driver library found — enter the Thiele-Small values below, or add CSV files to the driver library folder in Settings.'}
      </p>
      <DriverFields
        channel={channel}
        form={form}
        onField={(field, value) => state.setChannelDriverField(channel.id, field, value)}
      />
    </>;
  }

  return <div className="driver-picker">
    {preset === null
      ? <DriverSearch
          channel={channel}
          roleHint={roleHint}
          onPick={(picked) => state.setChannelDriverPreset(channel.id, picked)}
        />
      : <DriverSummary
          channel={channel}
          form={form!}
          onEdit={() => setSheetOpen(true)}
          onClear={() => state.setChannelDriverPreset(channel.id, null)}
        />}
    {sheetOpen && form && <DriverSheet channel={channel} form={form} onClose={() => setSheetOpen(false)}/>}
  </div>;
}
