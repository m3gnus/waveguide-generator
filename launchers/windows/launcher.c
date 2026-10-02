/* Native entry and installer recovery. No Python, runtime DLL or WG import.
 * See WINDOWS-INSTALLER-RECOVERY.md for the journal and failure boundaries. */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <tlhelp32.h>
#include <wchar.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include "startup-hook.h"

#ifndef WG_PYTHON_DLL
#define WG_PYTHON_DLL L"python313.dll"
#endif
#define CAP 1024
#define ITEMS 14
#define JOURNAL L".upgrade-in-progress"
#define OLD L".wg-install-old"
#define NEW L".wg-install-new"
#define MUTEX L"WaveguideGeneratorSetup"
#define PUBLIC_EXE L"Waveguide Generator.exe"
#define PRIVATE_EXE L"wg-python.exe"

static const wchar_t *names[ITEMS] = {
    L"app", L"runtime", L"recovery", PRIVATE_EXE, WG_PYTHON_DLL,
    L"python3.dll", L"vcruntime140.dll", L"vcruntime140_1.dll", L"msvcp140.dll",
    L"wg-python._pth", L"Waveguide Generator._pth", L"pyvenv.cfg",
    L"WaveguideGenerator.ico", L"READ ME FIRST.txt"
};
typedef struct { DWORD volume, high, low, present, directory; } Identity;
typedef struct {
    char magic[24]; DWORD schema, committed, boot_ready, crc;
    DWORD owner; FILETIME owner_created;
    DWORD loader, worker, watchdog;
    FILETIME loader_created, worker_created, watchdog_created;
    wchar_t root[CAP], outcome[CAP], log[CAP], from[128], to[128];
    Identity root_id, old_id, new_id, before[ITEMS], after[ITEMS];
} Journal;

