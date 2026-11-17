"""Linux subprocess trampoline: die with Petal, then become the requested program.

Runs in a fresh interpreter, avoiding preexec_fn in a multithreaded Qt process.
No Qt or application state is imported here.
"""
import ctypes
import os
import signal
import sys


def main():
    expected_parent = int(sys.argv[1])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    # Parent may have died before prctl was established.
    if os.getppid() != expected_parent:
        return 125
    priority = int(sys.argv[2])
    if priority:
        os.nice(priority)
    # The frozen application needs its bundled libraries; system FFmpeg needs
    # the host libraries. Restore the bootloader's original search path on exec.
    if getattr(sys, "frozen", False):
        original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            os.environ.pop("LD_LIBRARY_PATH", None)
        else:
            os.environ["LD_LIBRARY_PATH"] = original
    os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
