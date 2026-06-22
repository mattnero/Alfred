"""M0.3 — Voice in. Record a few seconds of mic audio and transcribe it with mlx-whisper.

Run with the project venv:
    ~/assistant-env/bin/python "record_test.py"

First run downloads the Whisper model (needs internet once). macOS will prompt for
microphone access for your terminal app the first time — grant it, then re-run.
"""
import sys
import sounddevice as sd
import soundfile as sf
import mlx_whisper

SR = 16000
SECONDS = 5
WAV = "/tmp/test.wav"
# small = better quality; base = faster/smaller fallback if small 404s or is slow.
WHISPER_REPO = "mlx-community/whisper-small-mlx"
WHISPER_FALLBACK = "mlx-community/whisper-base-mlx"


def main() -> None:
    try:
        default_in = sd.query_devices(kind="input")["name"]
    except Exception as e:
        print(f"No input device found: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Input device: {default_in}")
    print(f"Recording {SECONDS} seconds — speak now...")
    audio = sd.rec(SECONDS * SR, samplerate=SR, channels=1, dtype="float32")
    sd.wait()
    sf.write(WAV, audio, SR)

    print("Transcribing...")
    try:
        result = mlx_whisper.transcribe(WAV, path_or_hf_repo=WHISPER_REPO)
    except Exception as e:
        print(f"(small model failed: {e}; falling back to base)")
        result = mlx_whisper.transcribe(WAV, path_or_hf_repo=WHISPER_FALLBACK)

    text = result.get("text", "").strip()
    print("You said:", text if text else "(heard nothing)")


if __name__ == "__main__":
    main()
