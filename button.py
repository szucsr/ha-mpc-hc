from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import Cmd
from .coordinator import MpcHcConfigEntry, MpcHcCoordinator
from .entity import MpcHcEntity

# Commands are sent one at a time so MPC-HC is not flooded
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class MpcHcButtonDescription(ButtonEntityDescription):
    """Describes an MPC-HC button."""

    command: Cmd


def _button(key: str, command: Cmd, **kwargs) -> MpcHcButtonDescription:
    """Build a button description whose translation key equals its key."""
    return MpcHcButtonDescription(
        key=key, translation_key=key, command=command, **kwargs
    )


# Rarely used buttons are marked as configuration entities so they stay out of default dashboards.
_CONFIG = {"entity_category": EntityCategory.CONFIG}

BUTTONS: tuple[MpcHcButtonDescription, ...] = (
    # Seek jumps (the step sizes are configured in MPC-HC)
    _button("jump_back_small", Cmd.JUMP_BACK_SMALL),
    _button("jump_forward_small", Cmd.JUMP_FWD_SMALL),
    _button("jump_back_medium", Cmd.JUMP_BACK_MEDIUM, entity_registry_enabled_default=False),
    _button("jump_forward_medium", Cmd.JUMP_FWD_MEDIUM, entity_registry_enabled_default=False),
    _button("jump_back_large", Cmd.JUMP_BACK_LARGE),
    _button("jump_forward_large", Cmd.JUMP_FWD_LARGE),
    _button("jump_back_keyframe", Cmd.JUMP_BACK_KEYFRAME, entity_registry_enabled_default=False),
    _button("jump_forward_keyframe", Cmd.JUMP_FWD_KEYFRAME, entity_registry_enabled_default=False),
    _button("jump_to_beginning", Cmd.JUMP_TO_BEGINNING),
    # Playback speed
    _button("reset_rate", Cmd.RESET_RATE),
    _button("decrease_rate", Cmd.DECREASE_RATE),
    _button("increase_rate", Cmd.INCREASE_RATE),
    # Audio track cycling, delay in 10 ms steps
    _button("audio_previous", Cmd.AUDIO_PREV),
    _button("audio_next", Cmd.AUDIO_NEXT),
    _button("audio_delay_decrease", Cmd.AUDIO_DELAY_DEC),
    _button("audio_delay_increase", Cmd.AUDIO_DELAY_INC),
    # Subtitles on/off, track cycling, delay (the step size is configured in MPC-HC), jumping between subtitle lines
    _button("subtitle_toggle", Cmd.SUBTITLE_TOGGLE),
    _button("subtitle_previous", Cmd.SUBTITLE_PREV),
    _button("subtitle_next", Cmd.SUBTITLE_NEXT),
    _button("subtitle_delay_decrease", Cmd.SUBTITLE_DELAY_DEC),
    _button("subtitle_delay_increase", Cmd.SUBTITLE_DELAY_INC),
    _button("subtitle_goto_previous", Cmd.SUBTITLE_PREV_LINE),
    _button("subtitle_goto_next", Cmd.SUBTITLE_NEXT_LINE),
    # Fullscreen without changing the display resolution
    _button("fullscreen", Cmd.FULLSCREEN_NO_RES_CHANGE),
    # Closes MPC-HC on the PC, so it is kept out of default dashboards
    _button("exit", Cmd.EXIT, **_CONFIG),
    # Pan & Scan size
    _button("panscan_reset", Cmd.PANSCAN_RESET, **_CONFIG),
    _button("panscan_inc_width", Cmd.PANSCAN_INC_WIDTH, **_CONFIG),
    _button("panscan_dec_width", Cmd.PANSCAN_DEC_WIDTH, **_CONFIG),
    # Always on top
    _button("ontop_never", Cmd.ONTOP_NEVER, **_CONFIG),
    _button("ontop_always", Cmd.ONTOP_ALWAYS, **_CONFIG),
    _button("ontop_while_playing", Cmd.ONTOP_WHILE_PLAYING, **_CONFIG),
    _button("ontop_while_playing_video", Cmd.ONTOP_WHILE_PLAYING_VIDEO, **_CONFIG),
)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: MpcHcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the MPC-HC buttons."""
    coordinator = entry.runtime_data
    async_add_entities(MpcHcButton(coordinator, desc) for desc in BUTTONS)


class MpcHcButton(ButtonEntity, MpcHcEntity):
    """A button that sends one WM_COMMAND to MPC-HC."""

    entity_description: MpcHcButtonDescription

    def __init__(
        self, coordinator: MpcHcCoordinator, description: MpcHcButtonDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._entry_id}_{description.key}"

    async def async_press(self) -> None:
        await self.coordinator.async_command(self.entity_description.command)