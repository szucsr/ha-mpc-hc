"""Select platform: window and video settings (video frame mode, always on top)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
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

ONTOP_OPTIONS: dict[str, Cmd] = {
    "never": Cmd.ONTOP_NEVER,
    "always": Cmd.ONTOP_ALWAYS,
    "while_playing": Cmd.ONTOP_WHILE_PLAYING,
    "while_playing_video": Cmd.ONTOP_WHILE_PLAYING_VIDEO,
}


@dataclass(frozen=True, kw_only=True)
class MpcHcSelectDescription(SelectEntityDescription):
    """Describes an MPC-HC select whose options each send one command."""

    commands: dict[str, Cmd]  # option -> command
    default: str  # assumed starting option


SELECTS: tuple[MpcHcSelectDescription, ...] = (
    MpcHcSelectDescription(
        key="video_frame",
        translation_key="video_frame",
        entity_category=EntityCategory.CONFIG,
        options=list(FRAME_OPTIONS),
        commands=FRAME_OPTIONS,
        default="inside",
    ),
    MpcHcSelectDescription(
        key="always_on_top",
        translation_key="always_on_top",
        entity_category=EntityCategory.CONFIG,
        options=list(ONTOP_OPTIONS),
        commands=ONTOP_OPTIONS,
        default="never",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MpcHcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the video frame and always-on-top selects."""
    coordinator = entry.runtime_data
    async_add_entities(MpcHcSelect(coordinator, desc) for desc in SELECTS)


class MpcHcSelect(SelectEntity, MpcHcEntity):
    """A select that sends the command of the chosen option to MPC-HC."""

    entity_description: MpcHcSelectDescription
    # MPC-HC does not report these settings, so the state is the last option
    # chosen from Home Assistant
    _attr_assumed_state = True

    def __init__(
        self, coordinator: MpcHcCoordinator, description: MpcHcSelectDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._entry_id}_{description.key}"
        # Assumed starting option; MPC-HC does not report the real one
        self._attr_current_option = description.default

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_command(self.entity_description.commands[option])
        self._attr_current_option = option
        self.async_write_ha_state()