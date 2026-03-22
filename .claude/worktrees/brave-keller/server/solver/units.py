MM_PER_METER = 1000.0


def mm_to_m(value_mm: float) -> float:
    return float(value_mm) / MM_PER_METER


def m_to_mm(value_m: float) -> float:
    return float(value_m) * MM_PER_METER
