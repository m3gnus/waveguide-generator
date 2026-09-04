import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import {
  getDriver,
  searchDrivers,
  type DriverDetail,
  type DriverHit,
  type DriverKind,
  type DriverSearchKind,
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
import { Icon } from '../shell/icons';
import {
  CAD_CONTROLS,
  CAD_DRIVER_FIELD_CONTROLS,
  CAD_DRIVER_SHEET_FIELDS,
} from './cadControlRegistry';
import { driverDerivedValues, driverValuesDisagree } from './driverDerived';
import {
  driverCountText,
  driverEmptyState,
  driverKindCounts,
  driverKindLabel,
  driverKindTotal,
  openingSearchKind,
  type DriverKindTally,
} from './driverLibraryCounts';


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

/**
 * The type filter, with what each setting holds written on it.
 *
 * The counts are the whole point. This library is a thousand cone drivers and
 * one compression driver, and a filter that says only "Compression" invites a
 * user to search inside it, find nothing, and conclude the database is empty.
 * A filter that says "Compression 1" has already answered them, before they
 * type -- and "All 1,046" beside it is the way on. Counts are dropped rather
 * than guessed when the server did not send a breakdown.
 */
function KindToggle({ kind, counts, onChange, channelId }: {
  kind: DriverSearchKind;
  counts: DriverKindTally;
  onChange: (kind: DriverSearchKind) => void;
  channelId: string;
}) {
  return <div className="driver-kind-toggle" role="group" aria-label={`Driver type for ${channelId}`}>
    {(['lf', 'cd', 'all'] as const).map((option) => {
      const held = driverKindTotal(counts, option);
      return <button
        key={option}
        type="button"
        className={kind === option ? 'on' : ''}
        aria-pressed={kind === option}
        aria-label={counts.known ? `${driverKindLabel(option)}, ${driverCountText(held, option)}` : undefined}
        onClick={() => onChange(option)}
      >
        {driverKindLabel(option)}
        {counts.known && <b>{held.toLocaleString()}</b>}
      </button>;
    })}
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
  // Every impedance this driver comes in, not just the variant the search
  // pre-selected: the variant buttons live in the T/S sheet, so without this
  // the row is the only place the user looks and "8 Ω exists too" is invisible.
  const impedances = hit.variants
    .map((variant) => variant.z_ohm)
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  const zBadge = impedances.length > 1
    ? `${impedances.map((value) => formatNumber(value, 3)).join('|')} Ω`
    : null;
  const facts = [
    hit.size,
    zBadge === null ? impedanceText(hit.z_ohm) : null,
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
    {zBadge && <span className="driver-chip">{zBadge}</span>}
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

function DriverSearch({ channel, roleHint, counts, onPick }: {
  channel: CadDriveChannel;
  roleHint: string | undefined;
  counts: DriverKindTally;
  onPick: (preset: DriverPreset, options?: { edit?: boolean }) => void;
}) {
  const saved = useDriverLibraryStore((state) => state.saved);
  // The type a driver typed by hand into this channel is, whatever the filter
  // happens to be pointed at: the filter is a way of reading the library, the
  // channel's own role is what the driver actually is.
  const preferredKind = useMemo(
    () => defaultDriverKind(channel.id, roleHint),
    [channel.id, roleHint],
  );
  const [kind, setKind] = useState<DriverSearchKind>(() => openingSearchKind(preferredKind, counts));
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [hits, setHits] = useState<DriverHit[]>([]);
  // Matches the library has but cannot drive a channel with. Without this
  // number a search for a catalogue-only compression driver comes back empty
  // and reads as a broken library rather than as missing datasheet numbers.
  const [hiddenIncomplete, setHiddenIncomplete] = useState(0);
  // What every type would have answered, so an empty result can name the one
  // that would have. Empty until a search has run.
  const [matchesByKind, setMatchesByKind] = useState<Partial<Record<DriverKind, number>>>({});
  const [activeIndex, setActiveIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const input = useRef<HTMLInputElement>(null);
  // The counts normally arrive before this component mounts at all -- the card
  // shows the manual grid until the library report has landed -- but the
  // opening filter is chosen from them, so a build that renders the search
  // earlier must not be left seeding it from nothing. Re-seeding is therefore
  // allowed exactly once, and never after the user has touched the filter
  // themselves: their choice outranks any count that arrives later.
  const settled = useRef(counts.known);
  const chooseKind = useCallback((next: DriverSearchKind) => {
    settled.current = true;
    setKind(next);
  }, []);
  useEffect(() => {
    if (settled.current || !counts.known) return;
    settled.current = true;
    setKind(openingSearchKind(preferredKind, counts));
  }, [counts, preferredKind]);

  useEffect(() => {
    if (!open) return;
    const request = ++generation.current;
    void searchDrivers({ q: query, kind, limit: 40 }).then(
      (result) => {
        if (request !== generation.current) return;
        setHits(result.items);
        setHiddenIncomplete(result.hiddenIncomplete);
        setMatchesByKind(result.matchesByKind);
        setError(null);
      },
      (reason: unknown) => {
        if (request !== generation.current) return;
        setHits([]);
        setHiddenIncomplete(0);
        setMatchesByKind({});
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
      .filter((driver) => savedDriverMatches(driver, query)
        && (kind === 'all' || driver.kind === kind || driver.kind === 'unknown'))
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
    preset: () => manualDriverPreset(query, kind === 'all' ? preferredKind : kind),
    render: ({ active, id, onPick: pick, onHover }) =>
      <ManualResultRow key="manual" query={query} active={active} id={id} onPick={pick} onHover={onHover}/>,
  }], [kind, matches, preferredKind, query]);

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

  // The true match count, not the page: the search asks for forty rows, and
  // "40 drivers" where there are six hundred is a worse answer than none. Never
  // below the rows actually returned, which is also what keeps a server that
  // sends no breakdown from being reported as nought matches above real hits.
  const matchTotal = Math.max(
    kind === 'all'
      ? Object.values(matchesByKind).reduce((sum, count) => sum + count, 0)
      : matchesByKind[kind] ?? 0,
    hits.length,
  );
  const empty = !error && !matches.length
    ? driverEmptyState({ query, kind, counts, matchesByKind, hiddenIncomplete })
    : null;
  const escape = empty?.action ?? null;

  return <div className="driver-search" data-control-reveal-id={CAD_CONTROLS.driverSearch.reveal.id}>
    <div className="driver-search-row">
      <input
        ref={input}
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
    </div>
    {/* What the library holds, on the control that filters by it, before a
        single keystroke. A user who never opens the dropdown still learns that
        the compression half is one driver deep and where the rest of them are. */}
    <KindToggle kind={kind} counts={counts} channelId={channel.id} onChange={chooseKind}/>
    {/* The dropdown carries hand entry as its last row, but that row only
        exists once the field has been opened. A user whose driver is not in
        the library has no reason to open it, so the way in is also stated
        where it can be seen without searching first. */}
    <p className="driver-manual-line">
      Driver not in the library?
      <button
        type="button"
        className="driver-manual-link"
        onClick={() => {
          setOpen(false);
          onPick(manualDriverPreset(query, kind === 'all' ? preferredKind : kind), { edit: true });
        }}
      >Enter its T/S by hand</button>
    </p>
    {open && <div className="driver-results">
      {error && <p className="cad-driver-hint" role="status">{error}</p>}
      {!error && hits.length > 0 && <p className="driver-results-head">
        {matchTotal > hits.length
          ? `${hits.length.toLocaleString()} of ${driverCountText(matchTotal, kind)}`
          : driverCountText(matchTotal, kind)}
        {query.trim() ? ' match' : ''}
      </p>}
      {empty && <div className="empty-state driver-empty" role="status">
        <b>{empty.title}</b>
        <span>{empty.detail}</span>
        {escape && <button
          type="button"
          className="driver-empty-action"
          // Without this the input blurs, the dropdown closes, and the button
          // is gone before its own click lands.
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            if (escape.clearQuery) setQuery('');
            chooseKind(escape.kind);
            input.current?.focus();
          }}
        >{escape.label}</button>}
      </div>}
      {/* Matches the library knows and cannot drive. When they are the only
          answer the empty state above has already said so; this line is for
          when they sit behind rows that did come back. */}
      {!error && matches.length > 0 && hiddenIncomplete > 0 && <p className="cad-driver-hint" role="status">{hiddenIncomplete === 1
        ? 'One more driver matches, but the library lists no Thiele-Small data for it, so it cannot be driven.'
        : `${hiddenIncomplete} more drivers match, but the library lists no Thiele-Small data for them, so they cannot be driven.`
      } Enter the values by hand instead.</p>}
      <div id={listId} className="driver-results-list" role="listbox" aria-label={`Driver matches for ${channel.id}`}>
        {candidates.map((candidate, index) => candidate.render({
          active: index === activeIndex,
          id: `${listId}-${index}`,
          onPick: () => pick(candidate),
          onHover: () => setActiveIndex(index),
        }))}
      </div>
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
 * It expands in place under the channel it belongs to rather than opening a
 * dialog. The numbers are one channel's, and a sheet that covers the rail
 * hides both the channel it was opened from and the other channels its
 * crossover has to agree with.
 *
 * Every input writes an override rather than the value itself, so *Reset to
 * database values* is always available and an edit is always visible as one.
 *
 * A hand-entered driver goes through the same sheet with nothing underneath
 * it: the overrides are the whole driver, so there is no edit count and no
 * reset, and the name and nominal impedance are the user's to set because *My
 * drivers* has nothing else to list it under.
 */
function DriverSheet({ channel, form, onClose }: {
  channel: CadDriveChannel;
  form: ChannelDriverForm;
  onClose: () => void;
}) {
  const state = useCadReturnStore();
  const saveMine = useDriverLibraryStore((store) => store.save);
  const removeMine = useDriverLibraryStore((store) => store.remove);
  const savedDrivers = useDriverLibraryStore((store) => store.saved);
  const panel = useRef<HTMLDivElement>(null);
  const [detail, setDetail] = useState<DriverDetail | null>(null);
  const [savedAs, setSavedAs] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const [impedanceDraft, setImpedanceDraft] = useState<string | null>(null);
  const preset = form.preset;

  useEffect(() => {
    // The T/S grid, not the name field a hand-entered driver puts above it:
    // the name is already what the user typed to get here, the numbers are not.
    const frame = requestAnimationFrame(() => {
      const grid = panel.current?.querySelector<HTMLElement>('.driver-sheet-grid input');
      (grid ?? panel.current?.querySelector<HTMLElement>('input'))?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, []);

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
  // from. The nominal impedance travels with the name for the same reason: the
  // library states which winding a row is, a typed driver has to be told.
  const nameEditable = preset !== null && preset.source !== 'database';
  // A driver whose numbers are the user's own gets the whole grid, Mmd and Cms
  // included: those are what a datasheet without Mms or Vas prints, and hand
  // entry used to reach them through the no-library grid. A picked driver keeps
  // the shorter set, where a second mass beside the library's own would be an
  // invitation to state two (`channelDriverWire` sends only one either way).
  const sheetFields = nameEditable ? CAD_DRIVER_FIELD_CONTROLS : CAD_DRIVER_SHEET_FIELDS;
  const savedId = preset === null ? null : preset.source === 'mine' ? preset.id : `mine:${preset.id}`;
  const saved = savedId !== null && savedAs === `${savedId}::${preset!.label}`;
  const inMyDrivers = savedId !== null && savedDrivers.some((driver) => driver.id === savedId);

  const rename = (text: string) => {
    if (!preset) return;
    setNameDraft(text);
    // An empty field is a name in progress, not a driver with no name: the
    // stored label always stays something a reloaded profile can parse.
    state.setChannelDriverPreset(channel.id, { ...preset, label: text.trim() || MANUAL_DRIVER_LABEL }, true);
  };

  // The winding a typed driver is, which the library would otherwise have
  // stated. It names the driver rather than feeding the solve -- Re is the
  // number the motor model reads -- so it lives beside the name, not in the
  // T/S grid.
  const setImpedance = (text: string) => {
    if (!preset) return;
    setImpedanceDraft(text);
    const parsed = text.trim() === '' ? null : Number(text);
    const z = typeof parsed === 'number' && Number.isFinite(parsed) && parsed > 0 ? parsed : null;
    state.setChannelDriverPreset(channel.id, { ...preset, z_ohm: z }, true);
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
    setSavedAs(`${savedId}::${preset.label}`);
  };

  // Forgetting a saved driver leaves this channel holding it: the numbers on
  // screen are still the driver this solve uses, they are only no longer
  // offered to the next channel that searches.
  const forget = () => {
    if (!savedId) return;
    removeMine(savedId);
    setSavedAs(null);
  };

  const provenance = preset?.source === 'mine'
    ? 'My drivers'
    : detail?.source.file ?? (manual ? 'Typed by hand' : null);
  const zText = impedanceText(preset?.z_ohm);

  return <div
    ref={panel}
    id={`driver-sheet-${channel.id}`}
    className="driver-sheet"
    role="group"
    data-channel-id={channel.id}
    aria-labelledby={`driver-sheet-title-${channel.id}`}
    onKeyDown={(event) => {
      // Esc collapses the sheet. Every number is already stored as it was
      // typed, so there is nothing to discard and nothing to confirm.
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
    }}
  >
    <header>
      <h2 id={`driver-sheet-title-${channel.id}`}>{nameEditable
        ? <input
            type="text"
            className="driver-name-input"
            aria-label={`Driver name for ${channel.id}`}
            placeholder={MANUAL_DRIVER_LABEL}
            value={nameDraft ?? preset.label}
            onChange={(event) => rename(event.target.value)}
          />
        : preset?.label ?? `Driver for ${channel.id}`}</h2>
      <div className="driver-sheet-chips">
        {!nameEditable && zText && <span className="driver-chip">{zText}</span>}
        {provenance && <span className="driver-chip">{provenance}</span>}
        {edited.length > 0 && <span className="driver-chip accent">{edited.length} edited</span>}
      </div>
      <button className="dialog-close" aria-label="Close driver sheet" onClick={onClose}><Icon name="close"/></button>
    </header>
    <div className="driver-sheet-body">
      {variants.length > 1 && <div className="driver-variants" role="group" aria-label="Impedance variant">
        {variants.map((variant) => <button
          key={variant.id}
          type="button"
          className={variant.id === preset?.id ? 'on' : ''}
          aria-pressed={variant.id === preset?.id}
          onClick={() => void chooseVariant(variant.id)}
        >{impedanceText(variant.z_ohm) ?? 'unknown Ω'}</button>)}
      </div>}
      {nameEditable && <label className="cad-driver-field driver-sheet-impedance">
        <span>Nominal Z (Ω)</span>
        <input
          type="number"
          min={0}
          step={0.5}
          value={impedanceDraft ?? (preset.z_ohm === null ? '' : String(preset.z_ohm))}
          aria-label={`Nominal impedance for ${channel.id}`}
          onChange={(event) => setImpedance(event.target.value)}
        />
        <small>Which winding this driver is, for the saved list. The solve reads Re.</small>
      </label>}
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
      {inMyDrivers && <button type="button" onClick={forget}>Remove from My drivers</button>}
      <button
        type="button"
        className={saved ? '' : 'primary'}
        disabled={!preset || saved}
        onClick={saveToMine}
      >{saved ? 'Saved' : 'Save to My drivers'}</button>
      <button type="button" onClick={onClose}>Done</button>
    </footer>
  </div>;
}

/** The picked driver, as the channel card shows it. */
function DriverSummary({ channel, form, sheetOpen, onEdit, onClear }: {
  channel: CadDriveChannel;
  form: ChannelDriverForm;
  sheetOpen: boolean;
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
        aria-expanded={sheetOpen}
        aria-controls={`driver-sheet-${channel.id}`}
        data-control-reveal-id={CAD_CONTROLS.driverEdit.reveal.id}
        onClick={onEdit}
      >{sheetOpen ? 'Hide T/S' : 'Edit T/S…'}</button>
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
 * One channel's driver: search, the picked driver, and the T/S sheet that
 * expands under it.
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
  const counts = useMemo(() => driverKindCounts(info), [info]);
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
          counts={counts}
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
          sheetOpen={sheetOpen}
          onEdit={() => setSheetOpen((open) => !open)}
          onClear={() => {
            // The sheet belongs to the driver that was cleared: leaving it
            // expanded would show an empty grid under a channel that is back
            // to its search field.
            setSheetOpen(false);
            state.setChannelDriverPreset(channel.id, null);
          }}
        />}
    {sheetOpen && preset !== null && form
      && <DriverSheet channel={channel} form={form} onClose={() => setSheetOpen(false)}/>}
  </div>;
}
