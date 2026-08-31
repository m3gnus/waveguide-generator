"""Problem reports: one file that carries what a bug report needs."""

from .api import ClientErrorLog, create_diagnostics_router, mount_diagnostics
from .bundle import build_bundle, build_summary, bundle_filename, summary_text
from .capabilities import capabilities_or_none, capabilities_payload
from .scrub import scrub_rules, scrub_text, scrub_value

__all__ = [
    "ClientErrorLog",
    "build_bundle",
    "build_summary",
    "bundle_filename",
    "capabilities_or_none",
    "capabilities_payload",
    "create_diagnostics_router",
    "mount_diagnostics",
    "scrub_rules",
    "scrub_text",
    "scrub_value",
    "summary_text",
]
