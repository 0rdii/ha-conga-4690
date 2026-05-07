from datetime import timedelta
import logging
from homeassistant.util import Throttle
from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import (
    PERCENTAGE,
    UnitOfArea,
    UnitOfTime,
)
from .button import CongaEntity
from .const import DOMAIN

SCAN_INTERVAL = timedelta(seconds=5)
MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=5)

_LOGGER = logging.getLogger(__name__)

sensors = [
    {
        "id": "elec",
        "name": {"es": "Bateria", "en": "Battery"},
        "icon": "mdi:battery",
        "unit": PERCENTAGE,
        "device_class": SensorDeviceClass.BATTERY,
    },
    {
        "id": "statusLabel",
        "name": {"es": "Estado", "en": "Status"},
        "icon": "mdi:robot-vacuum",
        "unit": None,
    },
    {
        "id": "cleanArea",
        "name": {"es": "Área limpiada", "en": "Clean area"},
        "icon": "mdi:vector-square",
        "unit": UnitOfArea.SQUARE_METERS,
    },
    {
        "id": "allArea",
        "name": {"es": "Área total limpiada", "en": "Total cleaned area"},
        "icon": "mdi:vector-square",
        "unit": UnitOfArea.SQUARE_METERS,
    },
    {
        "id": "cleanTime",
        "name": {"es": "Tiempo de limpieza", "en": "Clean time"},
        "icon": "mdi:clock-outline",
        "unit": UnitOfTime.MINUTES,
    },
    {
        "id": "allTime",
        "name": {"es": "Tiempo total de limpieza", "en": "Total clean time"},
        "icon": "mdi:clock-outline",
        "unit": UnitOfTime.MINUTES,
    },
    {
        "id": "currentMapName",
        "name": {"es": "Mapa actual", "en": "Current map"},
        "icon": "mdi:map",
        "unit": None,
    },
    {
        "id": "mapHeadId",
        "name": {"es": "ID mapa", "en": "Map ID"},
        "icon": "mdi:identifier",
        "unit": None,
        "enabled": False,
    },
    {
        "id": "mapCount",
        "name": {"es": "Mapas guardados", "en": "Saved maps"},
        "icon": "mdi:map-marker-multiple",
        "unit": None,
        "enabled": False,
    },
    {
        "id": "houseName",
        "name": {"es": "Casa", "en": "House"},
        "icon": "mdi:home-map-marker",
        "unit": None,
    },
    {
        "id": "cleaningRoomId",
        "name": {"es": "Habitación actual", "en": "Current room"},
        "icon": "mdi:floor-plan",
        "unit": None,
    },
]

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Cecotec Conga sensor from a config entry."""
    entities = []

    devices = hass.data[DOMAIN][config_entry.entry_id]["devices"]
    lang = _language(hass)

    for device in devices:
        conga_data = hass.data[DOMAIN][config_entry.entry_id]
        for sensor in sensors:
            sensor_entity = CongaVacuumPlanButton(
                hass, conga_data, device["sn"], device["note_name"], sensor, lang
            )
            entities.append(sensor_entity)

    async_add_entities(entities, update_before_add=True)



class CongaVacuumPlanButton(SensorEntity, CongaEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        conga_data: dict,
        sn: str,
        device_name: str,
        sensor: dict,
        lang: str,
    ):
        self._hass = hass
        self._conga_data = conga_data
        self._conga_client = conga_data["controller"]
        self._device_name = device_name
        self._unit_of_measurement = sensor['unit']
        self._name = f"{self._device_name} {sensor['name'][lang]}"
        self._icon = sensor['icon']
        self._device_class = sensor.get("device_class")
        self._sn = sn
        self._state = None
        self._attribute_id = sensor['id']
        self._unique_id = f"{self._device_name}_{sensor['id']}"
        self._lang = lang
        self._attr_entity_registry_enabled_default = sensor.get("enabled", True)
        CongaEntity.__init__(self, conga_data, device_name, sn)
        SensorEntity.__init__(self)

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement
    
    @property
    def native_value(self):
        return self._state

    @property
    def icon(self) -> str | None:
        """Icon of the entity."""
        return self._icon

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Return the device class."""
        return self._device_class

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        """Get the next bus information."""
        try:
            self._conga_client.update_shadows(self._sn)
            state_all = self._conga_client.get_status()

            if self._attribute_id == "statusLabel":
                self._state = _status_label(state_all.get("mode"), self._lang)
            elif self._attribute_id == "cleaningRoomId":
                self._state = _room_label(state_all.get("cleaningRoomId"), self._lang)
            else:
                self._state = state_all.get(self._attribute_id)

        except Exception as exc:
            _LOGGER.error("Unable to fetch sensor data from Conga 4690 API: %s", exc)


def _language(hass: HomeAssistant) -> str:
    return "es" if getattr(hass.config, "language", "en").lower().startswith("es") else "en"


def _status_label(mode: str | None, lang: str) -> str:
    labels = {
        "sweep": {"es": "Limpiando", "en": "Cleaning"},
        "charge": {"es": "Cargando", "en": "Charging"},
        "fullcharge": {"es": "Cargado", "en": "Charged"},
        "backcharge": {"es": "Volviendo a base", "en": "Returning to dock"},
        "pause": {"es": "Pausado", "en": "Paused"},
        "idle": {"es": "Inactivo", "en": "Idle"},
        "error": {"es": "Error", "en": "Error"},
        "unknown": {"es": "Desconocido", "en": "Unknown"},
    }
    return labels.get(mode or "unknown", labels["unknown"])[lang]


def _room_label(room_id, lang: str) -> str:
    rooms = {
        10: {"es": "Cocina", "en": "Kitchen"},
        11: {"es": "Comedor", "en": "Dining room"},
        12: {"es": "Pasillo", "en": "Hallway"},
        13: {"es": "Salón", "en": "Living room"},
    }
    try:
        room_id = int(room_id)
    except (TypeError, ValueError):
        room_id = 0
    if room_id == 0:
        return "Ninguna" if lang == "es" else "None"
    return rooms.get(room_id, {"es": f"Habitación {room_id}", "en": f"Room {room_id}"})[lang]
