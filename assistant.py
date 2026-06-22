"""Closed-loop local voice assistant (Alfred).

Two modes:
  * Push-to-talk (default): press Enter to start speaking, Enter again to stop.
  * Hands-free (--hands-free): say the wake word, then speak your command.

Whisper transcribes your speech (mlx-whisper on Mac, faster-whisper elsewhere —
see voice.STT_BACKEND), the local LLM answers in Alfred's voice, and Kokoro (a
70/30 blend of the British male voices bm_lewis and bm_george) speaks the reply
aloud. Ctrl+C to quit.

This app does its STT and TTS locally; the shared audio building blocks live in
`voice.py` (also used by the brain's audio endpoint and the voice satellite).

Run with the project venv:
    ~/assistant-env/bin/python "assistant.py"               # push-to-talk
    ~/assistant-env/bin/python "assistant.py" --hands-free  # wake word

Alfred remembers durable facts about you across sessions in ~/.alfred/profile.md
(tell him "remember ..." or edit the file directly).

To retune the voice, edit KOKORO_BLEND / KOKORO_SPEED in voice.py. To use the
custom "Hey Alfred" wake word once trained, point voice.WAKE_MODEL at its .onnx
file. Tip: use earbuds so Alfred's voice doesn't bleed into the mic hands-free.
"""
import argparse
import os
import queue
import threading

import sounddevice as sd
from kokoro import KPipeline

from brain import Brain
from ha_tools import WebSocketHAClient
from voice import (
    KOKORO_LANG,
    SR,
    TTS_SR,
    WAKE_FRAME,
    _synth,
    build_voice,
    calibrate_threshold,
    listen_for_wake,
    load_wake_model,
    make_transcriber,
    record_command,
    record_turn,
)

MODEL = "llama3.2:3b"  # bump to "qwen2.5:7b" on the brain for reliable HA tools

# Persona, cross-session memory, and the LLM turn live in brain.py (the Brain
# class). Set HA_URL/HA_TOKEN below to let Alfred control Home Assistant devices.
HA_URL = os.environ.get("HA_URL", "")      # e.g. "http://localhost:8123"
HA_TOKEN = os.environ.get("HA_TOKEN", "")  # long-lived access token


def speak_stream(sentences, tts: KPipeline, voice) -> None:
    """Speak sentences as they arrive, synthesising the next while the current
    one plays. A background thread pulls from the (possibly slow) sentence
    generator and synthesises; the main thread plays buffers in order. This
    starts audio after the first sentence instead of the whole reply."""
    audio_q: "queue.Queue" = queue.Queue(maxsize=8)

    def producer():
        for sentence in sentences:
            audio = _synth(sentence, tts, voice)
            if audio is not None:
                audio_q.put(audio)
        audio_q.put(None)  # sentinel: no more audio

    worker = threading.Thread(target=producer, daemon=True)
    worker.start()
    while True:
        audio = audio_q.get()
        if audio is None:
            break
        sd.play(audio, samplerate=TTS_SR)
        sd.wait()
    worker.join()


def handle_turn(text, brain, tts, voice):
    """Run one user turn through the brain (LLM + memory + tools), speaking each
    sentence as soon as it's ready to minimise time-to-first-word."""
    print("You:", text)
    print("Alfred:", end=" ", flush=True)

    def spoken_sentences():
        for sentence in brain.chat_stream(text):
            print(sentence, end=" ", flush=True)
            yield sentence

    speak_stream(spoken_sentences(), tts, voice)
    print()


def push_to_talk_loop(brain, tts, voice, transcribe) -> None:
    print("Alfred — push-to-talk. Ctrl+C to quit.")
    while True:
        try:
            text = transcribe(record_turn())
            if not text:
                print("(heard nothing)")
                continue
            handle_turn(text, brain, tts, voice)
        except KeyboardInterrupt:
            print("\nVery good, sir. Goodbye.")
            break


def hands_free_loop(brain, tts, voice, transcribe) -> None:
    model, wake_key = load_wake_model()
    print(f"Alfred — hands-free. Say the wake word ('{wake_key}'), then speak. Ctrl+C to quit.")
    speech_rms = None
    while True:
        try:
            with sd.InputStream(
                samplerate=SR, channels=1, dtype="int16", blocksize=WAKE_FRAME
            ) as stream:
                if speech_rms is None:
                    speech_rms = calibrate_threshold(stream)
                listen_for_wake(stream, model, wake_key)
                print("(wake word detected — listening...)")
                audio = record_command(stream, speech_rms)
            if audio is None:
                print("(no command heard)")
                model.reset()
                continue
            text = transcribe(audio)
            if not text:
                print("(heard nothing)")
                model.reset()
                continue
            handle_turn(text, brain, tts, voice)
            model.reset()
        except KeyboardInterrupt:
            print("\nVery good, sir. Goodbye.")
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="Alfred — local voice assistant")
    parser.add_argument(
        "--hands-free",
        action="store_true",
        help="listen for a wake word instead of push-to-talk",
    )
    args = parser.parse_args()

    tts = KPipeline(lang_code=KOKORO_LANG)  # loads Kokoro once at startup
    voice = build_voice(tts)
    transcribe = make_transcriber()  # loads the STT model once at startup

    ha_client = WebSocketHAClient(HA_URL, HA_TOKEN) if HA_URL and HA_TOKEN else None
    brain = Brain(
        model=MODEL,
        ha_client=ha_client,
        on_remember=lambda fact: print(f"(remembered: {fact})"),
    )
    if brain.facts:
        print(f"(loaded {len(brain.facts)} remembered fact(s) from {brain.profile_path})")
    if ha_client:
        print(f"(home control enabled — Home Assistant at {HA_URL})")

    if args.hands_free:
        hands_free_loop(brain, tts, voice, transcribe)
    else:
        push_to_talk_loop(brain, tts, voice, transcribe)


if __name__ == "__main__":
    main()
