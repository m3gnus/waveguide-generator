"""
Axisymmetric fast-path scaffold.

This module intentionally provides only eligibility checks and a stub adapter.
The production solve path remains 3D BEMPP until a validated axisymmetric
implementation is introduced behind a feature flag.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class AxisymmetricEligibility:
    eligible: bool
    reason: str
    checks: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "checks": self.checks,
        }


def evaluate_axisymmetric_eligibility(
    sim_type: str,
    mesh_metadata: Optional[Dict[str, Any]] = None,
    feature_enabled: bool = False,
) -> AxisymmetricEligibility:
    metadata = mesh_metadata or {}
    checks = {
        "feature_enabled": bool(feature_enabled),
        "sim_type": str(sim_type),
        "full_circle": bool(metadata.get("fullCircle", False)),
    }

    if not checks["feature_enabled"]:
        return AxisymmetricEligibility(False, "feature_flag_disabled", checks)
    if checks["sim_type"] != "2":
        return AxisymmetricEligibility(False, "sim_type_not_supported", checks)
    if not checks["full_circle"]:
        return AxisymmetricEligibility(False, "geometry_not_full_circle", checks)
    return AxisymmetricEligibility(True, "eligible_for_axisymmetric_spike", checks)


class AxisymmetricAdapter:
    def solve(self, *args, **kwargs):
        raise NotImplementedError(
            "Axisymmetric solver adapter is a scaffold only and is not enabled in production."
        )
