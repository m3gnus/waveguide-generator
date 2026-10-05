"""Embedded native-owned admission hook, before every bundled Python import."""
import os
from pathlib import Path
import sys


def _ordinary_path(value):
    # Pre-import counterpart of launchers.update_lock.ordinary_path. This hook
    # is embedded in the runtime and cannot import the app layer; keep both
    # drive/UNC conversions in agreement, preserving other extended forms.
    if (value[:8].upper() == "\\\\?\\UNC\\"
            and len(parts := value[8:].split("\\")) >= 2
            and parts[0] and parts[1]):
        return "\\\\" + value[8:]
    if (value.startswith("\\\\?\\") and len(value) >= 7
            and value[4] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
            and value[5:7] == ":\\"):
        return value[4:]
    return value


def _admit():
    import ctypes
    from ctypes import wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    b = ctypes.WinDLL("kernelbase", use_last_error=True)
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.OpenProcess.restype = w.HANDLE
    k.GetCurrentProcess.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    k.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    k.DuplicateHandle.argtypes = [w.HANDLE, w.HANDLE, w.HANDLE, ctypes.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD]
    k.SetHandleInformation.argtypes = [w.HANDLE, w.DWORD, w.DWORD]
    k.SetEvent.argtypes = [w.HANDLE]
    k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k.WaitForSingleObject.restype = w.DWORD
    b.CompareObjectHandles.argtypes = [w.HANDLE, w.HANDLE]
    b.CompareObjectHandles.restype = w.BOOL
    protocol = os.environ.pop("WG_NATIVE_START", None)
    if protocol is None:
        # Private diagnostic invocation through hidden Python is never an
        # inherited admission grant. The normal generated guard checks setup.
        k.OpenMutexW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
        k.OpenMutexW.restype = w.HANDLE
        mutex = k.OpenMutexW(0x100000, False, "WaveguideGeneratorSetup")
        if mutex:
            k.CloseHandle(mutex)
            raise RuntimeError("setup is active without native admission")
        if ctypes.get_last_error() != 2:
            raise RuntimeError("setup exclusion could not be checked")
        return False
    inherited = []
    duplicated = []
    parent = None
    admitted = False
    try:
        fields = [int(value) for value in protocol.split(",")]
        if len(fields) != 7 or any(v < 0 or v >= 2**64 for v in fields):
            raise RuntimeError("invalid native admission fields")
        pid, high, low, ack, release, running, direct = fields
        if not pid or pid != os.getppid() or high >= 2**32 or low >= 2**32 or direct not in (0, 1):
            raise RuntimeError("native admission is not from the actual parent")
        parent = k.OpenProcess(0x1000 | 0x40, False, pid)
        created, exited, kernel, user = (w.FILETIME() for _ in range(4))
        if not parent or not k.GetProcessTimes(parent, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            raise RuntimeError("native parent identity unavailable")
        if (created.dwHighDateTime, created.dwLowDateTime) != (high, low):
            raise RuntimeError("native parent PID was reused")
        image = ctypes.create_unicode_buffer(1024)
        count = w.DWORD(len(image))
        expected = Path(_ordinary_path(sys.executable)).with_name("Waveguide Generator.exe").resolve()
        if not k.QueryFullProcessImageNameW(parent, 0, image, ctypes.byref(count)) or Path(_ordinary_path(image.value)).resolve() != expected:
            raise RuntimeError("native parent image is not this bundle's entry")
        for value in (ack, release, running):
            handle = w.HANDLE(value)
            copy = w.HANDLE()
            if not value or not k.DuplicateHandle(parent, handle, k.GetCurrentProcess(), ctypes.byref(copy), 0, False, 2):
                raise RuntimeError("native capability was not inherited")
            duplicated.append(copy)
            if not b.CompareObjectHandles(handle, copy) or not k.SetHandleInformation(handle, 1, 0):
                raise RuntimeError("native capability does not match the parent object")
            inherited.append(handle)
        if not k.SetEvent(inherited[0]) or k.WaitForSingleObject(inherited[1], 60000) != 0:
            raise RuntimeError("native parent did not release startup admission")
        sys._wg_native_start_admitted = True
        # Keep the inherited Running mutex alive even if the native parent is
        # killed after admission. It is non-inheritable; this process owns it
        # until exit. Workers receive their own native admission.
        sys._wg_native_running_handle = running
        # Every nested sys.executable worker must re-enter native admission and
        # receive its own running exclusion. Keep Python's argv untouched.
        sys.executable = str(expected)
        admitted = True
        return bool(direct)
    finally:
        for handle in duplicated:
            k.CloseHandle(handle)
        for index, handle in enumerate(inherited):
            if index != 2 or not admitted:
                k.CloseHandle(handle)
        if parent:
            k.CloseHandle(parent)


if sys.platform == "win32":
    try:
        _direct_native = _admit()
    except Exception:
        # sitecustomize exceptions are otherwise printed and ignored by site.
        # An invalid capability must terminate, before arbitrary -c/-m code.
        os._exit(4)
    if not _direct_native:
        root = Path(sys.executable).resolve().parent
        sys.path.insert(0, str(root / "recovery"))
        try:
            import wg_bundle_recovery
        except ModuleNotFoundError as exc:
            if exc.name != "wg_bundle_recovery":
                raise
        else:
            wg_bundle_recovery.windows_boot(executable=str(root / "Waveguide Generator.exe"))
        import wg_desktop_bootstrap as wg_desktop_bootstrap
