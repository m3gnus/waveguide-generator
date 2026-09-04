"""In-memory index over the driver library's CSV files.

Rebuilds itself whenever the file set or any file's mtime changes, so a
dropped-in CSV is visible without restarting the server. Rows that share a
brand and model but differ only in impedance become one driver record with
a ``variants`` list (CADLINK-CROSSOVER-DRIVERS.md §4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import csv
import logging
import math
from pathlib import Path
import re

from server.drivers.paths import bundled_library_dir
from server.drivers.csv_loader import (
    build_display,
    build_source,
    build_spec,
    classify_completeness,
    classify_kind,
    classify_size,
    parse_row,
)


logger = logging.getLogger(__name__)

#: "not given" for the bundled folder, which is different from "turned off".
_UNSET: Path = Path("\x00unset")

_COMPLETENESS_RANK = {"full": 0, "partial": 1, "catalogue": 2}
_WORD_SPLIT = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]")

#: The order the picker's type filter lists them in, so a caller can render the
#: breakdown without deciding on one of its own.
KIND_ORDER = ("lf", "cd", "unknown")


@dataclass(frozen=True, slots=True)
class DriverVariant:
    id: str
    z_ohm: float | None
    kind: str
    completeness: str
    fields: dict[str, float | str | None]
    extras: dict[str, str]
    spec: dict[str, float]
    display: dict[str, float | None]
    xo_min_hz: float | None
    source: dict[str, float | str | None]


@dataclass(frozen=True, slots=True)
class DriverRecord:
    brand: str
    model: str
    kind: str
    size: str | None
    variants: list[DriverVariant] = field(default_factory=list)
    #: ``brand model`` normalised once, at index time. The search matches every
    #: record on every request -- it has to count the matches the type filter
    #: is hiding, not just the ones it lets through -- so tokenising per request
    #: would mean re-splitting a thousand names on every keystroke.
    tokens: tuple[str, ...] = ()

    @property
    def primary(self) -> DriverVariant:
        return self.variants[0]

    def usable_variants(self) -> list[DriverVariant]:
        """The windings that carry enough Thiele-Small data to be driven.

        ``full`` is exactly ``DriverSpec``'s requirement -- Sd, Bl, Re, a mass
        and a compliance -- so a variant outside this list cannot drive a
        channel at all: WG drops it on the way to the wire and the run comes
        back with no power, current or excursion.
        """

        return [variant for variant in self.variants if variant.completeness == "full"]

    def variant_for(
        self, z_ohm: float | None, *, among: list[DriverVariant] | None = None
    ) -> DriverVariant | None:
        candidates = self.variants if among is None else among
        if not candidates:
            return None
        if z_ohm is not None:
            for variant in candidates:
                if variant.z_ohm is not None and math.isclose(
                    variant.z_ohm, z_ohm, rel_tol=1e-6, abs_tol=1e-6
                ):
                    return variant
        return candidates[0]


def _normalize_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for word in _WORD_SPLIT.split(text.strip()):
        cleaned = _NON_ALNUM.sub("", word.lower())
        if cleaned:
            tokens.append(cleaned)
    return tokens


def _match_score(query_tokens: list[str], driver_tokens: list[str]) -> float | None:
    """None means "does not match"; otherwise higher is a better match."""

    if not query_tokens:
        return 0.0
    total = 0.0
    for query_token in query_tokens:
        best: float | None = None
        for driver_token in driver_tokens:
            if driver_token == query_token:
                candidate = 2.0
            elif driver_token.startswith(query_token):
                candidate = 1.0
            elif query_token in driver_token:
                # Mid-word fragments are how people abbreviate drivers --
                # "ndl" for a 12NDL76, "lw" for a 15LW1400 -- so a substring
                # matches, ranked under any prefix hit.
                candidate = 0.5
            else:
                continue
            if best is None or candidate > best:
                best = candidate
        if best is None:
            return None
        total += best
    return total


def _format_z(z_ohm: float) -> str:
    if float(z_ohm).is_integer():
        return str(int(z_ohm))
    text = f"{z_ohm:.4f}".rstrip("0").rstrip(".")
    return text


def _make_id(brand: str, model: str, z_ohm: float | None, used_ids: set[str]) -> str:
    z_part = _format_z(z_ohm) if z_ohm is not None else "unk"
    base = f"{brand}::{model}::{z_part}"
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}#{suffix}"
        suffix += 1
    return candidate


def _hit_payload(
    record: DriverRecord,
    variant: DriverVariant,
    variants: list[DriverVariant] | None = None,
) -> dict[str, object]:
    """``variants`` narrows the impedance list the caller is offered.

    The picker turns that list into its winding buttons, so a filtered search
    has to filter it too -- otherwise the row says "8|16 ohm" and switching to
    the 16 is switching to a driver the search just refused to show.
    """

    listed = record.variants if variants is None else variants
    return {
        "id": variant.id,
        "brand": record.brand,
        "model": record.model,
        "z_ohm": variant.z_ohm,
        "variants": [{"id": v.id, "z_ohm": v.z_ohm} for v in listed],
        "kind": record.kind,
        "size": record.size,
        "completeness": variant.completeness,
        "spec": dict(variant.spec),
        "display": dict(variant.display),
        "xo_min_hz": variant.xo_min_hz,
        "source": dict(variant.source),
    }


def _detail_payload(
    record: DriverRecord,
    variant: DriverVariant,
    variants: list[DriverVariant] | None = None,
) -> dict[str, object]:
    payload = _hit_payload(record, variant, variants)
    payload["fields"] = dict(variant.fields)
    payload["extras"] = dict(variant.extras)
    return payload


@dataclass(frozen=True, slots=True)
class _Row:
    """One CSV line, with where it was read from."""

    filename: str
    bundled: bool
    fields: dict[str, float | str | None]
    extras: dict[str, str]


def _user_rows_win(entries: list[_Row]) -> list[_Row]:
    """Drop a shipped winding the user has a row of their own for.

    Same brand, same model, same impedance is the same driver, and the copy
    that matters is the one whose numbers this person has checked or corrected
    -- otherwise picking their own driver means choosing between two identical
    buttons, one of which quietly ignores their edits. Two rows in the *user's*
    own files are left alone: duplicates there are theirs to keep.
    """

    owned = {
        row.fields.get("z_ohm") for row in entries if not row.bundled
    }
    return [row for row in entries if not row.bundled or row.fields.get("z_ohm") not in owned]


def _build_index(
    paths: list[Path], bundled: Path | None = None
) -> tuple[list[DriverRecord], list[dict[str, object]]]:
    groups: dict[tuple[str, str], list[_Row]] = {}
    display_names: dict[tuple[str, str], tuple[str, str]] = {}
    file_stats: list[dict[str, object]] = []

    for path in paths:
        is_bundled = bundled is not None and path.parent == bundled
        rows = 0
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for raw_row in reader:
                    rows += 1
                    fields, extras = parse_row(raw_row)
                    brand = fields.get("brand")
                    model = fields.get("model")
                    if not isinstance(brand, str) or not isinstance(model, str):
                        continue
                    key = (brand.casefold(), model.casefold())
                    groups.setdefault(key, []).append(_Row(path.name, is_bundled, fields, extras))
                    display_names.setdefault(key, (brand, model))
        except OSError as exc:
            logger.warning("Could not read driver library file %s: %s", path, exc)
        file_stats.append({"name": path.name, "rows": rows, "bundled": is_bundled})

    records: list[DriverRecord] = []
    used_ids: set[str] = set()
    for key, entries in groups.items():
        brand, model = display_names[key]
        variants: list[DriverVariant] = []
        for row in _user_rows_win(entries):
            filename, fields, extras = row.filename, row.fields, row.extras
            z_ohm = fields.get("z_ohm")
            z_value = float(z_ohm) if isinstance(z_ohm, (int, float)) else None
            variant_id = _make_id(brand, model, z_value, used_ids)
            used_ids.add(variant_id)
            variants.append(
                DriverVariant(
                    id=variant_id,
                    z_ohm=z_value,
                    kind=classify_kind(fields),
                    completeness=classify_completeness(fields),
                    fields=fields,
                    extras=extras,
                    spec=build_spec(fields),
                    display=build_display(fields),
                    xo_min_hz=(
                        float(fields["xo_min_hz"])
                        if isinstance(fields.get("xo_min_hz"), (int, float))
                        else None
                    ),
                    source={**build_source(filename, fields), "bundled": row.bundled},
                )
            )
        variants.sort(key=lambda v: (v.z_ohm is None, v.z_ohm if v.z_ohm is not None else 0.0))
        if any(v.kind == "cd" for v in variants):
            group_kind = "cd"
        elif any(v.kind == "lf" for v in variants):
            group_kind = "lf"
        else:
            group_kind = "unknown"
        primary = variants[0]
        records.append(
            DriverRecord(
                brand=brand,
                model=model,
                kind=group_kind,
                size=classify_size(primary.fields, group_kind),
                variants=variants,
                tokens=tuple(_normalize_tokens(f"{brand} {model}")),
            )
        )

    records.sort(key=lambda r: (r.brand.casefold(), r.model.casefold()))
    return records, file_stats


class DriverLibrary:
    """The searchable index over the driver CSVs this installation can read.

    Two sources, and only one of them is the user's: the folder they drop
    files into, and the library that ships with the application. Both are
    indexed together so a fresh install can pick a real driver instead of
    meeting an empty search box, and ``folder`` still means the writable one --
    it is the path Settings shows and offers to open.
    """

    def __init__(self, folder: Path, *, bundled: Path | None = _UNSET) -> None:
        self.folder = Path(folder)
        # Explicit ``None`` turns the shipped library off, which is what a test
        # of the user's own folder wants; the default finds whatever shipped.
        self.bundled = bundled_library_dir() if bundled is _UNSET else (
            None if bundled is None else Path(bundled)
        )
        self._records: list[DriverRecord] = []
        self._by_id: dict[str, tuple[DriverRecord, DriverVariant]] = {}
        self._file_stats: list[dict[str, object]] = []
        self._mtimes: dict[str, tuple[int, int]] = {}
        self._last_scan: str | None = None
        self._scanned_once = False

    def _sources(self) -> list[Path]:
        """Where CSVs are read from, the user's folder first.

        Order is the tie-breaker nothing else states: the first file to name a
        brand and model owns how that driver is spelled on screen.
        """

        folders = [self.folder]
        if self.bundled is not None and self.bundled != self.folder:
            folders.append(self.bundled)
        return folders

    def _current_files(self) -> dict[str, tuple[int, int]]:
        """Every readable CSV and its change fingerprint, keyed by full path.

        Keyed by path rather than name so a file the user happens to call
        ``hornlab-drivers.csv`` cannot mask the shipped one out of the change
        detector and leave the index stale.

        The fingerprint is ``(mtime_ns, size)``, not a float mtime. Two edits
        inside one filesystem timestamp tick are indistinguishable by mtime
        alone, and the tick is not always small: Windows CI reproduced this by
        rewriting a shipped CSV immediately after the first scan and getting an
        identical ``st_mtime`` back, so the rescan never fired and the index
        silently served the old rows. FAT-family and some network filesystems
        are coarser still -- two seconds on FAT32 -- and this app's data
        directory is allowed to live on a network share. ``st_mtime_ns`` avoids
        the float rounding, and the size catches a same-tick edit that changes
        the file's length. A same-tick edit that preserves length exactly is
        still missed; catching that needs content hashing, which is not worth
        reading every CSV on every request.
        """

        stats: dict[str, tuple[int, int]] = {}
        for folder in self._sources():
            if not folder.is_dir():
                continue
            try:
                entries = sorted(folder.iterdir())
            except OSError:
                continue
            for path in entries:
                if path.is_file() and path.suffix.lower() == ".csv":
                    try:
                        info = path.stat()
                    except OSError:
                        continue
                    stats[str(path)] = (info.st_mtime_ns, info.st_size)
        return stats

    def ensure_indexed(self) -> None:
        if not self._scanned_once or self._current_files() != self._mtimes:
            self.rescan()

    def rescan(self) -> dict[str, object]:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Could not create driver library folder %s: %s", self.folder, exc)
        current = self._current_files()
        records, file_stats = _build_index([Path(name) for name in current], self.bundled)
        self._records = records
        self._by_id = {
            variant.id: (record, variant) for record in records for variant in record.variants
        }
        self._file_stats = file_stats
        self._mtimes = current
        self._scanned_once = True
        self._last_scan = datetime.now(timezone.utc).isoformat()
        return self.info()

    def _kind_counts(self) -> list[dict[str, object]]:
        """How many drivers of each type the index holds, and how many are drivable.

        The picker filters by type, so without this the only honest thing it can
        say about a type it has no match for is nothing. The shipped library is
        1,045 cone drivers and one compression driver, and a horn designer whose
        search starts on the compression half needs to be told that *before*
        concluding the database is empty -- the breakdown is what turns "no
        results" into "one compression driver exists, and 1,045 cone ones".

        Derived from the records already in memory, so it costs a pass over the
        index rather than any extra file reading.
        """

        totals: dict[str, int] = {}
        complete: dict[str, int] = {}
        for record in self._records:
            totals[record.kind] = totals.get(record.kind, 0) + 1
            if record.usable_variants():
                complete[record.kind] = complete.get(record.kind, 0) + 1
        # A type the library holds none of is left out rather than reported as a
        # zero: an empty filter button is noise, and the caller renders whatever
        # this lists. Anything the classifier grows later still appears, after
        # the two the picker knows by name.
        ordered = [kind for kind in KIND_ORDER if kind in totals]
        ordered += sorted(kind for kind in totals if kind not in KIND_ORDER)
        return [
            {"kind": kind, "total": totals[kind], "complete": complete.get(kind, 0)}
            for kind in ordered
        ]

    def info(self) -> dict[str, object]:
        return {
            "folder": str(self.folder),
            "files": list(self._file_stats),
            "total_drivers": len(self._records),
            # What the picker will actually offer. A catalogue CSV can index
            # thousands of rows and still be able to drive nothing, so a lone
            # total reads as a library that works when it does not.
            "complete_drivers": sum(
                1 for record in self._records if record.usable_variants()
            ),
            "kinds": self._kind_counts(),
            "last_scan": self._last_scan,
        }

    def search(
        self,
        *,
        q: str = "",
        kind: str = "all",
        z: float | None = None,
        limit: int = 20,
        complete: bool = False,
    ) -> list[dict[str, object]]:
        page = self.search_page(q=q, kind=kind, z=z, limit=limit, complete=complete)
        return page["items"]  # type: ignore[return-value]

    def search_page(
        self,
        *,
        q: str = "",
        kind: str = "all",
        z: float | None = None,
        limit: int = 20,
        complete: bool = False,
    ) -> dict[str, object]:
        """Ranked matches, how many were withheld, and what the other types hold.

        ``hidden_incomplete`` is what keeps ``complete`` from reading as a broken
        library: most compression-driver rows are catalogue entries with no motor
        data at all, so a filtered search over them comes back empty, and the
        caller needs to be able to say why rather than just showing nothing.

        ``matches_by_kind`` is the same argument applied to the type filter,
        which is the one that actually dead-ends people. The shipped library has
        a thousand cone drivers and one compression driver, so a search that
        starts on the compression half answers nothing for almost every query --
        and the useful thing to say is not "no matches" but "none of this type,
        seven of the other". It counts what each type *would* offer, so it is
        the same rule as ``items``: query, ``z`` and ``complete`` all apply, and
        only the type filter is lifted.
        """

        self.ensure_indexed()
        query_tokens = _normalize_tokens(q or "")
        ranked: list[tuple[tuple[float, bool, int, str, str], dict[str, object]]] = []
        hidden = 0
        matches_by_kind: dict[str, int] = {name: 0 for name in KIND_ORDER}
        for record in self._records:
            in_kind = kind == "all" or record.kind == kind
            score = _match_score(query_tokens, list(record.tokens))
            if score is None:
                continue
            listed = record.usable_variants() if complete else None
            variant = record.variant_for(z, among=listed)
            if variant is None:
                # Matched the query, but no winding of it can drive a channel.
                # It is not counted as a match of its type either: the point of
                # that count is what the caller could offer instead, and this is
                # a driver nothing can offer.
                if in_kind:
                    hidden += 1
                continue
            matches_by_kind[record.kind] = matches_by_kind.get(record.kind, 0) + 1
            if not in_kind:
                continue
            z_match = (
                z is not None
                and variant.z_ohm is not None
                and math.isclose(variant.z_ohm, z, rel_tol=1e-6, abs_tol=1e-6)
            )
            sort_key = (
                -score,
                not z_match,
                _COMPLETENESS_RANK[variant.completeness],
                record.brand.casefold(),
                record.model.casefold(),
            )
            ranked.append((sort_key, _hit_payload(record, variant, listed)))
        ranked.sort(key=lambda item: item[0])
        return {
            "items": [payload for _, payload in ranked[:limit]],
            "hidden_incomplete": hidden,
            "matches_by_kind": matches_by_kind,
        }

    def get(self, driver_id: str, *, complete: bool = False) -> dict[str, object] | None:
        self.ensure_indexed()
        entry = self._by_id.get(driver_id)
        if entry is None:
            return None
        record, variant = entry
        listed = None
        if complete:
            # The winding that was asked for by id stays listed whatever its
            # own data looks like: it is already on a channel, and dropping it
            # from its own variant list would leave the picker showing buttons
            # with none of them selected.
            usable = record.usable_variants()
            listed = [v for v in record.variants if v is variant or v in usable]
        return _detail_payload(record, variant, listed)


__all__ = ["KIND_ORDER", "DriverLibrary", "DriverRecord", "DriverVariant"]
