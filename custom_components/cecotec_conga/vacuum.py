from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumEntityFeature,
)
from homeassistant.const import (
    STATE_OFF,
)
from homeassistant.util import Throttle
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send

from .utils import build_device_info
from .button import CongaEntity

from .const import (
    BRAND,
    DOMAIN,
    MODEL,
    FAN_SPEED_0,
    FAN_SPEED_1,
    FAN_SPEED_2,
    FAN_SPEED_3,
    WATER_LEVEL_0,
    WATER_LEVEL_1,
    WATER_LEVEL_2,
    WATER_LEVEL_3,
)

SUPPORTED_FEATURES = (
    VacuumEntityFeature.TURN_ON
    | VacuumEntityFeature.TURN_OFF
    | VacuumEntityFeature.RETURN_HOME
    | VacuumEntityFeature.START
    | VacuumEntityFeature.PAUSE
    | VacuumEntityFeature.FAN_SPEED
    | VacuumEntityFeature.SEND_COMMAND
)

_LOGGER = logging.getLogger(__name__)

ICON = "mdi:robot-vacuum"
STATE_CLEANING = "cleaning"
STATE_DOCKED = "docked"
STATE_PAUSED = "paused"
STATE_RETURNING = "returning"
STATE_ERROR = "error"
STATE_IDLE = "idle"

ATTR_SN = "Serial Number"
ATTR_NAME = "Name"
ATTR_PLANS = "plans"
ATTR_WATER_LEVEL = "fregado"
ATTR_WATER_LEVELS = "water_levels"

FAN_SPEEDS = [FAN_SPEED_0, FAN_SPEED_1, FAN_SPEED_2, FAN_SPEED_3]

WATER_LEVELS = [WATER_LEVEL_0, WATER_LEVEL_1, WATER_LEVEL_2, WATER_LEVEL_3]
WATER_LEVELS_ES = ["Apagado", "Bajo", "Medio", "Alto"]

