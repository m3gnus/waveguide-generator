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
from typing import Any, Callable, Mapping, Sequence

from .client import OnshapeClient, OnshapeError, OnshapeHttpError


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


def send_bundle(
    adapter: OnshapeAdapter,
    bundle_path: str | Path,
    *,
    document_name: str,
    step_filename: str,
    target: OnshapeTarget | None = None,
    allow_public: bool = False,
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
    else:
        document_id = target.document_id
        workspace_id = target.workspace_id
        blob_element_id = target.blob_element_id
        variable_studio_id = target.variable_studio_element_id
        part_studio_id = target.part_studio_element_id
        summary = adapter.document_summary(document_id)
        if summary.get("trashed"):
            raise OnshapeAdapterError(
                "The linked Onshape document is in the trash. Restore it in "
                "Onshape, or unlink to create a new document."
            )
        reported = summary.get("public")
        is_public = bool(reported) if isinstance(reported, bool) else False

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

    return OnshapeSendResult(
        target=OnshapeTarget(
            document_id=document_id,
            workspace_id=workspace_id,
            blob_element_id=blob_element_id,
            part_studio_element_id=part_studio_id,
            variable_studio_element_id=variable_studio_id,
        ),
        document_name=document_name,
        document_url=adapter.client.document_url(document_id, workspace_id),
        created_document=created,
        is_public=is_public,
        variables_pushed=variables_pushed,
        translation_id=translation_id,
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
