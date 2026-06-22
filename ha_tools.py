"""Home Assistant tool layer for the Alfred brain.

Exposes two primitives as LLM-callable tools — `get_states` and `call_service` —
mirroring Home Assistant's own API. On the brain (the Windows PC) you supply only
HA_URL + HA_TOKEN; everything device-related lives here, not in the voice loop.

Two backends share one interface (get_states / call_service):
  * WebSocketHAClient — the real client; talks to a running HA over its
    WebSocket API (REST is frozen). Used on the PC.
  * MockHAClient — an in-memory fake with a few demo entities, for validating
    model tool-calling on a machine with no HA (e.g. the Mac).

`TOOL_SCHEMA` is the function-calling spec handed to Ollama; `dispatch()` routes
a model tool call to whichever client is active and returns a JSON-friendly dict.

The WebSocket client lazily imports `websocket-client`, so this module imports
cleanly on machines without it (the Mac only ever needs the mock).
"""
from __future__ import annotations

import json
from typing import Any, Optional, Protocol


# --- Tool schema handed to the model -----------------------------------------

TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_states",
            "description": (
                "Read the current state of Home Assistant entities (lights, "
                "switches, sensors, climate, etc.). Call this before answering "
                "any question about whether something is on/off or its value."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": (
                            "Optional domain filter, e.g. 'light', 'switch', "
                            "'climate'. Omit to list every entity."
                        ),
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "turn_off_all",
            "description": (
                "Turn off many devices at once — use this for 'turn everything "
                "off', 'shut it all down', or 'goodnight'. Optionally restrict to "
                "one domain (e.g. only lights). Prefer this over many call_service "
                "calls when the user means several/all devices."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Optional domain to limit to, e.g. 'light'. Omit for everything.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_service",
            "description": (
                "Control a single device by calling a Home Assistant service, "
                "e.g. turn a light on/off or toggle a switch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Service domain, e.g. 'light', 'switch', 'climate'.",
                    },
                    "service": {
                        "type": "string",
                        "description": "Service name, e.g. 'turn_on', 'turn_off', 'toggle'.",
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "Target entity id, e.g. 'light.office'.",
                    },
                    "data": {
                        "type": "object",
                        "description": "Optional extra service data, e.g. {'brightness_pct': 50}.",
                    },
                },
                "required": ["domain", "service"],
            },
        },
    },
]


# Domains with a meaningful on/off state for "turn everything off".
SWITCHABLE_DOMAINS = ("light", "switch", "fan", "media_player")


class HAClient(Protocol):
    def get_states(self, domain: Optional[str] = None) -> list[dict]: ...
    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: Optional[str] = None,
        data: Optional[dict] = None,
    ) -> dict: ...
    def turn_off_all(self, domain: Optional[str] = None) -> dict: ...


def dispatch(client: HAClient, name: str, arguments: dict) -> Any:
    """Route one model tool call to the active client. Never raises — failures
    come back as {'error': ...} so the model can read and recover."""
    try:
        if name == "get_states":
            return client.get_states(domain=arguments.get("domain"))
        if name == "turn_off_all":
            return client.turn_off_all(domain=arguments.get("domain"))
        if name == "call_service":
            return client.call_service(
                domain=arguments["domain"],
                service=arguments["service"],
                entity_id=arguments.get("entity_id"),
                data=arguments.get("data"),
            )
        return {"error": f"unknown tool: {name}"}
    except KeyError as e:
        return {"error": f"missing required argument: {e}"}
    except Exception as e:  # surface transport/HA errors to the model
        return {"error": str(e)}


# --- Mock client (no HA needed) ----------------------------------------------

_DEMO_ENTITIES = {
    "light.office": {"state": "off", "attributes": {"friendly_name": "Office Light"}},
    "light.living_room": {"state": "off", "attributes": {"friendly_name": "Living Room Light"}},
    "light.bedroom": {"state": "off", "attributes": {"friendly_name": "Bedroom Light"}},
    "switch.kettle": {"state": "off", "attributes": {"friendly_name": "Kettle"}},
    "climate.thermostat": {
        "state": "heat",
        "attributes": {"friendly_name": "Thermostat", "temperature": 20},
    },
}


