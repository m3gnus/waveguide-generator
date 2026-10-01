"""Optional disclosure for numeric artifacts reconstructed by adaptive sweeps."""

from typing import Any, Mapping


SAMPLING_WARNING = (
    "Adaptive sampling: interpolated rows are reconstructed estimates; "
    "narrow resonances can be missed."
)


def sampling_provenance(result: Mapping[str, Any]) -> dict[str, Any] | None:
    flags = result.get("frequency_status")
    if flags is None:
        return None
    frequencies = result.get("frequencies", [])
    return {
        "warning": SAMPLING_WARNING,
        "solved_count": flags.count("solved"),
        "interpolated_count": flags.count("interpolated"),
        "frequency_status": list(flags),
        "solved_frequencies_hz": [f for f, flag in zip(frequencies, flags, strict=True) if flag == "solved"],
    }
