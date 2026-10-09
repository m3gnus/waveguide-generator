"""Owned RSS transaction races must never produce partial live-tree evidence."""
import errno
from copy import deepcopy

import pytest

from scripts.beat_conformance.mac_processes import DarwinProcesses


def row(start=1, parent=1):
    return {'ppid': parent, 'start': (start, 0), 'rss': 0}


def reader(snapshots, rss):
    instance = object.__new__(DarwinProcesses)
    snapshots = iter(deepcopy(snapshots))
    instance.ancestry = lambda: next(snapshots)
    instance.task_rss = rss
    return instance


def denied(_):
    raise OSError(errno.EPERM, 'denied')


def test_exited_process_is_removed():
    native = reader([{2: row()}, {}], denied)
    assert native.read_owned(lambda rows: set(rows)) == {}


def test_reused_pid_is_removed_even_with_successful_rss():
    native = reader([{2: row()}, {2: row(2)}], lambda _: 999)
    assert native.read_owned(lambda rows: set(rows)) == {}


def test_exit_in_progress_retries_until_disappearance():
    native = reader([{2: row()}, {2: row()}, {2: row()}, {}], denied)
    assert native.read_owned(lambda rows: set(rows)) == {}


def test_live_permission_failure_refuses_evidence():
    native = reader([{2: row()}] * 6, denied)
    with pytest.raises(OSError, match='denied'):
        native.read_owned(lambda rows: set(rows))


def test_reparent_retries_and_preserves_resident_memory():
    native = reader([{2: row()}, {2: row(parent=3)},
                     {2: row(parent=3)}, {2: row(parent=3)}], lambda _: 777)
    assert native.read_owned(lambda rows: set(rows))[2]['rss'] == 777


def test_queries_only_selected_rss():
    queried = []
    def rss(pid):
        queried.append(pid)
        return 100
    native = reader([{2: row(), 3: row()}, {2: row(), 3: row()}], rss)
    native.read_owned(lambda rows: {2})
    assert queried == [2]


def test_sampler_keeps_transaction_selection_when_registry_changes(monkeypatch):
    from scripts.beat_conformance import run_perf as perf
    from scripts.beat_conformance import mac_processes
    monkeypatch.setattr(perf.sys, 'platform', 'darwin')
    scans = []
    def registry(_):
        scans.append(1)
        return set() if len(scans) == 1 else {3}
    monkeypatch.setattr(perf, 'registry_pids', registry)
    class Native:
        def read_owned(self, select, remember=None):
            rows = {1: row(parent=0), 3: row(parent=99)}
            selected = select(rows)
            for pid in selected:
                rows[pid]['rss'] = 100
            return rows
    monkeypatch.setattr(mac_processes, 'DarwinProcesses', Native)
    sampler = perf.RSSSampler(1, ())
    sampler.sample()
    assert sampler.samples[-1]['rss_bytes'] == 100
    assert sampler.samples[-1]['pids'] == [1]
    sampler.sample()
    assert sampler.samples[-1]['rss_bytes'] == 200
    assert sampler.samples[-1]['pids'] == [1, 3]


def test_sampler_does_not_fallback_after_native_read_error(monkeypatch):
    from scripts.beat_conformance import run_perf as perf
    from scripts.beat_conformance import mac_processes
    monkeypatch.setattr(perf.sys, 'platform', 'darwin')
    class Native:
        def read_owned(self, select, remember=None):
            raise OSError(errno.EPERM, 'owned inaccessible')
    monkeypatch.setattr(mac_processes, 'DarwinProcesses', Native)
    sampler = perf.RSSSampler(1, ())
    with pytest.raises(OSError, match='owned inaccessible'):
        sampler.sample()
    assert sampler.method == 'darwin-sysctl-libproc'
    assert sampler.samples == []


