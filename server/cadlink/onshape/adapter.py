"""Materialise a ``wglink`` bundle inside an Onshape document.

The bundle is unchanged from the one the Fusion add-in consumes -- that is the
point of CAD-LINK-PLAN.md section 8.3.  Only the materialisation differs:

* the solid arrives as a **blob element** that Onshape translates into an
  imported Part Studio;
* an **update** re-uploads over the same blob element, and Onshape propagates it
  to the imported part in place, keeping downstream references (measured in the
  O1 spike, 2026-08-08);
* the managed ``wg_*`` parameters live in a **Variable Studio**, which scopes
  them naturally -- Fusion needs the name prefix because its parameters are
  document-global, and the shared prefix keeps one bundle producing identical
  names in both CAD systems.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Callable, Literal, Mapping, Sequence

from .client import OnshapeClient, OnshapeError, OnshapeHttpError
from .datums import (
    DATUM_FEATURE_NAME,
    DATUM_FEATURE_PARAMETER,
    DATUM_FEATURE_TYPE,
    DATUM_STUDIO_NAME,
    build_datum_featurescript,
)
from .native import FEATURE_TYPE, build_featurescript


logger = logging.getLogger(__name__)

# Onshape's own rule for a variable name (BTVariableParams.name). WG's managed
# names are minted by the mesher and always satisfy it; a violation means the
# contract has drifted, so it is raised rather than papered over.
VARIABLE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

VARIABLE_STUDIO_NAME = "WG Parameters"
TRANSLATION_TIMEOUT_S = 300.0
_POLL_INITIAL_S = 2.0
_POLL_MAX_S = 15.0


class OnshapeAdapterError(OnshapeError):
    """The Onshape leg failed for a reason WG can explain."""


class OnshapePublicDocumentConsent(OnshapeAdapterError):
    """This account can only create public documents, and nobody said yes yet.

    Onshape's Free plan makes every document world-readable. WG refuses to
    create one silently: a user who does not know their speaker designs are
    public has been failed by us, not by Onshape (plan section 8.4).
    """


class OnshapeTranslationFailed(OnshapeAdapterError):
    """Onshape could not translate the uploaded STEP."""


@dataclass(frozen=True)
class OnshapeTarget:
    """Where one WG design lives inside an Onshape account."""

    document_id: str
    workspace_id: str
    blob_element_id: str
    part_studio_element_id: str | None = None
    variable_studio_element_id: str | None = None
    feature_studio_element_id: str | None = None
    native_feature_id: str | None = None
    datum_feature_studio_element_id: str | None = None
    datum_feature_id: str | None = None


@dataclass(frozen=True)
class OnshapeSendResult:
    """What one send did, in terms the CAD Link panel can present."""

    target: OnshapeTarget
    document_name: str
    document_url: str
    created_document: bool
    is_public: bool
    variables_pushed: int
    translation_id: str | None
    build_mode: str = "import"
    part_names: tuple[str, ...] = ()


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _plan_restricts_private(exc: OnshapeHttpError) -> bool:
    """Recognise 'your plan cannot do that' among the reasons a create fails."""

    if exc.status not in {400, 402, 403}:
        return False
    haystack = f"{exc} {exc.body}".lower()
    return any(
        token in haystack
        for token in ("public", "plan", "private", "subscription", "upgrade")
    )


def variable_params(parameters: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Translate the manifest's parameter table into Onshape variables.

    The manifest carries ``{name, value, unit, role}``; Onshape wants
    ``{name, type, expression, description}``, where the *expression* is the
    value with its unit -- not a bare number, which Onshape would read as a
    unitless quantity and refuse to use as a length.
    """

    result: list[dict[str, str]] = []
    for entry in parameters:
        if not isinstance(entry, Mapping):
            continue
        name = _string(entry.get("name"))
        if name is None:
            continue
        if VARIABLE_NAME.fullmatch(name) is None:
            raise OnshapeAdapterError(
                f"The bundle declares a CAD parameter named {name!r}, which "
                "Onshape cannot accept as a variable name. This is a defect in "
                "the wglink contract rather than in this account."
            )
        raw = entry.get("value")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        value = float(raw)
        unit = _string(entry.get("unit"))
        role = _string(entry.get("role")) or "interface"
        if unit:
            variable_type = "LENGTH"
            expression = f"{value:.6f} {unit}"
        else:
            variable_type = "NUMBER"
            expression = f"{value:.6f}"
        result.append(
            {
                "name": name,
                "type": variable_type,
                "expression": expression,
                "description": f"Waveguide Generator · {role}",
            }
        )
    return result


