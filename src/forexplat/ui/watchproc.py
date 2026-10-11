"""Control de `watch` como proceso aparte: iniciar, detener, saber si sigue vivo y leer su registro."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from ..safeio import atomic_write_text


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":  # os.kill(pid, 0) en Windows TERMINARÍA el proceso: no se usa
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    with contextlib.suppress(ChildProcessError, OSError):
        done, _ = os.waitpid(pid, os.WNOHANG)  # recoge el proceso si ya terminó (hijo nuestro)
        if done:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class WatchController:
    def __init__(self, root: Path, profile: str, command: list[str] | None = None) -> None:
        self.root = Path(root)
        self.profile = profile
        self._command = command  # solo para pruebas
        self.pid_path = self.root / "state" / f"watch_{profile}.pid"
        self.log_path = self.root / "logs" / f"watch_{profile}.log"

    # --- estado ------------------------------------------------------------
    def _read_pid(self) -> int | None:
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def is_running(self) -> bool:
        pid = self._read_pid()
        if pid is None:
            return False
        if _pid_alive(pid):
            return True
        with contextlib.suppress(OSError):  # el proceso ya no existe: se limpia el archivo viejo
            self.pid_path.unlink()
        return False

    # --- acciones ----------------------------------------------------------
    def build_command(self, strategies=None, symbols=None, interval: int = 60) -> list[str]:
        if self._command is not None:
            return list(self._command)
        command = [
            sys.executable, "-u", "-m", "forexplat", "watch", "--profile", self.profile,
            "--interval", str(interval),
        ]
        for name in strategies or []:
            command += ["--strategy", name]
        for symbol in symbols or []:
            command += ["--symbol", symbol]
        return command

    def start(self, strategies=None, symbols=None, interval: int = 60) -> int:
        if self.profile != "demo":
            raise ValueError(
                "Desde la interfaz solo se puede iniciar la vigilancia en demo. Para el perfil real "
                "usa la consola: py -m forexplat watch --profile real --i-understand-real-account"
            )
        if self.is_running():
            raise RuntimeError("La vigilancia ya está en marcha.")

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        src = self.root / "src"
        if src.exists():
            env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        else:
            kwargs["start_new_session"] = True

        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(f"--- inicio {datetime.now():%Y-%m-%d %H:%M:%S} ---\n")
            log.flush()
            process = subprocess.Popen(
                self.build_command(strategies, symbols, interval),
                cwd=self.root, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                **kwargs,
            )
        atomic_write_text(self.pid_path, str(process.pid))
        return process.pid

    def stop(self) -> bool:
        """Detiene la vigilancia. Devuelve False si no estaba en marcha."""
        pid = self._read_pid()
        running = pid is not None and _pid_alive(pid)
        if running:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGTERM)
                with contextlib.suppress(ChildProcessError, OSError):
                    for _ in range(50):
                        done, _ = os.waitpid(pid, os.WNOHANG)
                        if done:
                            break
                        time.sleep(0.1)
        with contextlib.suppress(OSError):
            self.pid_path.unlink()
        if running:
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(f"--- detenida {datetime.now():%Y-%m-%d %H:%M:%S} ---\n")
        return running

    def log_tail(self, lines: int = 40) -> str:
        try:
            content = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(content.splitlines()[-lines:])
