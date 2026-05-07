import logging
import json
from typing import Any

from homeassistant.helpers.entity import DeviceInfo

from .const import (
    BRAND,
    DOMAIN,
    MODEL,
)

_LOGGER = logging.getLogger(__name__)


def build_device_info(name, sn, firmware_version: str | None = None) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, sn)},
        name=name,
        manufacturer=BRAND,
        model=MODEL,
        sw_version=firmware_version or "Not provided",
    )


def firmware_version_from_devices(devices: list[dict[str, Any]], sn: str) -> str | None:
    """Return the robot firmware version from the bound device metadata."""
    for device in devices:
        if str(device.get("sn")) != str(sn):
            continue
        return _firmware_version_from_raw(device.get("raw") or {})
    return None


def _firmware_version_from_raw(raw: dict[str, Any]) -> str | None:
    versions = raw.get("versions")
    if isinstance(versions, str):
        try:
            versions = json.loads(versions)
        except json.JSONDecodeError:
            return None
    if not isinstance(versions, list):
        return None

    preferred = None
    for version in versions:
        if not isinstance(version, dict):
            continue
        if version.get("packageType") == "target":
            preferred = version
            break
        if preferred is None:
            preferred = version

    if preferred is None:
        return None
    return str(preferred.get("versionName") or preferred.get("version") or "") or None
