from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import MpcHcConfigEntry, MpcHcCoordinator
from .entity import MpcHcEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MpcHcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the volume and seek sliders."""
    coordinator = entry.runtime_data
    async_add_entities([MpcHcVolumeNumber(coordinator), MpcHcSeekNumber(coordinator)])


class MpcHcVolumeNumber(NumberEntity, MpcHcEntity):
    """Volume slider (0-100 %)."""

    _attr_translation_key = "volume"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: MpcHcCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._entry_id}_volume"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.volume

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_volume(int(value))


class MpcHcSeekNumber(NumberEntity, MpcHcEntity):
    """Position slider in seconds; its maximum is the length of the media."""

    _attr_translation_key = "seek"
    _attr_native_min_value = 0
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: MpcHcCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._entry_id}_seek"

    @property
    def native_max_value(self) -> float:
        # Never let max equal min, otherwise the slider breaks without media
        return max(self.coordinator.data.duration_ms // 1000, 1)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.coarse_position

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_seek(int(value))