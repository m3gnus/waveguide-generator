from __future__ import annotations

import ctypes
import os
from pathlib import Path
import types

import pytest

from server.solver.beat_runtime import windows_security as s


def write_pointer(destination, value):
    ctypes.cast(destination, ctypes.POINTER(ctypes.c_void_p))[0] = value


USER = s._nt_sid(21, 11, 22, 33, 1001)
OTHER = s._nt_sid(21, 11, 22, 33, 1002)
EVERYONE = bytes.fromhex("010100000000000100000000")


def _check_fake_dacl(owner, aces, member, refused):
    import struct

    owner_buffer = ctypes.create_string_buffer(owner)
    ace_buffers = [ctypes.create_string_buffer(
        struct.pack("<BBHI", entry[0], entry[2] if len(entry) > 2 else 0,
                    8 + len(entry[1]), entry[3] if len(entry) > 3 else 0x1f01ff) + entry[1]) for entry in aces or []]
    freed, memberships = [], []
    security = s.WindowsSecurity.__new__(s.WindowsSecurity)
    security.sid = USER
    security.kernel = types.SimpleNamespace(LocalFree=lambda value: freed.append(value.value))

    def named_security(path, object_type, information, owner_out, group_out, dacl_out, sacl_out, descriptor_out):
        assert (object_type, information) == (1, 5)  # File, owner + DACL.
        write_pointer(owner_out, ctypes.addressof(owner_buffer))
        write_pointer(dacl_out, 123 if aces is not None else None)
        write_pointer(descriptor_out, 456)
        return 0

    def acl_information(acl, buffer, size, kind):
        ctypes.cast(buffer, ctypes.POINTER(s._AclSize)).contents.count = len(ace_buffers)
        assert kind == 2
        return True

    def get_ace(acl, index, output):
        write_pointer(output, ctypes.addressof(ace_buffers[index]))
        return True

    def membership(token, sid, output):
        assert token is None  # Effective token, not an unfiltered group list.
        memberships.append(ctypes.string_at(sid, len(s.ADMINISTRATORS)))
        ctypes.cast(output, ctypes.POINTER(s.w.BOOL)).contents.value = member
        return True

    security.advapi = types.SimpleNamespace(GetNamedSecurityInfoW=named_security,
        GetLengthSid=lambda sid: 8 + ctypes.string_at(sid, 2)[1] * 4,
        GetAclInformation=acl_information, GetAce=get_ace, CheckTokenMembership=membership)
    if refused is None:
        security.check_path(Path('private-root'))
    else:
        with pytest.raises(ValueError, match=refused):
            security.check_path(Path('private-root'))
    assert freed == [456]
    assert all(sid == s.ADMINISTRATORS for sid in memberships)


