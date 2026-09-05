"""Legacy staging-ACL detection, repair, and the boot sweep that drives them.

The matcher is pure and is tested everywhere. The descriptor round-trip is
Windows-only, and is skipped rather than faked elsewhere -- a fake would assert
this module's own idea of a security descriptor rather than the operating
system's.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch

import pytest

from server.platform import acl_migration
from server.platform import acl_repair
from server.platform.acl_repair import (
    ADMINISTRATORS_SID,
    AccessEntry,
    FILE_ALL_ACCESS,
    OWNER_RIGHTS_SID,
    Outcome,
    RepairCounts,
    SYSTEM_SID,
    entries_match_staging_pattern,
    repair_path,
    sweep,
)

WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="Security descriptors are a Windows concept"
)

CONTAINER_AND_OBJECT_INHERIT = 0x03
INHERITED = 0x10


@pytest.fixture
def ordinary_dir():
    """A directory with ordinary, inheriting permissions.

    Deliberately not `tmp_path`. pytest builds its base directory with mode
    0o700, which on Windows *is* the descriptor this module repairs -- so
    `tmp_path` is itself protected, carrying exactly SYSTEM, Administrators and
    OWNER RIGHTS. A file published inside it therefore *inherits* those three
    entries and is not protected, which is a correctly-inheriting file and is
    rightly not repaired. The defect cannot be reproduced there at all.

    A plain `mkdir` under the temp root inherits normally, the way a user's
    Documents folder does, which is what makes the reproduction real. The same
    reasoning is recorded in `test_workspace_write_export.py`.
    """

    root = Path(tempfile.gettempdir()) / f"wg2-acl-test-{os.getpid()}-{id(object())}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def staging_entries(flags: int = 0) -> list[AccessEntry]:
    """The three entries `tempfile.mkdtemp`'s 0o700 leaves on Windows."""

    return [
        AccessEntry(SYSTEM_SID, FILE_ALL_ACCESS, flags, 0),
        AccessEntry(ADMINISTRATORS_SID, FILE_ALL_ACCESS, flags, 0),
        AccessEntry(OWNER_RIGHTS_SID, FILE_ALL_ACCESS, flags, 0),
    ]


class TestMatcher:
    """What may be repaired, and -- mostly -- what may not.

    Every negative case below is a descriptor that resembles the broken one.
    Widening access to a file somebody restricted on purpose would be a worse
    failure than declining to repair one this application broke, so the match
    is exact and these tests are the record of that.
    """

    def test_a_staged_file_descriptor_matches(self) -> None:
        assert entries_match_staging_pattern(
            True, staging_entries(), is_directory=False
        )

    def test_a_staged_directory_carries_the_two_inheritance_bits(self) -> None:
        # Measured on a real affected install: files carry no flags, the
        # staging directories left behind carry OBJECT|CONTAINER_INHERIT.
        assert entries_match_staging_pattern(
            True,
            staging_entries(CONTAINER_AND_OBJECT_INHERIT),
            is_directory=True,
        )

    def test_a_file_may_not_carry_inheritance_flags(self) -> None:
        assert not entries_match_staging_pattern(
            True,
            staging_entries(CONTAINER_AND_OBJECT_INHERIT),
            is_directory=False,
        )

    def test_an_unprotected_dacl_is_never_repaired(self) -> None:
        # Inheritance is already on, so this descriptor cannot be the one that
        # loses a file when its owner changes.
        assert not entries_match_staging_pattern(
            False, staging_entries(), is_directory=False
        )

    def test_an_inherited_entry_disqualifies_it(self) -> None:
        entries = staging_entries()
        entries[0] = AccessEntry(SYSTEM_SID, FILE_ALL_ACCESS, INHERITED, 0)
        assert not entries_match_staging_pattern(True, entries, is_directory=False)

    def test_an_extra_principal_disqualifies_it(self) -> None:
        entries = [*staging_entries(), AccessEntry("S-1-5-32-545", FILE_ALL_ACCESS, 0, 0)]
        assert not entries_match_staging_pattern(True, entries, is_directory=False)

    def test_a_missing_principal_disqualifies_it(self) -> None:
        assert not entries_match_staging_pattern(
            True, staging_entries()[:2], is_directory=False
        )

    def test_a_different_principal_disqualifies_it(self) -> None:
        entries = staging_entries()
        entries[2] = AccessEntry("S-1-5-21-1-2-3-1000", FILE_ALL_ACCESS, 0, 0)
        assert not entries_match_staging_pattern(True, entries, is_directory=False)

    def test_a_narrower_mask_disqualifies_it(self) -> None:
        # Someone tightened this deliberately; it is not ours to widen.
        entries = staging_entries()
        entries[1] = AccessEntry(ADMINISTRATORS_SID, 0x1200A9, 0, 0)
        assert not entries_match_staging_pattern(True, entries, is_directory=False)

    def test_a_deny_entry_disqualifies_it(self) -> None:
        entries = staging_entries()
        entries[0] = AccessEntry(SYSTEM_SID, FILE_ALL_ACCESS, 0, 1)
        assert not entries_match_staging_pattern(True, entries, is_directory=False)

    def test_an_empty_dacl_is_not_the_staging_pattern(self) -> None:
        assert not entries_match_staging_pattern(True, [], is_directory=False)


