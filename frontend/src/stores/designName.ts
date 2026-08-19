/**
 * The one name a design has.
 *
 * WG used to keep four: the document filename, a global `outputName`
 * preference the run list was labelled from, each job's own title, and ATH's
 * `Report.Title` -- which nothing read or wrote, so an imported file carried
 * its original ATH name forever. Renaming in one place moved one of the four,
 * and the same design answered to three different names depending on where you
 * looked.
 *
 * There is now a single `designName` on the document store. Everything else is
 * derived here: the `.cfg` filename, every export stem, the CAD-link name, and
 * the `Report.Title` written into the file so the name survives a round trip
 * and ATH's own reports agree with WG's.
 */
import type { ConfigBlock } from './design';

/** What an unnamed design is called in chrome, and in a filename. */
export const UNTITLED_DESIGN = 'Untitled';
export const UNTITLED_SLUG = 'untitled';

/** Long enough for any real horn name, short enough to stay a filename. */
const MAX_DESIGN_NAME = 120;

/**
 * A name that can be carried by both a filename and a quoted config value.
 *
 * Quotes would terminate `Title = "..."` early, a semicolon would turn the
 * rest of the line into an ATH comment, and braces would read as block syntax
 * -- the server rejects all three on the way out, so they are removed here
 * rather than allowed to fail a save.
 */
export function normalizeDesignName(value: unknown): string {
  return String(value ?? '')
    .replace(/["{};]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, MAX_DESIGN_NAME)
    .trim();
}

/**
 * The portable stem every artifact for this design is named with.
 *
 * One implementation, shared by the `.cfg` filename and the run exports, which
 * used to slug independently and could disagree about the same typed name.
 */
export function designNameSlug(name: unknown, fallback = UNTITLED_SLUG): string {
  const slug = String(name ?? '')
    .trim()
    .normalize('NFKD')
    .replace(/\p{Mark}+/gu, '')
    .replace(/[^A-Za-z0-9._-]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^[._-]+|[._-]+$/g, '');
  return slug || fallback;
}

/**
 * The file this design saves as.
 *
 * A name that already ends in a config extension loses it rather than gaining
 * a second one: "horn.cfg" typed into the name field is a filename the user
 * pasted, not a design called "horn.cfg".
 */
export function designFilename(name: string): string {
  const stem = designNameSlug(name).replace(/\.(cfg|txt|mwg)$/i, '');
  return `${stem || UNTITLED_SLUG}.cfg`;
}

/** The design name a picked file carries, in the file's own spelling. */
export function designNameFromFilename(filename: string): string {
  const basename = String(filename ?? '').replace(/^.*[\\/]/, '');
  return normalizeDesignName(basename.replace(/\.(cfg|txt|mwg)$/i, ''));
}

const REPORT_BLOCK = 'Report';
const TITLE_ENTRY = /^\s*Title\s*=/;

function unquote(value: string): string {
  const trimmed = value.trim();
  return trimmed.length >= 2 && trimmed.startsWith('"') && trimmed.endsWith('"')
    ? trimmed.slice(1, -1)
    : trimmed;
}

/** The name a `.cfg` states for itself, or '' when it states none. */
export function designTitleFromBlocks(blocks: Record<string, ConfigBlock> | undefined): string {
  return normalizeDesignName(unquote(String(blocks?.[REPORT_BLOCK]?.items?.Title ?? '')));
}

/**
 * The name a just-opened file should carry.
 *
 * The filename wins, because that is the name the user manages and sees in
 * their file browser, and a stale `Title` from whatever the file was called in
 * ATH is exactly the divergence this module exists to end. The one exception
 * is a `Title` that slugs to the same stem: then it is this same name in a
 * richer spelling -- "ATH Tritonia-M" for `ATH_Tritonia-M.cfg` -- and keeping
 * it is what makes the space survive a save-and-reopen.
 */
export function designNameForOpenedFile(
  filename: string,
  blocks: Record<string, ConfigBlock> | undefined,
): string {
  const fromFilename = designNameFromFilename(filename);
  const title = designTitleFromBlocks(blocks);
  return title && designNameSlug(title) === designNameSlug(fromFilename) ? title : fromFilename;
}

/**
 * State the design's name inside the file, first in its `Report` block.
 *
 * A parsed block replays its verbatim `entries` rather than its parsed items,
 * so the raw `Title` row has to be rewritten too or the old name would survive
 * byte-for-byte. Other `Report` keys -- ATH's own report settings -- are left
 * exactly as they were imported.
 */
export function blocksWithDesignTitle(
  blocks: Record<string, ConfigBlock> | undefined,
  name: string,
): Record<string, ConfigBlock> {
  const clean = normalizeDesignName(name);
  const existing = blocks ?? {};
  if (!clean) return existing;
  const report = existing[REPORT_BLOCK];
  const value = `"${clean}"`;
  const row = `Title = ${value}`;
  const entries = report?.entries ?? [];
  const { Title: _replaced, ...otherItems } = report?.items ?? {};
  const next: ConfigBlock = {
    items: { Title: value, ...otherItems },
    lines: report?.lines ?? [],
    comments: report?.comments ?? [],
    entries: entries.length
      ? (entries.some((entry) => TITLE_ENTRY.test(entry))
        ? entries.map((entry) => (TITLE_ENTRY.test(entry) ? row : entry))
        : [row, ...entries])
      : [],
  };
  // Report leads, so the name is the first block in the file the server writes.
  return {
    [REPORT_BLOCK]: next,
    ...Object.fromEntries(Object.entries(existing).filter(([blockName]) => blockName !== REPORT_BLOCK)),
  };
}
