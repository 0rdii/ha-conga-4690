from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.util import Throttle

from .button import CongaEntity
from .const import DOMAIN

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=60)

_LOGGER = logging.getLogger(__name__)

ICON = "mdi:water"
FAN_ICON = "mdi:fan"
FAN_SPEEDS = ["Apagado", "Eco", "Normal", "Turbo"]
WATER_LEVELS = ["Apagado", "Bajo", "Medio", "Alto"]
SIGNAL_FAN_SPEED = f"{DOMAIN}_fan_speed"
SIGNAL_WATER_LEVEL = f"{DOMAIN}_water_level"
SHARED_STATE = "shared_state"


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up Cecotec Conga select entities."""
    entities = []
    devices = hass.data[DOMAIN][config_entry.entry_id]["devices"]

    for device in devices:
        conga_data = hass.data[DOMAIN][config_entry.entry_id]
        entities.append(
            CongaWaterLevelSelect(
                hass,
                conga_data,
                device["sn"],
                device["note_name"],
            )
        )

    async_add_entities(entities, update_before_add=True)


class CongaFanSpeedSelect(SelectEntity, CongaEntity):
    """Fan speed selector."""

    def __init__(
        self,
        hass: HomeAssistant,
        conga_data: dict,
        sn: str,
        device_name: str,
    ):
        self._hass = hass
        self._conga_data = conga_data
        self._conga_client = conga_data["controller"]
        self._device_name = device_name
        self._name = f"{self._device_name} Ventilador"
        self._sn = sn
        self._current_option = FAN_SPEEDS[1]
        self._unique_id = f"{self._sn}_fan_speed"
        CongaEntity.__init__(self, conga_data, device_name, sn)
        SelectEntity.__init__(self)

    async def async_added_to_hass(self) -> None:
        """Register shared fan speed listener."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_FAN_SPEED, self._handle_fan_speed_signal)
        )

    def _handle_fan_speed_signal(self, sn: str, level: int) -> None:
        if sn != self._sn:
            return
        self._set_shared_level(level)
        self._current_option = FAN_SPEEDS[_normalize_option_level(level, len(FAN_SPEEDS) - 1)]
        self.schedule_update_ha_state()

    def _set_shared_level(self, level: int) -> None:
        shared = self._conga_data.setdefault(SHARED_STATE, {}).setdefault(self._sn, {})
        shared["fan_speed"] = level

    def _get_shared_level(self) -> int | None:
        shared = self._conga_data.setdefault(SHARED_STATE, {}).setdefault(self._sn, {})
        return shared.get("fan_speed")

    def _send_shared_state(self, level: int) -> None:
        self._hass.loop.call_soon_threadsafe(
            async_dispatcher_send,
            self._hass,
            SIGNAL_FAN_SPEED,
            self._sn,
            level,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def icon(self) -> str:
        return FAN_ICON

    @property
    def options(self) -> list[str]:
        return FAN_SPEEDS

    @property
    def current_option(self) -> str | None:
        shared_level = self._get_shared_level()
        if shared_level is not None:
            self._current_option = FAN_SPEEDS[_normalize_option_level(shared_level, len(FAN_SPEEDS) - 1)]
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Set fan speed."""
        if option not in FAN_SPEEDS:
            raise ValueError(f"Unsupported fan speed: {option}")

        level = FAN_SPEEDS.index(option)
        await self._hass.async_add_executor_job(
            self._conga_client.set_fan_speed,
            self._sn,
            level,
        )
        await self._hass.async_add_executor_job(self._conga_client.update_shadows, self._sn)
        self._set_shared_level(level)
        self._current_option = option
        _update_cached_vacuum(self._conga_data, self._sn, "set_cached_fan_speed", level)
        async_dispatcher_send(self.hass, SIGNAL_FAN_SPEED, self._sn, level)
        self.async_write_ha_state()

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        """Refresh fan speed from the last known vacuum status."""
        try:
            self._conga_client.update_shadows(self._sn)
            state_all = self._conga_client.get_status()
            level = _normalize_option_level(state_all.get("cleanPerference"), len(FAN_SPEEDS) - 1)
            self._set_shared_level(level)
            self._current_option = FAN_SPEEDS[level]
            self._send_shared_state(level)
        except Exception as exc:
            _LOGGER.error("Unable to fetch Conga 4690 fan speed: %s", exc)


class CongaWaterLevelSelect(SelectEntity, CongaEntity):
    """Water level selector for the mop function."""

    def __init__(
        self,
        hass: HomeAssistant,
        conga_data: dict,
        sn: str,
        device_name: str,
    ):
        self._hass = hass
        self._conga_data = conga_data
        self._conga_client = conga_data["controller"]
        self._device_name = device_name
        self._name = f"{self._device_name} Fregado"
        self._sn = sn
        self._current_option = WATER_LEVELS[0]
        self._unique_id = f"{self._sn}_water_level"
        CongaEntity.__init__(self, conga_data, device_name, sn)
        SelectEntity.__init__(self)

    async def async_added_to_hass(self) -> None:
        """Register shared water level listener."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_WATER_LEVEL, self._handle_water_level_signal)
        )

    def _handle_water_level_signal(self, sn: str, level: int) -> None:
        if sn != self._sn:
            return
        self._set_shared_level(level)
        self._current_option = WATER_LEVELS[_normalize_option_level(level, len(WATER_LEVELS) - 1)]
        self.schedule_update_ha_state()

    def _set_shared_level(self, level: int) -> None:
        shared = self._conga_data.setdefault(SHARED_STATE, {}).setdefault(self._sn, {})
        shared["water_level"] = level

    def _get_shared_level(self) -> int | None:
        shared = self._conga_data.setdefault(SHARED_STATE, {}).setdefault(self._sn, {})
        return shared.get("water_level")

    def _send_shared_state(self, level: int) -> None:
        self._hass.loop.call_soon_threadsafe(
            async_dispatcher_send,
            self._hass,
            SIGNAL_WATER_LEVEL,
            self._sn,
            level,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def icon(self) -> str:
        return ICON

    @property
    def options(self) -> list[str]:
        return WATER_LEVELS

    @property
    def current_option(self) -> str | None:
        shared_level = self._get_shared_level()
        if shared_level is not None:
            self._current_option = WATER_LEVELS[_normalize_option_level(shared_level, len(WATER_LEVELS) - 1)]
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Set water level."""
        if option not in WATER_LEVELS:
            raise ValueError(f"Unsupported water level: {option}")

        level = WATER_LEVELS.index(option)
        await self._hass.async_add_executor_job(
            self._conga_client.set_water_level,
            self._sn,
            level,
        )
        await self._hass.async_add_executor_job(self._conga_client.update_shadows, self._sn)
        self._set_shared_level(level)
        self._current_option = option
        _update_cached_vacuum(self._conga_data, self._sn, "set_cached_water_level", level)
        async_dispatcher_send(self.hass, SIGNAL_WATER_LEVEL, self._sn, level)
        self.async_write_ha_state()

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        """Refresh water level from the last known vacuum status."""
        try:
            self._conga_client.update_shadows(self._sn)
            state_all = self._conga_client.get_status()
            level = _normalize_option_level(state_all.get("waterlevel"), len(WATER_LEVELS) - 1)
            self._set_shared_level(level)
            self._current_option = WATER_LEVELS[level]
            self._send_shared_state(level)
        except Exception as exc:
            _LOGGER.error("Unable to fetch Conga 4690 water level: %s", exc)


def _normalize_option_level(value, max_level: int) -> int:
    try:
        level = int(float(value))
    except (TypeError, ValueError):
        return 0
    if level == 10:
        return 0
    return max(0, min(level, max_level))


def _update_cached_vacuum(conga_data: dict, sn: str, method_name: str, level: int) -> None:
    for entity in conga_data.get("entities", []):
        if getattr(entity, "_sn", None) != sn:
            continue
        method = getattr(entity, method_name, None)
        if method is not None:
            method(level)