class TestReparseBoundary:
    """Links are rejected before descriptor reads, repairs, or traversal."""

    def test_repair_path_rejects_a_file_link_before_reading_its_descriptor(
        self, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside.txt"
        outside.write_text("unchanged", encoding="utf-8")
        link = tmp_path / "external-link"
        link.symlink_to(outside)

        with (
            patch.object(acl_repair, "WINDOWS", True),
            patch.object(
                acl_repair,
                "read_dacl",
                side_effect=AssertionError("descriptor read crossed a link"),
            ),
            patch.object(
                acl_repair,
                "_reset_to_inherit",
                side_effect=AssertionError("ACL reset crossed a link"),
            ),
        ):
            assert repair_path(link) is Outcome.NOT_POISONED

        assert outside.read_text(encoding="utf-8") == "unchanged"

    def test_repair_path_rechecks_before_the_acl_reset(self, tmp_path: Path) -> None:
        target = tmp_path / "design.json"
        target.write_text("unchanged", encoding="utf-8")

        with (
            patch.object(acl_repair, "WINDOWS", True),
            patch.object(acl_repair, "descriptor_is_poisoned", return_value=True),
            patch.object(acl_repair, "path_has_reparse_point", return_value=True),
            patch.object(
                acl_repair,
                "_reset_to_inherit",
                side_effect=AssertionError("ACL reset crossed a replacement link"),
            ),
        ):
            assert repair_path(target) is Outcome.NOT_POISONED

        assert target.read_text(encoding="utf-8") == "unchanged"

    def test_repair_path_rejects_an_ordinary_leaf_below_a_directory_link(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "workspace"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        child = outside / "design.json"
        child.write_text("unchanged", encoding="utf-8")
        linked_directory = root / "linked-directory"
        linked_directory.symlink_to(outside, target_is_directory=True)

        with (
            patch.object(acl_repair, "WINDOWS", True),
            patch.object(
                acl_repair,
                "read_dacl",
                side_effect=AssertionError("descriptor read crossed a linked parent"),
            ),
            patch.object(
                acl_repair,
                "_reset_to_inherit",
                side_effect=AssertionError("ACL reset crossed a linked parent"),
            ),
        ):
            assert (
                repair_path(linked_directory / child.name, root=root)
                is Outcome.NOT_POISONED
            )

        assert child.read_text(encoding="utf-8") == "unchanged"

    def test_sweep_excludes_file_and_directory_links_before_repair_or_walk(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "workspace"
        root.mkdir()
        ordinary = root / "ordinary.txt"
        ordinary.write_text("inside", encoding="utf-8")
        outside_file = tmp_path / "outside.txt"
        outside_file.write_text("unchanged", encoding="utf-8")
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside_child = outside_dir / "child.txt"
        outside_child.write_text("unchanged", encoding="utf-8")
        file_link = root / "file-link"
        file_link.symlink_to(outside_file)
        directory_link = root / "directory-link"
        directory_link.symlink_to(outside_dir, target_is_directory=True)
        seen: list[Path] = []

        def record(path: Path, **_kwargs) -> Outcome:
            seen.append(path)
            return Outcome.NOT_POISONED

        with (
            patch.object(acl_repair, "WINDOWS", True),
            patch.object(acl_repair, "repair_path", side_effect=record),
        ):
            counts = sweep(root)

        assert root in seen
        assert ordinary in seen
        assert file_link not in seen
        assert directory_link not in seen
        assert outside_child not in seen
        assert counts.skipped >= 2
        assert outside_file.read_text(encoding="utf-8") == "unchanged"
        assert outside_child.read_text(encoding="utf-8") == "unchanged"

    def test_a_link_used_as_the_chosen_root_is_never_repaired_or_walked(
        self, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        child = outside / "child.txt"
        child.write_text("unchanged", encoding="utf-8")
        root_link = tmp_path / "selected-workspace"
        root_link.symlink_to(outside, target_is_directory=True)

        with (
            patch.object(acl_repair, "WINDOWS", True),
            patch.object(
                acl_repair,
                "repair_path",
                side_effect=AssertionError("selected link reached repair"),
            ),
        ):
            counts = sweep(root_link)

        assert counts.scanned == 1
        assert counts.skipped == 1
        assert child.read_text(encoding="utf-8") == "unchanged"

    def test_an_ordinary_root_below_a_link_is_never_repaired_or_walked(
        self, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        nested = outside / "nested"
        nested.mkdir(parents=True)
        child = nested / "child.txt"
        child.write_text("unchanged", encoding="utf-8")
        redirected = tmp_path / "redirected"
        redirected.symlink_to(outside, target_is_directory=True)

        with (
            patch.object(acl_repair, "WINDOWS", True),
            patch.object(
                acl_repair,
                "repair_path",
                side_effect=AssertionError("descendant of link reached repair"),
            ),
        ):
            counts = sweep(redirected / nested.name)

        assert counts.scanned == 1
        assert counts.skipped == 1
        assert child.read_text(encoding="utf-8") == "unchanged"

    @WINDOWS_ONLY
    def test_a_windows_junction_is_excluded_before_repair_or_traversal(
        self, ordinary_dir: Path
    ) -> None:
        import subprocess

        root = ordinary_dir / "workspace"
        root.mkdir()
        outside = ordinary_dir / "outside"
        outside.mkdir()
        child = outside / "child.txt"
        child.write_text("unchanged", encoding="utf-8")
        junction = root / "junction"
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            check=True,
            capture_output=True,
            text=True,
        )
        with (
            patch.object(
                acl_repair,
                "read_dacl",
                side_effect=AssertionError("descriptor read crossed a junction"),
            ),
            patch.object(
                acl_repair,
                "_reset_to_inherit",
                side_effect=AssertionError("ACL reset crossed a junction"),
            ),
        ):
            assert repair_path(junction / child.name, root=root) is Outcome.NOT_POISONED
        seen: list[Path] = []

        def record(path: Path, **_kwargs) -> Outcome:
            seen.append(path)
            return Outcome.NOT_POISONED

        with patch.object(acl_repair, "repair_path", side_effect=record):
            sweep(root)

        assert junction not in seen
        assert child not in seen
        assert child.read_text(encoding="utf-8") == "unchanged"


@WINDOWS_ONLY
class TestDescriptorRoundTrip:
    """Against the real API, on a descriptor produced the way the bug produced it."""

    @staticmethod
    def poisoned_file(parent: Path) -> Path:
        """Reproduce the defect exactly: stage under mkdtemp, then `os.replace`."""

        staging = Path(tempfile.mkdtemp(prefix=".wg2-test-staging-", dir=parent))
        staged = staging / "design.json"
        staged.write_text("{}", encoding="utf-8")
        published = parent / "design.json"
        os.replace(staged, published)
        os.rmdir(staging)
        return published

    def test_a_republished_file_is_detected_and_repaired(self, ordinary_dir: Path) -> None:
        from server.platform.acl_repair import descriptor_is_poisoned, read_dacl

        published = self.poisoned_file(ordinary_dir)
        assert descriptor_is_poisoned(published)

        assert repair_path(published) is Outcome.REPAIRED

        protected, entries = read_dacl(published)
        assert not protected
        assert entries, "the repaired file must inherit something from its parent"
        assert all(entry.flags & INHERITED for entry in entries)
        # The repair is to the access-control list alone.
        assert published.read_text(encoding="utf-8") == "{}"

    def test_repairing_twice_is_a_no_op(self, ordinary_dir: Path) -> None:
        published = self.poisoned_file(ordinary_dir)

        assert repair_path(published) is Outcome.REPAIRED
        assert repair_path(published) is Outcome.NOT_POISONED

    def test_an_ordinary_file_is_left_alone(self, ordinary_dir: Path) -> None:
        ordinary = ordinary_dir / "written-in-place.json"
        ordinary.write_text("{}", encoding="utf-8")

        assert repair_path(ordinary) is Outcome.NOT_POISONED

    def test_a_sweep_reports_what_it_did(self, ordinary_dir: Path) -> None:
        nested = ordinary_dir / "Horn_A"
        nested.mkdir()
        self.poisoned_file(nested)
        (nested / "ordinary.csv").write_text("a,b\n", encoding="utf-8")

        counts = sweep(ordinary_dir)

        assert counts.repaired == 1
        assert counts.scanned >= 3
        assert counts.failed == 0
        assert not counts.truncated

    def test_a_sweep_stops_at_its_limit_and_says_so(self, ordinary_dir: Path) -> None:
        for index in range(5):
            (ordinary_dir / f"file{index}.txt").write_text("x", encoding="utf-8")

        counts = sweep(ordinary_dir, limit=3)

        assert counts.truncated
        assert counts.scanned == 3


class TestBootSweep:
    """The marker, and the rule for when a root is finished."""

    @staticmethod
    def record(counts: dict, *, elevated: bool = False) -> dict:
        return {
            "version": acl_migration.SWEEP_VERSION,
            "elevated": elevated,
            "counts": {"unreadable": 0, "failed": 0, "truncated": False, **counts},
        }

    def test_a_clean_root_is_finished_for_good(self) -> None:
        assert acl_migration._already_finished(self.record({}), elevated_now=False)
        assert acl_migration._already_finished(self.record({}), elevated_now=True)

    def test_a_truncated_walk_never_established_that_it_was_clean(self) -> None:
        assert not acl_migration._already_finished(
            self.record({"truncated": True}), elevated_now=False
        )

    def test_damage_is_revisited_only_when_this_run_can_reach_further(self) -> None:
        """Elevation is the one thing that changes the answer.

        Without this the app would walk a permanently damaged workspace in full
        at every boot, forever, to reach the same result.
        """

        for damaged in ({"unreadable": 3}, {"failed": 1}):
            unelevated_sweep = self.record(damaged, elevated=False)
            # Same privilege as last time: nothing new to learn.
            assert acl_migration._already_finished(unelevated_sweep, elevated_now=False)
            # More privilege than last time: worth another look.
            assert not acl_migration._already_finished(
                unelevated_sweep, elevated_now=True
            )
            # Already tried elevated; a plain run cannot beat it.
            assert acl_migration._already_finished(
                self.record(damaged, elevated=True), elevated_now=False
            )

    def test_a_root_swept_by_an_older_version_is_revisited(self) -> None:
        assert not acl_migration._already_finished(
            {
                "version": acl_migration.SWEEP_VERSION - 1,
                "counts": {"unreadable": 0, "failed": 0, "truncated": False},
            },
            elevated_now=False,
        )

    def test_a_missing_or_malformed_record_is_not_finished(self) -> None:
        for record in (None, {}, "done", {"version": acl_migration.SWEEP_VERSION}):
            assert not acl_migration._already_finished(record, elevated_now=False)

    def test_a_reparse_point_cannot_be_used_as_a_migration_root(
        self, tmp_path: Path
    ) -> None:
        data_root = tmp_path / "data"
        data_root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        nested = outside / "nested"
        nested.mkdir()
        redirected_parent = tmp_path / "redirected-parent"
        redirected_parent.symlink_to(outside, target_is_directory=True)
        selected = redirected_parent / nested.name
        swept: list[Path] = []

        def record(root: Path) -> RepairCounts:
            swept.append(root)
            return RepairCounts()

        with (
            patch.object(acl_migration, "WINDOWS", True),
            patch.object(acl_migration, "process_is_elevated", return_value=False),
            patch.object(acl_migration, "sweep", side_effect=record),
            patch.object(acl_migration, "_write_marker"),
        ):
            acl_migration.repair_legacy_acls(data_root, selected)

        assert swept == [data_root]

    def test_a_redirected_data_root_is_rejected_before_marker_access(
        self, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        redirected = tmp_path / "redirected-data"
        redirected.symlink_to(outside, target_is_directory=True)

        with (
            patch.object(acl_migration, "WINDOWS", True),
            patch.object(
                acl_migration,
                "_read_marker",
                side_effect=AssertionError("marker read crossed a redirected root"),
            ),
            patch.object(
                acl_migration,
                "_write_marker",
                side_effect=AssertionError("marker write crossed a redirected root"),
            ),
        ):
            assert acl_migration.repair_legacy_acls(redirected) == {}

    def test_a_marker_symlink_cannot_redirect_migration_state_writes(
        self, tmp_path: Path
    ) -> None:
        data_root = tmp_path / "data"
        data_root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("unchanged", encoding="utf-8")
        marker = data_root / acl_migration.MARKER_NAME
        marker.symlink_to(outside)

        with (
            patch.object(acl_migration, "WINDOWS", True),
            patch.object(
                acl_migration,
                "_read_marker",
                side_effect=AssertionError("marker read followed a symlink"),
            ),
            patch.object(acl_migration, "process_is_elevated", return_value=False),
            patch.object(
                acl_migration, "sweep", return_value=RepairCounts(repaired=1)
            ),
            patch.object(
                acl_migration,
                "_write_marker",
                side_effect=AssertionError("marker write followed a symlink"),
            ),
        ):
            results = acl_migration.repair_legacy_acls(data_root)

        assert results[str(data_root)].repaired == 1
        assert outside.read_text(encoding="utf-8") == "unchanged"

    @WINDOWS_ONLY
    def test_the_sweep_records_a_marker_and_then_skips_the_root(
        self, ordinary_dir: Path
    ) -> None:
        data_root = ordinary_dir / "data"
        data_root.mkdir()

        first = acl_migration.repair_legacy_acls(data_root)
        assert str(data_root) in first

        marker = json.loads(
            (data_root / acl_migration.MARKER_NAME).read_text(encoding="utf-8")
        )
        assert marker["roots"][str(data_root)]["version"] == acl_migration.SWEEP_VERSION

        # Clean the first time means never again.
        assert acl_migration.repair_legacy_acls(data_root) == {}

    @WINDOWS_ONLY
    def test_the_workspace_is_swept_as_well_as_the_data_root(
        self, ordinary_dir: Path
    ) -> None:
        """The workspace is where the damage actually is.

        Measured on an affected install: two poisoned directories under the
        data root, 53 poisoned paths under the workspace -- including every
        `design.json` the export refusal is about. A sweep scoped to the
        application's own data directory would have repaired almost nothing.
        """

        data_root = ordinary_dir / "data"
        data_root.mkdir()
        workspace = ordinary_dir / "runs"
        workspace.mkdir()
        TestDescriptorRoundTrip.poisoned_file(workspace)

        results = acl_migration.repair_legacy_acls(data_root, workspace)

        assert results[str(workspace)].repaired == 1

    def test_posix_reports_nothing_and_writes_no_marker(self, tmp_path: Path) -> None:
        if os.name == "nt":
            pytest.skip("POSIX behaviour")
        data_root = tmp_path / "data"
        data_root.mkdir()

        assert acl_migration.repair_legacy_acls(data_root) == {}
        assert not (data_root / acl_migration.MARKER_NAME).exists()

    def test_feedback_labels_fresh_repairs_for_both_roots(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data"
        workspace = tmp_path / "runs"
        data_root.mkdir()
        workspace.mkdir()
        fresh = {
            str(data_root): RepairCounts(repaired=2),
            str(workspace): RepairCounts(repaired=3, unreadable=1),
        }

        with (
            patch.object(acl_migration, "WINDOWS", True),
            patch.object(acl_migration, "process_is_elevated", return_value=False),
        ):
            feedback = acl_migration.legacy_acl_repair_feedback(
                data_root, workspace, fresh
            )

        assert feedback == {
            "platform": "windows",
            "roots": [
                {"scope": "appData", "source": "current", "repaired": 2,
                 "remaining": 0, "unreadable": 0, "failed": 0,
                 "truncated": False, "administratorMayHelp": 0},
                {"scope": "workspace", "source": "current", "repaired": 3,
                 "remaining": 1, "unreadable": 1, "failed": 0,
                 "truncated": False, "administratorMayHelp": 1},
            ],
        }

    def test_successful_repeat_does_not_repeat_an_old_repair_claim(
        self, tmp_path: Path
    ) -> None:
        data_root = tmp_path / "data"
        data_root.mkdir()
        self._write_feedback_marker(
            data_root, RepairCounts(scanned=8, repaired=4, skipped=4)
        )

        with patch.object(acl_migration, "WINDOWS", True):
            feedback = acl_migration.legacy_acl_repair_feedback(data_root, None, {})

        assert feedback == {"platform": "windows", "roots": []}

    def test_unresolved_repeat_keeps_the_previous_result_visible(
        self, tmp_path: Path
    ) -> None:
        data_root = tmp_path / "data"
        data_root.mkdir()
        self._write_feedback_marker(
            data_root,
            RepairCounts(scanned=5, repaired=1, skipped=2, unreadable=2),
        )

        with patch.object(acl_migration, "WINDOWS", True):
            feedback = acl_migration.legacy_acl_repair_feedback(data_root, None, {})

        assert feedback["roots"] == [
            {"scope": "appData", "source": "previous", "repaired": 1,
             "remaining": 2, "unreadable": 2, "failed": 0,
             "truncated": False, "administratorMayHelp": 2}
        ]

    def test_elevated_failure_is_not_reported_as_repairable_by_elevation(
        self, tmp_path: Path
    ) -> None:
        data_root = tmp_path / "data"
        data_root.mkdir()
        self._write_feedback_marker(
            data_root, RepairCounts(scanned=1, failed=1), elevated=True
        )

        with patch.object(acl_migration, "WINDOWS", True):
            feedback = acl_migration.legacy_acl_repair_feedback(data_root, None, {})

        root = feedback["roots"][0]
        assert root["remaining"] == 1
        assert root["administratorMayHelp"] == 0

    @staticmethod
    def _write_feedback_marker(
        data_root: Path, counts: RepairCounts, *, elevated: bool = False
    ) -> None:
        (data_root / acl_migration.MARKER_NAME).write_text(
            json.dumps({"roots": {str(data_root): {
                "version": acl_migration.SWEEP_VERSION,
                "elevated": elevated,
                "counts": {
                    "scanned": counts.scanned,
                    "repaired": counts.repaired,
                    "skipped": counts.skipped,
                    "unreadable": counts.unreadable,
                    "failed": counts.failed,
                    "truncated": counts.truncated,
                },
            }}}),
            encoding="utf-8",
        )
