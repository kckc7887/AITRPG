import ctypes
import os


def is_process_alive(identifier: int) -> bool:
    if type(identifier) is not int or not 0 < identifier <= 0xFFFFFFFF:
        return False
    if os.name != 'nt':
        try:
            os.kill(identifier, 0)
        except (ProcessLookupError, OverflowError):
            return False
        except PermissionError:
            return True
        return True
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [
        ctypes.c_uint32,
        ctypes.c_bool,
        ctypes.c_uint32,
    ]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, identifier)
    if not handle:
        return ctypes.get_last_error() == 5
    try:
        # 正常退出码也可能为259，不能只比较STILL_ACTIVE。
        return kernel.WaitForSingleObject(handle, 0) == 0x102
    finally:
        kernel.CloseHandle(handle)