class MockHAClient:
    """In-memory HA stand-in with a few demo entities and recorded calls."""

    def __init__(self) -> None:
        self._states = {
            eid: {"state": v["state"], "attributes": dict(v["attributes"])}
            for eid, v in _DEMO_ENTITIES.items()
        }
        self.calls: list[dict] = []

    def state_of(self, entity_id: str) -> Optional[str]:
        s = self._states.get(entity_id)
        return s["state"] if s else None

    def get_states(self, domain: Optional[str] = None) -> list[dict]:
        out = []
        for eid, s in self._states.items():
            if domain and not eid.startswith(domain + "."):
                continue
            out.append({"entity_id": eid, "state": s["state"], "attributes": s["attributes"]})
        return out

    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: Optional[str] = None,
        data: Optional[dict] = None,
    ) -> dict:
        self.calls.append(
            {"domain": domain, "service": service, "entity_id": entity_id, "data": data}
        )
        if entity_id and entity_id in self._states:
            if service == "turn_on":
                self._states[entity_id]["state"] = "on"
            elif service == "turn_off":
                self._states[entity_id]["state"] = "off"
            elif service == "toggle":
                cur = self._states[entity_id]["state"]
                self._states[entity_id]["state"] = "off" if cur == "on" else "on"
        return {
            "success": True,
            "entity_id": entity_id,
            "new_state": self.state_of(entity_id) if entity_id else None,
        }

    def turn_off_all(self, domain: Optional[str] = None) -> dict:
        self.calls.append({"service": "turn_off_all", "domain": domain})
        turned_off = []
        for eid, s in self._states.items():
            if domain and not eid.startswith(domain + "."):
                continue
            if eid.split(".")[0] not in SWITCHABLE_DOMAINS:
                continue
            s["state"] = "off"
            turned_off.append(eid)
        return {"success": True, "turned_off": turned_off}


# --- Real client over HA's WebSocket API -------------------------------------

def _ws_url(url: str) -> str:
    """Normalise an HA base URL to its websocket endpoint."""
    u = url.strip().rstrip("/")
    if u.startswith("http://"):
        u = "ws://" + u[len("http://"):]
    elif u.startswith("https://"):
        u = "wss://" + u[len("https://"):]
    if not u.endswith("/api/websocket"):
        u = u + "/api/websocket"
    return u


def _conn_errors() -> tuple:
    """Exception types that mean 'the socket is gone, reconnect'. The
    websocket-client ones are added lazily so this works without the package."""
    excs: list[type] = [OSError, ConnectionError, EOFError]
    try:
        from websocket import (
            WebSocketConnectionClosedException,
            WebSocketTimeoutException,
        )

        excs += [WebSocketConnectionClosedException, WebSocketTimeoutException]
    except Exception:
        pass
    return tuple(excs)


class WebSocketHAClient:
    """Synchronous client for Home Assistant's WebSocket API.

    Needs `websocket-client` (pip install websocket-client) — imported lazily so
    this module still loads on machines without it.

    Resilient to dropped connections: a command that fails mid-flight triggers
    one reconnect (with a fresh auth handshake) and retry. The retry re-sends the
    command, which is safe for idempotent services (turn_on/turn_off) but could
    double-apply a `toggle` if the drop happened after HA already acted — a rare
    edge we accept over leaving the brain wedged on a stale socket.
    """

    def __init__(self, url: str, token: str, timeout: float = 10.0) -> None:
        self.url = _ws_url(url)
        self.token = token
        self.timeout = timeout
        self._ws = None
        self._id = 0

    def _connect(self) -> None:
        from websocket import create_connection  # lazy: PC-only dependency

        ws = create_connection(self.url, timeout=self.timeout)
        hello = json.loads(ws.recv())  # {"type": "auth_required", ...}
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"unexpected HA handshake: {hello}")
        ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        resp = json.loads(ws.recv())
        if resp.get("type") != "auth_ok":
            raise RuntimeError(f"HA auth failed: {resp}")
        self._ws = ws

    def _reset(self) -> None:
        """Drop the current socket so the next command reconnects from scratch."""
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _ensure_connection(self) -> None:
        if self._ws is None:
            self._connect()

    def _send_recv(self, payload: dict) -> Any:
        self._id += 1
        msg_id = self._id
        self._ws.send(json.dumps({**payload, "id": msg_id}))
        while True:
            resp = json.loads(self._ws.recv())
            if resp.get("id") != msg_id or resp.get("type") != "result":
                continue  # skip events / other ids
            if not resp.get("success", False):
                raise RuntimeError(f"HA command failed: {resp.get('error')}")
            return resp.get("result")

    def _command(self, payload: dict, _retry: bool = True) -> Any:
        try:
            self._ensure_connection()
            return self._send_recv(payload)
        except _conn_errors() as e:
            self._reset()
            if _retry:
                return self._command(payload, _retry=False)
            raise RuntimeError(f"HA connection lost: {e}") from e

    def get_states(self, domain: Optional[str] = None) -> list[dict]:
        result = self._command({"type": "get_states"})
        states = [
            {"entity_id": s["entity_id"], "state": s["state"], "attributes": s.get("attributes", {})}
            for s in result
        ]
        if domain:
            states = [s for s in states if s["entity_id"].startswith(domain + ".")]
        return states

    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: Optional[str] = None,
        data: Optional[dict] = None,
    ) -> dict:
        payload = {"type": "call_service", "domain": domain, "service": service}
        if entity_id:
            payload["target"] = {"entity_id": entity_id}
        if data:
            payload["service_data"] = data
        result = self._command(payload)
        return {"success": True, "result": result}

    def turn_off_all(self, domain: Optional[str] = None) -> dict:
        # Real HA: the `homeassistant.turn_off` service with entity_id "all"
        # turns off everything; a domain's own turn_off + "all" scopes it.
        target_domain = domain if domain else "homeassistant"
        return self.call_service(target_domain, "turn_off", entity_id="all")

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None