static int path(wchar_t *out, const wchar_t *root, const wchar_t *name) {
    /* Backup paths add a reserved component to payload paths already close to
     * MAX_PATH. Use explicit extended paths for all native traversal/moves. */
    if (!wcsncmp(root, L"\\\\?\\", 4)) return swprintf_s(out, CAP, L"%s\\%s", root, name) > 0;
    if (!wcsncmp(root, L"\\\\", 2)) return swprintf_s(out, CAP, L"\\\\?\\UNC\\%s\\%s", root + 2, name) > 0;
    if (wcslen(root) >= 3 && root[1] == L':' && root[2] == L'\\')
        return swprintf_s(out, CAP, L"\\\\?\\%s\\%s", root, name) > 0;
    return swprintf_s(out, CAP, L"%s\\%s", root, name) > 0;
}
static int ordinary_path(wchar_t *p) {
    wchar_t normal[CAP];
    if (!wcsncmp(p, L"\\\\?\\UNC\\", 8)) {
        if (swprintf_s(normal, CAP, L"\\\\%s", p + 8) <= 0) return 0;
        return wcscpy_s(p, CAP, normal) == 0;
    }
    if (!wcsncmp(p, L"\\\\?\\", 4)) memmove(p, p + 4, (wcslen(p + 4) + 1) * sizeof(*p));
    return 1;
}
static int absent(const wchar_t *p) {
    DWORD a = GetFileAttributesW(p);
    if (a != INVALID_FILE_ATTRIBUTES) return 0;
    return GetLastError() == ERROR_FILE_NOT_FOUND || GetLastError() == ERROR_PATH_NOT_FOUND;
}
static int identity(const wchar_t *p, Identity *id) {
    HANDLE h; BY_HANDLE_FILE_INFORMATION info; DWORD a;
    ZeroMemory(id, sizeof(*id));
    a = GetFileAttributesW(p);
    if (a == INVALID_FILE_ATTRIBUTES) return absent(p);
    if (a & FILE_ATTRIBUTE_REPARSE_POINT) return 0;
    h = CreateFileW(p, 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    if (!GetFileInformationByHandle(h, &info)) { CloseHandle(h); return 0; }
    CloseHandle(h);
    if (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) return 0;
    id->volume = info.dwVolumeSerialNumber; id->high = info.nFileIndexHigh;
    id->low = info.nFileIndexLow; id->present = 1;
    id->directory = !!(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY);
    return 1;
}
static int same(const Identity *a, const Identity *b) {
    return a->present == b->present && (!a->present ||
        (a->volume == b->volume && a->high == b->high && a->low == b->low && a->directory == b->directory));
}
static int matches(const wchar_t *p, const Identity *id) {
    Identity found; return identity(p, &found) && same(&found, id);
}
static uint32_t checksum(const Journal *j) {
    Journal copy = *j; const unsigned char *p; size_t n; uint32_t c = 0xffffffffU;
    copy.crc = 0; p = (const unsigned char *)&copy;
    for (n = 0; n < sizeof(copy); ++n) {
        unsigned k; c ^= p[n];
        for (k = 0; k < 8; ++k) c = (c >> 1) ^ (0xedb88320U & (0U - (c & 1U)));
    }
    return ~c;
}
static int durable_write(const wchar_t *target, const void *data, DWORD count, int replace) {
    wchar_t tmp[CAP]; HANDLE h; DWORD done; int ok;
    if (swprintf_s(tmp, CAP, L"%s.%lu.tmp", target, GetCurrentProcessId()) <= 0) return 0;
    h = CreateFileW(tmp, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    ok = WriteFile(h, data, count, &done, NULL) && done == count && FlushFileBuffers(h);
    if (!CloseHandle(h)) ok = 0;
    if (ok) ok = MoveFileExW(tmp, target, MOVEFILE_WRITE_THROUGH | (replace ? MOVEFILE_REPLACE_EXISTING : 0));
    if (!ok) DeleteFileW(tmp);
    return ok;
}
static int save(Journal *j, int replace) {
    wchar_t p[CAP]; j->crc = checksum(j);
    return path(p, j->root, JOURNAL) && durable_write(p, j, sizeof(*j), replace);
}
static int load(const wchar_t *root, Journal *j) {
    wchar_t p[CAP]; HANDLE h; DWORD got; LARGE_INTEGER size; unsigned i;
    if (!path(p, root, JOURNAL)) return 0;
    { Identity marker; if (!identity(p, &marker) || !marker.present || marker.directory) return 0; }
    h = CreateFileW(p, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    if (!GetFileSizeEx(h, &size) || size.QuadPart != (LONGLONG)sizeof(*j) ||
        !ReadFile(h, j, sizeof(*j), &got, NULL) || got != sizeof(*j)) { CloseHandle(h); return 0; }
    CloseHandle(h);
    if (memcmp(j->magic, "WG-INSTALL-JOURNAL-2", 20) || j->schema != 2 || j->crc != checksum(j) ||
        j->committed > 2 || j->boot_ready > 1 || !wmemchr(j->root, 0, CAP) ||
        !wmemchr(j->outcome, 0, CAP) || !wmemchr(j->log, 0, CAP) ||
        !wmemchr(j->from, 0, 128) || !wmemchr(j->to, 0, 128) || _wcsicmp(root, j->root) ||
        !matches(root, &j->root_id)) return 0;
    for (i = 0; i < ITEMS; ++i) {
        if (j->before[i].present > 1 || j->after[i].present > 1 ||
            (j->before[i].present && j->before[i].directory != (i < 3)) ||
            (j->after[i].present && j->after[i].directory != (i < 3))) return 0;
    }
    return 1;
}
static int move(const wchar_t *from, const wchar_t *to, const Identity *id) {
    return matches(from, id) && id->present && absent(to) && MoveFileExW(from, to, MOVEFILE_WRITE_THROUGH);
}
/* Do not traverse a junction, including one planted below an owned directory.
 * Deletion is always preceded by the identity check at the transaction root. */
static int remove_tree(const wchar_t *p) {
    wchar_t glob[CAP], child[CAP]; WIN32_FIND_DATAW f; HANDLE h; DWORD a = GetFileAttributesW(p);
    if (a == INVALID_FILE_ATTRIBUTES) return absent(p);
    if (a & FILE_ATTRIBUTE_REPARSE_POINT) return 0;
    if (!(a & FILE_ATTRIBUTE_DIRECTORY)) return DeleteFileW(p);
    if (!path(glob, p, L"*")) return 0;
    h = FindFirstFileW(glob, &f);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!wcscmp(f.cFileName, L".") || !wcscmp(f.cFileName, L"..")) continue;
            if (!path(child, p, f.cFileName) || !remove_tree(child)) { FindClose(h); return 0; }
        } while (FindNextFileW(h, &f));
        if (GetLastError() != ERROR_NO_MORE_FILES) { FindClose(h); return 0; }
        FindClose(h);
    } else if (GetLastError() != ERROR_FILE_NOT_FOUND) return 0;
    return RemoveDirectoryW(p);
}
static int erase(const wchar_t *p, const Identity *id) { return matches(p, id) && remove_tree(p); }
static int flush_tree(const wchar_t *p) {
    DWORD a = GetFileAttributesW(p); HANDLE h; wchar_t glob[CAP], child[CAP]; WIN32_FIND_DATAW f;
    if (a == INVALID_FILE_ATTRIBUTES || (a & FILE_ATTRIBUTE_REPARSE_POINT)) return 0;
    if (!(a & FILE_ATTRIBUTE_DIRECTORY)) {
        int ok;
        h = CreateFileW(p, GENERIC_WRITE, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
        if (h == INVALID_HANDLE_VALUE) return 0;
        ok = FlushFileBuffers(h); CloseHandle(h); return ok;
    }
    if (!path(glob, p, L"*")) return 0;
    h = FindFirstFileW(glob, &f);
    if (h == INVALID_HANDLE_VALUE) return GetLastError() == ERROR_FILE_NOT_FOUND;
    do {
        if (!wcscmp(f.cFileName, L".") || !wcscmp(f.cFileName, L"..")) continue;
        if (!path(child, p, f.cFileName) || !flush_tree(child)) { FindClose(h); return 0; }
    } while (FindNextFileW(h, &f));
    if (GetLastError() != ERROR_NO_MORE_FILES) { FindClose(h); return 0; }
    FindClose(h); return 1;
}

static int append(char *out, size_t cap, size_t *used, const char *s) {
    size_t n = strlen(s); if (*used + n >= cap) return 0;
    memcpy(out + *used, s, n); *used += n; out[*used] = 0; return 1;
}
static int json_string(char *out, size_t cap, size_t *used, const wchar_t *s) {
    char utf8[CAP * 4]; const unsigned char *p; wchar_t normal[CAP];
    if (!s || !*s) return append(out, cap, used, "null");
    if (wcscpy_s(normal, CAP, s) || !ordinary_path(normal)) return 0;
    s = normal;
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, s, -1, utf8, sizeof(utf8), NULL, NULL) ||
        !append(out, cap, used, "\"")) return 0;
    for (p = (unsigned char *)utf8; *p; ++p) {
        char one[8];
        if (*p < 32) sprintf_s(one, sizeof(one), "\\u%04x", *p);
        else if (*p == '"' || *p == '\\') sprintf_s(one, sizeof(one), "\\%c", *p);
        else { one[0] = (char)*p; one[1] = 0; }
        if (!append(out, cap, used, one)) return 0;
    }
    return append(out, cap, used, "\"");
}
static int outcome(const Journal *j, const char *result, int kept, const wchar_t *backup) {
    char out[CAP * 16], when[64]; size_t n = 0; SYSTEMTIME t; wchar_t marker[CAP];
    if (!j->outcome[0]) return 1;
    GetSystemTime(&t);
    sprintf_s(when, sizeof(when), "%04u-%02u-%02uT%02u:%02u:%02uZ", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
#define ADD(s) do { if (!append(out, sizeof(out), &n, s)) return 0; } while (0)
#define STR(s) do { if (!json_string(out, sizeof(out), &n, s)) return 0; } while (0)
    ADD("{\"from\":"); STR(j->from); ADD(",\"to\":"); STR(j->to);
    ADD(",\"result\":\""); ADD(result); ADD("\",\"previousKept\":"); ADD(kept ? "true" : "false");
    ADD(",\"when\":\""); ADD(when); ADD("\",\"log\":"); STR(j->log);
    ADD(",\"backupPath\":"); STR(backup);
    ADD(",\"journalPath\":"); if (!path(marker, j->root, JOURNAL)) return 0; STR(marker);
    ADD("}\n");
#undef ADD
#undef STR
    return durable_write(j->outcome, out, (DWORD)n, 1);
}
static HANDLE exact_process(DWORD pid, const FILETIME *expected, DWORD access) {
    HANDLE h; FILETIME created, exited, kernel, user;
    if (!pid) return NULL;
    h = OpenProcess(access | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, pid);
    if (!h) return NULL;
    if (!GetProcessTimes(h, &created, &exited, &kernel, &user) || memcmp(&created, expected, sizeof(created))) {
        CloseHandle(h); return NULL;
    }
    return h;
}
static int alive(DWORD pid, const FILETIME *expected) {
    HANDLE h; FILETIME created, exited, kernel, user; DWORD code; int alive;
    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!h) return GetLastError() != ERROR_INVALID_PARAMETER;
    alive = !GetProcessTimes(h, &created, &exited, &kernel, &user) ||
        (!memcmp(&created, expected, sizeof(created)) &&
         (!GetExitCodeProcess(h, &code) || code == STILL_ACTIVE));
    CloseHandle(h); return alive;
}
static int owner_alive(const Journal *j) { return alive(j->owner, &j->owner_created); }
static DWORD parent_of(DWORD pid) {
    PROCESSENTRY32W pe; DWORD parent = 0; HANDLE h = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (h == INVALID_HANDLE_VALUE) return 0;
    pe.dwSize = sizeof(pe);
    if (Process32FirstW(h, &pe)) do {
        if (pe.th32ProcessID == pid) { parent = pe.th32ParentProcessID; break; }
    } while (Process32NextW(h, &pe));
    CloseHandle(h); return parent;
}
static DWORD parent_pid(void) { return parent_of(GetCurrentProcessId()); }
static int creation_time(DWORD pid, FILETIME *created) {
    HANDLE h; FILETIME exited, kernel, user; int ok;
    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!h) return 0;
    ok = GetProcessTimes(h, created, &exited, &kernel, &user); CloseHandle(h); return ok;
}
static HANDLE startup_claim(void);
static HANDLE transaction_lock(const wchar_t *root) {
    wchar_t p[CAP]; Identity root_id; ULONGLONG end = GetTickCount64() + 120000;
    if (!identity(root, &root_id) || !root_id.present || !root_id.directory) return NULL;
    if (!path(p, root, L".wg-install-lock")) return NULL;
    for (;;) {
        HANDLE h; BY_HANDLE_FILE_INFORMATION info; DWORD attributes = GetFileAttributesW(p);
        if (attributes != INVALID_FILE_ATTRIBUTES &&
            (attributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY))) return NULL;
        h = CreateFileW(p, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_ALWAYS,
                        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
        if (h != INVALID_HANDLE_VALUE) {
            if (!GetFileInformationByHandle(h, &info) ||
                (info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY))) { CloseHandle(h); return NULL; }
            return h; /* OS-held; process death releases it without a stale-PID sweep. */
        }
        if (GetLastError() != ERROR_SHARING_VIOLATION || GetTickCount64() >= end) return NULL;
        Sleep(50);
    }
}
static int spawn_watchdog(Journal *j) {
    wchar_t self[CAP], command[CAP * 3]; STARTUPINFOW si; PROCESS_INFORMATION pi;
    if (!GetModuleFileNameW(NULL, self, CAP) || wcslen(self) >= CAP - 1 ||
        swprintf_s(command, CAP * 3, L"\"%s\" --installer-watch \"%s\"", self, j->root) <= 0) return 0;
    ZeroMemory(&si, sizeof(si)); si.cb = sizeof(si); ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(self, command, NULL, NULL, FALSE, DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                        NULL, j->root, &si, &pi)) return 0;
    j->watchdog = pi.dwProcessId;
    { FILETIME exited, kernel, user;
      int ok = GetProcessTimes(pi.hProcess, &j->watchdog_created, &exited, &kernel, &user) && save(j, 1);
      if (!ok) { TerminateProcess(pi.hProcess, 3); WaitForSingleObject(pi.hProcess, 5000); }
      CloseHandle(pi.hThread); CloseHandle(pi.hProcess); return ok; }
}
/* A backup path names the verified old object which still exists, never the
 * journal and never an earlier backup which was already restored. */
