from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import Cmd
from .coordinator import MpcHcConfigEntry, MpcHcCoordinator
from .entity import MpcHcEntity

PARALLEL_UPDATES = 1

# Option -> command. The options are translated through the translation key.
FRAME_OPTIONS: dict[str, Cmd] = {
    "normal": Cmd.FRAME_NORMAL,
    "inside": Cmd.FRAME_INSIDE,
    "stretch": Cmd.FRAME_STRETCH,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MpcHcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the video frame select."""
    async_add_entities([MpcHcVideoFrameSelect(entry.runtime_data)])


class MpcHcVideoFrameSelect(SelectEntity, MpcHcEntity):
    """Choose how the video is fitted into the player window."""

    _attr_translation_key = "video_frame"
    _attr_options = list(FRAME_OPTIONS)
    _attr_entity_category = EntityCategory.CONFIG
    # MPC-HC does not report the current frame mode, so the state is the last
    # option chosen from Home Assistant
    _attr_assumed_state = True
    # Assumed starting mode; MPC-HC does not report the real one
    _attr_current_option = "inside"

    def __init__(self, coordinator: MpcHcCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._entry_id}_video_frame"

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_command(FRAME_OPTIONS[option])
        self._attr_current_option = option
        self.async_write_ha_state()