"""Tests for the brain_server HTTP seam and the satellite client contract.

Run:  ~/assistant-env/bin/python -m pytest -q

A real ThreadingHTTPServer is started on an ephemeral loopback port with a FAKE
brain (no LLM), then driven both with raw urllib (to pin the wire contract) and
through SatelliteClient (to prove the satellite speaks that same contract).
"""
import json
import threading
import urllib.error
import urllib.request

import pytest
from http.server import ThreadingHTTPServer

from brain_server import make_handler
from satellite import SatelliteClient, SatelliteError


class FakeBrain:
    """Stand-in for Brain: records turns, echoes a scripted reply."""

    def __init__(self, model="fake-7b", tools=None, ha_client=None, google_client=None):
        self.model = model
        self.tools = tools
        self.ha_client = ha_client
        self.google_client = google_client
        self.seen = []
        self.last_display = None

    def chat(self, text):
        self.seen.append(text)
        if "calendar" in text:
            self.last_display = {"type": "calendar", "url": "/display/calendar?range=week"}
        return f"You said: {text}"

    def chat_stream(self, text):
        self.seen.append(text)
        if "calendar" in text:
            self.last_display = {"type": "calendar", "url": "/display/calendar?range=week"}
        for sentence in (f"You said: {text}.", "And more.", "And done."):
            yield sentence


@pytest.fixture
def server():
    brain = FakeBrain()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(brain, threading.Lock()))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield brain, f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        t.join()


