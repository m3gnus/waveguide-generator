"""The Onshape leg: credentials, transport, and bundle materialisation.

Every test here drives a recorded transport rather than the network, so the
suite is offline and deterministic. The live-account behaviours these fakes
encode were measured in the O1 spike (CAD-LINK-PLAN.md section 8.5) -- above
all, that a translation which updates an existing part reports
``resultElementIds: null`` and that this is success.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from server.cadlink.onshape.adapter import (
    OnshapeAdapter,
    OnshapeAdapterError,
    OnshapePublicDocumentConsent,
    OnshapeTarget,
    OnshapeTranslationFailed,
    read_bundle,
    send_bundle,
    variable_params,
)
from server.cadlink.onshape.client import (
    OnshapeClient,
    OnshapeHttpError,
    OnshapeTransportError,
    _multipart,
)
from server.cadlink.onshape.credentials import (
    OnshapeCredentials,
    OnshapeCredentialsError,
    load_credentials,
)


def _credentials() -> OnshapeCredentials:
    return OnshapeCredentials(
        access_key="ACCESS", secret_key="SECRET", base_url="https://cad.onshape.com/api"
    )


class FakeTransport:
    """Replays canned replies and records every call for assertions."""

    def __init__(self, replies: list[tuple[int, Any]] | None = None) -> None:
        self.replies = replies or []
        self.calls: list[dict[str, Any]] = []
        self.routes: dict[tuple[str, str], tuple[int, Any]] = {}

    def route(self, method: str, path_suffix: str, status: int, body: Any) -> None:
        self.routes[(method, path_suffix)] = (status, body)

    def route_many(self, method: str, path_suffix: str, replies: list[tuple[int, Any]]) -> None:
        self.routes[(method, path_suffix)] = replies  # type: ignore[assignment]

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, Mapping[str, str], bytes]:
        path = url.removeprefix("https://cad.onshape.com/api")
        self.calls.append(
            {
                "method": method,
                "path": path,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        matching = [
            (suffix, reply)
            for (route_method, suffix), reply in self.routes.items()
            if route_method == method and path.startswith(suffix)
        ]
        if matching:
            _suffix, reply = max(matching, key=lambda item: len(item[0]))
            if isinstance(reply, list):
                if not reply:
                    raise AssertionError(f"no canned reply for {method} {path}")
                status, payload = reply.pop(0)
            else:
                status, payload = reply
        else:
            if not self.replies:
                raise AssertionError(f"no canned reply for {method} {path}")
            status, payload = self.replies.pop(0)
        encoded = (
            b""
            if payload is None
            else json.dumps(payload).encode("utf-8")
            if not isinstance(payload, bytes)
            else payload
        )
        return status, {"x-rate-limit-remaining": "2999"}, encoded


# -- credentials -----------------------------------------------------------


def test_credentials_prefer_environment_over_file(tmp_path: Path) -> None:
    config = tmp_path / ".config" / "hornlab"
    config.mkdir(parents=True)
    (config / "onshape.env").write_text(
        "# a comment\nONSHAPE_ACCESS_KEY='from-file'\nONSHAPE_SECRET_KEY=\"file-secret\"\n",
        encoding="utf-8",
    )
    from_file = load_credentials(environ={}, home=tmp_path)
    assert from_file.access_key == "from-file"
    assert from_file.secret_key == "file-secret"

    overridden = load_credentials(
        environ={"ONSHAPE_ACCESS_KEY": "env", "ONSHAPE_SECRET_KEY": "env-secret"},
        home=tmp_path,
    )
    assert overridden.access_key == "env"
    assert overridden.source == "environment"


def test_missing_credentials_explain_where_to_put_them(tmp_path: Path) -> None:
    with pytest.raises(OnshapeCredentialsError) as caught:
        load_credentials(environ={}, home=tmp_path)
    assert "dev-portal.onshape.com/keys" in str(caught.value)
    assert str(tmp_path / ".config" / "hornlab" / "onshape.env") in str(caught.value)


def test_credentials_refuse_a_plaintext_base_url(tmp_path: Path) -> None:
    with pytest.raises(OnshapeCredentialsError, match="https"):
        load_credentials(
            environ={
                "ONSHAPE_ACCESS_KEY": "a",
                "ONSHAPE_SECRET_KEY": "b",
                "ONSHAPE_BASE_URL": "http://cad.onshape.com/api",
            },
            home=tmp_path,
        )


def test_credentials_never_repr_the_key_pair() -> None:
    rendered = f"{_credentials()!r} {_credentials()!s}"
    assert "ACCESS" not in rendered
    assert "SECRET" not in rendered


# -- client ----------------------------------------------------------------


def test_client_sends_basic_auth_and_parses_json() -> None:
    transport = FakeTransport([(200, {"name": "Test Account"})])
    client = OnshapeClient(_credentials(), transport=transport)
    assert client.session_info()["name"] == "Test Account"
    call = transport.calls[0]
    assert call["path"] == "/users/sessioninfo"
    # base64("ACCESS:SECRET")
    assert call["headers"]["Authorization"] == "Basic QUNDRVNTOlNFQ1JFVA=="
    assert client.last_rate_limit_remaining == 2999


def test_client_refuses_to_follow_a_redirect() -> None:
    transport = FakeTransport([(302, None)])
    client = OnshapeClient(_credentials(), transport=transport)
    with pytest.raises(OnshapeHttpError, match="another host"):
        client.get("/users/sessioninfo")


def test_binary_download_follows_cross_origin_redirect_without_authorization() -> None:
    calls: list[tuple[str, Mapping[str, str]]] = []

    def redirecting(method, url, headers, body, timeout):
        calls.append((url, dict(headers)))
        if len(calls) == 1:
            return 307, {"location": "https://attachments.onshapeusercontent.com/file.step"}, b""
        return 200, {"content-type": "application/step"}, b"ISO-10303-21;"

    client = OnshapeClient(_credentials(), transport=redirecting)
    response = client.request_bytes("GET", "/documents/d/D/externaldata/F")

    assert response.body == b"ISO-10303-21;"
    assert "Authorization" in calls[0][1]
    assert "Authorization" not in calls[1][1]


def test_binary_download_refuses_plaintext_redirect() -> None:
    def redirecting(method, url, headers, body, timeout):
        return 307, {"location": "http://attachments.example/file.step"}, b""

    client = OnshapeClient(_credentials(), transport=redirecting)
    with pytest.raises(OnshapeTransportError, match="unsafe download URL"):
        client.request_bytes("GET", "/documents/d/D/externaldata/F")


def test_client_maps_an_unauthorised_reply_to_actionable_text() -> None:
    transport = FakeTransport([(401, {"message": "Not authorized"})])
    client = OnshapeClient(_credentials(), transport=transport)
    with pytest.raises(OnshapeHttpError) as caught:
        client.get("/users/sessioninfo")
    assert caught.value.is_auth_failure
    assert "dev-portal.onshape.com/keys" in str(caught.value)


def test_client_wraps_a_transport_failure() -> None:
    def broken(*_args: Any, **_kwargs: Any) -> tuple[int, Mapping[str, str], bytes]:
        raise OSError("name resolution failed")

    client = OnshapeClient(_credentials(), transport=broken)
    with pytest.raises(OnshapeTransportError, match="Could not reach Onshape"):
        client.get("/users/sessioninfo")


def test_client_never_leaks_the_key_pair_in_an_error() -> None:
    transport = FakeTransport([(500, {"message": "boom"})])
    client = OnshapeClient(_credentials(), transport=transport)
    with pytest.raises(OnshapeHttpError) as caught:
        client.get("/documents")
    assert "SECRET" not in str(caught.value)


def test_multipart_body_carries_fields_and_file() -> None:
    body, content_type = _multipart(
        {"translate": "true"}, "horn.step", b"ISO-10303-21;", boundary="BOUND"
    )
    assert content_type == "multipart/form-data; boundary=BOUND"
    text = body.decode("utf-8")
    assert 'name="translate"' in text and "true" in text
    assert 'filename="horn.step"' in text
    assert "ISO-10303-21;" in text
    assert text.endswith("--BOUND--\r\n")


def test_multipart_filename_cannot_forge_headers() -> None:
    """A design name reaches a header, so it must not be able to break out.

    The injected text may survive *inside* the quoted filename -- that is inert.
    What must not survive is the quote that would close the value early or the
    CRLF that would start a header line of the attacker's choosing.
    """

    body, _ = _multipart(
        {}, 'evil"\r\nContent-Type: text/html', b"x", boundary="BOUND"
    )
    text = body.decode("utf-8")
    disposition = next(line for line in text.split("\r\n") if "Content-Disposition" in line)
    assert disposition == 'Content-Disposition: form-data; name="file"; filename="evil_Content-Type: text/html"'
    assert text.count("Content-Disposition") == 1
    # One part header block: disposition, our content type, then the payload.
    # The filename's own "Content-Type:" text is inert because it never starts
    # a line of its own.
    lines = text.split("\r\n")
    assert [line for line in lines if line.startswith("Content-Type:")] == [
        "Content-Type: application/step"
    ]


def test_plan_summary_reports_the_free_plan_as_public_only() -> None:
    transport = FakeTransport(
        [(200, {"activePlan": {"group": "Free", "description": "Onshape Free public only"}})]
    )
    client = OnshapeClient(_credentials(), transport=transport)
    assert client.plan_summary() == {
        "group": "Free",
        "name": "Onshape Free public only",
        "public_only": True,
    }


def test_plan_summary_is_advisory_when_the_call_fails() -> None:
    transport = FakeTransport([(403, {"message": "nope"})])
    client = OnshapeClient(_credentials(), transport=transport)
    # Unknown, not "private" -- the difference decides whether WG warns.
    assert client.plan_summary()["public_only"] is None


# -- variables -------------------------------------------------------------


def test_variable_params_carry_units_as_an_expression() -> None:
    params = variable_params(
        [
            {"name": "wg_demo_throat_dia", "value": 25.4, "unit": "mm", "role": "interface"},
            {"name": "wg_demo_ratio", "value": 1.5, "role": "informational"},
        ]
    )
    assert params[0]["type"] == "LENGTH"
    assert params[0]["expression"] == "25.400000 mm"
    assert params[1]["type"] == "NUMBER"
    assert params[1]["expression"] == "1.500000"
    assert "informational" in params[1]["description"]


def test_variable_params_reject_a_name_onshape_cannot_accept() -> None:
    with pytest.raises(OnshapeAdapterError, match="wglink contract"):
        variable_params([{"name": "9bad-name", "value": 1.0, "unit": "mm"}])


def test_variable_params_skip_non_numeric_values() -> None:
    assert variable_params([{"name": "wg_a", "value": "twelve", "unit": "mm"}]) == []


# -- adapter ---------------------------------------------------------------


def _adapter(transport: FakeTransport) -> OnshapeAdapter:
    return OnshapeAdapter(
        OnshapeClient(_credentials(), transport=transport),
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )


def test_create_document_asks_for_private_on_an_unknown_plan() -> None:
    transport = FakeTransport()
    transport.route("GET", "/users/current", 200, {"activePlan": {"group": "Professional"}})
    transport.route(
        "POST", "/documents", 200, {"id": "DID", "defaultWorkspace": {"id": "WID"}, "public": False}
    )
    document_id, workspace_id, is_public = _adapter(transport).create_document("Horn")
    assert (document_id, workspace_id, is_public) == ("DID", "WID", False)
    create = next(call for call in transport.calls if call["method"] == "POST")
    assert json.loads(create["body"])["isPublic"] is False


def test_free_plan_needs_explicit_consent_before_a_public_document() -> None:
    transport = FakeTransport()
    transport.route("GET", "/users/current", 200, {"activePlan": {"group": "Free"}})
    with pytest.raises(OnshapePublicDocumentConsent, match="public"):
        _adapter(transport).create_document("Horn")
    assert all(call["method"] == "GET" for call in transport.calls), "nothing was created"


def test_free_plan_creates_a_public_document_once_consent_is_given() -> None:
    transport = FakeTransport()
    transport.route("GET", "/users/current", 200, {"activePlan": {"group": "Free"}})
    transport.route(
        "POST", "/documents", 200, {"id": "DID", "defaultWorkspace": {"id": "WID"}, "public": True}
    )
    _, _, is_public = _adapter(transport).create_document("Horn", allow_public=True)
    assert is_public is True
    create = next(call for call in transport.calls if call["method"] == "POST")
    assert json.loads(create["body"])["isPublic"] is True


def test_a_plan_that_refuses_private_still_needs_consent() -> None:
    transport = FakeTransport()
    transport.route("GET", "/users/current", 200, {"activePlan": {"group": None}})
    transport.route(
        "POST", "/documents", 403, {"message": "Your plan only supports public documents"}
    )
    with pytest.raises(OnshapePublicDocumentConsent):
        _adapter(transport).create_document("Horn")


def test_translation_reaching_done_with_null_results_is_success() -> None:
    """The decisive spike finding: an update creates no new element."""

    transport = FakeTransport(
        [
            (200, {"requestState": "ACTIVE"}),
            (200, {"requestState": "DONE", "resultElementIds": None}),
        ]
    )
    body = _adapter(transport).await_translation("TID", timeout_s=100.0)
    assert body["requestState"] == "DONE"
    assert body["resultElementIds"] is None


def test_a_failed_translation_reports_onshape_reason() -> None:
    transport = FakeTransport([(200, {"requestState": "FAILED", "failureReason": "bad STEP"})])
    with pytest.raises(OnshapeTranslationFailed, match="bad STEP"):
        _adapter(transport).await_translation("TID", timeout_s=100.0)


def test_translation_polling_gives_up_with_advice() -> None:
    clock = iter([0.0, 0.0, 1000.0, 1000.0])
    adapter = OnshapeAdapter(
        OnshapeClient(
            _credentials(),
            transport=FakeTransport([(200, {"requestState": "ACTIVE"})] * 4),
        ),
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock),
    )
    with pytest.raises(OnshapeTranslationFailed, match="check the document|did not finish"):
        adapter.await_translation("TID", timeout_s=10.0)


def test_upload_targets_the_element_endpoint_when_updating() -> None:
    transport = FakeTransport()
    transport.route("POST", "/blobelements", 200, {"id": "BLOB", "translationId": "TID"})
    adapter = _adapter(transport)

    adapter.upload_step("DID", "WID", b"step", filename="horn.step")
    assert transport.calls[-1]["path"] == "/blobelements/d/DID/w/WID"

    adapter.upload_step("DID", "WID", b"step", filename="horn.step", blob_element_id="BLOB")
    assert transport.calls[-1]["path"] == "/blobelements/d/DID/w/WID/e/BLOB"
    assert transport.calls[-1]["headers"]["Content-Type"].startswith("multipart/form-data")


def test_step_export_success_polls_and_downloads_exact_bytes() -> None:
    transport = FakeTransport()
    transport.route("POST", "/partstudios", 200, {"id": "EXPORT-TID", "requestState": "ACTIVE"})
    transport.route(
        "GET",
        "/translations/EXPORT-TID",
        200,
        {"requestState": "DONE", "resultExternalDataIds": ["FOREIGN"]},
    )
    transport.route("GET", "/documents/d/DID/externaldata/FOREIGN", 200, b"ISO-10303-21;")
    adapter = _adapter(transport)

    translation_id = adapter.create_step_export("DID", "WID", "PART")
    _result, foreign_id = adapter.await_step_export(translation_id)
    payload = adapter.download_external_data("DID", foreign_id)

    assert payload == b"ISO-10303-21;"
    request = json.loads(transport.calls[0]["body"])
    assert request == {"formatName": "STEP", "storeInDocument": False, "translate": True}


def test_step_export_refuses_null_result_and_failed_translation() -> None:
    null_transport = FakeTransport([
        (200, {"requestState": "DONE", "resultExternalDataIds": None}),
    ])
    with pytest.raises(OnshapeTranslationFailed, match="exactly one"):
        _adapter(null_transport).await_step_export("TID")

    failed_transport = FakeTransport([
        (200, {"requestState": "FAILED", "failureReason": "regeneration failed"}),
    ])
    with pytest.raises(OnshapeTranslationFailed, match="regeneration failed"):
        _adapter(failed_transport).await_step_export("TID")


# -- bundle round trip -----------------------------------------------------


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "demo.wglink"
    bundle.mkdir()
    (bundle / "waveguide.step").write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
    (bundle / "wglink.json").write_text(
        json.dumps(
            {
                "wglink_version": "1.1",
                "parameters": [
                    {"name": "wg_demo_throat_dia", "value": 25.4, "unit": "mm", "role": "interface"},
                    {"name": "wg_demo_mouth_w", "value": 320.0, "unit": "mm", "role": "interface"},
                    {"name": "wg_demo_mouth_h", "value": 240.0, "unit": "mm", "role": "interface"},
                    {"name": "wg_demo_depth", "value": 100.0, "unit": "mm", "role": "interface"},
                    {
                        "name": "wg_demo_vertical_offset",
                        "value": 0.0,
                        "unit": "mm",
                        "role": "interface",
                    },
                ],
                "datums": {
                    "rim_planar": True,
                    "WG_AXIS": {"type": "axis", "origin_mm": [0, 0, 0], "direction": [0, 0, 1]},
                    "WG_THROAT_PLANE": {
                        "type": "plane",
                        "origin_mm": [0, 0, 0],
                        "normal": [0, 0, 1],
                        "exact": True,
                    },
                    "WG_MOUTH_PLANE": {
                        "type": "plane",
                        "origin_mm": [0, 0, 100],
                        "normal": [0, 0, 1],
                        "exact": True,
                    },
                    "WG_GEOM_MIDPLANE_Y": {
                        "type": "plane",
                        "origin_mm": [0, 0, 0],
                        "normal": [0, 1, 0],
                        "exact": True,
                    },
                    "WG_SOLVER_CUT_PLANE_Y": {
                        "type": "plane",
                        "origin_mm": [0, 0, 0],
                        "normal": [0, 1, 0],
                        "exact": True,
                    },
                    "WG_SOLVER_CUT_PLANE_X": {
                        "type": "plane",
                        "origin_mm": [0, 0, 0],
                        "normal": [1, 0, 0],
                        "exact": True,
                    },
                    "WG_MOUTH_OUTLINE_INNER": {
                        "type": "polyline",
                        "closed": True,
                        "points_mm": [[160, 0, 100], [0, 120, 100], [-160, 0, 100]],
                    },
                    "WG_MOUTH_OUTLINE_OUTER": {
                        "type": "polyline",
                        "closed": True,
                        "points_mm": [[166, 0, 100], [0, 126, 100], [-166, 0, 100]],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return bundle


def test_read_bundle_rejects_an_incomplete_bundle(tmp_path: Path) -> None:
    (tmp_path / "empty.wglink").mkdir()
    with pytest.raises(OnshapeAdapterError, match="missing its manifest"):
        read_bundle(tmp_path / "empty.wglink")


def _send_transport() -> FakeTransport:
    transport = FakeTransport()
    transport.route("GET", "/users/current", 200, {"activePlan": {"group": "Professional"}})
    transport.route(
        "POST", "/documents", 200, {"id": "DID", "defaultWorkspace": {"id": "WID"}, "public": False}
    )
    transport.route("GET", "/documents/DID", 200, {"name": "Horn", "public": False, "trash": False})
    transport.route("POST", "/blobelements", 200, {"id": "BLOB", "translationId": "TID"})
    transport.route(
        "GET", "/translations/TID", 200, {"requestState": "DONE", "resultElementIds": ["PART"]}
    )
    transport.route("POST", "/variables/d/DID/w/WID/variablestudio", 200, {"id": "VARS"})
    transport.route("POST", "/variables/d/DID/w/WID/e/VARS/variablestudioscope", 204, None)
    transport.route("POST", "/variables/d/DID/w/WID/e/VARS/variables", 204, None)
    transport.route("POST", "/variables/d/DID/w/WID/e/PART/variablestudioreferences", 204, None)
    transport.route("POST", "/featurestudios/d/DID/w/WID", 200, {"id": "DATUM-FS"})
    transport.route("POST", "/featurestudios/d/DID/w/WID/e/DATUM-FS", 204, None)
    transport.route(
        "GET",
        "/featurestudios/d/DID/w/WID/e/DATUM-FS/featurespecs",
        200,
        {"featureSpecs": [{"message": {"namespace": "datum::namespace"}}]},
    )
    transport.route(
        "GET",
        "/featurestudios/d/DID/w/WID/e/DATUM-FS",
        200,
        {"contents": "previous datum source"},
    )
    transport.route(
        "GET",
        "/partstudios/d/DID/w/WID/e/PART/features",
        200,
        {"serializationVersion": "1.2.3", "sourceMicroversion": "micro", "libraryVersion": 3044},
    )
    transport.route(
        "POST",
        "/partstudios/d/DID/w/WID/e/PART/features",
        200,
        {"feature": {"message": {"featureId": "DATUM"}}},
    )
    transport.route(
        "GET",
        "/documents/d/DID/w/WID/elements",
        200,
        [{"id": "PART", "name": "demo", "elementType": "PARTSTUDIO"}],
    )
    return transport


def test_first_send_creates_a_document_and_pushes_variables(tmp_path: Path) -> None:
    transport = _send_transport()
    result = send_bundle(
        _adapter(transport),
        _bundle(tmp_path),
        document_name="Demo Horn",
        step_filename="demo.step",
    )
    assert result.created_document is True
    assert result.target == OnshapeTarget(
        document_id="DID",
        workspace_id="WID",
        blob_element_id="BLOB",
        part_studio_element_id="PART",
        variable_studio_element_id="VARS",
        datum_feature_studio_element_id="DATUM-FS",
        datum_feature_id="DATUM",
    )
    assert result.variables_pushed == 5
    assert result.is_public is False
    assert result.document_url == "https://cad.onshape.com/documents/DID/w/WID"
    assert result.part_names == ("demo",)

    pushed = json.loads(
        next(
            call for call in transport.calls
            if call["path"] == "/variables/d/DID/w/WID/e/VARS/variables"
        )["body"]
    )
    assert [item["name"] for item in pushed] == [
        "wg_demo_throat_dia",
        "wg_demo_mouth_w",
        "wg_demo_mouth_h",
        "wg_demo_depth",
        "wg_demo_vertical_offset",
    ]


def test_second_send_updates_in_place_without_creating_anything(tmp_path: Path) -> None:
    transport = _send_transport()
    # What an update actually returns: no new element id.
    transport.route(
        "GET", "/translations/TID", 200, {"requestState": "DONE", "resultElementIds": None}
    )
    result = send_bundle(
        _adapter(transport),
        _bundle(tmp_path),
        document_name="Demo Horn",
        step_filename="demo.step",
        target=OnshapeTarget(
            document_id="DID",
            workspace_id="WID",
            blob_element_id="BLOB",
            part_studio_element_id="PART",
            variable_studio_element_id="VARS",
            datum_feature_studio_element_id="DATUM-FS",
            datum_feature_id="DATUM",
        ),
    )
    assert result.created_document is False
    assert result.target.part_studio_element_id == "PART"
    assert result.target.variable_studio_element_id == "VARS"
    assert not [call for call in transport.calls if call["path"] == "/documents"], (
        "an update must not create a second document"
    )
    assert not [
        call for call in transport.calls
        if call["path"].endswith("/variablestudio")
    ], "an update must reuse the existing Variable Studio"
    assert transport.calls[1]["path"] == "/blobelements/d/DID/w/WID/e/BLOB"


def test_send_refuses_a_trashed_document(tmp_path: Path) -> None:
    transport = _send_transport()
    transport.route("GET", "/documents/DID", 200, {"name": "Horn", "public": False, "trash": True})
    with pytest.raises(OnshapeAdapterError, match="trash"):
        send_bundle(
            _adapter(transport),
            _bundle(tmp_path),
            document_name="Demo Horn",
            step_filename="demo.step",
            target=OnshapeTarget(
                document_id="DID", workspace_id="WID", blob_element_id="BLOB"
            ),
        )


def test_a_new_variable_studio_is_scoped_into_every_part_studio(tmp_path: Path) -> None:
    """Unreferenced variables cannot drive a sketch, so pushing them is moot."""

    transport = _send_transport()
    send_bundle(
        _adapter(transport),
        _bundle(tmp_path),
        document_name="Demo Horn",
        step_filename="demo.step",
    )
    scope = next(
        call for call in transport.calls if call["path"].endswith("/variablestudioscope")
    )
    assert json.loads(scope["body"]) == {"isAutomaticallyInserted": True}


def test_the_imported_part_studio_references_the_managed_variables(tmp_path: Path) -> None:
    """Scope alone does not reach the Part Studio the translation creates.

    Measured against the live API on 2026-08-13: without this reference,
    ``getVariable`` inside the imported Part Studio cannot see ``wg_*``.
    """

    transport = _send_transport()
    send_bundle(
        _adapter(transport),
        _bundle(tmp_path),
        document_name="Demo Horn",
        step_filename="demo.step",
    )
    reference = next(
        call for call in transport.calls
        if call["path"] == "/variables/d/DID/w/WID/e/PART/variablestudioreferences"
    )
    assert json.loads(reference["body"]) == {
        "references": [{"referenceElementId": "VARS", "entireVariableStudio": True}]
    }


def test_a_refused_reference_does_not_fail_a_send(tmp_path: Path) -> None:
    transport = _send_transport()
    transport.route(
        "POST", "/variables/d/DID/w/WID/e/PART/variablestudioreferences", 403, {"message": "no"}
    )
    result = send_bundle(
        _adapter(transport),
        _bundle(tmp_path),
        document_name="Demo Horn",
        step_filename="demo.step",
    )
    assert result.variables_pushed == 5, "the geometry and variables still landed"


def test_a_refused_scope_does_not_fail_a_send(tmp_path: Path) -> None:
    transport = _send_transport()
    transport.route("POST", "/variables/d/DID/w/WID/e/VARS/variablestudioscope", 403, {"message": "no"})
    result = send_bundle(
        _adapter(transport),
        _bundle(tmp_path),
        document_name="Demo Horn",
        step_filename="demo.step",
    )
    assert result.variables_pushed == 5, "the geometry and variables still landed"


def test_an_update_leaves_variable_scope_and_references_alone(tmp_path: Path) -> None:
    """Re-asserting either would undo a scope the user narrowed by hand."""

    transport = _send_transport()
    send_bundle(
        _adapter(transport),
        _bundle(tmp_path),
        document_name="Demo Horn",
        step_filename="demo.step",
        target=OnshapeTarget(
            document_id="DID",
            workspace_id="WID",
            blob_element_id="BLOB",
            part_studio_element_id="PART",
            variable_studio_element_id="VARS",
        ),
    )
    assert not [call for call in transport.calls if call["path"].endswith("/variablestudioscope")]
    assert not [
        call for call in transport.calls if call["path"].endswith("/variablestudioreferences")
    ]
    # The values themselves still update -- that is the point of the send.
    assert [call for call in transport.calls if call["path"].endswith("/e/VARS/variables")]


def test_send_survives_an_element_listing_failure(tmp_path: Path) -> None:
    """The geometry is already in Onshape; a cosmetic read must not undo that."""

    transport = _send_transport()
    transport.route("GET", "/documents/d/DID/w/WID/elements", 500, {"message": "later"})
    result = send_bundle(
        _adapter(transport),
        _bundle(tmp_path),
        document_name="Demo Horn",
        step_filename="demo.step",
    )
    assert result.target.part_studio_element_id == "PART"
    assert result.part_names == ()
