from __future__ import annotations

import json

from scripts import gen_openapi


def test_committed_openapi_matches_the_live_application() -> None:
    rendered = gen_openapi.render()
    assert gen_openapi.OUTPUT.read_text(encoding="utf-8") == rendered
    schema = json.loads(rendered)
    assert "/api/solve" in schema["paths"]
    assert "/api/integration/v1/parameters" in schema["paths"]
    assert "SolveRequest" in schema["components"]["schemas"]
    assert "ErrorEnvelope" in schema["components"]["schemas"]
