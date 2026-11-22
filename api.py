from __future__ import annotations

import errno
import html
import re
from dataclasses import dataclass

import aiohttp

from .const import POSITION_RESOLUTION

# Matches the <p id="name">value</p> elements of /variables.html
VAR_RE = re.compile(r'<p id="([^"]+)">(.*?)</p>', re.DOTALL)
TIMEOUT = aiohttp.ClientTimeout(total=5)

# Values of the "state" variable reported by MPC-HC
STATE_IDLE = -1  # no file loaded
STATE_STOPPED = 0
STATE_PAUSED = 1
STATE_PLAYING = 2

# MPC-HC prints sizes like "350 MB". Assumption: it divides by 1024 per unit.
_SIZE_RE = re.compile(r"^\s*([\d.,]+)\s*([KMGT]?B)\s*$", re.IGNORECASE)
_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}

class MpcHcConnectionError(Exception):
    """Raised when the web server is unreachable or returns a bad response."""


class MpcHcAccessDeniedError(MpcHcConnectionError):
    """The server dropped the connection right after the request.

    MPC-HC does this to non-local clients while "Allow access from localhost
    only" is enabled (observed with 1.7.13.32): the TCP connection succeeds,
    then it is reset instead of an HTTP error being returned.
    """


class MpcHcInvalidResponseError(MpcHcConnectionError):
    """The server answered, but not with an MPC-HC variables page."""


def _to_connection_error(err: Exception) -> MpcHcConnectionError:
    """Translate a low-level request failure into one of our exceptions."""
    # A reset or early close after a successful TCP connect means the server
    # refused to talk to us. Failures while connecting (refused, unreachable,
    # timeout) are plain connection errors instead.
    dropped = isinstance(err, aiohttp.ServerDisconnectedError) or (
        isinstance(err, aiohttp.ClientOSError)
        and not isinstance(err, aiohttp.ClientConnectorError)
        and err.errno in (errno.ECONNRESET, errno.EPIPE)
    )
    if dropped:
        return MpcHcAccessDeniedError(str(err) or type(err).__name__)
    return MpcHcConnectionError(str(err) or type(err).__name__)


def _int(value: str | None, default: int = 0) -> int:
    """Convert a string to int, falling back to a default on bad input."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default

def _parse_size(text: str) -> int | None:
    """Convert a size string such as "350 MB" to bytes (approximate).

    MPC-HC rounds the number it prints, so the result is only accurate to
    about one unit. Returns None if the text is not a recognised size.
    """
    match = _SIZE_RE.match(text)
    if not match:
        return None
    try:
        number = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    return int(number * _SIZE_UNITS[match.group(2).upper()])


@dataclass(frozen=True, slots=True)
class MpcHcStatus:
    """Snapshot of the player state parsed from /variables.html."""

    file: str  # file name only
    filepath: str  # full path on the PC
    filedir: str  # directory of the file
    state: int  # one of the STATE_* constants
    state_string: str  # human readable state, e.g. "Paused"
    position_ms: int
    duration_ms: int
    volume: int  # 0-100
    muted: bool
    playback_rate: float
    size: str  # file size as shown by MPC-HC, e.g. "350 MB"
    size_bytes: int | None  # size converted to bytes, see _parse_size
    version: str  # MPC-HC version

    @property
    def has_media(self) -> bool:
        """Whether a file is currently playing or paused."""
        return self.state in (STATE_PLAYING, STATE_PAUSED)

    @property
    def coarse_position(self) -> int | None:
        """Return the position in seconds, or None when no media is loaded.

        While playing the value is rounded down to POSITION_RESOLUTION seconds.
        While paused the exact position is returned so seeking stays precise.
        """
        if not self.has_media:
            return None
        seconds = self.position_ms // 1000
        if self.state == STATE_PLAYING:
            seconds -= seconds % POSITION_RESOLUTION
        return seconds

    @property
    def folder_name(self) -> str:
        """Last component of the directory, e.g. "Star.Trek.Deep.Space.Nine.S05"."""
        parts = [p for p in re.split(r"[\\/]+", self.filedir) if p]
        return parts[-1] if parts else ""


class MpcHcApi:
    """Client wrapping the HTTP endpoints of the MPC-HC web server."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int) -> None:
        self._session = session
        self._base = f"http://{host}:{port}"

    async def async_get_status(self) -> MpcHcStatus:
        """Fetch and parse /variables.html."""
        try:
            async with self._session.get(
                f"{self._base}/variables.html", timeout=TIMEOUT
            ) as resp:
                resp.raise_for_status()
                # The server sends no charset header, so do not let aiohttp
                # guess: the pages are UTF-8 (file names may contain accents)
                text = await resp.text(encoding="utf-8", errors="replace")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _to_connection_error(err) from err

        # Values are HTML-escaped, so unescape them (e.g. "&amp;" -> "&")
        v = {key: html.unescape(val) for key, val in VAR_RE.findall(text)}

        # The version variable is always present on a genuine MPC-HC page
        if "version" not in v:
            raise MpcHcInvalidResponseError("Unexpected response from MPC-HC")

        try:
            rate = float(v.get("playbackrate", "1"))
        except ValueError:
            rate = 1.0

        return MpcHcStatus(
            file=v.get("file", ""),
            filepath=v.get("filepath", ""),
            filedir=v.get("filedir", ""),
            state=_int(v.get("state"), STATE_IDLE),
            state_string=v.get("statestring", ""),
            position_ms=_int(v.get("position")),
            duration_ms=_int(v.get("duration")),
            volume=_int(v.get("volumelevel")),
            muted=v.get("muted") == "1",
            playback_rate=rate,
            size=v.get("size", ""),
            size_bytes=_parse_size(v.get("size", "")),
            version=v["version"],
        )

    async def async_send_command(self, command: int, **extra: str | int) -> None:
        """POST a command to /command.html."""
        data = {"wm_command": command, **extra}
        try:
            async with self._session.post(
                f"{self._base}/command.html", data=data, timeout=TIMEOUT
            ) as resp:
                resp.raise_for_status()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _to_connection_error(err) from err

    async def async_set_volume(self, level: int) -> None:
        """Set the volume (0-100). Command -2 is the web server's volume command."""
        await self.async_send_command(-2, volume=max(0, min(100, level)))

    async def async_seek(self, seconds: int) -> None:
        """Seek to an absolute position. Command -1 expects an HH:MM:SS string."""
        hours, rest = divmod(max(0, seconds), 3600)
        minutes, secs = divmod(rest, 60)
        await self.async_send_command(
            -1, position=f"{hours:02d}:{minutes:02d}:{secs:02d}"
        )

    async def async_get_snapshot(self) -> bytes:
        """Fetch the current video frame as JPEG bytes."""
        try:
            async with self._session.get(
                f"{self._base}/snapshot.jpg", timeout=TIMEOUT
            ) as resp:
                resp.raise_for_status()
                return await resp.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _to_connection_error(err) from err