@pytest.mark.parametrize(("owner", "aces", "member", "refused"), [
    (USER, [(0, USER)], False, None),
    (OTHER, [(0, USER)], True, "another Windows user"),
    (USER, None, True, "unrestricted"),
    (USER, [], True, "does not grant"),
    (USER, [(0, OTHER)], True, "another user"),
    (USER, [(0, EVERYONE), (0, USER)], True, "another user"),
    (s.ADMINISTRATORS, [(0, USER), (0, OTHER, 0, 2)], True, "another user"),
    (s.LOCAL_SYSTEM, [(0, USER), (0, EVERYONE, 0, 0x120089)], True, "another user"),
    (s.TRUSTED_INSTALLER, [(0, USER), (0, OTHER, 0, 0x40000)], True, "another user"),
    (USER, [(1, OTHER), (0, OTHER), (0, USER)], True, "another user"),
    (USER, [(5, USER)], True, "Unsupported"),
    (USER, [(1, USER)], True, "does not grant"),
    (USER, [(0, USER, 8)], True, "does not grant"),
    (s.ADMINISTRATORS, [(0, USER)], True, None),
    (s.ADMINISTRATORS, [(0, USER)], False, "enabled token membership"),
    (s.ADMINISTRATORS, [(0, s.ADMINISTRATORS)], True, None),
    (s.ADMINISTRATORS, [(0, s.ADMINISTRATORS)], False, "enabled token membership"),
    (s.ADMINISTRATORS, [(0, USER), (0, EVERYONE)], True, "another user"),
    (s.LOCAL_SYSTEM, [(0, USER)], False, None),
    (s.TRUSTED_INSTALLER, [(0, USER)], False, None),
    (s.LOCAL_SYSTEM, [(0, USER), (0, OTHER)], True, "another user"),
    (s.TRUSTED_INSTALLER, [(0, USER), (0, EVERYONE)], True, "another user"),
    (USER, [(0, USER), (0, s.LOCAL_SYSTEM), (0, s.ADMINISTRATORS),
            (0, s.TRUSTED_INSTALLER)], False, None),
    (s.LOCAL_SYSTEM, [(0, s.LOCAL_SYSTEM)], False, "does not grant"),
    (USER, [(0, s.ADMINISTRATORS)], False, "does not grant"),
])
def test_windows_owner_sid_and_dacl_are_validated_with_ctypes_fakes(owner, aces, member, refused):
    _check_fake_dacl(owner, aces, member, refused)


@pytest.mark.parametrize("owner, member, refused", [
    (USER, False, None),
    (s.ADMINISTRATORS, True, None),
    (s.ADMINISTRATORS, False, "enabled token membership"),
    (s.LOCAL_SYSTEM, True, None),
    (s.LOCAL_SYSTEM, False, "does not grant"),
    (s.TRUSTED_INSTALLER, True, None),
    (s.TRUSTED_INSTALLER, False, "does not grant"),
    (OTHER, True, "another Windows user"),
])
def test_python313_private_directory_sy_ba_ow_dacl(owner, member, refused):
    # CPython's protected D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW).
    # GetNamedSecurityInfoW returns the same three ordinary ACEs regardless
    # of the descriptor's SE_DACL_PROTECTED bit.
    aces = [(0, sid, 0x03, 0x1f01ff)
            for sid in [s.LOCAL_SYSTEM, s.ADMINISTRATORS, s.OWNER_RIGHTS]]
    _check_fake_dacl(owner, aces, member, refused)


@pytest.mark.parametrize("owner, flags, refused", [
    (USER, 0x03, None),
    (USER, 0x13, None),
    (USER, 0x0b, "does not grant"),
    (s.ADMINISTRATORS, 0x03, "does not grant"),
    (s.LOCAL_SYSTEM, 0x03, "does not grant"),
    (s.TRUSTED_INSTALLER, 0x03, "does not grant"),
    (OTHER, 0x03, "another Windows user"),
])
def test_owner_rights_only_grants_current_user_access_when_user_owns_object(owner, flags, refused):
    assert s._sid_text(s.OWNER_RIGHTS) == "S-1-3-4"
    _check_fake_dacl(owner, [(0, s.OWNER_RIGHTS, flags)], True, refused)


@pytest.mark.parametrize("sid, sid_text", [
    (OTHER, "S-1-5-21-11-22-33-1002"),
    (EVERYONE, "S-1-1-0"),
    (bytes([1, 1]) + (3).to_bytes(6, "big") + b"\x00\x00\x00\x00", "S-1-3-0"),
])
@pytest.mark.parametrize("flags", [0x03, 0x13, 0x0b, 0x1b])
def test_untrusted_allow_ace_refusal_names_sid_and_inheritance(sid, sid_text, flags):
    import re

    message = (f"SID {sid_text} (inherited={bool(flags & 0x10)}, "
               f"inherit-only={bool(flags & 0x08)})")
    _check_fake_dacl(USER, [(0, USER), (0, sid, flags)], True, re.escape(message))


