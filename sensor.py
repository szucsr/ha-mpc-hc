"""Sensor platform: player state, file info and details recognised from the file name."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import MpcHcConfigEntry, MpcHcCoordinator
from .entity import MpcHcEntity

# Sensors only read the coordinator data, so updates may run in parallel
PARALLEL_UPDATES = 0

# Home Assistant does not accept states longer than 255 characters
MAX_STATE_LENGTH = 255

def _clip(text: str) -> str | None:
    """Limit text to the maximum state length; empty text becomes None."""
    if not text:
        return None
    if len(text) <= MAX_STATE_LENGTH:
        return text
    return text[: MAX_STATE_LENGTH - 1] + "…"

@dataclass(frozen=True, kw_only=True)
class MpcHcSensorDescription(SensorEntityDescription):
    """Describes an MPC-HC sensor."""

    value_fn: Callable[[MpcHcCoordinator], StateType]
    # Optional extra state attributes, e.g. the untruncated path
    attrs_fn: Callable[[MpcHcCoordinator], dict[str, Any]] | None = None

SENSORS: tuple[MpcHcSensorDescription, ...] = (
    MpcHcSensorDescription(
        key="file",
        translation_key="file",
        value_fn=lambda c: _clip(c.data.file),
        attrs_fn=lambda c: {"path": c.data.filepath or None},
    ),
    MpcHcSensorDescription(
        key="directory",
        translation_key="directory",
        # State: the last folder only; the full path stays in the attribute
        value_fn=lambda c: _clip(c.data.folder_name),
        attrs_fn=lambda c: {"path": c.data.filedir or None},
    ),
    MpcHcSensorDescription(
        key="player_state",
        translation_key="player_state",
        value_fn=lambda c: c.data.state_string or None,
    ),
    MpcHcSensorDescription(
        key="position",
        translation_key="position",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        value_fn=lambda c: c.data.coarse_position,
    ),
    MpcHcSensorDescription(
        key="duration",
        translation_key="duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        value_fn=lambda c: c.data.duration_ms // 1000 if c.data.has_media else None,
    ),
    MpcHcSensorDescription(
        key="file_size",
        translation_key="file_size",
        device_class=SensorDeviceClass.DATA_SIZE,
        # The value is stored in bytes; Home Assistant converts it for display
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEGABYTES,
        suggested_display_precision=0,
        value_fn=lambda c: c.data.size_bytes if c.data.has_media else None,
    ),
    MpcHcSensorDescription(
        key="playback_rate",
        translation_key="playback_rate",
        native_unit_of_measurement="x",
        # MPC-HC always reports 1 here, so the sensor is opt-in until it is fixed
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.data.playback_rate,
    ),
    MpcHcSensorDescription(
        key="version",
        translation_key="version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.version,
    ),
    # Values recognised from the file name (see parser.py)
    MpcHcSensorDescription(
        key="title",
        translation_key="title",
        value_fn=lambda c: (c.media_info.title or None) if c.data.has_media else None,
    ),
    MpcHcSensorDescription(
        key="season",
        translation_key="season",
        value_fn=lambda c: c.media_info.season if c.data.has_media else None,
    ),
    MpcHcSensorDescription(
        key="episode",
        translation_key="episode",
        value_fn=lambda c: c.media_info.episode if c.data.has_media else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MpcHcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the MPC-HC sensors."""
    coordinator = entry.runtime_data
    async_add_entities(MpcHcSensor(coordinator, desc) for desc in SENSORS)


class MpcHcSensor(SensorEntity, MpcHcEntity):
    """A sensor whose value is computed from the coordinator data."""

    entity_description: MpcHcSensorDescription

    def __init__(
        self, coordinator: MpcHcCoordinator, description: MpcHcSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._entry_id}_{description.key}"

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self.coordinator) if attrs_fn else None