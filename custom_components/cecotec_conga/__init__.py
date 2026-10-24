import logging

import voluptuous as vol

from .conga import Conga, CongaError
from .const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    DOMAIN,
)
from .dustbin import DustBinMeter

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["vacuum", "select", "button", "sensor", "binary_sensor", "number"]

SERVICE_CREATE_ROOM_PLAN = "create_room_plan"

ATTR_ENABLED = "enabled"
ATTR_FAN_SPEED = "fan_speed"
ATTR_NAME = "name"
ATTR_ROOM = "room"
ATTR_SN = "sn"
ATTR_TIME = "time"
ATTR_TWICE_CLEAN = "twice_clean"
ATTR_WATER_LEVEL = "water_level"
ATTR_WEEKDAYS = "weekdays"

CREATE_ROOM_PLAN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ROOM): cv.string,
        vol.Required(ATTR_TIME): cv.string,
        vol.Optional(ATTR_NAME, default=""): cv.string,
        vol.Optional(ATTR_WEEKDAYS, default=127): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=127)
        ),
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
        vol.Optional(ATTR_FAN_SPEED, default=3): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=3)
        ),
        vol.Optional(ATTR_WATER_LEVEL, default=10): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=10)
        ),
        vol.Optional(ATTR_TWICE_CLEAN, default=False): cv.boolean,
        vol.Optional(ATTR_SN): cv.string,
    }
)


async def async_setup_entry(hass, entry):
    """Set up Cecotec Conga sensors based on a config entry."""
    _LOGGER.info("Setting up Cecotec Conga integration")
    hass.data.setdefault(DOMAIN, {})

    conga_client = Conga(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    devices = entry.data["devices"]
    try:
        if not devices:
            devices = await hass.async_add_executor_job(conga_client.list_vacuums)
        if devices:
            await hass.async_add_executor_job(conga_client.update_shadows, devices[0]["sn"])
        plans = await hass.async_add_executor_job(conga_client.list_plans)
    except CongaError as exc:
        # The cloud is down or slow: let Home Assistant retry the setup later
        # instead of leaving the integration failed until a manual reload.
        await hass.async_add_executor_job(conga_client.close)
        raise ConfigEntryNotReady(f"Cecotec cloud not reachable: {exc}") from exc

    await _async_migrate_unique_ids(hass, entry, devices)
    dustbin_meter = await DustBinMeter.async_create(hass, entry.entry_id)

    hass.data[DOMAIN][entry.entry_id] = {
        "controller": conga_client,
        "devices": devices,
        "dustbin_meter": dustbin_meter,
        "plans": plans,
        "lastTimeSync": 0,
        "lastFirmwareCheck": 0,
        "latestFirmwareVersion": False,
        "entities": [],
        "name": "test",
    }

    async def async_create_room_plan(call):
        conga_data = hass.data[DOMAIN][entry.entry_id]
        controller = conga_data["controller"]
        entry_devices = conga_data["devices"]
        sn = call.data.get(ATTR_SN) or entry_devices[0]["sn"]
        day_time = _parse_day_time(call.data[ATTR_TIME])

        order_id = await hass.async_add_executor_job(
            controller.create_room_plan,
            sn,
            call.data[ATTR_ROOM],
            call.data[ATTR_NAME],
            day_time,
            call.data[ATTR_WEEKDAYS],
            call.data[ATTR_ENABLED],
            call.data[ATTR_FAN_SPEED],
            call.data[ATTR_WATER_LEVEL],
            call.data[ATTR_TWICE_CLEAN],
        )
        _LOGGER.info("Created Cecotec Conga room plan %s", order_id)

    if not hass.services.has_service(DOMAIN, SERVICE_CREATE_ROOM_PLAN):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CREATE_ROOM_PLAN,
            async_create_room_plan,
            schema=CREATE_ROOM_PLAN_SCHEMA,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry):
    """Unload a Cecotec Conga config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        conga_data = hass.data[DOMAIN].pop(entry.entry_id, {})
        controller = conga_data.get("controller")
        if controller is not None:
            await hass.async_add_executor_job(controller.close)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_CREATE_ROOM_PLAN)
    return unload_ok


async def _async_migrate_unique_ids(hass, entry, devices):
    """Move unique IDs from "<robot name>_<key>" to "<serial>_<key>".

    Older versions keyed most entities on the robot name, so renaming the robot
    in the Cecotec app created new entities and broke automations. Updating the
    registry in place keeps the existing entity IDs and their history.
    """
    prefixes = [
        (f"{device['note_name']}_", f"{device['sn']}_")
        for device in devices
        if device.get("note_name") and device.get("sn")
    ]

    @callback
    def _migrate(entity_entry):
        for old_prefix, new_prefix in prefixes:
            unique_id = entity_entry.unique_id
            if unique_id.startswith(old_prefix) and not unique_id.startswith(new_prefix):
                return {"new_unique_id": new_prefix + unique_id[len(old_prefix):]}
        return None

    # Must finish before the platforms add entities with the new IDs.
    await er.async_migrate_entries(hass, entry.entry_id, _migrate)


def _parse_day_time(value):
    parts = str(value).split(":")
    if len(parts) < 2:
        raise vol.Invalid("time must use HH:MM format")

    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise vol.Invalid("time must use HH:MM format") from exc

    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise vol.Invalid("time must use HH:MM format")
    return hour * 60 + minute