static const wchar_t *remaining_backup(const Journal *j, wchar_t *out) {
    wchar_t old[CAP]; unsigned i;
    if (!path(old, j->root, OLD) || !matches(old, &j->old_id)) return NULL;
    for (i = 0; i < ITEMS; ++i) {
        if (j->before[i].present && path(out, old, names[i]) && matches(out, &j->before[i])) return out;
    }
    return NULL;
}
static int cleanup(Journal *j) {
    wchar_t old[CAP], newer[CAP], marker[CAP];
    if (!path(old, j->root, OLD) || !path(newer, j->root, NEW) || !path(marker, j->root, JOURNAL)) return 0;
    if ((!absent(old) && !erase(old, &j->old_id)) || (!absent(newer) && !erase(newer, &j->new_id))) return 0;
    /* Outcome is already durable. An ordinary subsequent start sees no work. */
    return DeleteFileW(marker);
}
static int recover(Journal *j) {
    wchar_t old[CAP], newer[CAP], live[CAP], backup[CAP], stage[CAP], actual[CAP]; unsigned i; int ok = 1;
    if (!path(old, j->root, OLD) || !path(newer, j->root, NEW)) return 3;
    if (j->committed) {
        for (i = 0; i < ITEMS; ++i) {
            if (!path(live, j->root, names[i]) || !matches(live, j->committed == 1 ? &j->after[i] : &j->before[i])) return 3;
        }
        if (!outcome(j, j->committed == 1 ? "installed" : "failed",
                     j->committed == 2 && j->before[0].present && j->before[1].present, NULL)) return 3;
        cleanup(j); return j->committed == 1 ? 0 : 1;
    }
    if (!matches(old, &j->old_id) || !matches(newer, &j->new_id)) return 3;
    for (i = 0; i < ITEMS; ++i) {
        Identity found, saved;
        if (!path(live, j->root, names[i]) || !path(backup, old, names[i]) || !path(stage, newer, names[i]) ||
            !identity(live, &found) || !identity(backup, &saved)) { ok = 0; continue; }
        if (same(&found, &j->before[i]) && !saved.present) continue; /* Not moved, or already restored. */
        if (j->before[i].present && !same(&saved, &j->before[i])) { ok = 0; continue; }
        if (!j->before[i].present && saved.present) { ok = 0; continue; }
        if (found.present) {
            if (!same(&found, &j->after[i]) || !erase(live, &j->after[i])) { ok = 0; continue; }
        }
        if (j->before[i].present && !move(backup, live, &j->before[i])) { ok = 0; continue; }
        if (!matches(live, &j->before[i])) ok = 0;
    }
    if (!ok) { outcome(j, "rollback_incomplete", 0, remaining_backup(j, actual)); return 3; }
    j->committed = 2; if (!save(j, 1)) return 3;
    if (!outcome(j, "failed", j->before[0].present && j->before[1].present, NULL)) return 3;
    cleanup(j); return 1;
}
static int prepare(const wchar_t *root, const wchar_t *from, const wchar_t *to,
                   const wchar_t *record, const wchar_t *log) {
    Journal j; wchar_t old[CAP], newer[CAP], live[CAP], backup[CAP], stage[CAP], marker[CAP];
    unsigned i;
    if (!path(marker, root, JOURNAL)) return 3;
    if (!absent(marker)) {
        if (!load(root, &j) || owner_alive(&j)) return 3;
        if (recover(&j) == 3 || !absent(marker)) return 3;
    }
    ZeroMemory(&j, sizeof(j)); memcpy(j.magic, "WG-INSTALL-JOURNAL-2", 20); j.schema = 2;
    if (wcscpy_s(j.root, CAP, root) || wcscpy_s(j.outcome, CAP, record) || wcscpy_s(j.log, CAP, log) ||
        wcscpy_s(j.from, 128, from) || wcscpy_s(j.to, 128, to) || !identity(root, &j.root_id) ||
        !j.root_id.present || !j.root_id.directory) return 3;
    j.owner = parent_pid(); j.loader = parent_of(j.owner); j.worker = GetCurrentProcessId();
    if (!creation_time(j.owner, &j.owner_created) || !creation_time(j.loader, &j.loader_created) ||
        !creation_time(j.worker, &j.worker_created)) return 3;
    if (!path(old, root, OLD) || !path(newer, root, NEW) || !absent(old) || !absent(newer)) return 3;
    for (i = 0; i < ITEMS; ++i) {
        if (!path(live, root, names[i]) || !identity(live, &j.before[i]) ||
            (j.before[i].present && j.before[i].directory != (i < 3))) return 3;
    }
    if (j.before[0].present != j.before[1].present) return 3;
    if (!CreateDirectoryW(old, NULL) || !CreateDirectoryW(newer, NULL) ||
        !identity(old, &j.old_id) || !identity(newer, &j.new_id)) return 3;
    for (i = 0; i < 3; ++i) {
        if (!path(stage, newer, names[i]) || !CreateDirectoryW(stage, NULL) || !identity(stage, &j.after[i])) return 3;
    }
    if (!save(&j, 0)) return 3; /* No installed path moves before the durable journal. */
    if (!spawn_watchdog(&j)) { recover(&j); return 3; }
    for (i = 0; i < 3; ++i) {
        if (!path(live, root, names[i]) || !path(backup, old, names[i]) || !path(stage, newer, names[i]) ||
            (j.before[i].present && !move(live, backup, &j.before[i])) ||
            !move(stage, live, &j.after[i])) { recover(&j); return 3; }
    }
    return 0;
}
static int commit(Journal *j) {
    wchar_t old[CAP], newer[CAP], live[CAP], backup[CAP], stage[CAP]; unsigned i; HANDLE monitor;
    if (j->owner != parent_pid() || !owner_alive(j) || j->committed || j->boot_ready ||
        !path(old, j->root, OLD) || !path(newer, j->root, NEW) ||
        !matches(old, &j->old_id) || !matches(newer, &j->new_id)) return 3;
    for (i = 0; i < ITEMS; ++i) {
        if (i < 3) { if (!path(live, j->root, names[i]) || !matches(live, &j->after[i])) return 3; }
        else if (!path(stage, newer, names[i]) || !identity(stage, &j->after[i]) ||
                 (j->after[i].present && j->after[i].directory)) return 3;
    }
    for (i = 3; i < 12; ++i) if (!j->after[i].present) return 3; /* Complete boot set required. */
    monitor = exact_process(j->watchdog, &j->watchdog_created, 0);
    if (!monitor) return 3;
    if (WaitForSingleObject(monitor, 0) != WAIT_TIMEOUT) { CloseHandle(monitor); return 3; }
    j->worker = GetCurrentProcessId();
    if (!creation_time(j->worker, &j->worker_created)) { CloseHandle(monitor); return 3; }
    j->boot_ready = 1; if (!save(j, 1)) { CloseHandle(monitor); return 3; }
    /* Persist copied bytes before publishing a committed journal. Inno's
     * ssPostInstall proves copy completion, not a disk-cache flush. */
    for (i = 0; i < 3; ++i) {
        if (!path(live, j->root, names[i]) || !flush_tree(live)) { CloseHandle(monitor); recover(j); return 3; }
    }
    if (!flush_tree(newer)) { CloseHandle(monitor); recover(j); return 3; }
    for (i = 3; i < ITEMS; ++i) {
        if (WaitForSingleObject(monitor, 0) != WAIT_TIMEOUT ||
            !path(live, j->root, names[i]) || !path(backup, old, names[i]) || !path(stage, newer, names[i]) ||
            (j->before[i].present && !move(live, backup, &j->before[i])) ||
            (j->after[i].present && !move(stage, live, &j->after[i]))) { CloseHandle(monitor); recover(j); return 3; }
    }
    if (WaitForSingleObject(monitor, 0) != WAIT_TIMEOUT) { CloseHandle(monitor); recover(j); return 3; }
    j->committed = 1;
    if (!save(j, 1)) { CloseHandle(monitor); j->committed = 0; recover(j); return 3; }
    CloseHandle(monitor);
    if (!outcome(j, "installed", 0, NULL)) return 3;
    cleanup(j); return 0;
}
/* Inno's public setup.exe is a loader; actual copying runs in its temporary
 * child. Keep exact process handles open while observing/terminating the
 * recorded identities. Neither a reused PID nor an unrelated process is a
 * reason to terminate anything. This monitor uses only its static CRT/Win32. */
