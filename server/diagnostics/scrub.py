"""Rewrite the reporter's identity out of a diagnostics report.

Every path WG logs runs through the user's home directory, so a raw
``server.log`` names them in most of its lines: the data directory, every
workspace adoption, every traceback's file list. A bug report that cannot be
sent without disclosing who sent it is a bug report that does not get sent.

What this removes is the *identity*, not the structure. ``C:\\Users\\ada\\
Documents\\Horns`` becomes ``~\\Documents\\Horns``: the user name is gone and
the shape of the path -- its depth, its length, whether it sits on a synced
drive -- survives, because that shape is what several real defects are made of.
Anyone who names a folder after the client it belongs to is still disclosing
that name, which is why the dialog says what the report contains and the
manifest lists it.

Deliberately textual and deliberately dumb. It runs over log text as well as
over decoded JSON, and a rule that tried to understand path syntax would miss
the half of the corpus that is prose.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import os
from pathlib import Path
import platform
import re
from typing import Any


#: What a scrubbed home directory becomes. ``~`` rather than a redaction bar
#: because the report is read by a person diagnosing a path, and ``~`` still
#: reads as a path.
HOME_PLACEHOLDER = "~"

#: Environment variables whose *values* are locations rather than settings, and
#: so get the same treatment as the home directory itself.
LOCATION_VARIABLES = ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME")


class ScrubRules:
    """Compiled substitutions, ordered so the longest root wins.

    Order matters and is not alphabetical: ``APPDATA`` lives inside
    ``USERPROFILE`` on Windows, and applying the shorter root first would leave
    the longer rule with nothing to match. Longest-first also means a rule can
    never eat the prefix another rule needed.
    """

    def __init__(self, replacements: Iterable[tuple[str, str]], *, ignore_case: bool) -> None:
        flags = re.IGNORECASE if ignore_case else 0
        compiled = []
        for root, replacement in sorted(replacements, key=lambda item: -len(item[0])):
            pattern = _path_pattern(root)
            # An empty pattern matches at every position, so a root that
            # reduced to nothing -- ``/``, ``C:\``, or an unset variable read
            # back as ``Path("")`` -- would rewrite the entire report into
            # placeholders. Refusing it is the difference between scrubbing a
            # report and destroying one.
            if pattern:
                compiled.append((re.compile(pattern, flags), replacement))
        self._rules = tuple(compiled)

    def __bool__(self) -> bool:
        return bool(self._rules)

    def apply(self, text: str) -> str:
        for pattern, replacement in self._rules:
            text = pattern.sub(replacement, text)
        return text


def _path_pattern(root: str) -> str:
    """Match ``root`` however it was written into the text being scrubbed.

    One directory separator in a log is not one character. The same path is
    written ``C:\\Users\\ada`` by ``pathlib``, ``C:/Users/ada`` by anything that
    went through a URL or a config file, and ``C:\\\\Users\\\\ada`` by every
    line that was JSON-encoded before it was logged. ``[\\\\/]+`` covers all
    three, which is why this cannot simply be ``re.escape(root)``.
    """

    segments = [segment for segment in re.split(r"[\\/]+", root) if segment]
    if not segments:
        return ""
    body = r"[\\/]+".join(re.escape(segment) for segment in segments)
    # A POSIX root is absolute only because of its leading separator, so the
    # pattern has to carry it.
    leading = r"[\\/]+" if root[:1] in "\\/" else ""
    # ...and carrying it is what makes ``/home/ada`` match inside
    # ``/opt/home/ada``, which is a different directory belonging to nobody in
    # particular. The lookbehind requires the root to start where a path
    # starts: at the beginning of the text, or after a space, a quote, a
    # bracket -- anything that is not itself part of a path.
    return r"(?<![A-Za-z0-9_.\\/])" + leading + body


#: Absolute by syntax, not by ``Path.is_absolute``.
#:
#: ``Path`` answers for the host it runs on, so on Linux -- which is most of
#: the CI matrix -- ``Path(r"C:\Users\Ada").is_absolute()`` is False and every
#: Windows rule would be silently discarded on the platform that tests them.
_ABSOLUTE_ROOT = re.compile(r"^(?:[\\/]|[A-Za-z]:[\\/])")


def _usable_root(value: str) -> str:
    """Accept a root only if rewriting it could not swallow the whole report.

    ``Path("")`` is ``.``, an unset ``HOME`` is ``""``, and a container can
    genuinely report ``/`` as a home directory. Each of those is a rule that
    matches nearly everything, so the bar is: absolute, and naming at least one
    directory below its filesystem root.
    """

    candidate = value.strip()
    if not candidate or not _ABSOLUTE_ROOT.match(candidate):
        return ""
    segments = [segment for segment in re.split(r"[\\/]+", candidate) if segment]
    if not segments:  # a bare "/" or "\", which names nothing at all
        return ""
    named = len(segments) - (1 if re.fullmatch(r"[A-Za-z]:", segments[0]) else 0)
    return candidate.rstrip("\\/") if named >= 1 else ""


def scrub_rules(
    *,
    home: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    system: str | None = None,
) -> ScrubRules:
    """Build the substitutions for this machine.

    The keyword arguments exist so the suite can compile a Windows rule set on
    Linux and vice versa: the case sensitivity and the roots both differ, and a
    scrubber that is only ever tested against the host it runs on is a scrubber
    whose Windows behaviour is untested everywhere the CI matrix is green.
    """

    env = os.environ if environ is None else environ
    os_name = platform.system() if system is None else system
    roots: list[tuple[str, str]] = []

    home_dir = _usable_root(str(Path(home) if home is not None else Path.home()))
    if home_dir:
        roots.append((home_dir, HOME_PLACEHOLDER))
    for name in LOCATION_VARIABLES:
        value = _usable_root(env.get(name) or "")
        if not value:
            continue
        # Anything under the home directory is already covered by the rule
        # above, and adding it again would only produce ``%APPDATA%`` where
        # ``~/AppData/Roaming`` is more useful to read.
        if home_dir and value.lower().startswith(home_dir.lower()):
            continue
        roots.append((value, f"%{name}%"))

    # Windows path comparison is case-insensitive, and the same directory shows
    # up as ``C:\\Users\\Ada`` and ``c:\\users\\ada`` in one log: the shell, the
    # registry and Python all normalise differently.
    return ScrubRules(roots, ignore_case=os_name == "Windows")


def scrub_text(text: str, rules: ScrubRules) -> str:
    """Rewrite every location root out of one blob of text."""

    return rules.apply(text) if rules else text


def scrub_value(value: Any, rules: ScrubRules) -> Any:
    """Rewrite locations inside decoded JSON, keys included.

    Keys carry paths too -- the workspace registry is keyed by directory -- so
    scrubbing only the values would leave the user name in the half of the
    document that is hardest to notice.
    """

    if not rules:
        return value
    if isinstance(value, str):
        return rules.apply(value)
    if isinstance(value, Mapping):
        return {scrub_value(key, rules): scrub_value(item, rules) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_value(item, rules) for item in value]
    return value


__all__ = [
    "HOME_PLACEHOLDER",
    "LOCATION_VARIABLES",
    "ScrubRules",
    "scrub_rules",
    "scrub_text",
    "scrub_value",
]
