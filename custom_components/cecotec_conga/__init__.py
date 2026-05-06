import logging

from .conga import Conga
from .const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["vacuum", "select", "button", "sensor", "binary_sensor"]


async def async_setup_entry(hass, entry):
    """Set up Cecotec Conga sensors based on a config entry."""
    _LOGGER.info("Setting up Cecotec Conga integration")
    hass.data.setdefault(DOMAIN, {})

    conga_client = Conga(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    devices = entry.data["devices"]
    if not devices:
        devices = await hass.async_add_executor_job(conga_client.list_vacuums)
    if devices:
        await hass.async_add_executor_job(conga_client.update_shadows, devices[0]["sn"])
    plans = await hass.async_add_executor_job(conga_client.list_plans)

    hass.data[DOMAIN][entry.entry_id] = {
        "controller": conga_client,
        "devices": devices,
        "plans": plans,
        "lastTimeSync": 0,
        "lastFirmwareCheck": 0,
        "latestFirmwareVersion": False,
        "entities": [],
        "name": "test",
    }

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
    return unload_ok
