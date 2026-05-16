from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ACTIVE_MODES = {"sweep", "pause", "backcharge"}
DEFAULT_DUSTBIN_LIMIT_M2 = 80.0
STORAGE_VERSION = 1


class DustBinMeter:
    """Virtual dust bin counter based on cleaned square meters."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: Store,
        data: dict[str, Any] | None,
    ) -> None:
        self._hass = hass
        self._store = store
        self._data = data or {}
        self._data.setdefault("robots", {})

    @classmethod
    async def async_create(cls, hass: HomeAssistant, entry_id: str) -> "DustBinMeter":
        store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry_id}_dustbin")
        data = await store.async_load()
        return cls(hass, store, data)

    def update(self, sn: str, status: dict[str, Any]) -> None:
        robot = self._robot(sn)
        mode = status.get("mode")
        area = _to_float(status.get("cleanArea"))
        was_active = bool(robot.get("session_active"))
        is_active = mode in ACTIVE_MODES
        changed = False

        if is_active:
            if not was_active:
                robot["session_active"] = True
                robot["session_area"] = 0.0
                changed = True
            if area < _to_float(robot.get("last_seen_area")):
                robot["session_area"] = max(
                    _to_float(robot.get("session_area")),
                    _to_float(robot.get("last_seen_area")),
                )
                changed = True
            if area > _to_float(robot.get("session_area")):
                robot["session_area"] = area
                changed = True
        elif was_active:
            session_area = _to_float(robot.get("session_area"))
            if session_area > 0:
                robot["area_since_empty"] = round(
                    _to_float(robot.get("area_since_empty")) + session_area,
                    2,
                )
            robot["session_active"] = False
            robot["session_area"] = 0.0
            changed = True

        if robot.get("last_mode") != mode:
            robot["last_mode"] = mode
            changed = True
        if _to_float(robot.get("last_seen_area")) != area:
            robot["last_seen_area"] = area
            changed = True

        if changed:
            self._schedule_save()

    def area_since_empty(self, sn: str) -> float:
        robot = self._robot(sn)
        return round(
            _to_float(robot.get("area_since_empty"))
            + _to_float(robot.get("session_area")),
            2,
        )

    def is_full_recommended(self, sn: str) -> bool:
        return self.area_since_empty(sn) >= self.limit_m2(sn)

    def limit_m2(self, sn: str) -> float:
        return _to_float(self._robot(sn).get("limit_m2")) or DEFAULT_DUSTBIN_LIMIT_M2

    async def async_set_limit_m2(self, sn: str, value: float) -> None:
        robot = self._robot(sn)
        robot["limit_m2"] = round(max(1.0, min(float(value), 500.0)), 1)
        await self._store.async_save(self._data)

    async def async_reset(self, sn: str) -> None:
        robot = self._robot(sn)
        robot["area_since_empty"] = 0.0
        robot["session_area"] = 0.0
        robot["session_active"] = False
        await self._store.async_save(self._data)

    def _robot(self, sn: str) -> dict[str, Any]:
        robots = self._data.setdefault("robots", {})
        robot = robots.setdefault(str(sn), {})
        robot.setdefault("area_since_empty", 0.0)
        robot.setdefault("session_area", 0.0)
        robot.setdefault("session_active", False)
        robot.setdefault("last_seen_area", 0.0)
        robot.setdefault("last_mode", None)
        robot.setdefault("limit_m2", DEFAULT_DUSTBIN_LIMIT_M2)
        return robot

    def _schedule_save(self) -> None:
        try:
            self._hass.loop.call_soon_threadsafe(
                lambda: self._hass.async_create_task(self._store.async_save(self._data))
            )
        except RuntimeError:
            _LOGGER.debug("Unable to schedule dust bin meter save", exc_info=True)


def _to_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
