"""A tiny state indicator for the voice satellite — show what Alfred is doing.

The satellite (`voice_satellite.py`) moves through a pipeline every turn:
idle -> listening -> thinking -> speaking -> idle (and error on a failure). On the
Mac that's visible in the terminal, but the real Raspberry Pi robot is headless, so
it needs a physical readout. This module provides one small abstraction with three
backends:

  * ConsoleIndicator (default) — prints the state; fine on the Mac and in tests.
  * NullIndicator — silent no-op, for ``--indicator none``.
  * OledIndicator — drives a 0.96" SSD1306 I2C OLED on the Pi via luma.oled.

As with voice.py, all hardware imports (luma.*) live *inside* OledIndicator so
importing this module is free on a machine with no display libraries. Pick a
backend with make_indicator(); the satellite calls set_state() at each transition.
"""
from __future__ import annotations

# --- pipeline states (also the words shown on the display) ------------------
IDLE = "IDLE"
LISTENING = "LISTENING"
THINKING = "THINKING"
SPEAKING = "SPEAKING"
ERROR = "ERROR"

# friendly captions for the OLED's big top line, keyed by state
_CAPTIONS = {
    IDLE: "Alfred",
    LISTENING: "Listening",
    THINKING: "Thinking",
    SPEAKING: "Speaking",
    ERROR: "Error",
}


class StatusIndicator:
    """Base interface: drive a state, then clean up. Backends override these."""

    def set_state(self, state: str, detail: str = "") -> None:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        pass


class NullIndicator(StatusIndicator):
    """Show nothing — for headless runs where no readout is wanted."""

    def set_state(self, state: str, detail: str = "") -> None:
        pass


class ConsoleIndicator(StatusIndicator):
    """Print each state to the terminal. Default on the Mac and in tests."""

    def set_state(self, state: str, detail: str = "") -> None:
        line = f"[{state}]"
        if detail:
            line += f" {detail}"
        print(line, flush=True)


class OledIndicator(StatusIndicator):
    """Render the state (and any detail) on an SSD1306 I2C OLED via luma.oled.

    The Pi 5's RP1 chip breaks the RPi.GPIO / WS2812 timing libraries, so an
    addressable-LED readout is fiddly there — but I2C is unaffected, which is why
    we use an I2C OLED. If the panel can't be opened we degrade to console output
    rather than crashing the voice loop.
    """

    def __init__(self, port: int = 1, address: int = 0x3C):
        self._device = None
        self._fallback = None
        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306

            self._device = ssd1306(i2c(port=port, address=address))
        except Exception as e:
            print(f"(OLED unavailable, falling back to console: {e})", flush=True)
            self._fallback = ConsoleIndicator()

    def set_state(self, state: str, detail: str = "") -> None:
        if self._device is None:
            self._fallback.set_state(state, detail)
            return
        from luma.core.render import canvas

        caption = _CAPTIONS.get(state, state)
        with canvas(self._device) as draw:
            draw.text((0, 0), caption, fill="white")
            if detail:
                draw.text((0, 24), detail[:21], fill="white")

    def close(self) -> None:
        if self._device is not None:
            try:
                self._device.clear()
            except Exception:
                pass


def make_indicator(kind: str = "console") -> StatusIndicator:
    """Return a StatusIndicator for ``kind`` (console | none | oled).

    "console" and "none" never touch hardware; "oled" lazily imports luma.oled
    only when actually constructed.
    """
    if kind == "console":
        return ConsoleIndicator()
    if kind == "none":
        return NullIndicator()
    if kind == "oled":
        return OledIndicator()
    raise SystemExit(f"Unknown indicator: {kind!r} (use console/none/oled)")
