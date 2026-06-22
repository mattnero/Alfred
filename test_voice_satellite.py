"""Tests for the voice satellite's turn handling (voice_satellite.py).

Run:  ~/assistant-env/bin/python -m pytest -q

handle_audio is the one piece worth pinning without hardware: given the brain's
ask_audio event stream, it must drive the status indicator THINKING (with the
transcript) and then SPEAKING. We feed a fake client whose audio events carry
``wav=None`` so no speaker is touched, and a fake indicator that records the
states it was told to show. No mic, no sounddevice, no network.
"""
import voice_satellite
from status import SPEAKING, THINKING


class FakeIndicator:
    def __init__(self):
        self.states = []

    def set_state(self, state, detail=""):
        self.states.append((state, detail))

    def close(self):
        pass


class FakeClient:
    base_url = "http://fake"

    def __init__(self, events):
        self._events = events

    def ask_audio(self, wav_bytes):
        yield from self._events


def test_handle_audio_drives_thinking_then_speaking():
    events = [
        ("transcript", "what's the weather"),
        ("audio", "It is sunny, sir.", None),
        ("audio", "Quite pleasant.", None),
    ]
    indicator = FakeIndicator()
    voice_satellite.handle_audio(FakeClient(events), b"wav", indicator)

    assert indicator.states[0] == (THINKING, "what's the weather")
    # SPEAKING is set once, on the first audio event (not per sentence)
    speaking = [s for s in indicator.states if s[0] == SPEAKING]
    assert speaking == [(SPEAKING, "")]


def test_handle_audio_silent_reply_never_speaks():
    # transcript only (e.g. empty speech) — no audio events, so no SPEAKING
    indicator = FakeIndicator()
    voice_satellite.handle_audio(FakeClient([("transcript", "")]), b"wav", indicator)
    assert indicator.states == [(THINKING, "")]


def test_handle_audio_passes_display_url_through(capsys):
    events = [
        ("transcript", "show calendar"),
        ("audio", "Here you are, sir.", None),
        ("display_url", "/display/calendar?range=week"),
    ]
    voice_satellite.handle_audio(FakeClient(events), b"wav", FakeIndicator())
    out = capsys.readouterr().out
    assert "/display/calendar?range=week" in out
