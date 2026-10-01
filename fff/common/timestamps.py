"""File timestamp helpers.

``set_file_timestamps`` also handles Windows creation time. ``copy_file_times``
supports the access-time behavior required by e621 and FurAffinity downloads.
"""
import logging
import os
from pathlib import Path


def copy_file_times(source: Path, destination: Path, *, keep_source_atime: bool = False) -> None:
    """Copy the modified time from ``source`` to ``destination``.

    keep_source_atime=False: atime is set to the source mtime.
    keep_source_atime=True:  atime is implemented in the source.
    """
    try:
        stat_info = Path(source).stat()
        atime = stat_info.st_atime if keep_source_atime else stat_info.st_mtime
        os.utime(destination, (atime, stat_info.st_mtime))
    except Exception as e:
        print(f"    Warning: Could not copy file times: {e}")


def set_file_timestamps(file_path, atime=None, mtime=None, ctime=None):
    """Apply access/modified times and, on Windows, creation time.

    Any of atime/mtime/ctime left as None is read from the file's current
    stat rather than changed. ctime is only applied on Windows (os.name == 'nt');
    elsewhere there's no OS-level file "creation time" to set.
    """
    stat_result = os.stat(file_path)
    atime = stat_result.st_atime if atime is None else atime
    mtime = stat_result.st_mtime if mtime is None else mtime
    os.utime(file_path, (atime, mtime))

    if os.name != 'nt' or ctime is None:
        return

    try:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ('dwLowDateTime', wintypes.DWORD),
                ('dwHighDateTime', wintypes.DWORD),
            ]

        def to_filetime(timestamp):
            value = int(timestamp * 10000000) + 116444736000000000
            return FILETIME(value & 0xFFFFFFFF, value >> 32)

        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE

        set_file_time = kernel32.SetFileTime
        set_file_time.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        ]
        set_file_time.restype = wintypes.BOOL

        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = create_file(
            file_path,
            0x0100,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x80,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), f"CreateFileW failed for {file_path}")

        try:
            created = to_filetime(ctime)
            accessed = to_filetime(atime)
            modified = to_filetime(mtime)
            if not set_file_time(handle, ctypes.byref(created), ctypes.byref(accessed), ctypes.byref(modified)):
                raise OSError(ctypes.get_last_error(), f"SetFileTime failed for {file_path}")
        finally:
            close_handle(handle)
    except Exception as exc:
        logging.getLogger(__name__).warning(f"Failed to set creation time for {file_path}: {exc}")
