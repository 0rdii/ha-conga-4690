from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.const import UnitOfArea
from homeassistant.core import HomeAssistant

from .button import CongaEntity
from .const import DOMAIN
from .dustbin import DEFAULT_DUSTBIN_LIMIT_M2


async def async_setup_entry(hass, config_entry, async_add_entities):
    devices = hass.data[DOMAIN][config_entry.entry_id]["devices"]
    conga_data = hass.data[DOMAIN][config_entry.entry_id]
    lang = _language(hass)

    async_add_entities(
        [
            CongaDustBinLimitNumber(
                hass,
                conga_data,
                device["sn"],
                device["note_name"],
                lang,
            )
            for device in devices
        ],
        update_before_add=True,
    )


class CongaDustBinLimitNumber(NumberEntity, CongaEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        conga_data: dict,
        sn: str,
        device_name: str,
        lang: str,
    ) -> None:
        self._hass = hass
        self._dustbin_meter = conga_data["dustbin_meter"]
        self._device_name = device_name
        self._sn = sn
        self._name = (
            f"{device_name} Umbral deposito"
            if lang == "es"
            else f"{device_name} Dust bin threshold"
        )
        self._unique_id = f"{device_name}_dust_bin_threshold"
        CongaEntity.__init__(self, conga_data, device_name, sn)
        NumberEntity.__init__(self)

    @property
    def name(self) -> str:
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def native_value(self) -> float:
        return self._dustbin_meter.limit_m2(self._sn)

    @property
    def native_min_value(self) -> float:
        return 1.0

    @property
    def native_max_value(self) -> float:
        return 500.0

    @property
    def native_step(self) -> float:
        return 1.0

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfArea.SQUARE_METERS

    @property
    def icon(self) -> str:
        return "mdi:vector-square"

    async def async_set_native_value(self, value: float) -> None:
        await self._dustbin_meter.async_set_limit_m2(self._sn, value)
        self.async_write_ha_state()


def _language(hass: HomeAssistant) -> str:
    return "es" if getattr(hass.config, "language", "en").lower().startswith("es") else "en"
