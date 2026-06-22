"""M0.5 — Round 2 audition, refining around bm_lewis (the current favourite).

Run with the project venv:
    ~/assistant-env/bin/python "voice_audition_lewis.py"

Tests three in-scope ways to push bm_lewis closer to Arkham Alfred:
  1. Speed variants (a slower, more measured butler cadence).
  2. 50/50 blends of bm_lewis with each other British male voice.
  3. 70/30 lewis-heavy blends (keep lewis's character, borrow some timbre).

Each variant is saved to /tmp/lewis_*.wav and played via afplay. Tell me the
winner (e.g. "lewis at 0.9" or "lewis+george 70/30") and I'll lock it into
assistant.py.
"""
import subprocess

import numpy as np
import soundfile as sf
import torch
from kokoro import KPipeline

SAMPLE = (
    "Good evening, sir. Alfred at your service. "
    "If I may, the Batmobile is fuelled and the kettle is on. "
    "Do try not to get yourself killed before supper."
)
SR = 24000
OTHERS = ["bm_george", "bm_fable", "bm_daniel"]


def blend(a: torch.Tensor, b: torch.Tensor, w: float) -> torch.Tensor:
    """Weighted average of two voice tensors: w*a + (1-w)*b."""
    return a * w + b * (1.0 - w)


def play(pipeline: KPipeline, label: str, voice, speed: float) -> None:
    wav = f"/tmp/lewis_{label}.wav"
    print(f"\n=== {label}  (speed {speed}) ===")
    try:
        chunks = [au for _, _, au in pipeline(SAMPLE, voice=voice, speed=speed)]
    except Exception as e:
        print(f"(skipped {label}: {e})")
        return
    if not chunks:
        print(f"(no audio for {label})")
        return
    sf.write(wav, np.concatenate(chunks), SR)
    print(f"saved {wav} — playing...")
    subprocess.run(["afplay", wav])


def main() -> None:
    p = KPipeline(lang_code="b")
    lewis = p.load_voice("bm_lewis")

    # 1. Speed variants of plain bm_lewis.
    for spd in (1.0, 0.9, 0.85):
        play(p, f"plain_{spd}", lewis, spd)

    # 2 & 3. Blends with each other Brit, at 50/50 and 70/30 (lewis-heavy),
    # spoken at a slightly measured 0.9.
    for other_name in OTHERS:
        other = p.load_voice(other_name)
        short = other_name.replace("bm_", "")
        play(p, f"lewis+{short}_50", blend(lewis, other, 0.5), 0.9)
        play(p, f"lewis+{short}_70", blend(lewis, other, 0.7), 0.9)

    print(
        "\nDone. Re-listen any time, e.g.:  afplay /tmp/lewis_lewis+george_70.wav\n"
        "Tell me the winner and I'll wire it into assistant.py."
    )


if __name__ == "__main__":
    main()
