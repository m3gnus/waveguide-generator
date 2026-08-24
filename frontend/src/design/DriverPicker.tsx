import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import {
  getDriver,
  searchDrivers,
  type DriverDetail,
  type DriverHit,
  type DriverKind,
} from '../api/drivers';
import {
  DRIVER_INSTALLATION_KEYS,
  channelDriverPresent,
  driverEditedKeys,
  driverBaseFromSpec,
  driverShortfallText,
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
import { trapDialogFocus } from '../shell/dialogFocus';
import { Icon } from '../shell/icons';
import {
  CAD_CONTROLS,
  CAD_DRIVER_FIELD_CONTROLS,
  CAD_DRIVER_SHEET_FIELDS,
} from './cadControlRegistry';
import { driverDerivedValues, driverValuesDisagree } from './driverDerived';


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
  const base = driverBaseFromSpec(hit.spec);
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

/** The name a hand-entered driver gets when its row was chosen on an empty query. */
export const MANUAL_DRIVER_LABEL = 'Manual driver';

/**
 * A driver the library does not have, named after whatever was typed.
 *
 * The id is derived from the name rather than minted fresh so that typing the
 * same driver twice, or saving it twice, lands on one entry in *My drivers*
 * instead of two that differ only by a timestamp. A name with nothing sluggable
 * in it (or none at all) falls back to a unique id, which is the only case where
 * two hand-entered drivers can collide otherwise.
 */
export function manualDriverPreset(query: string, kind: DriverKind): DriverPreset {
  const label = query.trim() || MANUAL_DRIVER_LABEL;
  const slug = label.toLocaleLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return {
    id: `manual:${slug || `driver-${Date.now().toString(36)}`}`,
    label,
    source: 'manual',
    kind,
    z_ohm: null,
    xo_min_hz: null,
    base: {},
  };
}

/** The driver's own numbers, without WG's installation inputs: how many of it
 * there are and what box it sits in belong to this channel, not to the driver
 * *My drivers* will offer to every other one. */
function driverOwnValues(
  values: Partial<Record<DriverFieldKey, number>>,
): Partial<Record<DriverFieldKey, number>> {
  return Object.fromEntries(
    Object.entries(values).filter(([key]) => !DRIVER_INSTALLATION_KEYS.includes(key as DriverFieldKey)),
  );
}

/** What the library could not tell us about a driver the user picked. */
function shortfallText(form: ChannelDriverForm): string | null {
  const missing = driverShortfallText(form);
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
  const missing = driverShortfallText(form);
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
    {missing && <p className="cad-driver-hint">Required: {missing}. {channelDriverPresent(form)
      ? 'The solve is refused while a started driver is missing them.'
      : 'With nothing entered the channel solves as a unit-drive basis.'}</p>}
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

/** The way out of the library: type a driver it has never heard of. */
function ManualResultRow({ query, active, onPick, onHover, id }: {
  query: string;
  active: boolean;
  id: string;
  onPick: () => void;
  onHover: () => void;
}) {
  const named = query.trim();
  return <button
    type="button"
    id={id}
    role="option"
    aria-selected={active}
    className={`driver-result manual${active ? ' active' : ''}`}
    onMouseMove={onHover}
    onMouseDown={(event) => event.preventDefault()}
    onClick={onPick}
  >
    <span className="driver-result-name">Enter T/S manually…</span>
    <span className="driver-result-facts">{named ? `as “${named}”` : 'a driver the library does not have'}</span>
  </button>;
}

interface Candidate {
  key: string;
  /** Built when the row is chosen: a hand-entered driver's id is minted from
   * the query at that moment, not on every keystroke. */
  preset: () => DriverPreset;
  /** Whether choosing this row should land the user in the T/S sheet. */
  manual?: boolean;
  render: (props: { active: boolean; id: string; onPick: () => void; onHover: () => void }) => ReactElement;
}

function DriverSearch({ channel, roleHint, onPick }: {
  channel: CadDriveChannel;
  roleHint: string | undefined;
  onPick: (preset: DriverPreset, options?: { edit?: boolean }) => void;
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
    void searchDrivers({ q: query, kind, limit: 40 }).then(
      (items) => { if (request === generation.current) { setHits(items); setError(null); } },
      (reason: unknown) => {
        if (request !== generation.current) return;
        setHits([]);
        setError(reason instanceof Error ? reason.message : String(reason));
      },
    );
    return () => { generation.current += 1; };
  }, [kind, open, query]);

  const matches = useMemo<Candidate[]>(() => {
    const fromLibrary = hits.map((hit): Candidate => ({
      key: `db:${hit.id}`,
      preset: () => presetFromHit(hit),
      render: ({ active, id, onPick: pick, onHover }) =>
        <DriverResultRow key={hit.id} hit={hit} active={active} id={id} onPick={pick} onHover={onHover}/>,
    }));
    // Saved drivers sit under the database hits: they are the user's own
    // corrections, and burying them would defeat the point of saving one, but
    // leading with them would hide the library the query was aimed at.
    const fromSaved = saved
      .filter((driver) => savedDriverMatches(driver, query) && (driver.kind === kind || driver.kind === 'unknown'))
      .map((driver): Candidate => ({
        key: `mine:${driver.id}`,
        preset: () => savedDriverPreset(driver),
        render: ({ active, id, onPick: pick, onHover }) =>
          <SavedResultRow key={driver.id} driver={driver} active={active} id={id} onPick={pick} onHover={onHover}/>,
      }));
    return [...fromLibrary, ...fromSaved];
  }, [hits, kind, query, saved]);

  // Hand entry is the last row whatever the search did, including when it did
  // nothing: a driver the library has never heard of is exactly the case where
  // the search comes back empty, and that is the worst moment to be offered
  // only a dead end.
  const candidates = useMemo<Candidate[]>(() => [...matches, {
    key: 'manual',
    manual: true,
    preset: () => manualDriverPreset(query, kind),
    render: ({ active, id, onPick: pick, onHover }) =>
      <ManualResultRow key="manual" query={query} active={active} id={id} onPick={pick} onHover={onHover}/>,
  }], [kind, matches, query]);

  useEffect(() => setActiveIndex(0), [query, kind]);

  const pick = (candidate: Candidate | undefined) => {
    if (!candidate) return;
    setOpen(false);
    setQuery('');
    onPick(candidate.preset(), { edit: candidate.manual === true });
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
      {!error && !matches.length && <p className="cad-driver-hint" role="status">No driver matches that search.</p>}
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
 *
 * A hand-entered driver goes through the same sheet with nothing underneath
 * it: the overrides are the whole driver, so there is no edit count, no reset,
 * and the name is the user's to set because *My drivers* has nothing else to
 * list it under.
 */
function DriverSheet({ channel, form, onClose }: {
  channel: CadDriveChannel;
  form: ChannelDriverForm;
  onClose: () => void;
}) {
  const state = useCadReturnStore();
  const saveMine = useDriverLibraryStore((store) => store.save);
  const savedDrivers = useDriverLibraryStore((store) => store.saved);
  const dialog = useRef<HTMLDivElement>(null);
  const [detail, setDetail] = useState<DriverDetail | null>(null);
  const [savedAs, setSavedAs] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const preset = form.preset;

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return; }
      trapDialogFocus(dialog, event);
    };
    document.addEventListener('keydown', keydown);
    // The T/S grid, not the name field a hand-entered driver puts above it:
    // the name is already what the user typed to get here, the numbers are not.
    const frame = requestAnimationFrame(() => {
      const grid = dialog.current?.querySelector<HTMLElement>('.driver-sheet-grid input');
      (grid ?? dialog.current?.querySelector<HTMLElement>('input'))?.focus();
    });
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

  const manual = preset?.source === 'manual';
  // The name is the only thing *My drivers* will list a hand-entered driver
  // under, so it is the user's to set -- but a database driver's name is the
  // library's, and renaming it would break the trail back to the row it came
  // from.
  const nameEditable = preset !== null && preset.source !== 'database';
  // A driver whose numbers are the user's own gets the whole grid, Mmd and Cms
  // included: those are what a datasheet without Mms or Vas prints, and hand
  // entry used to reach them through the no-library grid. A picked driver keeps
  // the shorter set, where a second mass beside the library's own would be an
  // invitation to state two (`channelDriverWire` sends only one either way).
  const sheetFields = nameEditable ? CAD_DRIVER_FIELD_CONTROLS : CAD_DRIVER_SHEET_FIELDS;
  const savedId = preset === null ? null : preset.source === 'mine' ? preset.id : `mine:${preset.id}`;
  const saved = savedId !== null && savedAs === `${savedId}\u0000${preset!.label}`;

  const rename = (text: string) => {
    if (!preset) return;
    setNameDraft(text);
    // An empty field is a name in progress, not a driver with no name: the
    // stored label always stays something a reloaded profile can parse.
    state.setChannelDriverPreset(channel.id, { ...preset, label: text.trim() || MANUAL_DRIVER_LABEL }, true);
  };

  const saveToMine = () => {
    if (!preset || !savedId) return;
    // A hand-entered driver is all its own numbers, so they are its base and
    // it has no overrides; a picked one keeps the two apart so the saved copy
    // reopens with the same edit marks.
    const base = manual ? driverOwnValues(values) : { ...preset.base };
    const overrides = manual ? {} : Object.fromEntries(edited.map((key) => [key, form.fields[key]!]));
    const based = preset.source === 'manual' ? 'manual'
      : preset.source === 'mine' ? savedDrivers.find((driver) => driver.id === savedId)?.based_on ?? 'manual'
        : preset.id;
    saveMine({
      id: savedId,
      label: preset.label,
      based_on: based,
      base,
      overrides,
      kind: preset.kind,
      z_ohm: preset.z_ohm,
      xo_min_hz: preset.xo_min_hz,
    });
    // The saved copy is now what the channel holds, so saving again updates it
    // instead of leaving a second entry behind under a `mine:` id.
    if (manual) state.setChannelDriverPreset(channel.id, { ...preset, id: savedId, source: 'mine', base });
    setSavedAs(`${savedId}\u0000${preset.label}`);
  };

  const provenance = preset?.source === 'mine'
    ? 'My drivers'
    : detail?.source.file ?? (manual ? 'Typed by hand' : null);

  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={dialog} className="settings-dialog driver-sheet" role="dialog" aria-modal="true" aria-labelledby={`driver-sheet-title-${channel.id}`}>
      <header>
        <div>
          <h2 id={`driver-sheet-title-${channel.id}`}>{nameEditable
            ? <input
                type="text"
                className="driver-name-input"
                aria-label={`Driver name for ${channel.id}`}
                placeholder={MANUAL_DRIVER_LABEL}
                value={nameDraft ?? preset.label}
                onChange={(event) => rename(event.target.value)}
              />
            : preset?.label ?? `Driver · ${channel.id}`}</h2>
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
          {sheetFields.map(({ driverKey, label, unit, step, reveal }) => <label
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
        {driverShortfallText(form) && <p className="cad-driver-hint">Still needed: {driverShortfallText(form)}.</p>}
      </div>
      <footer className="driver-sheet-actions">
        {/* Nothing to reset to: a hand-entered driver has no database row
            behind it, so the button is absent rather than permanently dead. */}
        {!manual && <button
          type="button"
          disabled={!preset || edited.length === 0}
          onClick={() => state.clearChannelDriverOverrides(channel.id)}
        >Reset to database values</button>}
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
      {preset.source === 'manual' && <span className="driver-chip">manual</span>}
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
          onPick={(picked, options) => {
            state.setChannelDriverPreset(channel.id, picked);
            // A hand-entered driver arrives with nothing in it, so the sheet is
            // where choosing it has to land: the card alone has no fields.
            if (options?.edit) setSheetOpen(true);
          }}
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
