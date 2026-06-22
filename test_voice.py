"""Tests for the shared audio toolkit (voice.py).

Run:  ~/assistant-env/bin/python -m pytest -q

Only the pure, hardware-free parts are exercised here: the WAV (de)coding that is
the satellite/brain wire format, and the module's format constants. No mic, no
speaker, no Whisper, no Kokoro — those are loaded lazily inside the functions that
need them and never touched by importing voice.py.
"""
import numpy as np

import voice


def test_encode_decode_wav_round_trip():
    # a short ramp so we can tell silence from signal after the round-trip
    original = np.linspace(-0.5, 0.5, 2400, dtype="float32")
    wav = voice.encode_wav(original, voice.TTS_SR)
    assert isinstance(wav, bytes) and wav[:4] == b"RIFF"  # real WAV container
    decoded, sr = voice.decode_wav(wav)
    assert sr == voice.TTS_SR
    assert decoded.shape == original.shape
    # 16-bit PCM is lossy; assert it's close, not identical
    assert np.max(np.abs(decoded - original)) < 1e-3


def test_encode_wav_honours_sample_rate():
    audio = np.zeros(1600, dtype="float32")
    _, sr = voice.decode_wav(voice.encode_wav(audio, voice.SR))
    assert sr == voice.SR


def test_audio_format_constants():
    assert voice.SR == 16000      # capture / STT input
    assert voice.TTS_SR == 24000  # Kokoro output
    assert voice.WAKE_FRAME == 1280
    # the locked Alfred voice blend sums to 1.0
    assert abs(sum(voice.KOKORO_BLEND.values()) - 1.0) < 1e-9
    assert voice.KOKORO_LANG == "b"
