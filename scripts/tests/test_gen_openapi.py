from __future__ import annotations

import json

from scripts import gen_openapi


def test_committed_openapi_matches_the_live_application() -> None:
    rendered = gen_openapi.render()
    assert gen_openapi.OUTPUT.read_text(encoding="utf-8") == rendered
    schema = json.loads(rendered)
    assert "/api/solve" in schema["paths"]
    assert "/api/integration/v1/parameters" in schema["paths"]
    assert "/api/integration/v1/design-schema" in schema["paths"]
    assert "SolveRequest" in schema["components"]["schemas"]
    assert "ErrorEnvelope" in schema["components"]["schemas"]
    assert "ParameterCatalog" in schema["components"]["schemas"]
    assert "DesignSchemaDocument" in schema["components"]["schemas"]
    assert "ResultProvenance" in schema["components"]["schemas"]
    provenance_schema = schema["components"]["schemas"]["ResultProvenance"]
    assert {
        "request_identity",
        "execution_request_sha256",
        "execution_geometry_sha256",
        "execution_solve_options_sha256",
        "effective_request_sha256",
        "effective_geometry_sha256",
        "effective_solve_options_sha256",
    } <= set(provenance_schema["required"])
    assert provenance_schema["properties"]["request_identity"]["const"] == "execution"

    design_response = schema["paths"]["/api/integration/v1/design-schema"]["get"][
        "responses"
    ]["200"]["content"]
    assert set(design_response) == {"application/schema+json"}
    assert design_response["application/schema+json"]["schema"] == {
        "$ref": "#/components/schemas/DesignSchemaDocument"
    }

    catalog_response = schema["paths"]["/api/integration/v1/parameters"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    assert catalog_response == {"$ref": "#/components/schemas/ParameterCatalog"}

    solve_422 = schema["paths"]["/api/solve"]["post"]["responses"]["422"][
        "content"
    ]["application/json"]["schema"]
    assert solve_422 == {"$ref": "#/components/schemas/ErrorEnvelope"}
