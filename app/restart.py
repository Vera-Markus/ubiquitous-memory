"""
Restart Now (1.7.2 plan, 33.1): after a Full Reset or a database update, start a new copy
of the app and close this one.

The new copy is told this one's process ID (--after-pid=N) and waits for it to exit before
it starts up (run_gui.py): a database update is applied at startup by swapping eve.db, which
Windows won't allow while the old copy still has it open.
"""
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

from app import paths

AFTER_PID = "--after-pid="


def restart_command(pid: int) -> List[str]:
    """How to start the app again: the exe for a release build, run_gui.py from source."""
    if getattr(sys, "frozen", False):
        return [sys.executable, f"{AFTER_PID}{pid}"]
    return [sys.executable, str(Path(paths.PROJECT_ROOT) / "run_gui.py"), f"{AFTER_PID}{pid}"]


def start_new_copy(pid: int) -> None:
    """Starts the new copy on its own, so it outlives this one."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(restart_command(pid), cwd=str(paths.PROJECT_ROOT), creationflags=flags, close_fds=True)


def restart_app(root, pid: Optional[int] = None) -> None:
    """Restart Now: a new copy, then this one closes (the window's normal close)."""
    import os
    start_new_copy(pid if pid is not None else os.getpid())
    root.destroy()


def pid_to_wait_for(argv: Sequence[str]) -> Optional[int]:
    """The --after-pid=N a restart passed, or None."""
    for arg in argv:
        if arg.startswith(AFTER_PID):
            try:
                return int(arg[len(AFTER_PID):])
            except ValueError:
                return None
    return None


def wait_for_exit(pid: int, timeout: float = 20.0) -> bool:
    """Waits (at most timeout seconds) for the old copy to exit. True once it has, or it was already gone."""
    if sys.platform == "win32":
        import ctypes
        SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not handle:
            return True             # already gone (or not ours to see)
        try:
            return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) != WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    import os
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.2)
    return False
