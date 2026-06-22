"""A thin voice satellite for Alfred — mic in, speaker out, brain does the rest.

This is the voice front-end of the locked "central brain + thin satellites"
architecture. Unlike `assistant.py` (which runs Whisper and Kokoro locally), this
satellite stays *thin*: it captures microphone audio, ships the WAV to the brain's
`/chat/audio` endpoint, and plays back the audio the brain returns. STT (Whisper)
and TTS (Kokoro) both run on the brain, so a satellite needs only light deps —
sounddevice, numpy, and (for hands-free) openwakeword/onnxruntime. No Whisper, no
Kokoro, no LLM here. This is the exact program the Raspberry Pi robot will run;
the Mac is just the first device to run it.

Two modes (mirroring assistant.py):
  * Push-to-talk (default): press Enter to start speaking, Enter again to stop.
  * Hands-free (--hands-free): say the wake word, then speak your command.

    ~/assistant-env/bin/python voice_satellite.py --server http://192.168.1.50:8200
    ~/assistant-env/bin/python voice_satellite.py --hands-free

The capture helpers (record_turn, wake-word loop) are shared from voice.py; the
brain wire is SatelliteClient.ask_audio. On a {"display_url": ...} event we just
print the URL for now — the projector satellite (a Pi kiosk browser pointed at
the brain's /display/* pages) is a later increment.

A StatusIndicator (status.py) is driven at each transition so a headless Pi can
show what Alfred is doing — Listening / Thinking / Speaking — on an OLED. On the
Mac the default console backend just prints those states.
"""
from __future__ import annotations

import argparse

from satellite import DEFAULT_SERVER, SatelliteClient, SatelliteError
from status import (
    ERROR,
    IDLE,
    LISTENING,
    SPEAKING,
    THINKING,
    make_indicator,
)
from voice import (
    SR,
    TTS_SR,
    WAKE_FRAME,
    calibrate_threshold,
    encode_wav,
    listen_for_wake,
    load_wake_model,
    record_command,
    record_turn,
)


def _play(wav_bytes: bytes) -> None:
    """Decode 24 kHz WAV bytes from the brain and play them on the speaker."""
    import sounddevice as sd

    from voice import decode_wav

    audio, sr = decode_wav(wav_bytes)
    sd.play(audio, samplerate=sr)
    sd.wait()


def handle_audio(client: SatelliteClient, wav_bytes: bytes, indicator) -> None:
    """Send captured audio to the brain and speak each sentence as it returns.

    The brain streams a leading transcript, then one audio clip per sentence (so
    Alfred starts speaking before the whole reply exists), then an optional
    display_url. We play audio in order as it arrives — synthesis already
    happened on the brain. The indicator shows THINKING (with the transcript)
    once we know what was heard, then SPEAKING while playing the reply."""
    spoke = False
    for event in client.ask_audio(wav_bytes):
        kind = event[0]
        if kind == "transcript":
            print(f"You: {event[1]}")
            print("Alfred:", end=" ", flush=True)
            indicator.set_state(THINKING, event[1])
        elif kind == "audio":
            _, sentence, wav = event
            print(sentence, end=" ", flush=True)
            if not spoke:
                indicator.set_state(SPEAKING)
            spoke = True
            if wav:
                _play(wav)
        elif kind == "display_url":
            print(f"\n(display: {client.base_url}{event[1]})")
    if spoke:
        print()


def push_to_talk_loop(client: SatelliteClient, indicator) -> None:
    print("Alfred voice satellite — push-to-talk. Ctrl+C to quit.")
    while True:
        try:
            indicator.set_state(LISTENING)
            audio = record_turn()
            handle_audio(client, encode_wav(audio, SR), indicator)
            indicator.set_state(IDLE)
        except SatelliteError as e:
            print(f"\n(error: {e})")
            indicator.set_state(ERROR, str(e))
        except KeyboardInterrupt:
            print("\nVery good, sir. Goodbye.")
            break


def hands_free_loop(client: SatelliteClient, indicator) -> None:
    import sounddevice as sd

    model, wake_key = load_wake_model()
    print(f"Alfred voice satellite — hands-free. Say '{wake_key}', then speak. Ctrl+C to quit.")
    speech_rms = None
    while True:
        try:
            indicator.set_state(IDLE)
            with sd.InputStream(
                samplerate=SR, channels=1, dtype="int16", blocksize=WAKE_FRAME
            ) as stream:
                if speech_rms is None:
                    speech_rms = calibrate_threshold(stream)
                listen_for_wake(stream, model, wake_key)
                print("(wake word detected — listening...)")
                indicator.set_state(LISTENING)
                audio = record_command(stream, speech_rms)
            if audio is None:
                print("(no command heard)")
                model.reset()
                continue
            handle_audio(client, encode_wav(audio, SR), indicator)
            model.reset()
        except SatelliteError as e:
            print(f"\n(error: {e})")
            indicator.set_state(ERROR, str(e))
            model.reset()
        except KeyboardInterrupt:
            print("\nVery good, sir. Goodbye.")
            break


def main() -> None:
    ap = argparse.ArgumentParser(description="Alfred voice satellite — thin mic/speaker front-end")
    ap.add_argument("--server", default=DEFAULT_SERVER, help="brain_server base URL")
    ap.add_argument(
        "--hands-free",
        action="store_true",
        help="listen for a wake word instead of push-to-talk",
    )
    ap.add_argument("--timeout", type=float, default=60.0, help="request timeout (s)")
    ap.add_argument(
        "--indicator",
        choices=("console", "oled", "none"),
        default="console",
        help="state readout: console (default), oled (Pi SSD1306), or none",
    )
    args = ap.parse_args()

    client = SatelliteClient(args.server, timeout=args.timeout)
    try:
        info = client.health()
    except SatelliteError as e:
        raise SystemExit(f"Alfred voice satellite: {e}")
    print(f"Connected to Alfred ({info.get('model')}) at {client.base_url}.")

    indicator = make_indicator(args.indicator)
    try:
        if args.hands_free:
            hands_free_loop(client, indicator)
        else:
            push_to_talk_loop(client, indicator)
    finally:
        indicator.close()


if __name__ == "__main__":
    main()
