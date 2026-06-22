"""Regression tests for the Home Assistant tool layer (ha_tools.py).

Run:  ~/assistant-env/bin/python -m pytest -q
These cover the mock client, the dispatch router, the turn_off_all wrapper, and
URL normalisation — no real Home Assistant or network needed.
"""
import json

from ha_tools import MockHAClient, WebSocketHAClient, dispatch, _ws_url


def test_get_states_all_and_domain_filter():
    c = MockHAClient()
    assert len(c.get_states()) == 5
    lights = c.get_states(domain="light")
    assert {s["entity_id"] for s in lights} == {
        "light.office",
        "light.living_room",
        "light.bedroom",
    }


def test_call_service_on_off_toggle():
    c = MockHAClient()
    assert c.state_of("light.office") == "off"
    c.call_service("light", "turn_on", "light.office")
    assert c.state_of("light.office") == "on"
    c.call_service("light", "turn_off", "light.office")
    assert c.state_of("light.office") == "off"
    c.call_service("light", "toggle", "light.office")
    assert c.state_of("light.office") == "on"


def test_call_service_records_calls():
    c = MockHAClient()
    c.call_service("switch", "turn_on", "switch.kettle", data={"foo": 1})
    assert c.calls[-1] == {
        "domain": "switch",
        "service": "turn_on",
        "entity_id": "switch.kettle",
        "data": {"foo": 1},
    }


def test_turn_off_all_global_leaves_climate_alone():
    c = MockHAClient()
    for e in ("light.office", "light.living_room", "switch.kettle"):
        c.call_service(e.split(".")[0], "turn_on", e)
    result = c.turn_off_all()
    assert all(
        c.state_of(e) == "off"
        for e in ("light.office", "light.living_room", "light.bedroom", "switch.kettle")
    )
    # climate is not a switchable domain — must be untouched
    assert c.state_of("climate.thermostat") == "heat"
    assert "switch.kettle" in result["turned_off"]


def test_turn_off_all_scoped_to_domain():
    c = MockHAClient()
    for e in ("light.office", "light.bedroom"):
        c.call_service("light", "turn_on", e)
    c.call_service("switch", "turn_on", "switch.kettle")
    c.turn_off_all(domain="light")
    assert c.state_of("light.office") == "off"
    assert c.state_of("light.bedroom") == "off"
    assert c.state_of("switch.kettle") == "on"  # other domains untouched


def test_dispatch_routes_each_tool():
    c = MockHAClient()
    assert isinstance(dispatch(c, "get_states", {}), list)
    dispatch(c, "call_service", {"domain": "light", "service": "turn_on", "entity_id": "light.office"})
    assert c.state_of("light.office") == "on"
    dispatch(c, "turn_off_all", {})
    assert c.state_of("light.office") == "off"


def test_dispatch_unknown_tool_returns_error():
    c = MockHAClient()
    assert "error" in dispatch(c, "frobnicate", {})


def test_dispatch_missing_required_arg_returns_error():
    c = MockHAClient()
    # call_service requires domain + service; omit service
    out = dispatch(c, "call_service", {"domain": "light"})
    assert "error" in out


def test_ws_url_normalisation():
    assert _ws_url("http://localhost:8123") == "ws://localhost:8123/api/websocket"
    assert _ws_url("https://ha.example.com") == "wss://ha.example.com/api/websocket"
    assert _ws_url("http://localhost:8123/") == "ws://localhost:8123/api/websocket"
    # already-correct endpoint is left intact
    assert _ws_url("ws://x:8123/api/websocket") == "ws://x:8123/api/websocket"


# --- WebSocketHAClient resilience (no real HA / websocket package needed) ----
class _FakeWS:
    """A scriptable HA websocket: replies to each sent command with a result,
    optionally raising OSError on the first send to simulate a dropped socket."""

    def __init__(self, fail_send_once: bool = False, result=None):
        self.fail_send_once = fail_send_once
        self.result = result if result is not None else [{"entity_id": "light.x", "state": "on", "attributes": {}}]
        self._inbox: list[str] = []
        self.closed = False

    def send(self, data: str) -> None:
        if self.fail_send_once:
            self.fail_send_once = False
            raise OSError("broken pipe")
        msg = json.loads(data)
        # echo an unrelated event first to prove the recv loop skips it
        self._inbox.append(json.dumps({"id": 999, "type": "event"}))
        self._inbox.append(json.dumps({"id": msg["id"], "type": "result", "success": True, "result": self.result}))

    def recv(self) -> str:
        return self._inbox.pop(0)

    def close(self) -> None:
        self.closed = True


def _client_with_fake_connect(monkeypatch, ws_factory):
    c = WebSocketHAClient("http://ha:8123", "token")
    connects = {"n": 0}

    def fake_connect():
        connects["n"] += 1
        c._ws = ws_factory(connects["n"])

    monkeypatch.setattr(c, "_connect", fake_connect)
    return c, connects


def test_ws_command_happy_path_skips_events(monkeypatch):
    c, connects = _client_with_fake_connect(monkeypatch, lambda n: _FakeWS())
    states = c.get_states()
    assert connects["n"] == 1  # connected once, no reconnect
    assert states == [{"entity_id": "light.x", "state": "on", "attributes": {}}]


def test_ws_command_reconnects_after_drop(monkeypatch):
    # first socket fails on send (dropped); second is healthy
    c, connects = _client_with_fake_connect(
        monkeypatch, lambda n: _FakeWS(fail_send_once=(n == 1))
    )
    states = c.get_states()
    assert connects["n"] == 2  # reconnected exactly once
    assert states[0]["entity_id"] == "light.x"


def test_ws_command_raises_after_persistent_failure(monkeypatch):
    # every socket drops on send — one retry, then give up with a clear error
    c, connects = _client_with_fake_connect(
        monkeypatch, lambda n: _FakeWS(fail_send_once=True)
    )
    try:
        c.get_states()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "connection lost" in str(e).lower()
    assert connects["n"] == 2  # initial attempt + one retry
