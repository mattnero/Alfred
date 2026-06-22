"""M0.5 — Voice audition. Generate the same Alfred line in each British male
Kokoro voice so you can pick and lock the one that sounds most like Alfred.

Run with the project venv:
    ~/assistant-env/bin/python "voice_audition.py"

First run downloads the Kokoro model + voice tensors (needs internet once).
It writes one WAV per voice to /tmp and plays each through afplay so you can
compare them back-to-back. The locked pick goes into assistant.py (M0.5).
"""
import subprocess

import numpy as np
import soundfile as sf
from kokoro import KPipeline

# British English voices to audition. bm_george is the locked default; the
# others are the alternates noted in the plan.
VOICES = ["bm_george", "bm_daniel", "bm_lewis", "bm_fable"]

SAMPLE = (
    "Good evening, sir. Alfred at your service. "
    "If I may, the Batmobile is fuelled and the kettle is on. "
    "Do try not to get yourself killed before supper."
)

SR = 24000  # Kokoro outputs 24 kHz


def main() -> None:
    pipeline = KPipeline(lang_code="b")  # 'b' = British English
    for voice in VOICES:
        wav = f"/tmp/alfred_{voice}.wav"
        print(f"\n=== {voice} ===")
        try:
            audio_chunks = [
                audio for _, _, audio in pipeline(SAMPLE, voice=voice, speed=1.0)
            ]
        except Exception as e:
            print(f"(skipped {voice}: {e})")
            continue
        if not audio_chunks:
            print(f"(no audio produced for {voice})")
            continue
        sf.write(wav, np.concatenate(audio_chunks), SR)
        print(f"saved {wav} — playing...")
        subprocess.run(["afplay", wav])
    print(
        "\nDone. Re-listen any time with:  afplay /tmp/alfred_bm_george.wav\n"
        "Tell me which voice to lock in and I'll wire it into assistant.py."
    )


if __name__ == "__main__":
    main()
