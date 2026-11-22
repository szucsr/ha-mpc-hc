from __future__ import annotations

import logging
from collections.abc import Awaitable
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.debounce import Debouncer

from .api import (
    STATE_PLAYING,
    MpcHcAccessDeniedError,
    MpcHcApi,
    MpcHcConnectionError,
    MpcHcStatus,
)
from .const import DOMAIN, REFRESH_COOLDOWN, SCAN_IDLE, SCAN_PLAYING
from .parser import MediaInfo, parse_media

_LOGGER = logging.getLogger(__name__)

# The coordinator is stored in entry.runtime_data
type MpcHcConfigEntry = ConfigEntry["MpcHcCoordinator"]


class MpcHcCoordinator(DataUpdateCoordinator[MpcHcStatus]):
    """Polls MPC-HC and forwards commands to it."""

    def __init__(
        self, hass: HomeAssistant, entry: MpcHcConfigEntry, api: MpcHcApi
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=SCAN_IDLE),
            # Refresh right after a command, but allow quick successive commands
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=REFRESH_COOLDOWN, immediate=True
            ),
        )
        self.api = api
        # Cache of the parsed media info, keyed by (file, directory)
        self._media_cache: tuple[tuple[str, str], MediaInfo] | None = None

    @property
    def media_info(self) -> MediaInfo:
        """Return the parsed media info, re-parsing only when the file changes."""
        status = self.data
        key = (status.file, status.filedir)
        if self._media_cache is None or self._media_cache[0] != key:
            self._media_cache = (key, parse_media(*key))
        return self._media_cache[1]

    async def _async_update_data(self) -> MpcHcStatus:
        """Fetch the latest status; mark entities unavailable if unreachable."""
        try:
            status = await self.api.async_get_status()
        except MpcHcAccessDeniedError as err:
            raise UpdateFailed(
                "MPC-HC closed the connection. Make sure 'Allow access from "
                "localhost only' is disabled (View > Options > Player > Web Interface)"
            ) from err
        except MpcHcConnectionError as err:
            # Raised when the PC is off or MPC-HC is not running
            raise UpdateFailed(f"MPC-HC is unreachable: {err}") from err

        # Poll faster while playing so the position stays accurate
        self.update_interval = timedelta(
            seconds=SCAN_PLAYING if status.state == STATE_PLAYING else SCAN_IDLE
        )
        return status

    async def _run(self, call: Awaitable[None]) -> None:
        """Run an API call, surface failures to the UI, then refresh the state."""
        try:
            await call
        except MpcHcConnectionError as err:
            raise HomeAssistantError(f"MPC-HC command failed: {err}") from err
        # Refresh right away so the UI does not lag behind the player
        await self.async_request_refresh()

    async def async_command(self, command: int, **extra: str | int) -> None:
        """Send a WM_COMMAND to MPC-HC."""
        await self._run(self.api.async_send_command(command, **extra))

    async def async_set_volume(self, level: int) -> None:
        """Set the volume (0-100)."""
        await self._run(self.api.async_set_volume(level))

    async def async_seek(self, seconds: int) -> None:
        """Seek to an absolute position in seconds."""
        await self._run(self.api.async_seek(seconds))
