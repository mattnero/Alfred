"""Shared audio toolkit for Alfred — capture, STT, TTS, and WAV (de)coding.

These are the building blocks the voice pieces have in common, factored out of
`assistant.py` so three callers share one implementation:

  * the local voice app (`assistant.py`)         — capture + STT + TTS, all local
  * the brain's audio endpoint (`brain_server`)  — STT (Whisper) + TTS (Kokoro)
  * the voice satellite (`voice_satellite.py`)   — capture + playback only (thin)

The split matters for the "central brain + thin satellites" architecture: a
satellite never needs Whisper or Kokoro — it only captures the mic, ships the
WAV to the brain, and plays back the audio the brain returns. So every heavy
dependency (numpy, soundfile, sounddevice, kokoro, whisper, openwakeword) is
imported *inside* the function that needs it. Importing this module costs
nothing — a satellite install needs only sounddevice/numpy/openwakeword, and the
brain only loads Whisper/Kokoro when its audio endpoint is first used.
"""
from __future__ import annotations

import importlib.util
import os
import tempfile

# --- Audio formats ----------------------------------------------------------
SR = 16000      # capture / STT input sample rate
TTS_SR = 24000  # Kokoro output sample rate
WAV = os.path.join(tempfile.gettempdir(), "alfred_turn.wav")

# --- Speech-to-text backend (cross-platform) --------------------------------
# "auto" picks mlx-whisper on Apple Silicon (Metal) and faster-whisper elsewhere
# (Windows/Linux). On the 8GB-VRAM PC, faster-whisper runs on CPU so the GPU
# stays free for the 7B LLM. Override STT_BACKEND to force a specific engine.
STT_BACKEND = "auto"  # "auto" | "mlx" | "faster"
WHISPER_REPO = "mlx-community/whisper-small-mlx"     # mlx backend
WHISPER_FALLBACK = "mlx-community/whisper-base-mlx"  # mlx backend
FASTER_WHISPER_MODEL = "small"   # faster backend
FASTER_WHISPER_DEVICE = "cpu"    # keep 8GB VRAM free for the LLM
FASTER_WHISPER_COMPUTE = "int8"  # faster backend quantization

# --- Text-to-speech (Kokoro) ------------------------------------------------
# Locked Tier A Alfred voice: a 70/30 blend of bm_lewis and bm_george, spoken at
# a measured 0.9 for a dignified butler cadence (M0.5 audition).
KOKORO_LANG = "b"  # 'b' = British English
KOKORO_BLEND = {"bm_lewis": 0.7, "bm_george": 0.3}
KOKORO_SPEED = 0.9

# --- Hands-free wake word (openWakeWord) ------------------------------------
# WAKE_MODEL is either a bundled pretrained name ("hey_jarvis", "alexa", ...) or
# a path to a custom .onnx model. Swap to the trained "Hey Alfred" model here.
WAKE_MODEL = "hey_jarvis"
WAKE_THRESHOLD = 0.5      # detection confidence (0-1) needed to trigger
WAKE_FRAME = 1280         # 80 ms @ 16 kHz — openWakeWord's expected chunk size
CMD_START_TIMEOUT = 6.0   # seconds to wait for you to start speaking after wake
CMD_SILENCE_HANG = 1.2    # seconds of trailing silence that ends a command
CMD_MAX_SECONDS = 15.0    # hard cap on a single spoken command


# --- WAV (de)coding — the satellite/brain wire format -----------------------
def encode_wav(audio, sr: int = TTS_SR) -> bytes:
    """Encode a float32 mono numpy buffer as 16-bit PCM WAV bytes."""
    import io

    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def decode_wav(data: bytes):
    """Decode WAV bytes to (float32 numpy buffer, sample_rate)."""
    import io

    import soundfile as sf

    audio, sr = sf.read(io.BytesIO(data), dtype="float32")
    return audio, sr


# --- Capture (voice app + satellite) ----------------------------------------
def record_turn():
    """Push-to-talk capture: Enter to start, Enter to stop. Returns float32 mono."""
    import numpy as np
    import sounddevice as sd

    input("\n[Press Enter, then speak] ")
    frames: list = []
    stream = sd.InputStream(
        samplerate=SR,
        channels=1,
        dtype="float32",
        callback=lambda indata, n, t, s: frames.append(indata.copy()),
    )
    with stream:
        input("[Recording... press Enter to stop] ")
    if not frames:
        return np.zeros(1, dtype="float32")
    return np.concatenate(frames, axis=0)[:, 0]


def _rms(frame) -> float:
    import numpy as np

    if frame.size == 0:
        return 0.0
    f = frame.astype(np.float32)
    return float(np.sqrt(np.mean(f * f)))