def test_windows_membership_api_failure_fails_closed(monkeypatch):
    security = s.WindowsSecurity.__new__(s.WindowsSecurity)
    security.advapi = types.SimpleNamespace(CheckTokenMembership=lambda *args: False)

    def require(success):
        if not success:
            raise OSError("token query failed")

    monkeypatch.setattr(security, "_require", require)
    with pytest.raises(OSError, match="token query failed"):
        security._is_member(s.ADMINISTRATORS)


def test_windows_directory_creation_uses_user_only_protected_inheritable_dacl():
    security = s.WindowsSecurity.__new__(s.WindowsSecurity)
    security.sid_text = 'S-1-5-21-1234'
    freed, descriptors, directories = [], [], []

    def convert(text, revision, output, size):
        descriptors.append(text)
        assert revision == 1
        write_pointer(output, 123)
        return True

    def create(path, attrs):
        value = ctypes.cast(attrs, ctypes.POINTER(s._SecurityAttributes)).contents
        assert value.descriptor == 123 and not value.inherit
        directories.append(path)
        return True

    security.advapi = types.SimpleNamespace(ConvertStringSecurityDescriptorToSecurityDescriptorW=convert)
    security.kernel = types.SimpleNamespace(CreateDirectoryW=create, LocalFree=lambda value: freed.append(value.value))
    security.create_directory(Path('private-root'))
    assert descriptors == ['O:S-1-5-21-1234D:P(A;OICI;FA;;;S-1-5-21-1234)']
    assert directories == ['private-root'] and freed == [123]


def test_windows_current_owner_sid_comes_from_process_token_and_resources_close():
    security = s.WindowsSecurity.__new__(s.WindowsSecurity)
    sid = ctypes.create_string_buffer(b'user-sid')
    sid_text = ctypes.create_unicode_buffer('S-1-5-21-1234')
    closed, freed = [], []

    def open_token(process, access, output):
        assert (process, access) == (999, 8)
        write_pointer(output, 123)
        return True

    def token_information(token, kind, buffer, size, output_size):
        assert token.value == 123 and kind == 1
        ctypes.cast(output_size, ctypes.POINTER(s.w.DWORD)).contents.value = 32
        if buffer is None:
            return False  # Initial size query.
        write_pointer(buffer, ctypes.addressof(sid))
        return True

    def convert(pointer, output):
        assert pointer == ctypes.addressof(sid)
        write_pointer(output, ctypes.addressof(sid_text))
        return True

    security.kernel = types.SimpleNamespace(GetCurrentProcess=lambda: 999,
        CloseHandle=lambda token: closed.append(token.value), LocalFree=lambda value: freed.append(value.value))
    security.advapi = types.SimpleNamespace(OpenProcessToken=open_token, GetTokenInformation=token_information,
                                          GetLengthSid=lambda sid: 8, ConvertSidToStringSidW=convert)
    assert security._current_user() == (b'user-sid', 'S-1-5-21-1234')
    assert closed == [123] and freed == [ctypes.addressof(sid_text)]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows token and DACL APIs")
def test_native_private_root_and_inherited_file_security(tmp_path):
    security = s.WindowsSecurity()
    root = tmp_path / "private-registry"
    security.create_directory(root)
    security.check_path(root)
    # Elevated tokens may use Administrators as the default file owner even
    # when the containing root explicitly selected the user's SID.
    record = root / "host.json"
    record.write_text("secret")
    security.check_path(record)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows token and DACL APIs")
def test_native_registry_checker_accepts_pytest_tmp_path(tmp_path):
    from server.solver.beat_runtime import registry

    # Check pytest's directory itself, not a new root made with our own SDDL.
    assert registry.private_directory(tmp_path) == tmp_path.absolute()
    s.WindowsSecurity().check_path(tmp_path)
    python_root = tmp_path / "python-private"
    python_root.mkdir(mode=0o700)
    assert registry.private_directory(python_root) == python_root.absolute()
