"""Process memory for experiment reports, without extra dependencies.

Windows (the project's machine): the process's peak working set, read with the Win32
call GetProcessMemoryInfo. Elsewhere: peak resident size from `resource`.
"""

import ctypes
import sys


class _Counters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_memory_mb() -> float:
    """Highest memory this process has used so far, in MB."""
    if sys.platform == "win32":
        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        # Declare the C types: by default ctypes truncates the 64-bit process handle.
        kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_Counters), ctypes.c_ulong,
        ]  # fmt: skip
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters),
                                          counters.cb):  # fmt: skip
            raise OSError("GetProcessMemoryInfo failed")
        peak: int = counters.PeakWorkingSetSize
        return round(peak / 2**20, 1)
    import resource

    kilobytes: int = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KB on Linux
    return round(kilobytes / 1024, 1)