static int watch(const wchar_t *root) {
    Journal j; wchar_t marker[CAP];
    if (!path(marker, root, JOURNAL)) return 3;
    for (;;) {
        HANDLE owner, loader;
        if (absent(marker)) return 0;
        if (!load(root, &j)) return 3;
        if (!j.watchdog) { Sleep(100); continue; }
        if (j.watchdog != GetCurrentProcessId()) return 3;
        owner = exact_process(j.owner, &j.owner_created, PROCESS_TERMINATE);
        loader = exact_process(j.loader, &j.loader_created, 0);
        if ((!owner && owner_alive(&j)) || (!loader && alive(j.loader, &j.loader_created))) {
            if (owner) CloseHandle(owner);
            if (loader) CloseHandle(loader);
            return 3;
        }
        if (owner && WaitForSingleObject(owner, 0) == WAIT_TIMEOUT &&
            loader && WaitForSingleObject(loader, 0) == WAIT_TIMEOUT) {
            CloseHandle(owner); CloseHandle(loader); Sleep(100); continue;
        }
        if (loader) CloseHandle(loader);
        if (owner && WaitForSingleObject(owner, 0) == WAIT_TIMEOUT) {
            if (!TerminateProcess(owner, 3) || WaitForSingleObject(owner, 10000) != WAIT_OBJECT_0) { CloseHandle(owner); return 3; }
        }
        if (owner) CloseHandle(owner);
        /* A commit helper may have published its worker identity since the
         * previous observation. Stop it before taking recovery exclusion. */
        if (!absent(marker) && !load(root, &j)) return 3;
        { HANDLE worker = exact_process(j.worker, &j.worker_created, PROCESS_TERMINATE);
          if (worker) {
              if (WaitForSingleObject(worker, 0) == WAIT_TIMEOUT &&
                  (!TerminateProcess(worker, 3) || WaitForSingleObject(worker, 10000) != WAIT_OBJECT_0)) { CloseHandle(worker); return 3; }
              CloseHandle(worker);
          } }
        /* Reload under setup exclusion: ordinary DeinitializeSetup may have
         * completed recovery between owner exit and this claim. */
        { HANDLE claim = startup_claim(), lock; int code = 0;
          if (!claim) return 3;
          lock = transaction_lock(root); if (!lock) { CloseHandle(claim); return 3; }
          if (!absent(marker)) code = load(root, &j) && !owner_alive(&j) ? recover(&j) : 3;
          CloseHandle(lock); CloseHandle(claim); return code; }
    }
}
static int equal_files(const wchar_t *a, const wchar_t *b) {
    HANDLE x, y; unsigned char xb[8192], yb[8192]; DWORD xn, yn; int equal = 1;
    x = CreateFileW(a, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, 0, NULL);
    y = CreateFileW(b, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, 0, NULL);
    if (x == INVALID_HANDLE_VALUE || y == INVALID_HANDLE_VALUE) equal = 0;
    while (equal) {
        if (!ReadFile(x, xb, sizeof(xb), &xn, NULL) || !ReadFile(y, yb, sizeof(yb), &yn, NULL) || xn != yn || memcmp(xb, yb, xn)) equal = 0;
        if (!equal || !xn) break;
    }
    if (x != INVALID_HANDLE_VALUE) CloseHandle(x);
    if (y != INVALID_HANDLE_VALUE) CloseHandle(y);
    return equal;
}
static int bytes_match(const wchar_t *p, const char *bytes, DWORD length) {
    HANDLE h; char buffer[8192]; DWORD got, offset = 0; int ok = 1;
    h = CreateFileW(p, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    for (;;) {
        if (!ReadFile(h, buffer, sizeof(buffer), &got, NULL) || got > length - offset ||
            memcmp(buffer, bytes + offset, got)) { ok = 0; break; }
        offset += got; if (!got) break;
    }
    CloseHandle(h); return ok && offset == length;
}
/* Recognition only: no recorded hook is executed. The executing native image
 * always installs its own compiled hook after recognising the existing file.
 * Identities bind the ledger to this nominated root and sidecar directory. */
#define HOOK_HISTORY_MAX (1024U * 1024U)
#define HOOK_BYTES_MAX 65536U
#define HOOK_COUNT_MAX 64U
typedef struct {
    char magic[24]; DWORD schema, total, count, crc;
    Identity root_id, directory_id;
} HookHistory;
static uint32_t history_crc(const unsigned char *data, DWORD size) {
    uint32_t c = 0xffffffffU; DWORD i;
    for (i = 0; i < size; ++i) {
        unsigned k; c ^= data[i];
        for (k = 0; k < 8; ++k) c = (c >> 1) ^ (0xedb88320U & (0U - (c & 1U)));
    }
    return ~c;
}
static int history_valid(unsigned char *data, DWORD size, const Identity *root_id, const Identity *directory_id) {
    HookHistory *h = (HookHistory *)data; DWORD saved, offset, i;
    if (size < sizeof(*h) || size > HOOK_HISTORY_MAX || memcmp(h->magic, "WG-KNOWN-HOOKS-1", 16) ||
        h->schema != 1 || h->total != size || !h->count || h->count > HOOK_COUNT_MAX ||
        !same(&h->root_id, root_id) || !same(&h->directory_id, directory_id)) return 0;
    saved = h->crc; h->crc = 0;
    if (saved != history_crc(data, size)) { h->crc = saved; return 0; }
    h->crc = saved; offset = sizeof(*h);
    for (i = 0; i < h->count; ++i) {
        DWORD length;
        if (size - offset < sizeof(length)) return 0;
        memcpy(&length, data + offset, sizeof(length)); offset += sizeof(length);
        if (!length || length > HOOK_BYTES_MAX || length > size - offset) return 0;
        offset += length;
    }
    return offset == size;
}
static int history_read(const wchar_t *p, unsigned char *data, DWORD *size,
                        const Identity *root_id, const Identity *directory_id) {
    Identity id; HANDLE file; LARGE_INTEGER bytes; DWORD got; int ok;
    if (!identity(p, &id) || !id.present || id.directory) return 0;
    file = CreateFileW(p, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, NULL, OPEN_EXISTING,
                       FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    ok = GetFileSizeEx(file, &bytes) && bytes.QuadPart >= (LONGLONG)sizeof(HookHistory) &&
         bytes.QuadPart <= HOOK_HISTORY_MAX;
    if (ok) { *size = (DWORD)bytes.QuadPart; ok = ReadFile(file, data, *size, &got, NULL) && got == *size; }
    CloseHandle(file);
    return ok && history_valid(data, *size, root_id, directory_id);
}
static int history_has(const unsigned char *data, const char *bytes, DWORD length) {
    const HookHistory *h = (const HookHistory *)data; DWORD i, offset = sizeof(*h);
    for (i = 0; i < h->count; ++i) {
        DWORD n; memcpy(&n, data + offset, sizeof(n)); offset += sizeof(n);
        if (n == length && !memcmp(data + offset, bytes, n)) return 1;
        offset += n;
    }
    return 0;
}
static int history_has_file(const unsigned char *data, const wchar_t *p) {
    const HookHistory *h = (const HookHistory *)data; DWORD i, offset = sizeof(*h);
    for (i = 0; i < h->count; ++i) {
        DWORD n; memcpy(&n, data + offset, sizeof(n)); offset += sizeof(n);
        if (bytes_match(p, (const char *)data + offset, n)) return 1;
        offset += n;
    }
    return 0;
}
static int history_add(unsigned char *data, const char *bytes, DWORD length) {
    HookHistory *h = (HookHistory *)data;
    if (!length || length > HOOK_BYTES_MAX) return 0;
    if (history_has(data, bytes, length)) return 1;
    if (h->count >= HOOK_COUNT_MAX || sizeof(length) + length > HOOK_HISTORY_MAX - h->total) return 0;
    memcpy(data + h->total, &length, sizeof(length)); h->total += sizeof(length);
    memcpy(data + h->total, bytes, length); h->total += length; ++h->count;
    return 1;
}
static int history_add_file(unsigned char *data, const wchar_t *p) {
    HANDLE file; LARGE_INTEGER size; DWORD got; char *bytes; int ok = 0;
    file = CreateFileW(p, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    if (GetFileSizeEx(file, &size) && size.QuadPart > 0 && size.QuadPart <= HOOK_BYTES_MAX) {
        bytes = malloc((size_t)size.QuadPart);
        if (bytes) {
            if (ReadFile(file, bytes, (DWORD)size.QuadPart, &got, NULL) && got == size.QuadPart) ok = history_add(data, bytes, got);
            free(bytes);
        }
    }
    CloseHandle(file); return ok;
}
static int hook_write(const wchar_t *target, const void *data, DWORD count) {
    wchar_t tmp[CAP]; HANDLE h; DWORD done, disposition = 8; int ok;
    if (swprintf_s(tmp, CAP, L"%s.%lu.tmp", target, GetCurrentProcessId()) <= 0) return 0;
    /* Creation and delete-on-close are atomic. A terminated writer cannot
     * strand a partial sidecar file. Ex class 21 / ON_CLOSE (8) clears that
     * state only after the complete file is flushed. Never use the basic
     * disposition class, which cannot clear FILE_FLAG_DELETE_ON_CLOSE. */
    h = CreateFileW(tmp, GENERIC_WRITE | DELETE, FILE_SHARE_DELETE, NULL, CREATE_NEW,
                    FILE_ATTRIBUTE_NORMAL | FILE_FLAG_DELETE_ON_CLOSE, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    ok = WriteFile(h, data, count, &done, NULL) && done == count && FlushFileBuffers(h);
    if (ok) ok = SetFileInformationByHandle(h, (FILE_INFO_BY_HANDLE_CLASS)21, &disposition, sizeof(disposition));
    if (!CloseHandle(h)) ok = 0;
    if (ok) ok = MoveFileExW(tmp, target, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
    if (!ok) DeleteFileW(tmp);
    return ok;
}
static int hook_temp_name(const wchar_t *name, const wchar_t *target) {
    const wchar_t *p; size_t n = wcslen(target); DWORD digits = 0; uint64_t pid = 0;
    if (wcsncmp(name, target, n) || name[n] != L'.') return 0;
    p = name + n + 1;
    while (*p >= L'0' && *p <= L'9' && digits < 10) { pid = pid * 10 + (*p++ - L'0'); ++digits; }
    return digits && pid > 0 && pid <= 0xffffffffU && !wcscmp(p, L".tmp");
}
static int clean_hook_temp(const wchar_t *directory, const wchar_t *name, const unsigned char *history,
                           const Identity *root_id, const Identity *directory_id) {
    wchar_t p[CAP]; Identity id; int proven = 0;
    if (!path(p, directory, name) || !identity(p, &id) || !id.present || id.directory) return 0;
    if (hook_temp_name(name, L"known-hooks")) {
        unsigned char *data = malloc(HOOK_HISTORY_MAX); DWORD size = 0;
        if (data) { proven = history_read(p, data, &size, root_id, directory_id); free(data); }
    } else if (hook_temp_name(name, L"sitecustomize.py"))
        proven = bytes_match(p, NATIVE_START_HOOK, sizeof(NATIVE_START_HOOK) - 1) || history_has_file(history, p);
    return proven && matches(p, &id) && DeleteFileW(p);
}
static int refresh_hook(const wchar_t *root, wchar_t *hook) {
    wchar_t directory[CAP], glob[CAP], old_hook[CAP], ledger[CAP];
    Identity id, root_id, directory_id; WIN32_FIND_DATAW f; HANDLE find;
    unsigned char *data; HookHistory *h; DWORD size = 0; int ok = 0;
    if (!identity(root, &root_id) || !root_id.present || !root_id.directory ||
        !path(directory, root, L".native-start") || !identity(directory, &id)) return 0;
    if ((!id.present && !CreateDirectoryW(directory, NULL)) || (id.present && !id.directory) ||
        !identity(directory, &directory_id) || !directory_id.present || !directory_id.directory ||
        !path(hook, directory, L"sitecustomize.py") || !path(ledger, directory, L"known-hooks") ||
        !path(glob, directory, L"*")) return 0;
    data = calloc(1, HOOK_HISTORY_MAX); if (!data) return 0; h = (HookHistory *)data;
    if (absent(ledger)) {
        memcpy(h->magic, "WG-KNOWN-HOOKS-1", 16); h->schema = 1; h->total = sizeof(*h);
        h->root_id = root_id; h->directory_id = directory_id;
    } else if (!history_read(ledger, data, &size, &root_id, &directory_id)) goto hook_done;
    find = FindFirstFileW(glob, &f);
    if (find != INVALID_HANDLE_VALUE) {
        do {
            if (wcscmp(f.cFileName, L".") && wcscmp(f.cFileName, L"..") &&
                ((f.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)) ||
                 (wcscmp(f.cFileName, L"sitecustomize.py") && wcscmp(f.cFileName, L"known-hooks") &&
                  !clean_hook_temp(directory, f.cFileName, data, &root_id, &directory_id)))) { FindClose(find); goto hook_done; }
        } while (FindNextFileW(find, &f));
        if (GetLastError() != ERROR_NO_MORE_FILES) { FindClose(find); goto hook_done; }
        FindClose(find);
    } else if (GetLastError() != ERROR_FILE_NOT_FOUND) goto hook_done;
    if (!absent(hook) && !bytes_match(hook, NATIVE_START_HOOK, sizeof(NATIVE_START_HOOK) - 1) &&
        !history_has_file(data, hook)) {
        /* Initial adoption only: prove an installed packaged source exactly.
         * Thereafter the id-bound history survives rollback or runtime swap. */
        if (!path(old_hook, root, L"runtime\\wg-startup-hook.py") || !identity(old_hook, &id) ||
            !id.present || id.directory || !equal_files(hook, old_hook) || !history_add_file(data, hook)) goto hook_done;
    }
    if (!history_add(data, NATIVE_START_HOOK, sizeof(NATIVE_START_HOOK) - 1)) goto hook_done;
    h->crc = 0; h->crc = history_crc(data, h->total);
    /* Record recognition before publishing code, including interruption before
     * the public image changes. Startup executes its own embedded hook only. */
    if (!bytes_match(ledger, (const char *)data, h->total) && !hook_write(ledger, data, h->total)) goto hook_done;
    if (!bytes_match(hook, NATIVE_START_HOOK, sizeof(NATIVE_START_HOOK) - 1) &&
        !hook_write(hook, NATIVE_START_HOOK, sizeof(NATIVE_START_HOOK) - 1)) goto hook_done;
    ok = 1;
hook_done:
    free(data); return ok;
}
static HANDLE native_hook(const wchar_t *root) {
    wchar_t hook[CAP], pth[CAP], public_pth[CAP]; Identity id; HANDLE h;
    char paths[8192]; DWORD got; const char prefix[] = ".native-start\n";
    if (!refresh_hook(root, hook)) return NULL;
    if (!path(pth, root, L"wg-python._pth") || !path(public_pth, root, L"Waveguide Generator._pth") ||
        !identity(pth, &id) || id.directory) return NULL;
    { Identity public_id;
      if (!identity(public_pth, &public_id) || !public_id.present || public_id.directory) return NULL; }
    h = CreateFileW(public_pth, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (h == INVALID_HANDLE_VALUE) return NULL;
    memcpy(paths, prefix, sizeof(prefix) - 1);
    if (!ReadFile(h, paths + sizeof(prefix) - 1, sizeof(paths) - sizeof(prefix), &got, NULL) ||
        !got || got >= sizeof(paths) - sizeof(prefix)) { CloseHandle(h); return NULL; }
    CloseHandle(h);
    if (id.present && !bytes_match(pth, paths, got + sizeof(prefix) - 1) && !equal_files(pth, public_pth)) return NULL;
    if (!bytes_match(pth, paths, got + sizeof(prefix) - 1) &&
        !durable_write(pth, paths, got + sizeof(prefix) - 1, 1)) return NULL;
    /* Hold source immutable through the child's early admission import. -B
     * prevents a cache appearing in this narrowly owned sidecar directory. */
    return CreateFileW(hook, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
}
static int install_entry(const wchar_t *root, const wchar_t *self) {
    wchar_t public_path[CAP], tmp[CAP], hidden[CAP], hook[CAP]; Identity id;
    /* Verify/refresh the sidecar while the old packaged hook still exists.
     * The new compatible hook survives either new install or old rollback. */
    if (!refresh_hook(root, hook)) return 3;
    if (!path(public_path, root, PUBLIC_EXE) || !identity(public_path, &id) || id.directory ||
        !path(hidden, root, PRIVATE_EXE) || !identity(hidden, &id) || id.directory) return 3;
    if (equal_files(public_path, self)) return 0;
    /* A pre-bridge root pythonw is kept before the public image changes.
     * Candidate runtimes declare both images, so normal bridge starts already
     * have the hidden interpreter and the native public entry. */
    if (!id.present && !absent(public_path) && !CopyFileW(public_path, hidden, TRUE)) return 3;
    if (swprintf_s(tmp, CAP, L"%s.wg-new-%lu", public_path, GetCurrentProcessId()) <= 0 ||
        !CopyFileW(self, tmp, TRUE)) return 3;
    { HANDLE h = CreateFileW(tmp, GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
      int ok = h != INVALID_HANDLE_VALUE && FlushFileBuffers(h);
      if (h != INVALID_HANDLE_VALUE) CloseHandle(h);
      if (!ok || !MoveFileExW(tmp, public_path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) { DeleteFileW(tmp); return 3; } }
    return 0;
}
static HANDLE startup_claim(void) {
    ULONGLONG end = GetTickCount64() + 120000;
    for (;;) {
        HANDLE h; DWORD error; SetLastError(0); h = CreateMutexW(NULL, FALSE, MUTEX); error = GetLastError();
        if (!h) return NULL;
        if (error != ERROR_ALREADY_EXISTS) return h;
        CloseHandle(h); if (GetTickCount64() >= end) return NULL; Sleep(100);
    }
}
static int start(const wchar_t *root, const wchar_t *arguments) {
    wchar_t hidden[CAP], command[32768], protocol[512]; Journal j; HANDLE claim, lock, hook;
    STARTUPINFOEXW si; PROCESS_INFORMATION pi; DWORD code; wchar_t marker[CAP];
    HANDLE ack = NULL, release = NULL, running = NULL, handles[6], waits[2];
    SECURITY_ATTRIBUTES sa = {sizeof(sa), NULL, TRUE}; SIZE_T size = 0; DWORD count = 0, i;
    FILETIME created, exited, kernel, user; int result = 3, attributes_ready = 0;
    claim = startup_claim(); if (!claim) return 4;
    lock = transaction_lock(root); if (!lock) { CloseHandle(claim); return 3; }
    if (!path(marker, root, JOURNAL)) { CloseHandle(lock); CloseHandle(claim); return 3; }
    if (!absent(marker)) {
        if (!load(root, &j) || owner_alive(&j) || recover(&j) == 3) { CloseHandle(lock); CloseHandle(claim); return 3; }
    }
    hook = native_hook(root);
    if (hook == NULL || hook == INVALID_HANDLE_VALUE || !path(hidden, root, PRIVATE_EXE)) { CloseHandle(lock); CloseHandle(claim); return 3; }
    if (!arguments[0]) {
        wchar_t public_path[CAP];
        if (!path(public_path, root, PUBLIC_EXE) || swprintf_s(command, 32768,
            L"\"%s\" -B -c \"import sys,importlib\n"
            L"sys.executable=sys.argv.pop();sys.argv[0]=''\n"
            L"sys.path.insert(0,str(__import__('pathlib').Path(sys.executable).parent/'recovery'))\n"
            L"try:\n import wg_bundle_recovery\n"
            L"except ModuleNotFoundError as e:\n if e.name!='wg_bundle_recovery': raise\n"
            L"else:\n wg_bundle_recovery.windows_boot()\n"
            L"b=importlib.import_module('wg_desktop_bootstrap');importlib.reload(b)\" \"%s\"",
            hidden, public_path) <= 0) { CloseHandle(hook); CloseHandle(lock); CloseHandle(claim); return 3; }
    } else if (swprintf_s(command, 32768, L"\"%s\" -B %s", hidden, arguments) <= 0) { CloseHandle(hook); CloseHandle(lock); CloseHandle(claim); return 3; }
    ZeroMemory(&si, sizeof(si)); si.StartupInfo.cb = sizeof(si); ZeroMemory(&pi, sizeof(pi));
    ack = CreateEventW(&sa, TRUE, FALSE, NULL); release = CreateEventW(&sa, TRUE, FALSE, NULL);
    running = CreateMutexW(&sa, FALSE, L"WaveguideGeneratorRunning");
    if (!ack || !release || !running || !GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user)) goto start_done;
    handles[count++] = ack; handles[count++] = release; handles[count++] = running;
    si.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    for (i = 0; i < 3; ++i) {
        DWORD slot = i == 0 ? STD_INPUT_HANDLE : (i == 1 ? STD_OUTPUT_HANDLE : STD_ERROR_HANDLE);
        HANDLE original = GetStdHandle(slot), copy = NULL;
        if (original && original != INVALID_HANDLE_VALUE &&
            !DuplicateHandle(GetCurrentProcess(), original, GetCurrentProcess(), &copy, 0, TRUE, DUPLICATE_SAME_ACCESS)) goto start_done;
        if (copy) handles[count++] = copy;
        if (i == 0) si.StartupInfo.hStdInput = copy;
        else if (i == 1) si.StartupInfo.hStdOutput = copy;
        else si.StartupInfo.hStdError = copy;
    }
    InitializeProcThreadAttributeList(NULL, 1, 0, &size);
    si.lpAttributeList = HeapAlloc(GetProcessHeap(), 0, size);
    if (!si.lpAttributeList || !InitializeProcThreadAttributeList(si.lpAttributeList, 1, 0, &size)) goto start_done;
    attributes_ready = 1;
    if (!UpdateProcThreadAttribute(si.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handles, count * sizeof(HANDLE), NULL, NULL)) goto start_done;
    if (swprintf_s(protocol, 512, L"%lu,%lu,%lu,%llu,%llu,%llu,%d", GetCurrentProcessId(), created.dwHighDateTime,
        created.dwLowDateTime, (unsigned long long)(uintptr_t)ack, (unsigned long long)(uintptr_t)release,
        (unsigned long long)(uintptr_t)running, !arguments[0]) <= 0 || !SetEnvironmentVariableW(L"WG_NATIVE_START", protocol)) goto start_done;
    /* The child cannot load any bundled DLL until native exclusion is held.
     * Its hook proves parent identity and inherited object identity before ACK. */
    if (!CreateProcessW(hidden, command, NULL, NULL, TRUE, EXTENDED_STARTUPINFO_PRESENT, NULL, root, &si.StartupInfo, &pi)) goto start_done;
    SetEnvironmentVariableW(L"WG_NATIVE_START", NULL);
    CloseHandle(pi.hThread);
    waits[0] = ack; waits[1] = pi.hProcess;
    if (WaitForMultipleObjects(2, waits, FALSE, 60000) != WAIT_OBJECT_0) {
        TerminateProcess(pi.hProcess, 4); WaitForSingleObject(pi.hProcess, 5000); CloseHandle(pi.hProcess); result = 4; goto start_done;
    }
    CloseHandle(lock); lock = NULL; CloseHandle(claim); claim = NULL;
    if (!SetEvent(release)) { TerminateProcess(pi.hProcess, 4); WaitForSingleObject(pi.hProcess, 5000); CloseHandle(pi.hProcess); result = 4; goto start_done; }
    CloseHandle(hook); hook = NULL;
    if (WaitForSingleObject(pi.hProcess, INFINITE) != WAIT_OBJECT_0 || !GetExitCodeProcess(pi.hProcess, &code)) code = 3;
    CloseHandle(pi.hProcess); result = (int)code;
start_done:
    SetEnvironmentVariableW(L"WG_NATIVE_START", NULL);
    if (si.lpAttributeList) {
        if (attributes_ready) DeleteProcThreadAttributeList(si.lpAttributeList);
        HeapFree(GetProcessHeap(), 0, si.lpAttributeList);
    }
    for (i = 3; i < count; ++i) CloseHandle(handles[i]);
    if (ack) CloseHandle(ack);
    if (release) CloseHandle(release);
    if (running) CloseHandle(running);
    if (hook) CloseHandle(hook);
    if (lock) CloseHandle(lock);
    if (claim) CloseHandle(claim);
    return result;
}
int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show) {
    wchar_t self[CAP], root[CAP]; wchar_t **argv; int argc, code; Journal j; HANDLE lock = NULL;
    (void)instance; (void)previous; (void)show;
    if (!GetModuleFileNameW(NULL, self, CAP) || wcslen(self) >= CAP - 1 || !ordinary_path(self)) return 3;
    argv = CommandLineToArgvW(GetCommandLineW(), &argc); if (!argv) return 3;
    if (argc >= 3 && !wcsncmp(argv[1], L"--installer-", 12) && wcscmp(argv[1], L"--installer-watch")) {
        lock = transaction_lock(argv[2]); if (!lock) { LocalFree(argv); return 3; }
    }
    if (argc >= 3 && !wcscmp(argv[1], L"--installer-entry")) code = install_entry(argv[2], self);
    else if (argc == 7 && !wcscmp(argv[1], L"--installer-prepare"))
        code = prepare(argv[2], argv[3], argv[4], argv[5], argv[6]);
    else if (argc == 3 && !wcscmp(argv[1], L"--installer-commit"))
        code = load(argv[2], &j) ? commit(&j) : 3;
    else if (argc == 3 && !wcscmp(argv[1], L"--installer-rollback")) {
        code = 3;
        if (load(argv[2], &j) && j.owner == parent_pid() && owner_alive(&j)) {
            j.worker = GetCurrentProcessId();
            if (creation_time(j.worker, &j.worker_created) && save(&j, 1)) code = recover(&j);
        }
    }
    else if (argc == 3 && !wcscmp(argv[1], L"--installer-watch")) code = watch(argv[2]);
    else {
        wchar_t *separator; wcscpy_s(root, CAP, self); separator = wcsrchr(root, L'\\');
        if (!separator) code = 3;
        else { *separator = 0; code = start(root, arguments); }
    }
    if (lock) CloseHandle(lock);
    LocalFree(argv); return code;
}