def load_wake_model():
    """Load the openWakeWord model (pretrained name or path to a custom .onnx)."""
    from openwakeword.model import Model

    spec = WAKE_MODEL
    if spec.endswith(".onnx") or os.sep in spec:
        path = spec if os.path.isabs(spec) else os.path.join(os.path.dirname(__file__), spec)
        if not os.path.exists(path):
            raise SystemExit(
                f"Wake model not found: {path}\n"
                "Train a custom model (see README 'M0.6 — Hands-free Wake Word') "
                "or set WAKE_MODEL to a pretrained name like 'hey_jarvis'."
            )
        spec = path
    model = Model(wakeword_models=[spec], inference_framework="onnx")
    key = list(model.models.keys())[0]
    return model, key


def calibrate_threshold(stream) -> float:
    """Sample ~0.5s of ambient noise to set a speech-detection RMS threshold."""
    rmss = [_rms(stream.read(WAKE_FRAME)[0][:, 0]) for _ in range(int(0.5 * SR / WAKE_FRAME))]
    ambient = sorted(rmss)[len(rmss) // 2] if rmss else 0.0
    return max(ambient * 4.0, 500.0)


def listen_for_wake(stream, model, wake_key) -> None:
    """Block until the wake word is detected on the open input stream."""
    while True:
        frame = stream.read(WAKE_FRAME)[0][:, 0]
        if model.predict(frame).get(wake_key, 0.0) >= WAKE_THRESHOLD:
            model.reset()
            return


def record_command(stream, speech_rms: float):
    """Record until the user stops speaking; None if nothing was said."""
    import numpy as np

    frames, started, silence, elapsed = [], False, 0.0, 0.0
    dur = WAKE_FRAME / SR
    while True:
        frame = stream.read(WAKE_FRAME)[0][:, 0]
        frames.append(frame.copy())
        elapsed += dur
        if _rms(frame) >= speech_rms:
            started, silence = True, 0.0
        elif started:
            silence += dur
        if started and silence >= CMD_SILENCE_HANG:
            break
        if not started and elapsed >= CMD_START_TIMEOUT:
            return None
        if elapsed >= CMD_MAX_SECONDS:
            break
    return np.concatenate(frames) if frames else None


# --- Speech-to-text (brain) -------------------------------------------------
def make_transcriber():
    """Return a transcribe(audio)->str closure using the best STT backend here.

    Mac: mlx-whisper (Metal). Windows/Linux: faster-whisper (CPU int8 to spare
    VRAM). The model is loaded once and captured in the closure. ``audio`` is a
    float32 mono numpy buffer at ``SR``.
    """
    import soundfile as sf

    backend = STT_BACKEND
    if backend == "auto":
        backend = "mlx" if importlib.util.find_spec("mlx_whisper") else "faster"

    if backend == "mlx":
        import mlx_whisper

        def transcribe(audio) -> str:
            sf.write(WAV, audio, SR)
            try:
                result = mlx_whisper.transcribe(WAV, path_or_hf_repo=WHISPER_REPO)
            except Exception:
                result = mlx_whisper.transcribe(WAV, path_or_hf_repo=WHISPER_FALLBACK)
            return result.get("text", "").strip()

        return transcribe

    if backend == "faster":
        from faster_whisper import WhisperModel

        model = WhisperModel(
            FASTER_WHISPER_MODEL,
            device=FASTER_WHISPER_DEVICE,
            compute_type=FASTER_WHISPER_COMPUTE,
        )

        def transcribe(audio) -> str:
            sf.write(WAV, audio, SR)
            segments, _ = model.transcribe(WAV)
            return "".join(seg.text for seg in segments).strip()

        return transcribe

    raise SystemExit(f"Unknown STT_BACKEND: {STT_BACKEND!r} (use auto/mlx/faster)")


# --- Text-to-speech (brain + voice app) -------------------------------------
def build_voice(tts):
    """Weighted average of the blend's voice tensors -> a single Alfred voice."""
    voice = None
    for name, weight in KOKORO_BLEND.items():
        tensor = tts.load_voice(name) * weight
        voice = tensor if voice is None else voice + tensor
    return voice


def _synth(text: str, tts, voice):
    """Render one piece of text to a single audio buffer (None if empty)."""
    import numpy as np

    parts = [audio for _, _, audio in tts(text, voice=voice, speed=KOKORO_SPEED)]
    return np.concatenate(parts) if parts else None


def make_synthesizer():
    """Return a synth(text)->WAV-bytes closure; loads Kokoro once.

    Returns 24 kHz PCM WAV bytes (or None for empty text) so the brain can ship
    a satellite ready-to-play audio without the satellite needing Kokoro.
    """
    from kokoro import KPipeline

    tts = KPipeline(lang_code=KOKORO_LANG)
    voice = build_voice(tts)

    def synth(text: str):
        audio = _synth(text, tts, voice)
        return encode_wav(audio, TTS_SR) if audio is not None else None

    return synth
