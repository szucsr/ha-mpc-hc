"""Image platform: the live video snapshot and the TMDb pictures.

- Snapshot of the current video frame, refreshed when it is likely to change
- Poster, backdrop and episode picture downloaded from TMDb
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import aiohttp
from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import STATE_PLAYING, MpcHcConnectionError
from .coordinator import MpcHcConfigEntry, MpcHcCoordinator
from .entity import MpcHcEntity
from .metadata import MediaMetadata
from .shared import get_shared

PARALLEL_UPDATES = 0

# While playing, the picture is marked as changed at most this often. Every
# change is a state write, so refreshing on each 2 s poll would be wasteful.
PLAYING_REFRESH_MS = 15_000
# While paused any position change (e.g. a seek) shows a new frame
PAUSED_REFRESH_MS = 1_000

PICTURE_TIMEOUT = aiohttp.ClientTimeout(total=15)


@dataclass(frozen=True, kw_only=True)
class MpcHcImageDescription(ImageEntityDescription):
    """Describes a TMDb picture."""

    kind: str  # "poster", "backdrop" or "still"
    path_fn: Callable[[MediaMetadata], str | None]


TMDB_IMAGES: tuple[MpcHcImageDescription, ...] = (
    MpcHcImageDescription(
        key="poster",
        translation_key="poster",
        kind="poster",
        path_fn=lambda m: m.poster_path,
    ),
    MpcHcImageDescription(
        key="backdrop",
        translation_key="backdrop",
        kind="backdrop",
        path_fn=lambda m: m.backdrop_path,
    ),
    MpcHcImageDescription(
        key="episode_still",
        translation_key="episode_still",
        kind="still",
        entity_registry_enabled_default=False,
        path_fn=lambda m: m.still_path,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MpcHcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the snapshot and the TMDb pictures."""
    coordinator = entry.runtime_data
    entities: list[ImageEntity] = [MpcHcSnapshot(hass, coordinator)]
    entities += [
        MpcHcTmdbImage(hass, coordinator, description) for description in TMDB_IMAGES
    ]
    async_add_entities(entities)


class MpcHcSnapshot(ImageEntity, MpcHcEntity):
    """Screenshot of the video, proxied through Home Assistant."""

    _attr_translation_key = "snapshot"
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, coordinator: MpcHcCoordinator) -> None:
        ImageEntity.__init__(self, hass)
        MpcHcEntity.__init__(self, coordinator)
        self._attr_unique_id = f"{self._entry_id}_snapshot"
        self._attr_image_last_updated = dt_util.utcnow()
        # What the picture was last refreshed for
        self._last_key = (coordinator.data.file, coordinator.data.state)
        self._last_position_ms = coordinator.data.position_ms

    @callback
    def _handle_coordinator_update(self) -> None:
        """Bump the image timestamp when the picture is likely to have changed."""
        status = self.coordinator.data
        key = (status.file, status.state)
        threshold = (
            PLAYING_REFRESH_MS if status.state == STATE_PLAYING else PAUSED_REFRESH_MS
        )
        moved = abs(status.position_ms - self._last_position_ms) >= threshold
        if key != self._last_key or moved:
            self._last_key = key
            self._last_position_ms = status.position_ms
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Fetch a fresh frame; None when nothing is loaded or the player fails."""
        if not self.coordinator.data.has_media:
            return None
        try:
            return await self.coordinator.api.async_get_snapshot()
        except MpcHcConnectionError:
            return None


class MpcHcTmdbImage(ImageEntity, MpcHcEntity):
    """A picture from TMDb: poster, backdrop or the picture of the episode."""

    entity_description: MpcHcImageDescription

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: MpcHcCoordinator,
        description: MpcHcImageDescription,
    ) -> None:
        ImageEntity.__init__(self, hass)
        MpcHcEntity.__init__(self, coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._entry_id}_{description.key}"
        self._shared = get_shared(hass)
        self._url: str | None = None
        # The picture is downloaded once per URL and kept in memory
        self._loaded_url: str | None = None
        self._loaded_bytes: bytes | None = None

    def _current_url(self) -> str | None:
        metadata = self.coordinator.metadata
        if metadata is None:
            return None
        path = self.entity_description.path_fn(metadata)
        return self._shared.image_url(path, self.entity_description.kind)

    @callback
    def _handle_coordinator_update(self) -> None:
        url = self._current_url()
        if url != self._url:
            self._url = url
            # A new timestamp tells the frontend to fetch the new picture
            self._attr_image_last_updated = dt_util.utcnow() if url else None
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Download the picture (once per URL); None when there is none."""
        url = self._url
        if url is None:
            return None
        if url != self._loaded_url:
            session = async_get_clientsession(self.hass)
            try:
                async with session.get(url, timeout=PICTURE_TIMEOUT) as resp:
                    resp.raise_for_status()
                    self._loaded_bytes = await resp.read()
                    self._attr_content_type = resp.content_type
            except (aiohttp.ClientError, TimeoutError):
                return None
            self._loaded_url = url
        return self._loaded_bytes