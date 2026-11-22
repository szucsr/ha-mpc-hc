"""Image platform: the live video snapshot and the TMDb pictures.

- Snapshot of the current video frame, refreshed when it is likely to change
- Poster, backdrop and episode picture downloaded from TMDb
"""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import STATE_PLAYING, MpcHcConnectionError
from .coordinator import MpcHcConfigEntry, MpcHcCoordinator
from .entity import MpcHcEntity

PARALLEL_UPDATES = 0

# While playing, the picture is marked as changed at most this often. Every
# change is a state write, so refreshing on each 2 s poll would be wasteful.
PLAYING_REFRESH_MS = 15_000
# While paused any position change (e.g. a seek) shows a new frame
PAUSED_REFRESH_MS = 1_000


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MpcHcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the snapshot image."""
    async_add_entities([MpcHcSnapshot(hass, entry.runtime_data)])


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