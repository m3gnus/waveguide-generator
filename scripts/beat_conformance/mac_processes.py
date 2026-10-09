"""Darwin 64-bit process snapshots without spawning a sampler subprocess.

KERN_PROC_ALL supplies ancestry and microsecond birth identities in one kernel
snapshot; libproc supplies RSS only for the invocation's owned processes. The
64-bit kinfo_proc ABI is validated against libproc for this process at startup.
Unsupported ABIs keep the existing portable reader rather than guessing.
"""
from __future__ import annotations

import ctypes as C
import errno
import os
import platform
import struct


class BSDInfo(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid',
        'rgid', 'svuid', 'svgid', 'reserved')]
    _fields_ += [('comm', C.c_char * 16), ('name', C.c_char * 32)]
    _fields_ += [(name, C.c_uint32) for name in ('nfiles', 'pgid', 'jobc', 'tdev', 'tpgid')]
    _fields_ += [('nice', C.c_int32), ('sec', C.c_uint64), ('usec', C.c_uint64)]


class TaskInfo(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in (
        'virtual', 'rss', 'total_user', 'total_system', 'threads_user', 'threads_system')]
    _fields_ += [(name, C.c_int32) for name in (
        'policy', 'faults', 'pageins', 'cow', 'sent', 'received', 'mach', 'unix',
        'csw', 'threads', 'running', 'priority')]


class DarwinProcesses:
    def __init__(self):
        if platform.system() != 'Darwin' or platform.machine() not in ('arm64', 'x86_64'):
            raise NotImplementedError('Unsupported Darwin process ABI')
        if C.sizeof(C.c_void_p) != 8 or C.sizeof(BSDInfo) != 136 or C.sizeof(TaskInfo) != 96:
            raise NotImplementedError('Unsupported Darwin process ABI')
        self.lib = C.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        self.lib.proc_pidinfo.argtypes = [C.c_int, C.c_int, C.c_uint64, C.c_void_p, C.c_int]
        self.lib.proc_pidinfo.restype = C.c_int
        self.kernel = C.CDLL(None, use_errno=True)
        self.kernel.sysctl.argtypes = [C.POINTER(C.c_int), C.c_uint, C.c_void_p,
                                      C.POINTER(C.c_size_t), C.c_void_p, C.c_size_t]
        self.kernel.sysctl.restype = C.c_int
        own = self.ancestry().get(os.getpid())
        info = BSDInfo()
        size = self.lib.proc_pidinfo(os.getpid(), 3, 0, C.byref(info), C.sizeof(info))
        if size != C.sizeof(info) or own is None or (
            own['ppid'], own['start']) != (info.ppid, (info.sec, info.usec)):
            raise NotImplementedError('Darwin ancestry ABI validation failed')

    def ancestry(self):
        mib = (C.c_int * 4)(1, 14, 0, 0)  # CTL_KERN, KERN_PROC, KERN_PROC_ALL
        size = C.c_size_t()
        if self.kernel.sysctl(mib, 4, None, C.byref(size), None, 0):
            raise OSError(C.get_errno(), 'Cannot size Darwin process snapshot')
        for _ in range(5):
            capacity = size.value + size.value // 2 + 64 * 648
            buf = C.create_string_buffer(capacity)
            size.value = capacity
            if self.kernel.sysctl(mib, 4, buf, C.byref(size), None, 0):
                code = C.get_errno()
                if code == errno.ENOMEM:
                    continue
                raise OSError(code, 'Cannot read complete Darwin process snapshot')
            if size.value > capacity or size.value % 648:
                raise ValueError('Unexpected Darwin process snapshot ABI')
            rows = {}
            for offset in range(0, size.value, 648):
                pid = struct.unpack_from('=i', buf, offset + 40)[0]
                status = buf[offset + 36][0]
                if pid <= 0 or status == 5:  # SZOMB owns no resident memory
                    continue
                rows[pid] = {
                    'ppid': struct.unpack_from('=i', buf, offset + 560)[0],
                    'start': struct.unpack_from('=qq', buf, offset), 'rss': 0}
            return rows
        raise OSError(errno.EAGAIN, 'Darwin process snapshot keeps growing')

    def task_rss(self, pid):
        info = TaskInfo()
        C.set_errno(0)
        size = self.lib.proc_pidinfo(pid, 4, 0, C.byref(info), C.sizeof(info))
        if size != C.sizeof(info):
            raise OSError(C.get_errno(), f'Cannot read owned PID {pid} RSS ({size} bytes)')
        return int(info.rss)

    def read_owned(self, select, remember=None):
        # Exit can expose ancestry briefly after task information disappears.
        # Retry the complete transaction, never substitute zero for live RSS.
        for attempt in range(3):
            rows = self.ancestry()
            selected = select(rows)
            # Fresh ancestry establishes ownership independently of RSS. Keep
            # its birth identity even if the process exits/reuses its PID before
            # the validation snapshot; cleanup still requires a fresh match.
            if remember is not None:
                for pid in selected:
                    remember(pid, rows[pid]['start'])
            values, errors = {}, {}
            for pid in selected:
                try:
                    values[pid] = self.task_rss(pid)
                except OSError as exc:
                    errors[pid] = exc
            after = self.ancestry()
            live_errors = {}
            for pid in selected:
                current = after.get(pid)
                if current is None or current['start'] != rows[pid]['start']:
                    del rows[pid]
                elif pid in errors:
                    live_errors[pid] = errors[pid]
                elif current['ppid'] != rows[pid]['ppid']:
                    live_errors[pid] = OSError(errno.EAGAIN, f'Owned PID {pid} reparented during RSS read')
                else:
                    rows[pid]['rss'] = values[pid]
            if not live_errors:
                return rows
            if attempt == 2:
                raise next(iter(live_errors.values()))
        raise AssertionError('Unreachable')
