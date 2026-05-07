from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import ssl
import threading
import time
from typing import Any

import requests
import websocket

_LOGGER = logging.getLogger(__name__)

UPGRADE_URL = "https://cecotec-ota.3irobotix.net:8001"
UPGRADE_SERVICE = "service-publish/open/upgrade/try_upgrade"
DEFAULT_WS_URL = "wss://tcp-cecotec.3irobotix.net:9090"
DEFAULT_HTTP_URL = "https://web-cecotec.3irobotix.net:8002"

FACTORY_ID = 1003
PROJECT_TYPE = "android-es.cecotec.s4690v1"
VERSION_NAME = "2.1.3"
VERSION_CODE = 20103
PACKAGE_TYPE = "android"
ROBOT_TYPE = "sweeper"

SERVICE_LOGIN = "sweeper-app-user/auth/login"
SERVICE_LOGIN_TOKEN = "sweeper-app-user/auth/login_token"
SERVICE_USER_GET_BIND_ROBOT = "sweeper-robot-center/app/get_user_bind"
SERVICE_TRANSMIT = "sweeper-transmit/transmit/to_bind"
SERVICE_HEARTBEAT = "heart-beat"

METHOD_GET_STATUS = "get_status"
METHOD_SET_MODE = "set_mode"
METHOD_SET_PREFERENCE = "set_preference"
METHOD_SELECT_MAP_PLAN = "selectMapPlan"
METHOD_SET_ROOM_CLEAN = "setRoomClean"
METHOD_SET_ROOM_CLEAN_PLAN = "setRoomCleanPlan"

MODE_AUTO = 0
MODE_EDGE = 1
MODE_MOPPING = 2
MODE_BACK_CHARGE = 3
MODE_SPIRAL = 5
MODE_AREA = 6
MODE_EXPLORE = 7
MODE_RANDOM = 8
MODE_TWICE = 10
MODE_POINT = 101

VALUE_STOP = 0
VALUE_START = 1
VALUE_PAUSE = 2
VALUE_IDLE = 4

PREFERENCE_POWER = 1
PREFERENCE_WATER = 2

FAULT_ROBOT_GLOBAL_GO_HOME = 2102
FAULT_ROBOT_CHARGING = 2103
FAULT_ROBOT_USER_GO_HOME = 2104
FAULT_ROBOT_CHARGE_FINISH = 2105

DEFAULT_TIMEOUT = 20


class CongaError(RuntimeError):
    """Base error raised by the 3irobotix client."""


class CongaAuthError(CongaError):
    """Raised when the cloud rejects the credentials."""


@dataclass
class Device:
    robot_id: int
    sn: str
    name: str
    raw: dict[str, Any]

    def as_ha_device(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "sn": self.sn or str(self.robot_id),
            "note_name": self.name or self.sn or f"Conga {self.robot_id}",
            "raw": self.raw,
        }


