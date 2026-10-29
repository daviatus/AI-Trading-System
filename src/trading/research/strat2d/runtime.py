"""Standard-library process high-water memory measurement; no service dependencies."""

import ctypes
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def _windows_api():
    """One structure/pointer type per process; ctypes caches POINTER types globally."""
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    return Counters, kernel, psapi


def process_memory():
    if os.name != "nt":
        import resource

        with open(f"/proc/{os.getpid()}/statm", encoding="ascii") as handle:
            rss = int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        return {
            "rss_bytes": rss,
            "peak_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024,
        }
    counters, kernel, psapi = _windows_api()
    value = counters()
    value.cb = ctypes.sizeof(value)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
        raise RuntimeError("STRAT-2D process high-water memory measurement unavailable")
    return {
        "rss_bytes": int(value.WorkingSetSize),
        "peak_rss_bytes": int(value.PeakWorkingSetSize),
        "private_commit_bytes": int(value.PagefileUsage),
    }


def peak_rss_bytes():
    """Retained public helper; reads the same cached OS high-water counter."""
    return process_memory()["peak_rss_bytes"]
