"""Problem-report scrubbing; the rules live in ``server.platform.scrub``.

They moved below every server package so incident records
(``server.platform.incidents``), which the CAD pipeline writes, rewrite paths
with the same code a problem report does.
"""

from server.platform.scrub import (
    HOME_PLACEHOLDER,
    LOCATION_VARIABLES,
    ScrubRules,
    scrub_rules,
    scrub_text,
    scrub_value,
)

__all__ = [
    "HOME_PLACEHOLDER",
    "LOCATION_VARIABLES",
    "ScrubRules",
    "scrub_rules",
    "scrub_text",
    "scrub_value",
]
