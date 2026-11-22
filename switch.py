"""Switch platform: the player's mute state."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import Cmd
from .coordinator import MpcHcConfigEntry, MpcHcCoordinator
from .entity import MpcHcEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MpcHcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the mute switch."""
    async_add_entities([MpcHcMuteSwitch(entry.runtime_data)])


class MpcHcMuteSwitch(SwitchEntity, MpcHcEntity):
    """Reflects the player's mute state (from the "muted" variable)."""

    _attr_translation_key = "muted"

    def __init__(self, coordinator: MpcHcCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._entry_id}_muted"

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.muted

    async def async_turn_on(self, **kwargs: Any) -> None:
        # MPC-HC only has a mute toggle, so send it only if the state differs
        if not self.is_on:
            await self.coordinator.async_command(Cmd.MUTE_TOGGLE)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.is_on:
            await self.coordinator.async_command(Cmd.MUTE_TOGGLE)