class Conga:
    """Synchronous Cecotec Conga 4690 cloud client.

    This follows the protocol used by the official es.cecotec.s4690 Android app:
    first ask the OTA service for active target URLs, then talk JSON over a
    3irobotix WebSocket.
    """

    def __init__(self, username: str, password: str):
        self._username = username
        self._password = password
        self._token: str | None = None
        self._user_id: int | None = None
        self._ws_url = DEFAULT_WS_URL
        self._http_url = DEFAULT_HTTP_URL
        self._ws: websocket.WebSocket | None = None
        self._logged_in = False
        self._lock = threading.RLock()
        self._devices: list[Device] = []
        self._shadow: dict[str, Any] = {}
        self._last_shadow_update = 0.0
        self._plans: dict[str, dict[str, Any]] = {}
        self._rooms: dict[str, int] = {
            "Cocina": 10,
            "Comedor": 11,
            "Pasillo": 12,
            "Salón": 13,
        }
        self._last_heartbeat = 0.0

    def list_vacuums(self) -> list[dict[str, Any]]:
        self._ensure_logged_in()
        response = self._request("GET", SERVICE_USER_GET_BIND_ROBOT, "")
        result = response.get("result") or []
        if isinstance(result, dict) and "result" in result:
            result = result["result"]

        devices = []
        for item in result or []:
            robot_id = _to_int(item.get("robotId") or item.get("id") or item.get("did"))
            sn = str(item.get("sn") or item.get("mac") or robot_id)
            name = str(item.get("nickname") or item.get("note_name") or sn)
            device = Device(robot_id=robot_id, sn=sn, name=name, raw=item)
            devices.append(device)

        self._devices = devices
        return [device.as_ha_device() for device in devices]

    def list_plans(self) -> list[str]:
        self._refresh_known_plans()
        return list(self._plans)

    def list_rooms(self) -> dict[str, int]:
        return dict(self._rooms)

    def update_shadows(self, sn: str) -> dict[str, Any]:
        if self._shadow and time.time() - self._last_shadow_update < 5:
            return self._shadow
        device = self._device_for_sn(sn)
        response = self._transmit(
            {
                "clientType": "ROBOT",
                "targets": [device.robot_id],
                "data": _device_ctrl_data(METHOD_GET_STATUS, -1, -1),
            }
        )
        status = self._extract_status(response)
        if not status:
            self.list_vacuums()
            device = self._device_for_sn(sn)
        self._shadow = self._normalize_status(status, device)
        self._last_shadow_update = time.time()
        self._refresh_known_plans()
        return self._shadow

    def get_status(self) -> dict[str, Any]:
        return self._shadow

    def start(self, sn: str, fan_speed: int = 1) -> None:
        self.set_fan_speed(sn, fan_speed)
        self._set_mode(sn, MODE_AUTO, VALUE_START)

    def pause(self, sn: str) -> None:
        self._set_mode(sn, MODE_AUTO, VALUE_PAUSE)

    def stop(self, sn: str) -> None:
        self._set_mode(sn, MODE_AUTO, VALUE_STOP)

    def home(self, sn: str) -> None:
        self._set_mode(sn, MODE_BACK_CHARGE, VALUE_START)

    def set_fan_speed(self, sn: str, level: int) -> None:
        self._set_preference(sn, PREFERENCE_POWER, level)

    def set_water_level(self, sn: str, level: int) -> None:
        self._set_preference(sn, PREFERENCE_WATER, level)

    def start_plan(self, sn: str, plan_name: str) -> None:
        self._refresh_known_plans()
        plan = self._plans.get(plan_name)
        if plan is None:
            raise CongaError(f"Unknown Conga plan: {plan_name}")

        device = self._device_for_sn(sn)
        self._transmit(
            {
                "clientType": "ROBOT",
                "data": {
                    "control": METHOD_SELECT_MAP_PLAN,
                    "mapid": _to_int(plan.get("mapid") or self._shadow.get("mapHeadId")),
                    "planid": _to_int(plan.get("planid"), default=1),
                    "type": _to_int(plan.get("type"), default=0),
                },
                "targets": [device.robot_id],
            }
        )
        self._set_mode(sn, MODE_AUTO, VALUE_START)

    def start_room(self, sn: str, room_name: str) -> None:
        room_id = self._rooms.get(room_name)
        if room_id is None:
            raise CongaError(f"Unknown Conga room: {room_name}")

        device = self._device_for_sn(sn)
        self._transmit(
            {
                "clientType": "ROBOT",
                "data": {
                    "control": METHOD_SET_ROOM_CLEAN_PLAN,
                    "order": {
                        "roomPer": [
                            {
                                "room_id": room_id,
                                "room_name": room_name,
                                "cleanmode": 0,
                                "windpower": 3,
                                "waterlevel": 10,
                                "twiceclean": 0,
                                "carpet": 0,
                                "room_material": 0,
                                "room_type": 0,
                                "sweep_mode": 0,
                            }
                        ],
                        "virwallList": [],
                        "arealist": [],
                    },
                },
                "targets": [device.robot_id],
            }
        )
        self._last_shadow_update = 0.0

    def close(self) -> None:
        with self._lock:
            if self._ws is not None:
                self._ws.close()
                self._ws = None
                self._logged_in = False

    def _set_mode(self, sn: str, mode_type: int, value: int) -> None:
        device = self._device_for_sn(sn)
        self._transmit(
            {
                "clientType": "ROBOT",
                "targets": [device.robot_id],
                "data": _device_ctrl_data(METHOD_SET_MODE, mode_type, value),
            }
        )
        self._last_shadow_update = 0.0

    def _set_preference(self, sn: str, preference: int, value: int) -> None:
        device = self._device_for_sn(sn)
        self._transmit(
            {
                "clientType": "ROBOT",
                "targets": [device.robot_id],
                "data": _device_ctrl_data(METHOD_SET_PREFERENCE, preference, value),
            }
        )
        if preference == PREFERENCE_POWER:
            self._shadow["cleanPerference"] = value
        elif preference == PREFERENCE_WATER:
            self._shadow["waterlevel"] = value
        self._last_shadow_update = 0.0

    def _transmit(self, content: dict[str, Any]) -> dict[str, Any]:
        self._ensure_logged_in()
        payload = json.dumps(content, separators=(",", ":"))
        try:
            return self._request("POST", SERVICE_TRANSMIT, payload)
        except CongaError as exc:
            message = str(exc).lower()
            if "login" not in message and "登录" not in message:
                raise
            _LOGGER.debug("3irobotix session expired, logging in again")
            self._logged_in = False
            self._ensure_logged_in()
            return self._request("POST", SERVICE_TRANSMIT, payload)

    def _request(
        self,
        method: str,
        service: str,
        content: str,
        token: str | None = None,
        retry_login: bool = True,
    ) -> dict[str, Any]:
        self._ensure_socket()
        self._send_heartbeat_if_needed()

        trace_id = str(int(time.time() * 1000))
        packet = {
            "traceId": trace_id,
            "method": method,
            "service": service,
            "content": content,
        }

        with self._lock:
            assert self._ws is not None
            _LOGGER.debug("3irobotix request %s", packet)
            self._ws.send(json.dumps(packet, separators=(",", ":")))
            deadline = time.time() + DEFAULT_TIMEOUT
            while time.time() < deadline:
                raw = self._ws.recv()
                if isinstance(raw, bytes):
                    _LOGGER.debug("Ignoring binary frame of %s bytes", len(raw))
                    continue
                _LOGGER.debug("3irobotix response %s", raw)
                response = json.loads(raw)
                if response.get("service") == SERVICE_HEARTBEAT:
                    continue
                if response.get("traceId") == trace_id:
                    code = _to_int(response.get("code"), default=-1)
                    if code not in (0, -1):
                        message = response.get("msg") or raw
                        if retry_login and service not in (SERVICE_LOGIN, SERVICE_LOGIN_TOKEN) and _is_login_required(message):
                            _LOGGER.debug("3irobotix session expired during %s, logging in again", service)
                            self._logged_in = False
                            self._ensure_logged_in()
                            return self._request(method, service, content, token, retry_login=False)
                        if service in (SERVICE_LOGIN, SERVICE_LOGIN_TOKEN):
                            raise CongaAuthError(message)
                        raise CongaError(message)
                    return response
                self._handle_push(response)

        raise TimeoutError(f"Timed out waiting for {service}")

    def _ensure_logged_in(self) -> None:
        self._ensure_socket()
        if self._logged_in:
            return
        if self._token and self._user_id:
            payload = _base_payload()
            payload.update(
                {
                    "token": self._token,
                    "userId": self._user_id,
                    "lang": "en",
                }
            )
            self._request("POST", SERVICE_LOGIN_TOKEN, json.dumps(payload, separators=(",", ":")))
            self._logged_in = True
            return

        payload = _base_payload()
        payload.update(
            {
                "username": self._username,
                "password": self._password,
                "lang": "en",
            }
        )
        response = self._request("POST", SERVICE_LOGIN, json.dumps(payload, separators=(",", ":")))
        result = response.get("result") or {}
        data = result.get("data") or {}
        self._token = data.get("AUTH") or data.get("auth") or result.get("token")
        self._user_id = _to_int(result.get("id") or result.get("userId"))
        if not self._token or not self._user_id:
            raise CongaAuthError(f"Login response did not include token/user id: {response}")
        self._logged_in = True

    def _ensure_socket(self) -> None:
        with self._lock:
            if self._ws is not None and self._ws.connected:
                return
            self._refresh_target_urls()
            self._ws = websocket.create_connection(
                self._ws_url,
                timeout=DEFAULT_TIMEOUT,
                sslopt={"cert_reqs": ssl.CERT_NONE},
            )
            self._logged_in = False

    def _refresh_target_urls(self) -> None:
        payload = _base_payload()
        payload["robotType"] = ROBOT_TYPE
        try:
            response = requests.post(
                f"{UPGRADE_URL}/{UPGRADE_SERVICE}",
                json=payload,
                timeout=DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            urls = ((data.get("result") or {}).get("targetUrls")) or []
            for url in urls:
                lower = url.lower()
                if lower.startswith(("ws://", "wss://")):
                    self._ws_url = url
                elif lower.startswith(("http://", "https://")):
                    self._http_url = url
            _LOGGER.debug("3irobotix target URLs: ws=%s http=%s", self._ws_url, self._http_url)
        except Exception as exc:
            _LOGGER.warning("Unable to refresh 3irobotix URLs, using defaults: %s", exc)

    def _send_heartbeat_if_needed(self) -> None:
        if time.time() - self._last_heartbeat < 10:
            return
        with self._lock:
            if self._ws is not None and self._ws.connected:
                self._ws.send(json.dumps({"traceId": "0", "method": "", "service": SERVICE_HEARTBEAT, "content": ""}))
                self._last_heartbeat = time.time()

    def _handle_push(self, response: dict[str, Any]) -> None:
        if response.get("tag") != "sweeper-transmit/to_bind":
            return
        content = _maybe_json(response.get("content"))
        status = self._extract_status({"result": content})
        if status:
            device = self._device_for_robot_id(_to_int(status.get("devid") or status.get("did")))
            if device is not None:
                self._shadow = self._normalize_status(status, device)

    def _device_for_sn(self, sn: str) -> Device:
        if not self._devices:
            self.list_vacuums()
        for device in self._devices:
            if device.sn == sn or str(device.robot_id) == str(sn):
                return device
        raise CongaError(f"Unknown Conga device: {sn}")

    def _device_for_robot_id(self, robot_id: int) -> Device | None:
        for device in self._devices:
            if device.robot_id == robot_id:
                return device
        return None

    def _extract_status(self, response: dict[str, Any]) -> dict[str, Any]:
        result = response.get("result")
        if isinstance(result, str):
            result = _maybe_json(result)
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, str):
                content = _maybe_json(content)
            if isinstance(content, dict):
                result = content
            data = result.get("data")
            if isinstance(data, dict):
                return data
            if "control" in result:
                return result

        content = response.get("content")
        if isinstance(content, str):
            content = _maybe_json(content)
        if isinstance(content, dict):
            data = content.get("data")
            if isinstance(data, dict):
                return data
            return content

        return {}

    def _normalize_status(self, status: dict[str, Any], device: Device) -> dict[str, Any]:
        raw_stats = _maybe_json(device.raw.get("stats"))
        if not status and isinstance(raw_stats, dict):
            status = raw_stats.get("data") or raw_stats

        battery = _normalize_battery(status.get("battary") or status.get("battery"))
        mode = _to_int(status.get("workMode"), default=_to_int(status.get("mode"), default=-1))
        clean_type = _to_int(status.get("type"), default=-1)
        charge_status = _to_int(status.get("chargeStatus"), default=0)
        fault = _to_int(status.get("faultCode") or status.get("fault"), default=0)

        return {
            "connected": _to_int(device.raw.get("status"), default=1) != 0,
            "robot_id": device.robot_id,
            "sn": device.sn,
            "name": device.name,
            "elec": battery,
            "battery": battery,
            "mode": _status_name(mode, clean_type, charge_status, fault),
            "workMode": mode,
            "type": clean_type,
            "chargeStatus": charge_status,
            "faultCode": fault,
            "cleanPerference": _to_int(status.get("cleanPerference") or status.get("pref")),
            "waterlevel": _to_int(status.get("waterlevel") or status.get("water")),
            "cleanTime": _to_int(status.get("cleanTime") or status.get("time")),
            "cleanSize": _normalize_area(status.get("cleanSize") or status.get("area")),
            "cleanArea": _normalize_area(status.get("cleanSize") or status.get("area")),
            "allArea": _normalize_area(status.get("cleanSize") or status.get("area")),
            "allTime": _to_int(status.get("cleanTime") or status.get("time")),
            "currentMapName": status.get("current_map_name") or status.get("mapName") or "",
            "houseName": status.get("house_name") or status.get("houseName") or "",
            "mapHeadId": _to_int(status.get("map_head_id") or status.get("mapHeadId")),
            "mapCount": _to_int(status.get("map_count") or status.get("mapCount")),
            "cleaningRoomId": _to_int(status.get("cleaning_roomId") or status.get("cleaningRoomId")),
            "raw": status,
        }

    def _refresh_known_plans(self) -> None:
        """Populate 4690 plan metadata from the active map status."""
        map_id = _to_int(self._shadow.get("mapHeadId"))
        if not map_id:
            return

        self._plans.setdefault(
            "Limpieza completa",
            {
                "mapid": map_id,
                "planid": 1,
                "type": 0,
            },
        )
        self._plans["Limpieza completa"]["mapid"] = map_id


