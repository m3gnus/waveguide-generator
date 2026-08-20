"""Machine-readable design and parameter discovery contract."""

from __future__ import annotations

import asyncio
import json

from server.integration.api import (
    ParameterCatalog,
    design_schema,
    parameter_catalog,
)


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
    ParameterCatalog.model_validate(catalog)


def test_design_schema_exposes_the_discriminated_families() -> None:
    response = asyncio.run(design_schema())
    schema = json.loads(response.body)
    encoded = json.dumps(schema)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["x-wg-schema-version"] == 1
    assert all(family in encoded for family in ("R-OSSE", "OSSE", "ICW", "FREEFORM"))
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.media_type == "application/schema+json"


def test_design_schema_discriminator_excludes_an_invalid_design_family() -> None:
    """Assert the draft-2020-12 keywords without adding a validator dependency."""

    schema = json.loads(asyncio.run(design_schema()).body)
    discriminator = schema["discriminator"]
    assert discriminator["propertyName"] == "formula"
    assert set(discriminator["mapping"]) == {"R-OSSE", "OSSE", "ICW", "FREEFORM"}

    invalid_design = {"formula": "UNKNOWN"}
    matched_branches = []
    for branch in schema["oneOf"]:
        definition_name = branch["$ref"].removeprefix("#/$defs/")
        definition = schema["$defs"][definition_name]
        assert "formula" in definition["required"]
        expected_family = definition["properties"]["formula"]["const"]
        if invalid_design["formula"] == expected_family:
            matched_branches.append(definition_name)
    assert matched_branches == []
