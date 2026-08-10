"""Operating-system integration for Waveguide Generator."""

from .paths import DataPaths, ensure_data_layout, migrate_legacy_data_dir, resolve_data_dir

__all__ = ["DataPaths", "ensure_data_layout", "migrate_legacy_data_dir", "resolve_data_dir"]
