"""A thin Alfred satellite — a remote front-end to the central brain.

This is the satellite half of the locked "central brain + thin satellites"
architecture: the brain (LLM + persona + memory + home control) runs once on the
PC via `brain_server.py`; satellites are cheap front-ends scattered around the
house that just ferry text to it and speak/print the reply. This one is a text
REPL, but `SatelliteClient` is the reusable seam a voice satellite would build on
(do STT locally → `client.ask(text)` → TTS the reply).

Stdlib only (urllib) so a satellite needs no heavy deps — just point it at the
brain:

    ~/assistant-env/bin/python satellite.py --server http://192.168.1.50:8200
    ~/assistant-env/bin/python satellite.py            # defaults to localhost:8200
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

DEFAULT_SERVER = "http://localhost:8200"


class SatelliteError(RuntimeError):
    """The brain was unreachable or returned an error."""


class SatelliteClient:
    """Minimal HTTP client for a brain_server. Reusable by any front-end."""

    def __init__(self, base_url: str = DEFAULT_SERVER, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            # the server reports problems as JSON {"error": ...}
            try:
                body = json.loads(e.read() or b"{}")
            except (json.JSONDecodeError, ValueError):
                body = {}
            raise SatelliteError(body.get("error") or f"HTTP {e.code}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise SatelliteError(f"cannot reach brain at {self.base_url}: {e}") from e

    def health(self) -> dict:
        return self._request("/health")

    def ask(self, text: str) -> str:
        reply = self._request("/chat", {"text": text})
        return reply.get("reply", "")

    def ask_stream(self, text: str):
        """Yield Alfred's reply sentence by sentence from /chat/stream as it
        forms, so a voice satellite can start speaking before the whole reply
        exists. Reads the server's newline-delimited JSON ({"sentence": ...} per
        line); a {"error": ...} line is raised as a SatelliteError."""
        url = f"{self.base_url}/chat/stream"
        data = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for raw in resp:  # http.client yields lines as they arrive
                    line = raw.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if "error" in obj:
                        raise SatelliteError(obj["error"])
                    sentence = obj.get("sentence")
                    if sentence:
                        yield sentence
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b"{}")
            except (json.JSONDecodeError, ValueError):
                body = {}
            raise SatelliteError(body.get("error") or f"HTTP {e.code}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise SatelliteError(f"cannot reach brain at {self.base_url}: {e}") from e

    def ask_audio(self, wav_bytes: bytes):
        """POST a WAV (mono 16 kHz) to /chat/audio and yield typed events as the
        brain streams them, so a voice satellite can speak each sentence as it
        arrives without running Whisper or Kokoro itself. Decodes the brain's
        NDJSON into tuples:

            ("transcript", text)         what the brain heard (first event)
            ("audio", sentence, wav)     one per sentence; wav is 24 kHz WAV
                                         bytes (None if synthesis was empty)
            ("display_url", url)         a view to show (trailing, optional)

        A {"error": ...} line is raised as a SatelliteError, as are connection
        failures."""
        import base64

        url = f"{self.base_url}/chat/audio"
        req = urllib.request.Request(
            url, data=wav_bytes, headers={"Content-Type": "audio/wav"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for raw in resp:  # http.client yields lines as they arrive
                    line = raw.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if "error" in obj:
                        raise SatelliteError(obj["error"])
                    if "transcript" in obj:
                        yield ("transcript", obj["transcript"])
                    elif "sentence" in obj:
                        audio_b64 = obj.get("audio")
                        wav = base64.b64decode(audio_b64) if audio_b64 else None
                        yield ("audio", obj["sentence"], wav)
                    elif "display_url" in obj:
                        yield ("display_url", obj["display_url"])
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b"{}")
            except (json.JSONDecodeError, ValueError):
                body = {}
            raise SatelliteError(body.get("error") or f"HTTP {e.code}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise SatelliteError(f"cannot reach brain at {self.base_url}: {e}") from e


def repl(client: SatelliteClient) -> None:
    try:
        info = client.health()
    except SatelliteError as e:
        raise SystemExit(f"Alfred satellite: {e}")
    caps = [c for c, on in (("home control", info.get("home_control")),
                            ("calendar", info.get("calendar"))) if on]
    control = ", ".join(caps) if caps else "chat only"
    print(f"Connected to Alfred ({info.get('model')}, {control}) at {client.base_url}.")
    print("Type a message, or Ctrl+C / 'quit' to exit.")
    while True:
        try:
            text = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nVery good, sir. Goodbye.")
            return
        if not text:
            continue
        if text.lower() in ("quit", "exit"):
            print("Very good, sir. Goodbye.")
            return
        try:
            print("Alfred:", end=" ", flush=True)
            for sentence in client.ask_stream(text):
                print(sentence, end=" ", flush=True)
            print()
        except SatelliteError as e:
            print(f"\n(error: {e})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Alfred satellite — remote brain front-end")
    ap.add_argument("--server", default=DEFAULT_SERVER, help="brain_server base URL")
    ap.add_argument("--timeout", type=float, default=60.0, help="request timeout (s)")
    args = ap.parse_args()
    repl(SatelliteClient(args.server, timeout=args.timeout))


if __name__ == "__main__":
    main()
