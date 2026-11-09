"""Local LibreOffice process isolation, ported from MarkItDown-Flow preconvert."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

_ENV = frozenset(
    "PATH SYSTEMROOT SYSTEMDRIVE COMSPEC WINDIR HOME USERPROFILE APPDATA LOCALAPPDATA "
    "TEMP TMP LANG LC_ALL".split()
)


def _resolve(path):
    executable = shutil.which(os.fspath(path))
    if executable:
        executable = str(Path(executable).absolute())
        console = Path(executable).with_suffix(".com")
        if (
            os.name == "nt"
            and Path(executable).name.lower() == "soffice.exe"
            and console.is_file()
        ):
            executable = str(console)
    return executable


def _write_profile(profile):
    (profile / "user").mkdir(parents=True)
    (profile / "user/registrymodifications.xcu").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>
<item oor:path="/org.openoffice.Office.Impress/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>
<item oor:path="/org.openoffice.Office.Jobs/Jobs/org.openoffice.Office.Jobs:Job['UpdateCheck']"><prop oor:name="Enabled" oor:op="fuse"><value>false</value></prop></item>
</oor:items>""",
        encoding="utf-8",
    )


class _WindowsJob:
    """Contain descendants before the suspended launcher can create soffice.bin."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]

        class Basic(ctypes.Structure):
            _fields_ = [
                ("time_process", ctypes.c_int64),
                ("time_job", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("min_ws", ctypes.c_size_t),
                ("max_ws", ctypes.c_size_t),
                ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.handle = self.kernel.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def attach(self, child):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(child._handle)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        resume = self.ctypes.WinDLL("ntdll").NtResumeProcess
        resume.argtypes = [self.ctypes.c_void_p]
        resume.restype = self.ctypes.c_long
        if resume(int(child._handle)) != 0:
            raise OSError("Could not resume LibreOffice")

    def kill(self):
        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        # Termination is asynchronous; descendants must release cwd/profile/logs
        # before TemporaryDirectory can remove them on Windows.
        accounting = (self.ctypes.c_byte * 48)()
        deadline = time.monotonic() + 10
        while True:
            if not self.kernel.QueryInformationJobObject(
                self.handle, 1, self.ctypes.byref(accounting), 48, None
            ):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            active = self.ctypes.c_uint32.from_buffer(accounting, 40).value
            if not active:
                return
            if time.monotonic() >= deadline:
                raise OSError("LibreOffice process tree did not terminate")
            time.sleep(0.01)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _kill_tree(child: subprocess.Popen, job=None) -> None:
    if os.name == "nt":
        job.kill()
    else:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if child.poll() is None:
        child.kill()
    child.wait(timeout=10)


def _execute(args: list[str], *, cwd: Path, timeout_s: float, output: Path) -> str:
    try:
        return _capture(args, cwd=cwd, timeout_s=timeout_s, output=output)
    finally:
        # Windows can signal process death just before closing its inherited
        # file handles. Retry only this owned capture, with a bounded deadline.
        deadline = time.monotonic() + 2
        while True:
            try:
                output.unlink(missing_ok=True)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)


def _capture(args: list[str], *, cwd: Path, timeout_s: float, output: Path) -> str:
    """Bound both process lifetime and captured output without inherited pipe readers."""
    with output.open("w+b") as stream:
        job = _WindowsJob() if os.name == "nt" else None
        try:
            child = subprocess.Popen(
                args,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=stream,
                env={k: v for k, v in os.environ.items() if k.upper() in _ENV},
                start_new_session=os.name != "nt",
                creationflags=0x08000004 if job else 0,  # NO_WINDOW | SUSPENDED
            )
            try:
                if job:
                    job.attach(child)
                deadline = time.monotonic() + timeout_s
                while child.poll() is None:
                    if time.monotonic() >= deadline or output.stat().st_size > 65536:
                        raise TimeoutError("LibreOffice deadline/output limit exceeded")
                    time.sleep(0.02)
                if child.returncode:
                    raise OSError("LibreOffice failed")
            finally:
                _kill_tree(child, job)
        finally:
            if job:
                job.close()
                if "child" in locals() and child.poll() is not None:
                    child._handle.Close()
        stream.seek(0)
        return stream.read(65536).decode("utf-8", errors="replace")
