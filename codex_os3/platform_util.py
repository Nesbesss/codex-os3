"""Process helpers that behave the same on macOS, Linux and Windows.
(Careful: on Windows os.kill(pid, 0) terminates the process instead of probing it.)"""
import os, signal, subprocess, sys

WINDOWS = sys.platform == "win32"


def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if WINDOWS:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def popen_group_kwargs():
    """Start a child in its own process group so we can kill it with its children."""
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_tree(p):
    """Kill a Popen and everything it spawned (codex is a node wrapper around a native binary)."""
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass


def terminate(pid):
    """Ask a process to stop (POSIX SIGTERM; on Windows a hard stop, use drain files instead)."""
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(int(pid), signal.SIGTERM)
    except (OSError, ValueError):
        pass