SCAN_INTERVAL = timedelta(seconds=5)
MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=5)
SIGNAL_FAN_SPEED = f"{DOMAIN}_fan_speed"
SIGNAL_WATER_LEVEL = f"{DOMAIN}_water_level"
SHARED_STATE = "shared_state"


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Cecotec Conga sensor from a config entry."""
    entities = []

    devices = hass.data[DOMAIN][config_entry.entry_id]["devices"]

    for device in devices:
        conga_data = hass.data[DOMAIN][config_entry.entry_id]
        entities.append(
            CongaVacuum(
                hass,
                conga_data,
                device["note_name"],
                device["sn"],
            )
        )

    hass.data[DOMAIN][config_entry.entry_id]["entities"] = entities

    async_add_entities(entities, update_before_add=True)


class CongaVacuum(StateVacuumEntity, CongaEntity):
    """Implementation of a Cecotec Conga Vacuum."""

    def __init__(self, hass, conga_data, name, sn):
        """Initialize the vacuum."""
        self._hass = hass
        self._conga_data = conga_data
        self._conga_client = conga_data["controller"]
        self._name = name
        self._sn = sn
        self._battery = 0
        self._state = "loading"
        self._state_all = {}
        self._plans = []
        self._water_levels = WATER_LEVELS
        self._water_level = WATER_LEVEL_1
        self._fan_speeds = FAN_SPEEDS
        self._fan_speed = FAN_SPEED_1
        self._supported_features = SUPPORTED_FEATURES
        CongaEntity.__init__(self, conga_data, name, sn)
        StateVacuumEntity.__init__(self)

    async def async_added_to_hass(self) -> None:
        """Register shared state listeners."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_FAN_SPEED, self._handle_fan_speed_signal)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_WATER_LEVEL, self._handle_water_level_signal)
        )

    def _handle_fan_speed_signal(self, sn: str, level: int) -> None:
        if sn != self._sn:
            return
        self._set_shared_level("fan_speed", level)
        self._fan_speed = _fan_speed_name(level)
        self.schedule_update_ha_state()

    def _handle_water_level_signal(self, sn: str, level: int) -> None:
        if sn != self._sn:
            return
        self._set_shared_level("water_level", level)
        self._water_level = _water_level_name(level)
        self.schedule_update_ha_state()

    def set_cached_fan_speed(self, level: int) -> None:
        """Update the cached fan speed without sending a robot command."""
        self._set_shared_level("fan_speed", level)
        self._fan_speed = _fan_speed_name(level)
        self.schedule_update_ha_state()

    def set_cached_water_level(self, level: int) -> None:
        """Update the cached water level without sending a robot command."""
        self._set_shared_level("water_level", level)
        self._water_level = _water_level_name(level)
        self.schedule_update_ha_state()

    def _set_shared_level(self, key: str, level: int) -> None:
        shared = self._conga_data.setdefault(SHARED_STATE, {}).setdefault(self._sn, {})
        shared[key] = level

    def _get_shared_level(self, key: str) -> int | None:
        shared = self._conga_data.setdefault(SHARED_STATE, {}).setdefault(self._sn, {})
        return shared.get(key)

    def _send_shared_state(self, signal: str, level: int) -> None:
        self._hass.loop.call_soon_threadsafe(
            async_dispatcher_send,
            self._hass,
            signal,
            self._sn,
            level,
        )

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def brand(self):
        return BRAND

    @property
    def model(self):
        return MODEL

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self._name, self._sn)

    @property
    def icon(self):
        """Return the icon for the frontend."""
        return ICON

    @property
    def unique_id(self):
        """Return a unique, HASS-friendly identifier for this entity."""
        return self._sn

    @property
    def state(self):
        """Return the vacuum status."""
        if self._state == "sweep":
            return STATE_CLEANING
        elif self._state == "backcharge" or self._state == "DustCenterWorking":
            return STATE_RETURNING
        elif self._state == "fullcharge" or self._state == "charge":
            return STATE_DOCKED
        elif self._state == "pause":
            return STATE_PAUSED
        elif self._state == "idle" or self._state == "unknown":
            return STATE_IDLE
        elif self._state == "shutdown":
            return STATE_OFF
        else:
            _LOGGER.warning(f"Unknown status: {self._state}")
            return STATE_ERROR

    @property
    def extra_state_attributes(self):
        """Return some attributes."""
        return {
            ATTR_SN: self._sn,
            ATTR_NAME: self._name,
            ATTR_PLANS: ",".join(self._plans),
            ATTR_WATER_LEVEL: self._water_level,
            ATTR_WATER_LEVELS: ",".join(WATER_LEVELS_ES),
        }

    @property
    def fan_speed(self):
        """Return the fan speed of the vacuum cleaner."""
        shared_level = self._get_shared_level("fan_speed")
        if shared_level is not None:
            self._fan_speed = _fan_speed_name(shared_level)
        return self._fan_speed

    @property
    def fan_speed_list(self):
        """Get the list of available fan speed steps of the vacuum cleaner."""
        return self._fan_speeds

    @property
    def supported_features(self):
        """Flag supported features."""
        return self._supported_features

    def start(self):
        """Start or resume the cleaning task."""
        self.turn_on()

    def turn_on(self, **kwargs):
        """Turn the vacuum on."""
        self._conga_client.start(self._sn, self._fan_speeds.index(self._fan_speed))
        self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        """Turn off the vacuum."""
        self.return_to_base()

    def return_to_base(self, **kwargs):
        """Ask vacuum to go home."""
        self._conga_client.home(self._sn)
        self.schedule_update_ha_state()

    def pause(self):
        """Pause the vacuum."""
        self._conga_client.pause(self._sn)
        self.schedule_update_ha_state()

    def stop(self, **kwargs):
        """Stop the vacuum."""
        self._conga_client.stop(self._sn)
        self.schedule_update_ha_state()

    def set_fan_speed(self, fan_speed, **kwargs):
        """Set fan speed."""

        _LOGGER.info(f"Setting fan speed to {fan_speed}")

        self._conga_client.set_fan_speed(self._sn, self._fan_speeds.index(fan_speed))
        self._set_shared_level("fan_speed", self._fan_speeds.index(fan_speed))
        self._fan_speed = fan_speed
        self._send_shared_state(SIGNAL_FAN_SPEED, self._fan_speeds.index(fan_speed))
        self.schedule_update_ha_state()

    def send_command(self, command, params=None, **kwargs):
        """Send raw command."""
        _LOGGER.info(f"Sending command {command} with params {params}")

        if command == "start_plan":
            plan = params["plan"]
            if plan in self._plans:
                self._conga_client.start_plan(self._sn, plan)
                self.schedule_update_ha_state()
            else:
                _LOGGER.error(f"Plan {plan} not found. Allowed plans: {self._plans}")
        elif command == "set_water_level":
            water_level = params["water_level"]
            if water_level in self._water_levels:
                self._conga_client.set_water_level(
                    self._sn, self._water_levels.index(water_level)
                )
                self._set_shared_level("water_level", self._water_levels.index(water_level))
                self._water_level = _water_level_name(self._water_levels.index(water_level))
                self._send_shared_state(SIGNAL_WATER_LEVEL, self._water_levels.index(water_level))
                self.schedule_update_ha_state()
            else:
                _LOGGER.error(
                    f"Invalid water level: {water_level}. Allowed water levels: {self._water_levels}"
                )
        else:
            _LOGGER.error(f"Unknown command {command}")

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        """Get the next bus information."""
        try:
            self._conga_client.update_shadows(self._sn)
            self._state_all = self._conga_client.get_status()

            self._battery = self._state_all["elec"]
            self._state = self._state_all["mode"]
            self._plans = self._conga_client.list_plans()
            fan_level = _normalize_option_level(self._state_all.get("cleanPerference"), len(FAN_SPEEDS) - 1)
            water_level = _normalize_option_level(self._state_all.get("waterlevel"), len(WATER_LEVELS_ES) - 1)
            self._set_shared_level("fan_speed", fan_level)
            self._set_shared_level("water_level", water_level)
            self._fan_speed = _fan_speed_name(fan_level)
            self._water_level = _water_level_name(water_level)
            self._send_shared_state(
                SIGNAL_FAN_SPEED,
                fan_level,
            )
            self._send_shared_state(
                SIGNAL_WATER_LEVEL,
                water_level,
            )
        except Exception as exc:
            _LOGGER.error("Unable to fetch data from Conga 4690 API: %s", exc)


def _normalize_option_level(value, max_level: int) -> int:
    try:
        level = int(float(value))
    except (TypeError, ValueError):
        return 0
    if level == 10:
        return 0
    return max(0, min(level, max_level))


def _fan_speed_name(value) -> str:
    return FAN_SPEEDS[_normalize_option_level(value, len(FAN_SPEEDS) - 1)]


def _water_level_name(value) -> str:
    return WATER_LEVELS_ES[_normalize_option_level(value, len(WATER_LEVELS_ES) - 1)]