def _base_payload() -> dict[str, Any]:
    return {
        "factoryId": FACTORY_ID,
        "projectType": PROJECT_TYPE,
        "versionName": VERSION_NAME,
        "versionCode": VERSION_CODE,
        "packageVersions": [{"packageType": PACKAGE_TYPE, "version": VERSION_CODE}],
    }


def _device_ctrl_data(control: str, first: int, second: int) -> dict[str, Any]:
    data = {
        "control": control,
        "result": -1,
        "type": -1,
        "value": -1,
        "ctrltype": -1,
        "is_open": -1,
        "begin_time": 1,
        "end_time": 1,
        "voiceMode": -1,
        "volume": -1,
        "isSave": -1,
    }
    if control == METHOD_SET_MODE:
        data["type"] = first
        data["value"] = second
    elif control in (METHOD_SET_PREFERENCE, "get_preference"):
        data["ctrltype"] = first
        data["value"] = second
    return data


def _maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _is_login_required(message: Any) -> bool:
    text = str(message).lower()
    return "login" in text or "登录" in text


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize_battery(value: Any) -> int:
    battery = _to_int(value)
    if battery > 100:
        battery -= 100
    return max(0, min(battery, 100))


def _normalize_area(value: Any) -> float:
    # Conga 4690 reports cleanSize as centi-square-meters: 528 means 5.28 m2.
    area = _to_int(value)
    return round(area / 100, 2)


def _status_name(work_mode: int, clean_type: int, charge_status: int, fault: int) -> str:
    if fault == FAULT_ROBOT_CHARGING:
        return "charge"
    if fault == FAULT_ROBOT_CHARGE_FINISH:
        return "fullcharge"
    if fault in (FAULT_ROBOT_GLOBAL_GO_HOME, FAULT_ROBOT_USER_GO_HOME):
        return "backcharge"
    if fault:
        return "error"
    if clean_type == MODE_BACK_CHARGE or charge_status == 1:
        return "backcharge"
    if charge_status in (2, 3):
        return "charge"
    if work_mode == VALUE_START:
        return "sweep"
    if work_mode == VALUE_PAUSE:
        return "pause"
    if work_mode in (VALUE_STOP, VALUE_IDLE, -1):
        return "idle"
    return "unknown"
