from __future__ import annotations

import ctypes
from pathlib import Path
import types

import pytest

from server.solver.beat_runtime import windows_security as s


def write_pointer(destination, value):
    ctypes.cast(destination, ctypes.POINTER(ctypes.c_void_p))[0] = value


@pytest.mark.parametrize(("owner", "dacl", "ace_kind", "ace_sid", "refused"), [
    (b'user-sid', True, 0, b'user-sid', None),
    (b'otherSID', True, 0, b'user-sid', 'another Windows user'),
    (b'user-sid', False, 0, b'user-sid', 'unrestricted'),
    (b'user-sid', True, 0, b'otherSID', 'another user'),
    (b'user-sid', True, 5, b'user-sid', 'Unsupported'),
    (b'user-sid', True, 1, b'user-sid', 'does not grant'),
])
def test_windows_owner_sid_and_dacl_are_validated_with_ctypes_fakes(owner, dacl, ace_kind, ace_sid, refused):
    owner_buffer = ctypes.create_string_buffer(owner)
    ace_buffer = ctypes.create_string_buffer(bytes([ace_kind, 0, 16, 0]) + bytes(4) + ace_sid)
    freed = []
    security = s.WindowsSecurity.__new__(s.WindowsSecurity)
    security.sid = b'user-sid'
    security.kernel = types.SimpleNamespace(LocalFree=lambda value: freed.append(value.value))

    def named_security(path, object_type, information, owner_out, group_out, dacl_out, sacl_out, descriptor_out):
        assert (object_type, information) == (1, 5)  # File, owner + DACL.
        write_pointer(owner_out, ctypes.addressof(owner_buffer))
        write_pointer(dacl_out, 123 if dacl else None)
        write_pointer(descriptor_out, 456)
        return 0

    def acl_information(acl, buffer, size, kind):
        ctypes.cast(buffer, ctypes.POINTER(s._AclSize)).contents.count = 1
        assert kind == 2
        return True

    def get_ace(acl, index, output):
        assert index == 0
        write_pointer(output, ctypes.addressof(ace_buffer))
        return True

    security.advapi = types.SimpleNamespace(GetNamedSecurityInfoW=named_security, GetLengthSid=lambda sid: 8,
                                          GetAclInformation=acl_information, GetAce=get_ace)
    if refused is None:
        security.check_path(Path('private-root'))
    else:
        with pytest.raises(ValueError, match=refused):
            security.check_path(Path('private-root'))
    assert freed == [456]


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
