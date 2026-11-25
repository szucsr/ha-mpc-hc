"""Data update coordinator: the single place that talks to a player.

- Polls the player state (faster while playing)
- Sends commands and refreshes the state right after
- Looks up TMDb metadata in the background when a new file starts
- Saves and removes the user's TMDb corrections
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    STATE_PLAYING,
    MpcHcAccessDeniedError,
    MpcHcApi,
    MpcHcConnectionError,
    MpcHcStatus,
)
from .const import DOMAIN, REFRESH_COOLDOWN, SCAN_IDLE, SCAN_PLAYING, SCOPE_FILE
from .metadata import (
    STATUS_DISABLED,
    STATUS_ERROR,
    MediaMetadata,
    async_fetch_metadata,
    file_override_key,
    series_override_key,
)
from .parser import MediaInfo, parse_media
from .shared import get_shared
from .tmdb import TmdbClient

_LOGGER = logging.getLogger(__name__)

# The coordinator is stored in entry.runtime_data
type MpcHcConfigEntry = ConfigEntry["MpcHcCoordinator"]


class MpcHcCoordinator(DataUpdateCoordinator[MpcHcStatus]):
    """Polls MPC-HC, forwards commands to it and looks up TMDb metadata."""

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

        # TMDb metadata of the current file; None while the lookup is running
        self.metadata: MediaMetadata | None = None
        self._metadata_key: tuple[str, str] | None = None
        self._metadata_task: asyncio.Task | None = None

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
        self._track_media(status)
        return status

    # ----- TMDb metadata -----

    def _track_media(self, status: MpcHcStatus) -> None:
        """Start a metadata lookup when a different file starts playing."""
        key = (status.file, status.filedir) if status.has_media else None
        if key == self._metadata_key:
            return
        self._metadata_key = key
        self._cancel_metadata_task()
        self.metadata = None
        if key is not None:
            self._start_metadata_task(status)

    def _cancel_metadata_task(self) -> None:
        if self._metadata_task is not None and not self._metadata_task.done():
            self._metadata_task.cancel()
        self._metadata_task = None

    def _start_metadata_task(self, status: MpcHcStatus) -> None:
        """Look the file up in the background so polling is never delayed."""
        if not get_shared(self.hass).tmdb_token:
            self.metadata = MediaMetadata(status=STATUS_DISABLED)
            return
        key = (status.file, status.filedir)
        self._metadata_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_load_metadata(key, status.filepath),
            name=f"{DOMAIN} metadata lookup",
        )

    async def _async_load_metadata(self, key: tuple[str, str], filepath: str) -> None:
        shared = get_shared(self.hass)
        client = TmdbClient(
            async_get_clientsession(self.hass),
            shared.tmdb_token,
            shared.tmdb_language or self.hass.config.language,
        )
        try:
            metadata = await async_fetch_metadata(
                client, shared, parse_media(*key), filepath
            )
        except Exception:
            _LOGGER.exception("Unexpected error while looking up TMDb metadata")
            metadata = MediaMetadata(status=STATUS_ERROR)

        # Another file may have started playing while we were waiting
        if key != self._metadata_key:
            return
        self.metadata = metadata
        self.async_update_listeners()

    def _restart_metadata(self) -> None:
        """Repeat the lookup for the current file, e.g. after a correction."""
        self._cancel_metadata_task()
        self.metadata = None
        if self.data.has_media:
            self._start_metadata_task(self.data)
        self.async_update_listeners()

    async def async_set_override(
        self, scope: str, tmdb_id: int | None, media_type: str | None
    ) -> None:
        """Save (or, without an ID, remove) a TMDb correction for the current file."""
        status = self.data
        if not status.has_media:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_media"
            )
        if tmdb_id is not None and media_type is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="media_type_required"
            )

        if scope == SCOPE_FILE:
            key = file_override_key(status.filepath)
        else:
            title = self.media_info.title
            if not title:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="no_title"
                )
            key = series_override_key(title)

        value = {"id": tmdb_id, "type": media_type} if tmdb_id is not None else None
        await get_shared(self.hass).async_set_override(key, value)
        self._restart_metadata()

    # ----- Commands -----

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

    @property
    def media_info(self) -> MediaInfo:
        """Return the parsed media info, re-parsing only when the file changes."""
        status = self.data
        key = (status.file, status.filedir)
        if self._media_cache is None or self._media_cache[0] != key:
            self._media_cache = (key, parse_media(*key))
        return self._media_cache[1]