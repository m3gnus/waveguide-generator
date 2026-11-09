"""Explicit Mmd suggestions using the pinned coupling model's single correction."""

from hornlab_sim.methods import driver, driver_coupling, radiation_impedance

from server.drivers.models import MmdDerivation, MmdSuggestion


def suggest_mmd(mms_g: float, sd_cm2: float) -> MmdSuggestion:
    # The helper needs only Sd and Mms from `derived`. Do not derive unrelated
    # compliance/damping or require a full DriverSpec just to suggest a mass.
    record = driver.Driver(Sd=sd_cm2 * 1e-4, Mms=mms_g * 1e-3, Bl=1, Re=1)
    effective_mmd = getattr(driver_coupling, "_effective_mmd", None)
    if not callable(effective_mmd):
        return MmdSuggestion(reason="The free-air mass correction is unavailable. Enter the manufacturer's Mmd.")
    try:
        mmd, correction, _ = effective_mmd(record, record, radiation_impedance.RHO_AIR)
    except ValueError as exc:
        return MmdSuggestion(reason=str(exc))
    fraction = correction / record.Mms
    return MmdSuggestion(
        suggestion=MmdDerivation(
            mms_g=mms_g, sd_cm2=sd_cm2, mmd_kg=mmd,
            correction_kg=correction, correction_fraction=fraction,
            rho_kg_m3=radiation_impedance.RHO_AIR,
        ),
        warning=("Check Sd and Mms against the datasheet: the air-load correction exceeds 30% of Mms."
                 if fraction > driver_coupling.MMD_CORRECTION_WARN else None),
    )
