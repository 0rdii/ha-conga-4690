import logging
from datetime import timedelta
from homeassistant.core import HomeAssistant
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import DeviceInfo, Entity

from .utils import build_device_info
from .const import (
    BRAND,
    CONF_DEVICES,
    DOMAIN,
    MODEL,
)

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=5)
RUNNING_MODES = {"sweep"}
ACTIVE_MODES = {"sweep", "pause", "backcharge"}

ROOM_LABELS = {
    "Cocina": {"es": "Cocina", "en": "Kitchen"},
    "Comedor": {"es": "Comedor", "en": "Dining room"},
    "Pasillo": {"es": "Pasillo", "en": "Hallway"},
    "Salón": {"es": "Salón", "en": "Living room"},
}

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Cecotec Conga sensor from a config entry."""
    entities = []

    devices = hass.data[DOMAIN][config_entry.entry_id]["devices"]
    plans = hass.data[DOMAIN][config_entry.entry_id]["plans"]
    lang = _language(hass)

    conga_data = hass.data[DOMAIN][config_entry.entry_id]
    for device in devices:
        for plan in plans:
            entities.append(
                CongaVacuumPlanButton(
                    hass, conga_data, plan, device["sn"], device["note_name"], lang
                )
            )
        entities.append(CongaVacuumStopButton(hass, conga_data, device["sn"], device["note_name"], lang))
        entities.append(CongaVacuumHomeButton(hass, conga_data, device["sn"], device["note_name"], lang))

    async_add_entities(entities, update_before_add=True)


class CongaEntity(Entity):
    def __init__(
        self,
        conga_data: dict,
        device_name: str,
        sn: str,
    ):
        self._enabled = False
        self._device_name = device_name
        self._conga_data = conga_data
        self._conga_client = conga_data["controller"]
        self._sn = sn

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self._device_name, self._sn)

    @property
    def model(self):
        return MODEL

    @property
    def brand(self):
        return BRAND

    async def async_added_to_hass(self) -> None:
        self._enabled = True

    async def async_will_remove_from_hass(self) -> None:
        self._enabled = False


class CongaVacuumPlanButton(ButtonEntity, CongaEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        conga_data: dict,
        plan_name: str,
        sn: str,
        device_name: str,
        lang: str,
    ):
        self._hass = hass
        self._conga_data = conga_data
        self._conga_client = conga_data["controller"]
        self._plan_name = plan_name
        self._device_name = device_name
        self._name = _format_name(
            self._device_name,
            "Iniciar limpieza" if lang == "es" else "Start cleaning",
        )
        self._sn = sn
        self._unique_id = f"{self._device_name}_{self._plan_name}"
        self._available = True
        CongaEntity.__init__(self, conga_data, device_name, sn)
        ButtonEntity.__init__(self)

    @property
    def should_poll(self) -> bool:
        return True

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def available(self) -> bool:
        return self._available

    def update(self) -> None:
        try:
            self._conga_client.update_shadows(self._sn)
            status = self._conga_client.get_status()
            self._available = status.get("mode") not in RUNNING_MODES
        except Exception as exc:
            _LOGGER.error("Unable to update Conga 4690 plan button state: %s", exc)
            self._available = False

    async def async_press(self) -> None:
        status = await self._hass.async_add_executor_job(self._conga_client.update_shadows, self._sn)
        if status.get("mode") in RUNNING_MODES:
            _LOGGER.info("Ignoring start because %s is already cleaning", self._device_name)
            return

        _LOGGER.info("Starting %s", self._device_name)
        await self._hass.async_add_executor_job(self._conga_client.start, self._sn)


class CongaVacuumRoomButton(ButtonEntity, CongaEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        conga_data: dict,
        room_name: str,
        sn: str,
        device_name: str,
        lang: str,
    ):
        self._hass = hass
        self._conga_data = conga_data
        self._conga_client = conga_data["controller"]
        self._room_name = room_name
        self._device_name = device_name
        room_label = ROOM_LABELS.get(room_name, {}).get(lang, room_name)
        self._name = _format_name(
            self._device_name,
            f"Limpiar {room_label}" if lang == "es" else f"Clean {room_label}",
        )
        self._sn = sn
        self._unique_id = f"{self._device_name}_room_{room_name}"
        self._available = True
        CongaEntity.__init__(self, conga_data, device_name, sn)
        ButtonEntity.__init__(self)

    @property
    def should_poll(self) -> bool:
        return True

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def available(self) -> bool:
        return self._available

    def update(self) -> None:
        self._available = _is_robot_available_to_start(self._conga_client, self._sn)

    async def async_press(self) -> None:
        status = await self._hass.async_add_executor_job(self._conga_client.update_shadows, self._sn)
        if status.get("mode") in RUNNING_MODES:
            _LOGGER.info("Ignoring room clean because %s is already cleaning", self._device_name)
            return
        await self._hass.async_add_executor_job(
            self._conga_client.start_room, self._sn, self._room_name
        )


class CongaVacuumStopButton(ButtonEntity, CongaEntity):
    def __init__(self, hass: HomeAssistant, conga_data: dict, sn: str, device_name: str, lang: str):
        self._hass = hass
        self._conga_data = conga_data
        self._conga_client = conga_data["controller"]
        self._device_name = device_name
        self._name = _format_name(device_name, "Detener limpieza" if lang == "es" else "Stop cleaning")
        self._sn = sn
        self._unique_id = f"{self._device_name}_stop_cleaning"
        self._available = False
        CongaEntity.__init__(self, conga_data, device_name, sn)
        ButtonEntity.__init__(self)

    @property
    def should_poll(self) -> bool:
        return True

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def available(self) -> bool:
        return self._available

    def update(self) -> None:
        self._available = _current_mode(self._conga_client, self._sn) in ACTIVE_MODES

    async def async_press(self) -> None:
        await self._hass.async_add_executor_job(self._conga_client.stop, self._sn)


class CongaVacuumHomeButton(ButtonEntity, CongaEntity):
    def __init__(self, hass: HomeAssistant, conga_data: dict, sn: str, device_name: str, lang: str):
        self._hass = hass
        self._conga_data = conga_data
        self._conga_client = conga_data["controller"]
        self._device_name = device_name
        self._name = _format_name(device_name, "Volver a base" if lang == "es" else "Return to dock")
        self._sn = sn
        self._unique_id = f"{self._device_name}_return_to_dock"
        self._available = True
        CongaEntity.__init__(self, conga_data, device_name, sn)
        ButtonEntity.__init__(self)

    @property
    def should_poll(self) -> bool:
        return True

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def available(self) -> bool:
        return self._available

    def update(self) -> None:
        self._available = _current_mode(self._conga_client, self._sn) not in {"charge", "fullcharge"}

    async def async_press(self) -> None:
        await self._hass.async_add_executor_job(self._conga_client.home, self._sn)


def _current_mode(conga_client, sn: str) -> str:
    try:
        return conga_client.update_shadows(sn).get("mode")
    except Exception as exc:
        _LOGGER.error("Unable to update Conga 4690 button state: %s", exc)
        return "unknown"


def _is_robot_available_to_start(conga_client, sn: str) -> bool:
    return _current_mode(conga_client, sn) not in RUNNING_MODES


def _language(hass: HomeAssistant) -> str:
    return "es" if getattr(hass.config, "language", "en").lower().startswith("es") else "en"


def _format_name(device_name: str, label: str) -> str:
    return f"{device_name} {label}"
