"""Minimal HTTP front door to Alfred's brain, for satellites.

This is the central-brain + thin-satellite seam from the locked architecture:
it serves one shared Brain over HTTP so any device on the LAN can POST text and
receive Alfred's reply. Text-only — satellites do their own STT/TTS (or send
already-transcribed text). Home control is enabled when HA_URL + HA_TOKEN are set.

    HA_URL=http://localhost:8123 HA_TOKEN=xxxx \
      ~/assistant-env/bin/python brain_server.py --model qwen2.5:7b --port 8200

    curl -s localhost:8200/chat -d '{"text":"turn on the office light"}'
    curl -sN localhost:8200/chat/stream -d '{"text":"tell me about tomatoes"}'
    curl -s localhost:8200/health

The /chat/stream route returns newline-delimited JSON (one {"sentence": ...} per
sentence as it forms), so a satellite can speak incrementally; /chat returns the
whole reply in one {"reply": ...} blob.

The /chat/audio route is the voice-satellite seam: POST a WAV (mono 16 kHz) and
the brain transcribes it (Whisper), runs the turn, and streams NDJSON back — a
leading {"transcript": ...} line, then one {"sentence", "audio"} per sentence
(audio is base64 24 kHz WAV synthesised by Kokoro on the brain), then a trailing
{"display_url": ...}. So the satellite stays thin: it captures the mic and plays
audio, never running Whisper or Kokoro itself.

Note: one shared conversation for now (fine for a single household); a lock
serialises turns. Per-satellite sessions are a later step.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from brain import Brain
from google_tools import (
    DEFAULT_CREDENTIALS_PATH,
    DEFAULT_TOKEN_PATH,
    render_calendar_html,
)
from ha_tools import WebSocketHAClient
from web_app import INDEX_HTML


def make_handler(brain: Brain, lock: threading.Lock, transcribe_fn=None, synth_fn=None):
    # STT (audio -> text) and TTS (text -> WAV bytes) for the /chat/audio route.
    # Injected for testing (fakes); built lazily from voice.py on first real use
    # so the text routes never pull in Whisper/Kokoro.
    audio = {"transcribe": transcribe_fn, "synth": synth_fn}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, code: int, markup: str) -> None:
            body = markup.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_text(self) -> str | None:
            """Parse the request body's 'text' field, or send a 400 and return
            None. Safe to call before any streaming has started."""
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
                text = (data.get("text") or "").strip()
            except (json.JSONDecodeError, ValueError):
                self._send(400, {"error": "body must be JSON with a 'text' field"})
                return None
            if not text:
                self._send(400, {"error": "empty 'text'"})
                return None
            return text

        def do_GET(self) -> None:
            route = urlparse(self.path).path
            if route in ("/", "/app"):
                self._send_html(200, INDEX_HTML)
            elif route == "/health":
                self._send(200, {
                    "status": "ok",
                    "model": brain.model,
                    "home_control": bool(getattr(brain, "ha_client", None)),
                    "calendar": bool(getattr(brain, "google_client", None)),
                })
            elif route == "/display/calendar":
                self._display_calendar()
            else:
                self._send(404, {"error": "not found"})

        def _display_calendar(self) -> None:
            """Render the calendar as an HTML page a satellite (or projector)
            shows. Reads events from the brain's Google client; ?range= sets the
            page title. (Per-range windowing arrives with the live client in
            step 4 — the mock returns all upcoming events.)"""
            gclient = getattr(brain, "google_client", None)
            if gclient is None:
                self._send_html(404, "<h1>Calendar not configured, sir.</h1>")
                return
            qs = parse_qs(urlparse(self.path).query)
            range_label = (qs.get("range", ["week"])[0]) or "week"
            try:
                events = gclient.list_events(max_results=50)
            except Exception as e:  # noqa: BLE001 - show the failure on the screen
                self._send_html(500, f"<h1>Could not load the calendar, sir.</h1><p>{e}</p>")
                return
            self._send_html(200, render_calendar_html(events, range_label))

        def do_POST(self) -> None:
            if self.path == "/chat":
                text = self._read_text()
                if text is None:
                    return
                with lock:  # one conversation, one turn at a time
                    reply = brain.chat(text)
                    display = getattr(brain, "last_display", None)
                payload = {"reply": reply}
                if display:
                    payload["display_url"] = display["url"]
                self._send(200, payload)
            elif self.path == "/chat/stream":
                self._chat_stream()
            elif self.path == "/chat/audio":
                self._chat_audio()
            else:
                self._send(404, {"error": "not found"})

        def _chat_stream(self) -> None:
            """Stream Alfred's reply as newline-delimited JSON, one object per
            sentence ({"sentence": ...}), flushing as each is formed so a remote
            satellite can speak before the whole reply exists. The turn holds the
            lock for its duration. Errors after the stream has begun are reported
            as a trailing {"error": ...} line, since the 200 status is already
            committed."""
            text = self._read_text()
            if text is None:
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()

            def write(obj: dict) -> None:
                self.wfile.write(json.dumps(obj).encode() + b"\n")
                self.wfile.flush()

            try:
                with lock:  # one conversation, one turn at a time
                    for sentence in brain.chat_stream(text):
                        write({"sentence": sentence})
                    display = getattr(brain, "last_display", None)
                if display:  # trailing line tells the satellite what to show
                    write({"display_url": display["url"]})
            except Exception as e:  # noqa: BLE001 - report to the client, never 500 mid-stream
                try:
                    write({"error": str(e)})
                except Exception:
                    pass

        def _ensure_audio(self) -> None:
            """Lazily build the real STT/TTS the first time /chat/audio is hit
            (tests inject fakes, so this never runs there)."""
            if audio["transcribe"] is None or audio["synth"] is None:
                import voice

                if audio["transcribe"] is None:
                    audio["transcribe"] = voice.make_transcriber()
                if audio["synth"] is None:
                    audio["synth"] = voice.make_synthesizer()

        def _chat_audio(self) -> None:
            """Transcribe a posted WAV, run the turn, and stream the reply back
            as NDJSON: a leading {"transcript": ...}, then one {"sentence",
            "audio"} per sentence (base64 24 kHz WAV), then a trailing
            {"display_url": ...}. STT happens before the 200 is committed so a bad
            body still gets a clean 400; errors after that are a trailing
            {"error": ...} line. Empty speech is a normal 200 with an empty
            transcript and no sentences."""
            import base64
            import voice

            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            if not body:
                self._send(400, {"error": "empty audio body"})
                return
            try:
                samples, _sr = voice.decode_wav(body)
            except Exception:  # noqa: BLE001 - malformed audio is a client error
                self._send(400, {"error": "body must be WAV audio"})
                return

            self._ensure_audio()
            text = (audio["transcribe"](samples) or "").strip()

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()

            def write(obj: dict) -> None:
                self.wfile.write(json.dumps(obj).encode() + b"\n")
                self.wfile.flush()

            write({"transcript": text})
            if not text:  # heard nothing — transcript-only, nothing to say
                return
            try:
                with lock:  # one conversation, one turn at a time
                    for sentence in brain.chat_stream(text):
                        wav = audio["synth"](sentence)
                        line = {"sentence": sentence}
                        if wav:
                            line["audio"] = base64.b64encode(wav).decode("ascii")
                        write(line)
                    display = getattr(brain, "last_display", None)
                if display:  # trailing line tells the satellite what to show
                    write({"display_url": display["url"]})
            except Exception as e:  # noqa: BLE001 - report to the client, never 500 mid-stream
                try:
                    write({"error": str(e)})
                except Exception:
                    pass

        def log_message(self, *args) -> None:  # keep the console quiet
            pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Alfred brain HTTP server")
    ap.add_argument("--model", default=None, help="Ollama model (default: brain's default)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8200)
    ap.add_argument(
        "--no-voice",
        action="store_true",
        help="skip loading Whisper/Kokoro (text routes only; disables /chat/audio)",
    )
    args = ap.parse_args()

    url, token = os.environ.get("HA_URL", ""), os.environ.get("HA_TOKEN", "")
    ha_client = WebSocketHAClient(url, token) if url and token else None

    # Calendar/Tasks: the real OAuth-backed GoogleAPIClient is used when a stored
    # token exists (created once by authorize_google.py on the brain). Otherwise
    # GOOGLE_MOCK=1 wires the in-memory mock so the tools can be exercised
    # end-to-end (including over the wire) without a Google account.
    google_client = None
    token_path = os.environ.get("GOOGLE_TOKEN", DEFAULT_TOKEN_PATH)
    creds_path = os.environ.get("GOOGLE_CREDENTIALS", DEFAULT_CREDENTIALS_PATH)
    if os.path.exists(token_path):
        from google_tools import GoogleAPIClient

        google_client = GoogleAPIClient(creds_path, token_path)
    elif os.environ.get("GOOGLE_MOCK"):
        from google_tools import MockGoogleClient

        google_client = MockGoogleClient()

    kwargs = {"ha_client": ha_client, "google_client": google_client}
    if args.model:
        kwargs["model"] = args.model
    brain = Brain(**kwargs)

    # Preload Whisper + Kokoro so the first spoken request isn't slow. Degrade to
    # text-only (no /chat/audio) if the audio deps aren't installed on this brain.
    transcribe_fn = synth_fn = None
    voice_status = "disabled (--no-voice)"
    if not args.no_voice:
        try:
            import voice

            transcribe_fn = voice.make_transcriber()
            synth_fn = voice.make_synthesizer()
            voice_status = "ready (Whisper + Kokoro)"
        except Exception as e:  # noqa: BLE001 - text routes must still work
            voice_status = f"unavailable ({e})"

    httpd = ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(brain, threading.Lock(), transcribe_fn, synth_fn),
    )
    caps = [c for c, on in (("home control", ha_client), ("calendar", google_client)) if on]
    where = ", ".join(caps) if caps else "chat only"
    print(f"Alfred brain on http://{args.host}:{args.port} ({brain.model}, {where}). Ctrl+C to stop.")
    print(f"  Web satellite: open http://<this-host>:{args.port}/ in a browser on the LAN.")
    print(f"  Voice satellite (POST /chat/audio): {voice_status}.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nVery good, sir. Shutting down.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
