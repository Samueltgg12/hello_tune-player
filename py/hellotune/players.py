"""
Audio playback for hello_tune-player.

Zero required dependencies: we write a WAV to a temp file and hand it to
whatever the platform offers.  Priority order:

  Windows : winsound (stdlib, built-in)
  macOS   : afplay
  Unix    : pw-play, paplay, aplay, ffplay, play (SoX), cvlc

A custom command can be supplied with ``--player``; ``{}`` (or a trailing
argument) is replaced by the file path, e.g.::

    hellotune play ip32 --player "mpv --no-video {}"
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from typing import List, Optional, Tuple


class PlaybackError(RuntimeError):
    pass


# candidate unix players in preference order: (name, argv-prefix, needs_dash?)
_UNIX_CANDIDATES: List[Tuple[str, List[str]]] = [
    ("pw-play", ["pw-play"]),
    ("paplay", ["paplay"]),
    ("aplay", ["aplay", "-q"]),
    ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
    ("play", ["play", "-q"]),
    ("cvlc", ["cvlc", "--play-and-exit", "--quiet"]),
]


def _winsound():
    try:
        import winsound  # noqa: F401  (Windows only)
        return True
    except ImportError:
        return False


def available_backends() -> List[str]:
    """Names of usable playback backends on this machine, best first."""
    found: List[str] = []
    if _winsound():
        found.append("winsound")
    for name, argv in _UNIX_CANDIDATES:
        if shutil.which(argv[0]):
            found.append(name)
    found.append("custom")
    return found


class Playback:
    """Play a WAV file asynchronously; stop() is safe at any time."""

    def __init__(self, backend: Optional[str] = None):
        self.backend = backend
        self.proc: Optional[subprocess.Popen] = None
        self._winsound_busy = False
        self._path: Optional[str] = None

    # -- backend resolution -------------------------------------------------

    def resolve_backend(self, backend: Optional[str] = None) -> str:
        want = backend or self.backend
        avail = available_backends()
        if not want or want == "auto":
            if not avail or avail == ["custom"]:
                raise PlaybackError(
                    "no audio player found; install one of "
                    + ", ".join(n for n, _ in _UNIX_CANDIDATES)
                    + " (or pass --player CMD)")
            return avail[0]
        if want == "custom":
            raise PlaybackError("custom backend needs an explicit command")
        if want in avail:
            return want
        raise PlaybackError(
            f"playback backend {want!r} is not available here "
            f"(have: {', '.join(avail)})")

    # -- playback ------------------------------------------------------------

    def play_file(self, path: str, backend: Optional[str] = None,
                  block: bool = False) -> None:
        self.stop()
        if not os.path.exists(path):
            raise PlaybackError(f"file not found: {path}")
        name = self.resolve_backend(backend)

        if name == "winsound":
            import winsound
            flags = winsound.SND_FILENAME | winsound.SND_ASYNC
            winsound.PlaySound(path, flags)
            self._winsound_busy = True
            self._path = path
            self.backend = name
            if block:
                time.sleep(self._estimate_seconds(path))
            return

        if name == "custom":
            raise PlaybackError("custom backend needs an explicit command")

        argv: Optional[List[str]] = None
        for cand, prefix in _UNIX_CANDIDATES:
            if cand == name:
                argv = prefix + [path]
                break
        if argv is None:
            raise PlaybackError(f"unknown backend {name!r}")

        try:
            self.proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except OSError as e:
            raise PlaybackError(f"failed to start {name}: {e}") from e
        self.backend = name
        self._path = path
        if block:
            self.wait()

    def play_custom(self, command: str, path: str, block: bool = False) -> None:
        """Play via a user-supplied command; {} or trailing arg = file."""
        self.stop()
        if "{}" in command:
            argv = _split_with_quotes(command.replace("{}", path))
        else:
            argv = _split_with_quotes(command) + [path]
        try:
            self.proc = subprocess.Popen(
                argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL)
        except OSError as e:
            raise PlaybackError(f"failed to start custom player: {e}") from e
        self.backend = "custom"
        self._path = path
        if block:
            self.wait()

    def is_playing(self) -> bool:
        if self._winsound_busy:
            # winsound gives no progress; assume playing until stopped
            return True
        if self.proc is None:
            return False
        return self.proc.poll() is None

    def wait(self, timeout: Optional[float] = None) -> None:
        t0 = time.monotonic()
        while self.is_playing():
            if timeout is not None and time.monotonic() - t0 > timeout:
                return
            time.sleep(0.05)
        if self.proc is not None:
            self.proc.wait()

    def stop(self) -> None:
        if self._winsound_busy:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
            self._winsound_busy = False
        if self.proc is not None:
            if self.proc.poll() is None:
                try:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                        self.proc.wait(timeout=1.0)
                except OSError:
                    pass
            self.proc = None
        self._path = None

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _estimate_seconds(path: str) -> float:
        from .wavio import read_wav_info
        try:
            info = read_wav_info(path)
            frames = info.get("data_bytes", 0) // max(1, info.get("block_align", 2))
            return frames / float(info.get("rate", 44100) or 44100)
        except Exception:
            return 5.0


def _split_with_quotes(s: str) -> List[str]:
    """shlex-like split that keeps this module dependency-free."""
    import shlex
    try:
        return shlex.split(s)
    except ValueError:
        return s.split()


def temp_wav_path(prefix: str = "hellotune-") -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".wav")
    os.close(fd)
    return path
