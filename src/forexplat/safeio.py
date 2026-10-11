"""Escritura segura de archivos compartidos entre procesos (la interfaz y `watch`)."""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Escribe el archivo completo o nada: nunca deja un archivo a medias aunque otro proceso lea."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


@contextlib.contextmanager
def file_lock(path: Path, timeout: float = 5.0, stale_after: float = 30.0):
    """Bloqueo simple por archivo `<path>.lock` (funciona igual en Windows y Linux)."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            with contextlib.suppress(OSError):  # un bloqueo olvidado por un proceso caído
                if time.time() - lock.stat().st_mtime > stale_after:
                    lock.unlink()
                    continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"No se pudo bloquear {path.name}: otro proceso lo está usando")
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock.unlink()
