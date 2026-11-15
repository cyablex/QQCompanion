"""Find executable names without opening Windows process handles."""

import ctypes
import os
from ctypes import wintypes

import psutil

_SYSTEM_BASIC_PROCESS_INFORMATION = 252
_STATUS_INVALID_INFO_CLASS = 0xC0000003
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_TH32CS_SNAPPROCESS = 0x00000002
_ERROR_NO_MORE_FILES = 18


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    ]


class _BasicProcessEntry(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", wintypes.ULONG),
        ("UniqueProcessId", wintypes.HANDLE),
        ("InheritedFromUniqueProcessId", wintypes.HANDLE),
        ("SequenceNumber", ctypes.c_uint64),
        ("ImageName", _UnicodeString),
    ]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _basic_named_pids(wanted):
    # Available on recent Windows 11. This query does not synchronize process
    # timing data, which avoids the additional work of a full snapshot.
    query = ctypes.WinDLL("ntdll").NtQuerySystemInformation
    query.argtypes = [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    query.restype = wintypes.ULONG
    size = wintypes.ULONG()
    buffer = ctypes.create_string_buffer(65536)
    result = query(_SYSTEM_BASIC_PROCESS_INFORMATION, buffer, len(buffer), ctypes.byref(size))
    if result == _STATUS_INVALID_INFO_CLASS:
        return None
    if result == _STATUS_INFO_LENGTH_MISMATCH:
        buffer = ctypes.create_string_buffer(size.value * 2)
        result = query(_SYSTEM_BASIC_PROCESS_INFORMATION, buffer, len(buffer), ctypes.byref(size))
    if result:
        raise OSError(f"Windows process list query failed (0x{result:08X})")

    rows = []
    offset = 0
    while True:
        entry = _BasicProcessEntry.from_buffer(buffer, offset)
        if entry.ImageName.Length:
            chars = entry.ImageName.Length // ctypes.sizeof(wintypes.WCHAR)
            name = ctypes.wstring_at(entry.ImageName.Buffer, chars).casefold()
            if name in wanted:
                rows.append((entry.UniqueProcessId, name))
        if not entry.NextEntryOffset:
            return rows
        offset += entry.NextEntryOffset


def _snapshot_named_pids(wanted):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    snapshot.restype = wintypes.HANDLE
    first = kernel.Process32FirstW
    next_entry = kernel.Process32NextW
    for function in (first, next_entry):
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
        function.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL

    handle = snapshot(_TH32CS_SNAPPROCESS, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = _ProcessEntry(dwSize=ctypes.sizeof(_ProcessEntry))
        rows = []
        valid = first(handle, ctypes.byref(entry))
        while valid:
            name = entry.szExeFile.casefold()
            if name in wanted:
                rows.append((entry.th32ProcessID, name))
            valid = next_entry(handle, ctypes.byref(entry))
        error = ctypes.get_last_error()
        if error != _ERROR_NO_MORE_FILES:
            raise ctypes.WinError(error)
        return rows
    finally:
        close(handle)


def named_pids(names):
    """Return (PID, casefolded name) pairs for the requested executable names."""
    wanted = {name.casefold() for name in names}
    if os.name != "nt":
        return [(proc.pid, proc.info["name"].casefold()) for proc in psutil.process_iter(["name"])
                if proc.info["name"] and proc.info["name"].casefold() in wanted]
    rows = _basic_named_pids(wanted)
    return _snapshot_named_pids(wanted) if rows is None else rows


def named_processes(names):
    """Inspect only candidates with an expected name; callers still check paths."""
    for pid, _ in named_pids(names):
        try:
            yield psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