class OnshapeAdapter:
    """The WG-level operations, each one a small group of API calls."""

    def __init__(
        self,
        client: OnshapeClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._sleep = sleep
        self._monotonic = monotonic

    @property
    def client(self) -> OnshapeClient:
        return self._client

    # -- documents ---------------------------------------------------------

    def create_document(
        self, name: str, *, allow_public: bool = False
    ) -> tuple[str, str, bool]:
        """Create a document, returning ``(document_id, workspace_id, public)``.

        Private is attempted first on every plan that might allow it. Falling
        back to a public document always needs ``allow_public``.
        """

        plan = self._client.plan_summary()
        public_only = plan.get("public_only")
        if public_only is True and not allow_public:
            raise OnshapePublicDocumentConsent(
                f"This Onshape account is on the {plan.get('name') or 'Free'} plan, "
                "which can only create public documents. Anyone with the link "
                "would be able to view this waveguide. Confirm to continue, or "
                "upgrade the Onshape plan for private documents."
            )
        want_public = bool(public_only)
        try:
            response = self._client.post(
                "/documents",
                json_body={"name": name, "ownerType": 0, "isPublic": want_public},
            )
        except OnshapeHttpError as exc:
            if want_public or not _plan_restricts_private(exc):
                raise
            if not allow_public:
                raise OnshapePublicDocumentConsent(
                    "This Onshape account cannot create private documents, so "
                    "the waveguide would be world-readable. Confirm to continue, "
                    "or upgrade the Onshape plan for private documents."
                ) from exc
            response = self._client.post(
                "/documents",
                json_body={"name": name, "ownerType": 0, "isPublic": True},
            )
            want_public = True

        body = response.body if isinstance(response.body, Mapping) else {}
        document_id = _string(body.get("id"))
        workspace = body.get("defaultWorkspace")
        workspace_id = (
            _string(workspace.get("id")) if isinstance(workspace, Mapping) else None
        )
        if document_id is None or workspace_id is None:
            raise OnshapeAdapterError(
                "Onshape created a document but did not report its id and "
                "default workspace, so WG cannot link to it."
            )
        reported = body.get("public")
        is_public = bool(reported) if isinstance(reported, bool) else want_public
        return document_id, workspace_id, is_public

    def document_summary(self, document_id: str) -> dict[str, Any]:
        """Read a document's name and visibility; used to verify a stored link."""

        response = self._client.get(f"/documents/{document_id}")
        body = response.body if isinstance(response.body, Mapping) else {}
        return {
            "name": _string(body.get("name")),
            "public": bool(body.get("public")) if isinstance(body.get("public"), bool) else None,
            "trashed": bool(body.get("trash")) if isinstance(body.get("trash"), bool) else False,
        }

    def list_elements(self, document_id: str, workspace_id: str) -> list[dict[str, Any]]:
        response = self._client.get(
            f"/documents/d/{document_id}/w/{workspace_id}/elements"
        )
        body = response.body
        return [item for item in body if isinstance(item, Mapping)] if isinstance(body, list) else []

    # -- geometry ----------------------------------------------------------

    def upload_step(
        self,
        document_id: str,
        workspace_id: str,
        step_bytes: bytes,
        *,
        filename: str,
        blob_element_id: str | None = None,
    ) -> tuple[str, str | None]:
        """Create or replace the blob element, returning ``(eid, translation)``.

        Passing ``blob_element_id`` is the update path: Onshape replaces the
        blob and propagates the change to the Part Studio it already created,
        which is what makes the link stateful rather than a fresh import.
        """

        path = f"/blobelements/d/{document_id}/w/{workspace_id}"
        if blob_element_id:
            path = f"{path}/e/{blob_element_id}"
        response = self._client.upload_file(
            path,
            fields={
                "encodedFilename": filename,
                "fileContentLength": str(len(step_bytes)),
                "translate": "true",
                "flattenAssemblies": "false",
                "yAxisIsUp": "false",
                "allowFaultyParts": "true",
            },
            filename=filename,
            content=step_bytes,
        )
        body = response.body if isinstance(response.body, Mapping) else {}
        element_id = _string(body.get("id")) or blob_element_id
        if element_id is None:
            raise OnshapeAdapterError(
                "Onshape accepted the upload but reported no blob element id."
            )
        translation_id = _string(body.get("translationId"))
        return element_id, translation_id

    def await_translation(
        self, translation_id: str, *, timeout_s: float = TRANSLATION_TIMEOUT_S
    ) -> dict[str, Any]:
        """Poll one translation to ``DONE``, with the backoff Onshape asks for.

        A ``DONE`` update reports ``resultElementIds: null`` because no new
        element was created -- the existing one was updated. That is success.
        Treating a null result as a failure is the single easiest way to get
        this adapter wrong, so it is asserted by test rather than by comment.
        """

        deadline = self._monotonic() + timeout_s
        delay = _POLL_INITIAL_S
        while True:
            response = self._client.get(f"/translations/{translation_id}")
            body = response.body if isinstance(response.body, Mapping) else {}
            state = _string(body.get("requestState"))
            if state == "DONE":
                return dict(body)
            if state == "FAILED":
                reason = _string(body.get("failureReason")) or "no reason given"
                raise OnshapeTranslationFailed(
                    f"Onshape could not import the waveguide STEP: {reason}"
                )
            if self._monotonic() >= deadline:
                raise OnshapeTranslationFailed(
                    "Onshape did not finish importing the waveguide within "
                    f"{int(timeout_s)} seconds. The upload may still complete; "
                    "check the document in Onshape before sending again."
                )
            self._sleep(delay)
            delay = min(delay * 1.5, _POLL_MAX_S)

    # -- native features --------------------------------------------------

    def ensure_feature_studio(
        self,
        document_id: str,
        workspace_id: str,
        *,
        element_id: str | None = None,
        name: str = "WG Build",
    ) -> str:
        """Return the generator Feature Studio, creating it only when absent."""

        if element_id:
            return element_id
        response = self._client.post(
            f"/featurestudios/d/{document_id}/w/{workspace_id}",
            json_body={"name": name},
        )
        body = response.body if isinstance(response.body, Mapping) else {}
        studio_id = _string(body.get("id"))
        if studio_id is None:
            raise OnshapeAdapterError(
                "Onshape created the WG Feature Studio but reported no element id."
            )
        return studio_id

    def push_feature_studio_source(
        self,
        document_id: str,
        workspace_id: str,
        element_id: str,
        source: str,
    ) -> str:
        """Replace the source and return Onshape's authoritative namespace.

        A namespace assembled from document ids looks plausible but Onshape
        rejects it. The compiled feature spec is the only reliable source.
        """

        path = f"/featurestudios/d/{document_id}/w/{workspace_id}/e/{element_id}"
        self._client.post(path, json_body={"contents": source})
        response = self._client.get(f"{path}/featurespecs")
        body = response.body if isinstance(response.body, Mapping) else {}
        specs = body.get("featureSpecs")
        if not isinstance(specs, list) or not specs:
            raise OnshapeAdapterError(
                "Onshape could not compile the generated WG Feature Studio "
                "(its featureSpecs reply was empty, and Onshape supplied no diagnostic)."
            )
        first = specs[0]
        message = first.get("message") if isinstance(first, Mapping) else None
        namespace = _string(message.get("namespace")) if isinstance(message, Mapping) else None
        if namespace is None:
            raise OnshapeAdapterError(
                "Onshape compiled the WG Feature Studio but reported no feature namespace."
            )
        return namespace

    def read_feature_studio_source(
        self, document_id: str, workspace_id: str, element_id: str
    ) -> str:
        """Read source before an update so a failed regeneration can roll back."""

        response = self._client.get(
            f"/featurestudios/d/{document_id}/w/{workspace_id}/e/{element_id}"
        )
        body = response.body if isinstance(response.body, Mapping) else {}
        source = _string(body.get("contents"))
        if source is None:
            raise OnshapeAdapterError(
                "Onshape did not return the existing WG datum source, so WG "
                "refused to replace it without a rollback point."
            )
        return source

    def create_part_studio(
        self, document_id: str, workspace_id: str, name: str
    ) -> str:
        """Create the Part Studio that owns the native WG feature."""

        response = self._client.post(
            f"/partstudios/d/{document_id}/w/{workspace_id}",
            json_body={"name": name},
        )
        body = response.body if isinstance(response.body, Mapping) else {}
        studio_id = _string(body.get("id"))
        if studio_id is None:
            raise OnshapeAdapterError(
                "Onshape created the native WG Part Studio but reported no element id."
            )
        return studio_id

    def _feature_request(
        self,
        document_id: str,
        workspace_id: str,
        part_studio_id: str,
        namespace: str,
        *,
        feature_id: str | None = None,
        feature_type: str = FEATURE_TYPE,
        feature_name: str = "WG Waveguide",
        boolean_parameter: str = "buildEnclosure",
    ) -> dict[str, Any]:
        """Build the live-proven BTJson feature envelope and version context."""

        current_response = self._client.get(
            f"/partstudios/d/{document_id}/w/{workspace_id}/e/{part_studio_id}/features"
        )
        current = (
            current_response.body
            if isinstance(current_response.body, Mapping)
            else {}
        )
        try:
            versions = {
                key: current[key]
                for key in (
                    "serializationVersion",
                    "sourceMicroversion",
                    "libraryVersion",
                )
            }
        except KeyError as exc:
            raise OnshapeAdapterError(
                "Onshape did not report the Part Studio version context needed "
                "to add the native WG feature."
            ) from exc

        # The features endpoint accepts this BTJson envelope. The flatter
        # OpenAPI `btType` representation is rejected with an unhelpful 400.
        message: dict[str, Any] = {
            "featureType": feature_type,
            "name": feature_name,
            "suppressed": False,
            "namespace": namespace,
            "parameters": [
                {
                    "type": 144,
                    "typeName": "BTMParameterBoolean",
                    "message": {
                        "parameterId": boolean_parameter,
                        "value": True,
                    },
                }
            ],
        }
        if feature_id is not None:
            message["featureId"] = feature_id
        return {
            "feature": {
                "type": 134,
                "typeName": "BTMFeature",
                "message": message,
            },
            **versions,
        }

    def add_native_feature(
        self,
        document_id: str,
        workspace_id: str,
        part_studio_id: str,
        namespace: str,
    ) -> str:
        """Instantiate the generated custom feature and return its feature id."""

        path = f"/partstudios/d/{document_id}/w/{workspace_id}/e/{part_studio_id}/features"
        response = self._client.post(
            path,
            json_body=self._feature_request(
                document_id, workspace_id, part_studio_id, namespace
            ),
            timeout=600.0,
        )
        body = response.body if isinstance(response.body, Mapping) else {}
        feature = body.get("feature")
        message = feature.get("message") if isinstance(feature, Mapping) else None
        feature_id = (
            _string(message.get("featureId"))
            if isinstance(message, Mapping)
            else None
        ) or _string(body.get("featureId"))
        if feature_id is None:
            raise OnshapeAdapterError(
                "Onshape added the native WG feature but reported no feature id."
            )
        return feature_id

    def update_native_feature(
        self,
        document_id: str,
        workspace_id: str,
        part_studio_id: str,
        feature_id: str,
        namespace: str,
    ) -> None:
        """Regenerate the existing feature so downstream references survive."""

        path = (
            f"/partstudios/d/{document_id}/w/{workspace_id}/e/{part_studio_id}"
            f"/features/featureid/{feature_id}"
        )
        self._client.post(
            path,
            json_body=self._feature_request(
                document_id,
                workspace_id,
                part_studio_id,
                namespace,
                feature_id=feature_id,
            ),
            timeout=600.0,
        )

    def add_datum_feature(
        self,
        document_id: str,
        workspace_id: str,
        part_studio_id: str,
        namespace: str,
    ) -> str:
        """Add the single managed feature that owns every contract datum."""

        path = f"/partstudios/d/{document_id}/w/{workspace_id}/e/{part_studio_id}/features"
        response = self._client.post(
            path,
            json_body=self._feature_request(
                document_id,
                workspace_id,
                part_studio_id,
                namespace,
                feature_type=DATUM_FEATURE_TYPE,
                feature_name=DATUM_FEATURE_NAME,
                boolean_parameter=DATUM_FEATURE_PARAMETER,
            ),
        )
        body = response.body if isinstance(response.body, Mapping) else {}
        feature = body.get("feature")
        message = feature.get("message") if isinstance(feature, Mapping) else None
        feature_id = (
            _string(message.get("featureId")) if isinstance(message, Mapping) else None
        ) or _string(body.get("featureId"))
        if feature_id is None:
            raise OnshapeAdapterError(
                "Onshape added the WG datum feature but reported no feature id."
            )
        return feature_id

    def update_datum_feature(
        self,
        document_id: str,
        workspace_id: str,
        part_studio_id: str,
        feature_id: str,
        namespace: str,
    ) -> None:
        """Regenerate the datum feature in place, preserving its feature id."""

        path = (
            f"/partstudios/d/{document_id}/w/{workspace_id}/e/{part_studio_id}"
            f"/features/featureid/{feature_id}"
        )
        self._client.post(
            path,
            json_body=self._feature_request(
                document_id,
                workspace_id,
                part_studio_id,
                namespace,
                feature_id=feature_id,
                feature_type=DATUM_FEATURE_TYPE,
                feature_name=DATUM_FEATURE_NAME,
                boolean_parameter=DATUM_FEATURE_PARAMETER,
            ),
        )

    def materialize_datums(
        self,
        document_id: str,
        workspace_id: str,
        part_studio_id: str,
        source: str,
        *,
        feature_studio_id: str | None = None,
        feature_id: str | None = None,
    ) -> tuple[str, str]:
        """Create or atomically update the managed datum source and feature.

        Updating a Feature Studio and updating its Part Studio feature are two
        API edits.  Keep the previous source and feature namespace so the
        second edit can be reversed if regeneration is rejected.
        """

        studio_id = self.ensure_feature_studio(
            document_id,
            workspace_id,
            element_id=feature_studio_id,
            name=DATUM_STUDIO_NAME,
        )
        previous_source: str | None = None
        if feature_id is not None:
            previous_source = self.read_feature_studio_source(document_id, workspace_id, studio_id)
        namespace = self.push_feature_studio_source(document_id, workspace_id, studio_id, source)
        if feature_id is None:
            return studio_id, self.add_datum_feature(
                document_id, workspace_id, part_studio_id, namespace
            )
        try:
            self.update_datum_feature(
                document_id, workspace_id, part_studio_id, feature_id, namespace
            )
        except OnshapeError as update_error:
            assert previous_source is not None
            try:
                previous_namespace = self.push_feature_studio_source(
                    document_id, workspace_id, studio_id, previous_source
                )
                self.update_datum_feature(
                    document_id,
                    workspace_id,
                    part_studio_id,
                    feature_id,
                    previous_namespace,
                )
            except OnshapeError as rollback_error:
                raise OnshapeAdapterError(
                    "Onshape rejected the WG datum update and also refused its "
                    "automatic rollback. Open the document and inspect WG Datums "
                    "before sending again."
                ) from rollback_error
            raise update_error
        return studio_id, feature_id

    # -- parameters --------------------------------------------------------

    def ensure_variable_studio(
        self,
        document_id: str,
        workspace_id: str,
        *,
        element_id: str | None = None,
        name: str = VARIABLE_STUDIO_NAME,
    ) -> str:
        """Return a Variable Studio element id, creating one when needed."""

        if element_id:
            return element_id
        response = self._client.post(
            f"/variables/d/{document_id}/w/{workspace_id}/variablestudio",
            json_body={"name": name},
        )
        body = response.body if isinstance(response.body, Mapping) else {}
        studio_id = _string(body.get("id"))
        if studio_id is None:
            raise OnshapeAdapterError(
                "Onshape created a Variable Studio but reported no element id."
            )
        self.auto_insert_variable_studio(document_id, workspace_id, studio_id)
        return studio_id

    def reference_variable_studio(
        self,
        document_id: str,
        workspace_id: str,
        part_studio_id: str,
        variable_studio_id: str,
    ) -> bool:
        """Make one Part Studio see the managed variables.

        Setting the studio's scope to automatic covers Part Studios created
        afterwards; the imported one is created *by the translation*, so it
        needs the reference stated explicitly. Verified against the live API on
        2026-08-13: with this reference in place ``getVariable(context,
        "wg_..._depth")`` inside the imported Part Studio returns the pushed
        value, and without it the variables are not in scope.

        Note that ``GET .../e/<part studio>/variables`` still reports an empty
        list either way -- it enumerates an element's *own* variables, not the
        studios it references -- so it is not a usable check.
        """

        try:
            self._client.post(
                f"/variables/d/{document_id}/w/{workspace_id}/e/{part_studio_id}/variablestudioreferences",
                json_body={
                    "references": [
                        {
                            "referenceElementId": variable_studio_id,
                            "entireVariableStudio": True,
                        }
                    ]
                },
            )
        except OnshapeError:
            logger.warning(
                "Onshape refused to reference the WG Variable Studio from the "
                "imported Part Studio; its variables must be added by hand."
            )
            return False
        return True

    def auto_insert_variable_studio(
        self, document_id: str, workspace_id: str, variable_studio_id: str
    ) -> bool:
        """Make the managed variables visible to every Part Studio here.

        A Variable Studio is inert until something references it: without this
        the ``wg_*`` values exist in the document but cannot be typed into a
        sketch dimension, which is most of the reason to push them at all.
        Advisory -- a document whose geometry arrived is still a successful
        send, so a refusal here is reported rather than raised.
        """

        try:
            self._client.post(
                f"/variables/d/{document_id}/w/{workspace_id}/e/{variable_studio_id}/variablestudioscope",
                json_body={"isAutomaticallyInserted": True},
            )
        except OnshapeError:
            logger.warning(
                "Onshape kept the WG Variable Studio out of automatic scope; "
                "its variables must be referenced by hand in each Part Studio."
            )
            return False
        return True

    def push_variables(
        self,
        document_id: str,
        workspace_id: str,
        variable_studio_id: str,
        parameters: Sequence[Mapping[str, Any]],
    ) -> int:
        """Assign the managed ``wg_*`` variables. Returns how many were sent."""

        payload = variable_params(parameters)
        if not payload:
            return 0
        self._client.post(
            f"/variables/d/{document_id}/w/{workspace_id}/e/{variable_studio_id}/variables",
            json_body=payload,
        )
        return len(payload)

    def read_variables(
        self, document_id: str, workspace_id: str, variable_studio_id: str
    ) -> list[dict[str, Any]]:
        """Read back what actually landed. Used by verification, not by send."""

        response = self._client.get(
            f"/variables/d/{document_id}/w/{workspace_id}/e/{variable_studio_id}/variables"
        )
        body = response.body
        if not isinstance(body, list):
            return []
        found: list[dict[str, Any]] = []
        for table in body:
            if not isinstance(table, Mapping):
                continue
            variables = table.get("variables")
            if isinstance(variables, list):
                found.extend(item for item in variables if isinstance(item, Mapping))
        return found


def read_bundle(bundle_path: str | Path) -> tuple[dict[str, Any], bytes]:
    """Load one ``.wglink`` bundle's manifest and its STEP payload."""

    root = Path(bundle_path)
    manifest_path = root / "wglink.json"
    step_path = root / "waveguide.step"
    if not manifest_path.is_file() or not step_path.is_file():
        raise OnshapeAdapterError(
            "The CAD-link bundle is missing its manifest or STEP body. "
            "Send the design to CAD again."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OnshapeAdapterError(
            "The CAD-link bundle manifest could not be read. Send the design "
            "to CAD again."
        ) from exc
    if not isinstance(manifest, Mapping):
        raise OnshapeAdapterError("The CAD-link bundle manifest is not an object.")
    return dict(manifest), step_path.read_bytes()


def _read_point_grid(bundle_path: str | Path) -> dict[str, Any]:
    """Read the realised geometry needed by the native FeatureScript build."""

    path = Path(bundle_path) / "point-grid.json"
    if not path.is_file():
        raise OnshapeAdapterError(
            "The CAD-link bundle is missing point-grid.json, so it cannot be "
            "built from native Onshape features. Send the design to CAD again."
        )
    try:
        grid = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OnshapeAdapterError(
            "The CAD-link point grid could not be read. Send the design to CAD again."
        ) from exc
    if not isinstance(grid, Mapping):
        raise OnshapeAdapterError("The CAD-link point grid is not an object.")
    return dict(grid)


def send_bundle(
    adapter: OnshapeAdapter,
    bundle_path: str | Path,
    *,
    document_name: str,
    step_filename: str,
    target: OnshapeTarget | None = None,
    allow_public: bool = False,
    build_mode: Literal["import", "native"] = "import",
) -> OnshapeSendResult:
    """Create or update the Onshape materialisation of one bundle.

    ``target`` is the stored link. Absent, this creates a document; present, it
    updates that document in place.
    """

    manifest, step_bytes = read_bundle(bundle_path)
    created = False
    is_public = False
    if target is None:
        document_id, workspace_id, is_public = adapter.create_document(
            document_name, allow_public=allow_public
        )
        created = True
        blob_element_id: str | None = None
        variable_studio_id: str | None = None
        part_studio_id: str | None = None
        feature_studio_id: str | None = None
        native_feature_id: str | None = None
        datum_feature_studio_id: str | None = None
        datum_feature_id: str | None = None
    else:
        document_id = target.document_id
        workspace_id = target.workspace_id
        blob_element_id = target.blob_element_id
        variable_studio_id = target.variable_studio_element_id
        part_studio_id = target.part_studio_element_id
        feature_studio_id = target.feature_studio_element_id
        native_feature_id = target.native_feature_id
        datum_feature_studio_id = target.datum_feature_studio_element_id
        datum_feature_id = target.datum_feature_id
        summary = adapter.document_summary(document_id)
        if summary.get("trashed"):
            raise OnshapeAdapterError(
                "The linked Onshape document is in the trash. Restore it in "
                "Onshape, or unlink to create a new document."
            )
        reported = summary.get("public")
        is_public = bool(reported) if isinstance(reported, bool) else False

    translation_id: str | None = None
    if build_mode == "import":
        blob_element_id, translation_id = adapter.upload_step(
            document_id,
            workspace_id,
            step_bytes,
            filename=step_filename,
            blob_element_id=blob_element_id,
        )
        if translation_id:
            translation = adapter.await_translation(translation_id)
            # Present on a create, null on an update because the existing Part
            # Studio was updated rather than replaced. Keep whichever id we know.
            results = translation.get("resultElementIds")
            if isinstance(results, list):
                first = next((_string(item) for item in results if _string(item)), None)
                part_studio_id = first or part_studio_id
    else:
        try:
            source = build_featurescript(_read_point_grid(bundle_path), manifest)
        except ValueError as exc:
            raise OnshapeAdapterError(
                f"The CAD-link point grid cannot produce a native Onshape build: {exc}"
            ) from exc
        feature_studio_id = adapter.ensure_feature_studio(
            document_id,
            workspace_id,
            element_id=feature_studio_id,
        )
        namespace = adapter.push_feature_studio_source(
            document_id, workspace_id, feature_studio_id, source
        )
        if part_studio_id is None:
            part_studio_id = adapter.create_part_studio(
                document_id, workspace_id, document_name
            )
        if native_feature_id is None:
            native_feature_id = adapter.add_native_feature(
                document_id, workspace_id, part_studio_id, namespace
            )
        else:
            adapter.update_native_feature(
                document_id,
                workspace_id,
                part_studio_id,
                native_feature_id,
                namespace,
            )

    part_names: tuple[str, ...] = ()
    try:
        elements = adapter.list_elements(document_id, workspace_id)
    except OnshapeError:
        elements = []  # advisory: the send already succeeded
    for element in elements:
        if str(element.get("elementType") or "").upper() != "PARTSTUDIO":
            continue
        element_id = _string(element.get("id"))
        if part_studio_id is None:
            part_studio_id = element_id
        if element_id == part_studio_id:
            name = _string(element.get("name"))
            if name:
                part_names = (name,)

    fresh_studio = variable_studio_id is None
    variable_studio_id = adapter.ensure_variable_studio(
        document_id, workspace_id, element_id=variable_studio_id
    )
    variables_pushed = adapter.push_variables(
        document_id,
        workspace_id,
        variable_studio_id,
        manifest.get("parameters") or [],
    )
    # Only on the send that created the studio. Re-asserting the reference on
    # every update would silently undo a scope the user narrowed by hand.
    if fresh_studio and part_studio_id is not None:
        adapter.reference_variable_studio(
            document_id, workspace_id, part_studio_id, variable_studio_id
        )

    if part_studio_id is None:
        raise OnshapeAdapterError(
            "Onshape did not report the Part Studio that should own the WG datums."
        )
    try:
        datum_source = build_datum_featurescript(manifest)
    except ValueError as exc:
        raise OnshapeAdapterError(
            f"The CAD-link datum catalogue cannot be materialized in Onshape: {exc}"
        ) from exc
    datum_feature_studio_id, datum_feature_id = adapter.materialize_datums(
        document_id,
        workspace_id,
        part_studio_id,
        datum_source,
        feature_studio_id=datum_feature_studio_id,
        feature_id=datum_feature_id,
    )

    return OnshapeSendResult(
        target=OnshapeTarget(
            document_id=document_id,
            workspace_id=workspace_id,
            blob_element_id=blob_element_id or "",
            part_studio_element_id=part_studio_id,
            variable_studio_element_id=variable_studio_id,
            feature_studio_element_id=feature_studio_id,
            native_feature_id=native_feature_id,
            datum_feature_studio_element_id=datum_feature_studio_id,
            datum_feature_id=datum_feature_id,
        ),
        document_name=document_name,
        document_url=adapter.client.document_url(document_id, workspace_id),
        created_document=created,
        is_public=is_public,
        variables_pushed=variables_pushed,
        translation_id=translation_id,
        build_mode=build_mode,
        part_names=part_names,
    )


__all__ = [
    "OnshapeAdapter",
    "OnshapeAdapterError",
    "OnshapePublicDocumentConsent",
    "OnshapeSendResult",
    "OnshapeTarget",
    "OnshapeTranslationFailed",
    "read_bundle",
    "send_bundle",
    "variable_params",
    "VARIABLE_STUDIO_NAME",
]
