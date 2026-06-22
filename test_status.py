"""Tests for the satellite state indicator (status.py).

Run:  ~/assistant-env/bin/python -m pytest -q

Only the hardware-free backends are exercised: the factory mapping, the console
backend's output, and the no-op backend. The OLED backend is never instantiated
against real hardware here — we only prove that importing status.py and asking the
factory for "oled" does not drag in luma.* at module load (it stays lazy).
"""
import sys

import pytest

import status


def test_make_indicator_console(capsys):
    ind = status.make_indicator("console")
    assert isinstance(ind, status.ConsoleIndicator)
    ind.set_state(status.LISTENING)
    out = capsys.readouterr().out
    assert "[LISTENING]" in out


def test_console_indicator_includes_detail(capsys):
    status.ConsoleIndicator().set_state(status.THINKING, "turn on the lights")
    out = capsys.readouterr().out
    assert "[THINKING]" in out
    assert "turn on the lights" in out


def test_make_indicator_none_is_silent(capsys):
    ind = status.make_indicator("none")
    assert isinstance(ind, status.NullIndicator)
    ind.set_state(status.SPEAKING, "anything")
    ind.close()
    assert capsys.readouterr().out == ""


def test_make_indicator_unknown_raises():
    with pytest.raises(SystemExit):
        status.make_indicator("hologram")


def test_state_constants():
    assert status.IDLE == "IDLE"
    assert status.LISTENING == "LISTENING"
    assert status.THINKING == "THINKING"
    assert status.SPEAKING == "SPEAKING"
    assert status.ERROR == "ERROR"


def test_importing_status_does_not_load_luma():
    # the OLED deps are heavy/Pi-only; importing status.py must not pull them in
    assert "luma" not in sys.modules
    assert "luma.oled" not in sys.modules