def _post(base, path, raw_body):
    req = urllib.request.Request(
        base + path, data=raw_body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def _post_ndjson(base, path, raw_body):
    """POST and return (status, [parsed JSON line, ...]) from an NDJSON stream."""
    req = urllib.request.Request(
        base + path, data=raw_body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        lines = [json.loads(ln) for ln in resp.read().splitlines() if ln.strip()]
        return resp.status, lines


# --- raw wire contract ------------------------------------------------------
def test_health(server):
    brain, base = server
    with urllib.request.urlopen(base + "/health", timeout=5) as resp:
        body = json.loads(resp.read())
    assert resp.status == 200
    assert body == {
        "status": "ok",
        "model": "fake-7b",
        "home_control": False,
        "calendar": False,
    }


def test_chat_happy_path(server):
    brain, base = server
    status, body = _post(base, "/chat", b'{"text": "hello"}')
    assert status == 200
    assert body == {"reply": "You said: hello"}
    assert brain.seen == ["hello"]


def test_chat_bad_json_returns_400(server):
    brain, base = server
    with pytest.raises(urllib.error.HTTPError) as ei:
        _post(base, "/chat", b"not json")
    assert ei.value.code == 400
    assert brain.seen == []  # brain never invoked on a bad request


def test_chat_empty_text_returns_400(server):
    brain, base = server
    with pytest.raises(urllib.error.HTTPError) as ei:
        _post(base, "/chat", b'{"text": "   "}')
    assert ei.value.code == 400
    assert brain.seen == []


def test_chat_stream_yields_ndjson_sentences(server):
    brain, base = server
    status, lines = _post_ndjson(base, "/chat/stream", b'{"text": "hello"}')
    assert status == 200
    assert lines == [
        {"sentence": "You said: hello."},
        {"sentence": "And more."},
        {"sentence": "And done."},
    ]
    assert brain.seen == ["hello"]


def test_chat_stream_empty_text_returns_400(server):
    brain, base = server
    with pytest.raises(urllib.error.HTTPError) as ei:
        _post_ndjson(base, "/chat/stream", b'{"text": "   "}')
    assert ei.value.code == 400
    assert brain.seen == []


def test_chat_stream_reports_midstream_error_as_trailing_line():
    # a brain that yields one sentence, then fails partway through the turn
    class BrokenBrain(FakeBrain):
        def chat_stream(self, text):
            self.seen.append(text)
            yield "Starting up."
            raise RuntimeError("ollama fell over")

    brain = BrokenBrain()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(brain, threading.Lock()))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        status, lines = _post_ndjson(base, "/chat/stream", b'{"text": "hi"}')
        assert status == 200  # status was committed before the failure
        assert lines == [
            {"sentence": "Starting up."},
            {"error": "ollama fell over"},
        ]
    finally:
        httpd.shutdown()
        t.join()


def test_unknown_route_404(server):
    brain, base = server
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(base + "/nope", timeout=5)
    assert ei.value.code == 404


# --- web satellite (served single-page client) ------------------------------
def test_root_serves_web_client(server):
    brain, base = server
    for path in ("/", "/app"):
        with urllib.request.urlopen(base + path, timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type", "").startswith("text/html")
            body = resp.read().decode()
        assert "Alfred" in body
        assert 'id="input"' in body          # the chat input the page is built around
        assert "/chat/stream" in body        # it streams from the brain
        assert "/display/calendar" not in body or "showCalendar" in body


# --- display contract -------------------------------------------------------
def _serve(brain):
    """Start a throwaway server for one brain; returns (httpd, thread, base)."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(brain, threading.Lock()))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t, f"http://127.0.0.1:{port}"


def test_chat_returns_display_url_when_calendar_shown(server):
    brain, base = server
    status, body = _post(base, "/chat", b'{"text": "show me my calendar"}')
    assert status == 200
    assert body["reply"] == "You said: show me my calendar"
    assert body["display_url"] == "/display/calendar?range=week"


def test_chat_omits_display_url_when_nothing_to_show(server):
    brain, base = server
    status, body = _post(base, "/chat", b'{"text": "hello"}')
    assert status == 200
    assert "display_url" not in body


def test_chat_stream_emits_trailing_display_url(server):
    brain, base = server
    status, lines = _post_ndjson(base, "/chat/stream", b'{"text": "show calendar"}')
    assert status == 200
    assert lines[-1] == {"display_url": "/display/calendar?range=week"}
    # the sentences still come first
    assert lines[0] == {"sentence": "You said: show calendar."}


def test_display_calendar_route_renders_events():
    from google_tools import MockGoogleClient

    brain = FakeBrain(google_client=MockGoogleClient())
    httpd, t, base = _serve(brain)
    try:
        with urllib.request.urlopen(base + "/display/calendar?range=week", timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type", "").startswith("text/html")
            body = resp.read().decode()
        assert "This Week" in body
        assert "Dentist appointment" in body
    finally:
        httpd.shutdown()
        t.join()


def test_display_calendar_404_when_not_configured(server):
    brain, base = server  # FakeBrain with no google_client
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(base + "/display/calendar", timeout=5)
    assert ei.value.code == 404


# --- satellite client speaks the same contract ------------------------------
def test_satellite_health_and_ask(server):
    brain, base = server
    client = SatelliteClient(base, timeout=5)
    info = client.health()
    assert info["model"] == "fake-7b"
    assert info["home_control"] is False
    assert client.ask("good evening") == "You said: good evening"
    assert brain.seen == ["good evening"]


def test_satellite_surfaces_server_error(server):
    brain, base = server
    client = SatelliteClient(base, timeout=5)
    with pytest.raises(SatelliteError):
        client.ask("   ")  # empty text -> server 400 -> SatelliteError


def test_satellite_ask_stream_yields_sentences(server):
    brain, base = server
    client = SatelliteClient(base, timeout=5)
    sentences = list(client.ask_stream("good evening"))
    assert sentences == ["You said: good evening.", "And more.", "And done."]
    assert brain.seen == ["good evening"]


def test_satellite_ask_stream_empty_text_raises(server):
    brain, base = server
    client = SatelliteClient(base, timeout=5)
    with pytest.raises(SatelliteError):
        list(client.ask_stream("   "))  # server 400 -> SatelliteError


def test_satellite_ask_stream_surfaces_midstream_error():
    class BrokenBrain(FakeBrain):
        def chat_stream(self, text):
            self.seen.append(text)
            yield "Starting up."
            raise RuntimeError("ollama fell over")

    brain = BrokenBrain()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(brain, threading.Lock()))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        client = SatelliteClient(f"http://127.0.0.1:{port}", timeout=5)
        got = []
        with pytest.raises(SatelliteError, match="ollama fell over"):
            for sentence in client.ask_stream("hi"):
                got.append(sentence)
        assert got == ["Starting up."]  # earlier sentences still delivered
    finally:
        httpd.shutdown()
        t.join()


def test_satellite_unreachable_brain_raises():
    # nothing is listening on this port
    client = SatelliteClient("http://127.0.0.1:9", timeout=1)
    with pytest.raises(SatelliteError):
        client.health()


# --- /chat/audio (voice satellite) ------------------------------------------
# These pin the audio wire contract without any real STT/TTS: the handler takes
# injected transcribe_fn/synth_fn fakes. The posted body is a real (tiny) WAV so
# the server's decode_wav still runs; the fakes ignore the samples.
import base64

import numpy as np

import voice


def _wav_bytes():
    """A short, real 16 kHz WAV so the server's decode_wav accepts the body."""
    return voice.encode_wav(np.zeros(1600, dtype="float32"), voice.SR)


def _fake_transcribe(text="turn on the lights"):
    return lambda samples: text


def _fake_synth(samples_per_sentence=400):
    # return distinct, decodable WAV bytes per sentence (24 kHz, like Kokoro)
    return lambda sentence: voice.encode_wav(
        np.zeros(samples_per_sentence, dtype="float32"), voice.TTS_SR
    )


def _serve_audio(brain, transcribe_fn=None, synth_fn=None):
    handler = make_handler(
        brain,
        threading.Lock(),
        transcribe_fn=transcribe_fn or _fake_transcribe(),
        synth_fn=synth_fn or _fake_synth(),
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t, f"http://127.0.0.1:{port}"


def _post_audio_ndjson(base, body, content_type="audio/wav"):
    req = urllib.request.Request(
        base + "/chat/audio", data=body, headers={"Content-Type": content_type}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        lines = [json.loads(ln) for ln in resp.read().splitlines() if ln.strip()]
        return resp.status, lines


def test_chat_audio_transcript_then_audio_sentences():
    brain = FakeBrain()
    httpd, t, base = _serve_audio(brain, transcribe_fn=_fake_transcribe("hello there"))
    try:
        status, lines = _post_audio_ndjson(base, _wav_bytes())
        assert status == 200
        # first line is the transcript of what was "heard"
        assert lines[0] == {"transcript": "hello there"}
        # the brain ran the transcribed text, not the raw audio
        assert brain.seen == ["hello there"]
        # then one {sentence, audio} per sentence, audio is decodable base64 WAV
        sentence_lines = [ln for ln in lines if "sentence" in ln]
        assert [ln["sentence"] for ln in sentence_lines] == [
            "You said: hello there.",
            "And more.",
            "And done.",
        ]
        for ln in sentence_lines:
            wav = base64.b64decode(ln["audio"])
            audio, sr = voice.decode_wav(wav)
            assert sr == voice.TTS_SR
    finally:
        httpd.shutdown()
        t.join()


def test_chat_audio_emits_trailing_display_url():
    brain = FakeBrain()
    httpd, t, base = _serve_audio(brain, transcribe_fn=_fake_transcribe("show calendar"))
    try:
        status, lines = _post_audio_ndjson(base, _wav_bytes())
        assert status == 200
        assert lines[0] == {"transcript": "show calendar"}
        assert lines[-1] == {"display_url": "/display/calendar?range=week"}
    finally:
        httpd.shutdown()
        t.join()


def test_chat_audio_empty_speech_is_transcript_only():
    brain = FakeBrain()
    httpd, t, base = _serve_audio(brain, transcribe_fn=_fake_transcribe(""))
    try:
        status, lines = _post_audio_ndjson(base, _wav_bytes())
        assert status == 200
        assert lines == [{"transcript": ""}]  # heard nothing, nothing to say
        assert brain.seen == []  # brain never invoked
    finally:
        httpd.shutdown()
        t.join()


def test_chat_audio_empty_body_returns_400():
    brain = FakeBrain()
    httpd, t, base = _serve_audio(brain)
    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            _post_audio_ndjson(base, b"")
        assert ei.value.code == 400
    finally:
        httpd.shutdown()
        t.join()


def test_chat_audio_non_wav_body_returns_400():
    brain = FakeBrain()
    httpd, t, base = _serve_audio(brain)
    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            _post_audio_ndjson(base, b"this is not a wav file")
        assert ei.value.code == 400
        assert brain.seen == []  # never reached the brain
    finally:
        httpd.shutdown()
        t.join()


def test_chat_audio_reports_midstream_error_as_trailing_line():
    class BrokenBrain(FakeBrain):
        def chat_stream(self, text):
            self.seen.append(text)
            yield "Starting up."
            raise RuntimeError("ollama fell over")

    brain = BrokenBrain()
    httpd, t, base = _serve_audio(brain, transcribe_fn=_fake_transcribe("hi"))
    try:
        status, lines = _post_audio_ndjson(base, _wav_bytes())
        assert status == 200  # committed before the failure
        assert lines[0] == {"transcript": "hi"}
        assert lines[-1] == {"error": "ollama fell over"}
    finally:
        httpd.shutdown()
        t.join()


# --- satellite client speaks the audio contract -----------------------------
def test_satellite_ask_audio_yields_typed_events():
    brain = FakeBrain()
    httpd, t, base = _serve_audio(brain, transcribe_fn=_fake_transcribe("show calendar"))
    try:
        client = SatelliteClient(base, timeout=5)
        events = list(client.ask_audio(_wav_bytes()))
        assert events[0] == ("transcript", "show calendar")
        audio_events = [e for e in events if e[0] == "audio"]
        assert [e[1] for e in audio_events] == [
            "You said: show calendar.",
            "And more.",
            "And done.",
        ]
        # each audio event carries decodable WAV bytes
        for _, _sentence, wav in audio_events:
            _audio, sr = voice.decode_wav(wav)
            assert sr == voice.TTS_SR
        assert events[-1] == ("display_url", "/display/calendar?range=week")
    finally:
        httpd.shutdown()
        t.join()


def test_satellite_ask_audio_surfaces_midstream_error():
    class BrokenBrain(FakeBrain):
        def chat_stream(self, text):
            self.seen.append(text)
            yield "Starting up."
            raise RuntimeError("ollama fell over")

    brain = BrokenBrain()
    httpd, t, base = _serve_audio(brain, transcribe_fn=_fake_transcribe("hi"))
    try:
        client = SatelliteClient(base, timeout=5)
        got = []
        with pytest.raises(SatelliteError, match="ollama fell over"):
            for event in client.ask_audio(_wav_bytes()):
                got.append(event)
        assert got[0] == ("transcript", "hi")
        assert got[1] == ("audio", "Starting up.", got[1][2])  # earlier sentence delivered
    finally:
        httpd.shutdown()
        t.join()
