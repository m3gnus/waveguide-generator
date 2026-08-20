"""Machine-readable design and parameter discovery contract."""

from __future__ import annotations

import asyncio
import json

from server.integration.api import design_schema, parameter_catalog


def test_parameter_catalog_is_versioned_unique_and_declarative() -> None:
    response = asyncio.run(parameter_catalog())
    catalog = json.loads(response.body)

    assert catalog["schema_version"] == 1
    assert catalog["catalog_version"] == 1
    assert catalog["design_families"] == ["R-OSSE", "OSSE", "ICW", "FREEFORM"]
    ids = [field["id"] for field in catalog["parameters"]]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 100
    osse_length = next(field for field in catalog["parameters"] if field["id"] == "osse.L")
    assert osse_length["path"] == "L"
    assert osse_length["unit"] == "mm"
    assert osse_length["families"] == ["OSSE"]
    assert osse_length["default_by_family"] == {"OSSE": 130}
    assert osse_length["accepts_expression"] is True
    guide = next(
        field for field in catalog["parameters"] if field["id"] == "guide.superellipse_n"
    )
    assert guide["visible_when"] == {
        "path": "guiding_curve.curve_type",
        "operator": "equals",
        "value": 1,
    }
    assert "recommended search ranges" in catalog["validation_authority"]["note"]
    assert response.headers["etag"].startswith('"sha256:')


def test_design_schema_exposes_the_discriminated_families() -> None:
    response = asyncio.run(design_schema())
    document = json.loads(response.body)
    encoded = json.dumps(document["schema"])

    assert document["schema_version"] == 1
    assert all(family in encoded for family in ("R-OSSE", "OSSE", "ICW", "FREEFORM"))
    assert response.headers["cache-control"] == "public, max-age=3600"
