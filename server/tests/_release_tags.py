"""Single source of truth for the release tags the frozen-release tests need.

``test_update_transaction_contract.py`` and ``test_cadlink_store_rollback.py``
each run a *previously released* build's own code by extracting it from a
tag with ``git archive``. That only works when the checkout the suite runs in
can reach the tag; an ordinary developer clone can, but a CI checkout starts
shallow and without any tags (``actions/checkout@v4`` defaults), so
``.github/workflows/ci.yml`` has a step that fetches exactly this list before
the suite runs. Keep this the only place that names the tags, so the workflow
step and the tests can never drift apart silently.

``WG_REQUIRE_RELEASE_TAGS=1`` (set by that CI step, unset for local
development) turns a missing tag from a quiet skip into a failure: it is the
guard against a future checkout change silently disabling these tests again.
"""

from __future__ import annotations

import os

#: Every release tag a test in this suite extracts via ``git archive``,
#: oldest first. ``v0.3.3-rc.1`` is a published pre-release: a beta-channel
#: user can roll back to it, so it is a rollback target like any release.
RELEASE_TAGS: tuple[str, ...] = ("v0.3.1", "v0.3.2", "v0.3.3-rc.1")

#: The most recent stable release -- what a rollback/upgrade test compares
#: "the build under test" against. A pre-release is never this.
LATEST_RELEASE_TAG = "v0.3.2"

#: The releases a CAD Link registry this build wrote must still open in: the
#: latest stable release, and every pre-release published since it.
CADLINK_ROLLBACK_TAGS: tuple[str, ...] = ("v0.3.2", "v0.3.3-rc.1")

#: True only in CI, once it has fetched every tag in RELEASE_TAGS.
REQUIRE_RELEASE_TAGS = os.environ.get("WG_REQUIRE_RELEASE_TAGS") == "1"


def skip_or_fail_missing_tag(message: str) -> None:
    """Skip when a release tag is unreachable, unless CI promised to fetch it.

    Local development keeps its ordinary skip: a clone that lacks one of
    these tags for whatever reason should not fail the suite over it. CI
    fetches the tags itself (see the module docstring), so there a miss is a
    real regression in the checkout or the fetch step, not an environment
    limitation -- and must fail loudly instead of silently losing coverage.
    """

    import pytest

    if REQUIRE_RELEASE_TAGS:
        pytest.fail(message)
    pytest.skip(message)
