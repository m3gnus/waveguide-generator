"""Windows owner and private DACL checks using only ctypes."""

from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from pathlib import Path
import struct


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", w.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", w.BOOL)]


class _AclSize(ctypes.Structure):
    _fields_ = [("count", w.DWORD), ("used", w.DWORD), ("free", w.DWORD)]


class _AceHeader(ctypes.Structure):
    _fields_ = [("kind", w.BYTE), ("flags", w.BYTE), ("size", w.WORD)]


def _nt_sid(*subauthorities: int) -> bytes:
    return bytes([1, len(subauthorities)]) + (5).to_bytes(6, "big") + struct.pack(
        "<" + "I" * len(subauthorities), *subauthorities
    )


ADMINISTRATORS = _nt_sid(32, 544)
LOCAL_SYSTEM = _nt_sid(18)
TRUSTED_INSTALLER = _nt_sid(80, 956008885, 3418522649, 1831038044, 1853292631, 2271478464)
PRIVILEGED_SIDS = {ADMINISTRATORS, LOCAL_SYSTEM, TRUSTED_INSTALLER}


class WindowsSecurity:
    """Keep tokens private to the user and Windows' local privileged principals.

    Elevated processes commonly create Administrators-owned files. Accept that
    owner only with enabled Administrators membership in the effective token;
    LocalSystem and TrustedInstaller also already control local security. None
    of these owners excuses a permissive DACL. New roots remain user-only.
    """

    def __init__(self) -> None:
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        pointer = ctypes.c_void_p
        signatures = {
            "GetCurrentProcess": (self.kernel, [], w.HANDLE),
            "CloseHandle": (self.kernel, [w.HANDLE], w.BOOL),
            "LocalFree": (self.kernel, [pointer], pointer),
            "CreateDirectoryW": (self.kernel, [w.LPCWSTR, ctypes.POINTER(_SecurityAttributes)], w.BOOL),
            "OpenProcessToken": (self.advapi, [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], w.BOOL),
            "GetTokenInformation": (self.advapi, [w.HANDLE, ctypes.c_int, pointer, w.DWORD,
                                                 ctypes.POINTER(w.DWORD)], w.BOOL),
            "CheckTokenMembership": (self.advapi, [w.HANDLE, pointer, ctypes.POINTER(w.BOOL)], w.BOOL),
            "GetLengthSid": (self.advapi, [pointer], w.DWORD),
            "ConvertSidToStringSidW": (self.advapi, [pointer, ctypes.POINTER(w.LPWSTR)], w.BOOL),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW":
                (self.advapi, [w.LPCWSTR, w.DWORD, ctypes.POINTER(pointer), ctypes.POINTER(w.DWORD)], w.BOOL),
            "GetNamedSecurityInfoW": (self.advapi, [w.LPCWSTR, ctypes.c_int, w.DWORD,
                ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer), pointer,
                ctypes.POINTER(pointer)], w.DWORD),
            "GetAclInformation": (self.advapi, [pointer, pointer, w.DWORD, ctypes.c_int], w.BOOL),
            "GetAce": (self.advapi, [pointer, w.DWORD, ctypes.POINTER(pointer)], w.BOOL),
        }
        for name, (library, args, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = args, result
        self.sid, self.sid_text = self._current_user()

    def _require(self, success: object) -> None:
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

    def _sid_bytes(self, sid: int) -> bytes:
        if not sid:
            raise ValueError("Missing Windows owner SID")
        return ctypes.string_at(sid, self.advapi.GetLengthSid(sid))

    def _current_user(self) -> tuple[bytes, str]:
        token = w.HANDLE()
        self._require(self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, ctypes.byref(token)))
        try:
            size = w.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            self._require(size.value)
            buffer = ctypes.create_string_buffer(size.value)
            self._require(self.advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)))
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
            text = w.LPWSTR()
            self._require(self.advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)))
            try:
                return self._sid_bytes(sid), text.value
            finally:
                self.kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        finally:
            self.kernel.CloseHandle(token)

    def _is_member(self, sid: bytes) -> bool:
        enabled = w.BOOL()
        buffer = ctypes.create_string_buffer(sid)
        # NULL checks the effective token, including UAC deny-only restrictions.
        self._require(self.advapi.CheckTokenMembership(None, buffer, ctypes.byref(enabled)))
        return bool(enabled.value)

    def create_directory(self, path: Path) -> None:
        descriptor = ctypes.c_void_p()
        sddl = f"O:{self.sid_text}D:P(A;OICI;FA;;;{self.sid_text})"
        self._require(self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None))
        try:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            if not self.kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
                    raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.kernel.LocalFree(descriptor)

    def check_path(self, path: Path) -> None:
        owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        error = self.advapi.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None,
                                                 ctypes.byref(dacl), None, ctypes.byref(descriptor))
        if error:
            raise ctypes.WinError(error)
        try:
            owner_sid = self._sid_bytes(owner.value)
            if owner_sid != self.sid:
                if owner_sid not in PRIVILEGED_SIDS:
                    raise ValueError("Registry path belongs to another Windows user")
                if owner_sid == ADMINISTRATORS and not self._is_member(ADMINISTRATORS):
                    raise ValueError("Registry Administrators owner requires enabled token membership")
            if not dacl.value:
                raise ValueError("Registry path has an unrestricted Windows DACL")
            size = _AclSize()
            self._require(self.advapi.GetAclInformation(dacl, ctypes.byref(size), ctypes.sizeof(size), 2))
            allowed = False
            for index in range(size.count):
                ace = ctypes.c_void_p()
                self._require(self.advapi.GetAce(dacl, index, ctypes.byref(ace)))
                header = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
                # Only ordinary allow/deny ACEs are accepted. Unknown or
                # object/callback ACEs cannot establish an owner-private ACL.
                if header.kind not in {0, 1} or header.size < 16:
                    raise ValueError("Unsupported Windows registry ACE")
                sid = self._sid_bytes(ace.value + 8)
                if header.kind == 0:
                    if sid != self.sid and sid not in PRIVILEGED_SIDS:
                        # Read access also exposes authentication secrets; reject
                        # untrusted readers as well as writers, even with deny ACEs.
                        raise ValueError("Registry Windows DACL grants another user access")
                    if not header.flags & 0x08:  # INHERIT_ONLY_ACE grants no access here.
                        allowed |= sid == self.sid or (sid == ADMINISTRATORS and self._is_member(sid))
            if not allowed:
                raise ValueError("Registry Windows DACL does not grant its owner access")
        finally:
            self.kernel.LocalFree(descriptor)