def test_sampler_keeps_new_descendant_reparented_during_first_transaction(monkeypatch):
    from scripts.beat_conformance import run_perf as perf
    from scripts.beat_conformance import mac_processes
    monkeypatch.setattr(perf.sys, 'platform', 'darwin')
    before = {10: row(parent=1), 11: row(start=2, parent=10)}
    detached = {10: row(parent=1), 11: row(start=2, parent=1)}
    native = reader([before, detached, detached, detached],
                    lambda pid: {10: 100, 11: 900}[pid])
    monkeypatch.setattr(mac_processes, 'DarwinProcesses', lambda: native)
    sampler = perf.RSSSampler(10, ())
    sampler.sample()
    assert sampler.samples[-1]['rss_bytes'] == 1000
    assert sampler.samples[-1]['pids'] == [10, 11]
    assert sampler.known[11] == (2, 0)


def test_retry_does_not_adopt_reused_detached_descendant(monkeypatch):
    from scripts.beat_conformance import run_perf as perf
    from scripts.beat_conformance import mac_processes
    monkeypatch.setattr(perf.sys, 'platform', 'darwin')
    before = {10: row(parent=1), 11: row(start=2, parent=10)}
    detached = {10: row(parent=1), 11: row(start=2, parent=1)}
    reused = {10: row(parent=1), 11: row(start=3, parent=1)}
    native = reader([before, detached, reused, reused], lambda pid: 100)
    monkeypatch.setattr(mac_processes, 'DarwinProcesses', lambda: native)
    sampler = perf.RSSSampler(10, ())
    sampler.sample()
    assert sampler.samples[-1]['pids'] == [10]
    assert sampler.known[11] == (2, 0)


def test_retry_never_adopts_reused_registry_root(monkeypatch):
    from scripts.beat_conformance import run_perf as perf
    from scripts.beat_conformance import mac_processes
    monkeypatch.setattr(perf.sys, 'platform', 'darwin')
    monkeypatch.setattr(perf, 'registry_pids', lambda _: {11})
    before = {10: row(parent=1), 11: row(start=2, parent=1)}
    reparented = {10: row(parent=1), 11: row(start=2, parent=2)}
    reused = {10: row(parent=1), 11: row(start=3, parent=1)}
    queried = []
    def rss(pid):
        queried.append(pid)
        return 100
    native = reader([before, reparented, reused, reused], rss)
    monkeypatch.setattr(mac_processes, 'DarwinProcesses', lambda: native)
    sampler = perf.RSSSampler(10, ())
    sampler.sample()
    assert sampler.samples[-1]['pids'] == [10]
    assert sampler.known[11] == (2, 0)
    assert queried.count(11) == 1


def test_retry_discovers_children_of_retained_detached_parent(monkeypatch):
    from scripts.beat_conformance import run_perf as perf
    from scripts.beat_conformance import mac_processes
    monkeypatch.setattr(perf.sys, 'platform', 'darwin')
    before = {10: row(parent=1), 11: row(start=2, parent=10)}
    detached = {10: row(parent=1), 11: row(start=2, parent=1)}
    forked = dict(detached)
    forked[12] = row(start=3, parent=11)
    native = reader([before, detached, forked, forked],
                    lambda pid: {10: 100, 11: 900, 12: 500}[pid])
    monkeypatch.setattr(mac_processes, 'DarwinProcesses', lambda: native)
    sampler = perf.RSSSampler(10, ())
    sampler.sample()
    assert sampler.samples[-1]['rss_bytes'] == 1500
    assert sampler.samples[-1]['pids'] == [10, 11, 12]
    assert sampler.known[11] == (2, 0)
    assert sampler.known[12] == (3, 0)


def test_registry_pid_reused_within_read_stays_excluded_on_next_sample(monkeypatch):
    from scripts.beat_conformance import run_perf as perf
    from scripts.beat_conformance import mac_processes
    monkeypatch.setattr(perf.sys, 'platform', 'darwin')
    monkeypatch.setattr(perf, 'registry_pids', lambda _: {11})
    before = {10: row(parent=1), 11: row(start=2, parent=1)}
    reused = {10: row(parent=1), 11: row(start=3, parent=1)}
    native = reader([before, reused, reused, reused], lambda pid: 100)
    monkeypatch.setattr(mac_processes, 'DarwinProcesses', lambda: native)
    sampler = perf.RSSSampler(10, ())
    sampler.sample()
    sampler.sample()
    assert [sample['pids'] for sample in sampler.samples] == [[10], [10]]
    assert sampler.known[11] == (2, 0)
