# AI Assistant Robot — Research & Build

> Living planning doc. Kickoff decisions: spend what it takes (capability over cost); fastest-to-result stack (Python-heavy AI, glue as needed); balanced roadmap (core first, stretch goals scoped throughout); persona is Alfred (Tier A voice, default Kokoro `bm_george` — locked 2026-06-19).

---

## Vision

Build a self-contained AI assistant with a physical form that:

- Runs its own LLM locally — no internet dependency, no subscription
- Listens and talks back (voice in / voice out)
- Has the persona of **Alfred Pennyworth** (Batman: Arkham incarnation) — a composed, eloquent English butler with dry wit and unfailing loyalty (see [Persona & Voice](#persona--voice--the-alfred-treatment))
- Controls **home automation** (planned) — voice commands act on local smart-home devices, not just answer questions
- Reachable from **multiple devices/rooms** (planned) — speak to Alfred from satellites, not only the robot itself
- **Stretch goal:** projects requested images via a built-in pico projector instead of a screen
- **Super stretch goal:** the whole thing lives in a small drone that can fly to the right distance from a wall and project on demand

---

## Reality Check

- The **core** (local LLM + voice) is very achievable today on a single small board. This is where to start.
- The **projector** is achievable but constrained. Pico projectors are dim (tens to a few hundred lumens), so expect to need a dim room. Projecting a *retrieved* image is easy; generating images locally on demand (Stable Diffusion) is heavy and slow on small hardware.
- The **drone** is the hard part, and not for software reasons:
  - Payload: compute + projector + battery is heavy; small drones carry very little.
  - Flight time: every added gram cuts already-short flight times (often under 15 minutes).
  - Projection while airborne: a hovering drone vibrates and drifts; a stable projected image without a heavy gimbal is essentially impossible.
  - Safety and regulations: indoor flight near walls and people, plus aviation rules.
  - **Recommendation:** treat the drone as a separate research track far down the line. The assistant should be excellent as a stationary or rolling device first.

---

## System Architecture

The whole pipeline runs on one device:

| Component | Role |
|-----------|------|
| Wake word | Always-listening tiny model that triggers on a keyword |
| STT | Converts speech to text |
| LLM | Generates the response (the brain) |
| TTS | Speaks the response aloud |
| Visual output (stretch) | Projector renders requested or generated images |
| Orchestration | Wires mic → STT → LLM → TTS → speaker and manages state |

---

## Architecture Decision — Hybrid (LOCKED 2026-06-20)

**Decision: a hybrid of a custom "Alfred brain" plus Home Assistant for device control.** Home Assistant is *not* the brain and does *not* own Alfred's persona/voice — it's the device control plane. These are three separate decisions, resolved as follows:

| Decision | Choice | Why |
|---|---|---|
| **Device control** | **Home Assistant** | It already speaks Zigbee/Z-Wave/Matter/WiFi/etc. Rebuilding per-brand device integrations is a bottomless pit. Alfred controls devices by calling HA's REST/WebSocket API as LLM tools. |
| **Voice pipeline + persona** | **Custom (`assistant.py`), not HA Assist** | Protects Alfred's character and the locked Kokoro voice blend. HA Assist would constrain the persona and make the custom voice a fight (HA's native TTS is Piper; Kokoro would need a custom Wyoming wrapper). |
| **LLM topology** | **Central brain + thin satellites** (start), brain-per-robot deferred | Only one device today; central gives free unified memory and one upgrade point. Brain-per-robot stays *possible* (the architecture allows promoting a robot to its own brain later) but each brain needs its own GPU/Jetson and a memory-sync strategy — pay that cost only when there are actually multiple robots. |

**Concrete shape:**
- **Alfred brain** = `assistant.py` grown into an always-on service (Ollama + Whisper + Kokoro + memory + persona). One box — the Phase 1 Jetson.
- **Home Assistant** = separate host (Pi/container), owns all devices. Alfred invokes HA actions as tools.
- **Robots / rooms** = satellites (mic + speaker) that stream audio to the central brain.

**Implications for the roadmap:**
- The assistant core must become a **network service/API** (so satellites and the HA integration can reach it) rather than a single self-contained binary — settle this API shape before/early in Phase 1.
- Alfred needs a **reliable tool/function-calling model** to drive HA; the current `llama3.2:3b` may need to grow (e.g. Qwen2.5 7B) for dependable home control — validate during Phase 1.

---

## Persona & Voice — The Alfred Treatment

Goal: the assistant should feel like **Alfred Pennyworth** (the *Batman: Arkham* incarnation) — a composed, eloquent English butler with dry wit, unfailing loyalty, and the occasional note of paternal concern.

This splits into two **independent** problems with very different difficulty. The key insight: **Alfred is ~80% writing, ~20% timbre.** The system prompt does most of the work; the voice just supplies the accent and gravitas.

### 1. Personality (what he says) — easy, free, do this first

This is entirely a **system-prompt** change on the LLM. No new hardware, no new models, works identically on the Mac (Phase 0) and the Jetson (Phase 1). Alfred's verbal fingerprint:

- Formal, precise British diction; complete sentences; never slangy.
- Addresses you as **"sir"** (or "Master [Name]") — pick your form of address.
- Dry, understated wit; gentle irony rather than overt jokes.
- Loyal and solicitous; voices tactful concern ("If I may, sir...", "Might I suggest...").
- Elegant *and* economical — a butler does not ramble.

Drop-in system prompt to start from:

```
You are Alfred, a personal AI assistant modeled on Alfred Pennyworth —
a refined English butler. Address the user as "sir". Speak in precise,
formal British English with dry, understated wit and unfailing courtesy.
Offer gentle, tactful counsel ("If I may, sir..."). Be loyal, calm, and
concise — never verbose, never slangy. Keep replies short and easily
spoken aloud, as they will be read by a text-to-speech voice.
```

> Tuning tip: keep replies short. TTS makes long, ornate sentences feel slow to hear. Alfred is eloquent *and* brief.

### 2. Voice (how he sounds) — DECIDED (2026-06-22)

**Tier A soundalike is the permanent voice. Voice training/cloning is dropped.**

The locked voice is a **Kokoro `bm_lewis` + `bm_george` 70/30 blend at speed 0.9** — a refined, RP-accented British male that reads as an older, well-spoken butler. Kokoro is tiny (<2GB), fast, and high quality (it tops the TTS Arena despite its size); `assistant.py` averages the two voices' tensors at startup (`KOKORO_BLEND` / `KOKORO_SPEED`). Piper `en_GB-alan-medium` / the RP speakers in `en_GB-vctk-medium` remain a lighter fallback.

**Why training was dropped:** the earlier plan was to fine-tune a single-speaker model (StyleTTS2) to get closer to the actual Arkham-Alfred sound (actor **Martin Jarvis**). The user has decided the soundalike blend is good enough, so the StyleTTS2 fine-tune, the Jarvis dataset-sourcing work, and the one-time 24GB training GPU are **all off the plan.** This was never a rights/ethics question (private home-only use) — it's simply "the current voice is good enough."

**The payoff is hardware.** The 24GB-GPU requirement in the old plan was driven by *voice training*, not by running the assistant. With training gone, the brain only ever does **inference** — 7B LLM + Whisper (STT) + Kokoro (TTS) — which is far lighter and makes an embeddable, battery-powered brain realistic. See [Phase 1 — Deep Dive](#phase-1--deep-dive) for the revised hardware.

> *Historical (dropped):* Tier B/C aimed at an offline StyleTTS2 fine-tune (≈4 hrs of clean single-speaker audio, 24GB GPU for the one-time training), optionally on isolated Jarvis game audio. Recorded here only so the decision trail is clear; it is not active work.

---

## Phased Roadmap

### Phase 0 — Software Prototype (no new hardware)
Goal: a working voice-to-LLM loop on your existing computer.
- Install Ollama, run a small model, chat via terminal
- Add Whisper (STT) and Piper (TTS) to make it voice-driven
- **Outcome:** prove the pipeline before spending on hardware

### Phase 1 — Headless Device (the brain in a box)
Goal: the same loop running on dedicated, portable hardware.
- NVIDIA Jetson Orin for GPU-accelerated local LLM
- USB mic array plus speaker; add a wake word
- **Outcome:** it talks back with no laptop attached

### Phase 2 — Physical Form
Goal: give it a body.
- 3D-printed enclosure, power (battery + regulation), power button, status LED
- Optional: simple movement (head turn, or a wheeled base)
- **Outcome:** a thing that sits on a desk and is the assistant

### Phase 3 — Projector (stretch)
Goal: project images on request.
- Add a pico/DLP projector module driven over HDMI or USB
- Start with projecting retrieved images; later attempt local image generation
- **Outcome:** "show me X" puts an image on the wall

### Phase 4 — Drone (super stretch, separate track)
Goal: airborne projection. Long-horizon R&D.
- Start by studying payload and flight-time tradeoffs and existing dev drone platforms
- Prototype projection-from-hover stability before committing money
- **Outcome:** an honest go/no-go after small experiments

---

## Software Stack — Fastest-to-Result Picks

| Component | Pick | Notes |
|-----------|------|-------|
| LLM runtime | Ollama (easiest) or llama.cpp (more control) | |
| Models (quantized) | Llama 3.2 3B, Qwen2.5 7B, Phi-3.5 | Pick by speed and quality on your board |
| STT | faster-whisper or whisper.cpp | |
| Wake word | openWakeWord (free) or Picovoice Porcupine | |
| TTS | Kokoro (`bm_george` British male) or Piper (`en_GB-alan`) | British voice for the Alfred persona — see [Persona & Voice](#persona--voice--the-alfred-treatment) |
| Orchestration | Python glue script first | Consider a framework later |

> The ecosystem is Python-first. You can wrap or reimplement pieces in .NET later (LLamaSharp, Whisper.net) once the design is proven — don't fight the ecosystem during prototyping.

---

## First Steps (no purchase required)

1. Install Ollama, pull a small model (e.g. `llama3.2`), chat in the terminal. Confirm it feels useful.
2. Add voice: get Whisper transcribing your mic, and Piper speaking text back. Wire mic → STT → LLM → TTS into one script.
3. Measure: note response latency and quality. This tells you how much GPU you actually need.
4. Decide form factor: stationary desk device vs. wheeled vs. (eventually) drone. This drives every later choice.
5. Then buy the Phase 1 board and move the working software onto it.

---

## Open Questions

- Capabilities: Q&A plus **home automation** (now a goal), reminders, timers, etc. — confirms the need for an LLM "actions"/tool-calling layer.
- ~~**Architecture / Backbone / brain-host**~~ — **RESOLVED (2026-06-20):** hybrid — custom Alfred brain + Home Assistant for device control, central brain + satellites. See [Architecture Decision](#architecture-decision--hybrid-locked-2026-06-20).
- **Model for actions:** which model gives reliable tool/function calling (Qwen2.5, Llama 3.1/3.2)? The 3B target may need to grow for dependable home-control. (Now scoped to Phase 1 validation.)
- Movement: does it need to move, or is a stationary device fine?
- Always-listening vs. push-to-talk (privacy and power tradeoffs)
- Projector: is requiring a dim room acceptable?
- Wake word: which trigger phrase? (Persona is settled — Alfred. Default TTS voice: Kokoro `bm_george`.)
- Form of address: "sir" vs. "Master [Name]" for the Alfred persona?
- ~~Is a Tier-A soundalike voice enough, or is a custom voice (Tier B) worth the extra weight later?~~ — **RESOLVED (2026-06-22):** Tier A soundalike is the permanent voice; voice training/cloning dropped. See [Voice — DECIDED](#2-voice-how-he-sounds--decided-2026-06-22).

---

## Hardware Shopping List

| Item | Purpose | Phase | Est. Cost (USD) | Notes |
|------|---------|-------|----------------|-------|
| NVIDIA Jetson Orin Nano Super Dev Kit | Main compute / local LLM brain | 1 | ~$249 | 8GB, USB-C PD powered; cheap proof of the Jetson path. Step up to **Orin NX 16GB (~$899)** for the permanent brain (holds 7B + Whisper + Kokoro resident). See [Phase 1 — Deep Dive](#phase-1--deep-dive). |
| High-speed storage (256GB+ microSD or NVMe SSD) | OS + model storage | 1 | $30–80 | Models are several GB each; NVMe is much faster |
| ReSpeaker USB Mic Array (or 4-Mic) | Far-field voice capture | 1 | $35–70 | Good for across-the-room listening |
| Small powered speaker (USB or 3.5mm) | Voice output | 1 | $15–40 | Any compact powered speaker |
| Jetson power supply | Power | 1 | ~$20 | Match the Jetson kit spec |
| Cooling (fan / heatsink) | Thermal | 1 | $0–25 | Orin Nano Super kit usually includes a fan |
| 3D-printed enclosure | Body | 2 | $20–60 | Or design your own |
| USB-C PD power bank (74–99Wh) | Untethered/battery power for the brain | 1–2 | $60–150 | Orin Nano Super runs natively off USB-C PD (~15W → 4–6 hrs). Orin NX (DC barrel) needs a USB-C-PD→12V cable or small power station. Want UPS behaviour? Confirm clean pass-through charging. |
| Status LED + small button | UX / on-off + state | 2 | ~$10 | Basic interaction feedback |
| Pan/tilt servo mount (optional) | Look-at-you / aim projector | 2–3 | $20–50 | Adds movement and projector aiming |
| Pico / DLP projector module (HDMI or USB) | Project images | 3 | $80–300 | Brightness varies; expect a dim-room requirement |
| Drone dev platform | Airborne base | 4 | TBD — research first | Do not buy until Phase 3 is proven |

**Ballpark totals:** a working assistant (Phases 1–2) lands around $400–600. The projector adds $80–300. The drone is a separate, later investment to scope only after the projector works.

---

## Phase 0 — Deep Dive

### Why Phase 0 First
- Free and reversible. Learn the whole pipeline before committing to any hardware.
- De-risks expensive decisions: the model size that feels good on your Mac directly informs which board to buy in Phase 1.
- Separates "is this idea fun and useful?" from "can I build the hardware?"

### Success Criteria
- Speak to your Mac → it transcribes → a local model answers → you hear the spoken reply — fully offline.
- Measured round-trip latency and judged answer quality.
- Decided which model size to target and what form factor you want.
- Locked the Alfred voice (Tier A soundalike; default Kokoro `bm_george`) after a quick listening test.

### Your Hardware: Apple Silicon Mac
- The Metal GPU and unified memory let Ollama run models well with no setup beyond installing the app.
- Built-in mic and speakers mean no audio hardware to source (use earbuds to avoid speaker-into-mic echo during early testing).
- No CUDA — prefer Apple-Silicon-native speech tools: **mlx-whisper** or **whisper.cpp (Metal)** for STT.

### Software Stack (Mac)

| Component | Pick |
|-----------|------|
| LLM runtime | Ollama (Metal-accelerated). LM Studio as GUI alternative. |
| STT | mlx-whisper or whisper.cpp with Metal. Start with `base` or `small`. |
| TTS | Start with macOS `say` (try the `Daniel` British voice: `say -v Daniel`) for an instant loop; upgrade to Kokoro `bm_george` or Piper `en_GB-alan` for the Alfred voice. |
| Wake word | openWakeWord (optional in Phase 0; push-to-talk is fine to start) |
| Orchestrator | Small Python script in a venv |

### Choosing a Model Size (by Mac RAM)

| RAM | Target models |
|-----|--------------|
| 8GB | 3–4B models (Llama 3.2 3B, Qwen3 4B, Phi-4-mini). Best mirrors 8GB Jetson. |
| 16GB | 7–8B models comfortable (Qwen3 8B, Llama 3.1 8B) |
| 24GB+ | 14B+ possible, but treat as "nice to know" — not the Phase 1 target |

**Key habit:** benchmark a 3–4B model specifically. Do not fall in love with a 14B model you cannot deploy on the Jetson.

### Build Milestones

| Milestone | Goal |
|-----------|------|
| M0.1 | Brain in the terminal: Ollama + 3–4B model, chat by typing — **[DONE 2026-06-19]** Ollama v0.30.10, `llama3.2:3b` pulled; Alfred persona validated on M4 Pro / 48GB |
| M0.2 | Voice out: model reply spoken via `say -v Daniel` (British) — **[DONE 2026-06-19]** clean text via the Ollama HTTP API with Alfred as the `system` prompt |
| M0.3 | Voice in: record mic audio, transcribe with mlx-whisper — **[DONE 2026-06-19]** venv `~/assistant-env` (Python 3.12), `record_test.py` in project; transcription confirmed accurate by user. |
| M0.4 | Closed loop (push-to-talk): one Python script, full voice conversation — **[DONE 2026-06-19]** `assistant.py` in project; full spoken conversation confirmed working. |
| M0.5 | Swap `say` for Kokoro and **lock the Alfred voice** — **[DONE 2026-06-19]** Locked voice = **70/30 blend of `bm_lewis` + `bm_george` at speed 0.9** (chosen via two audition rounds). Wired into `assistant.py`. |
| M0.6 | Hands-free wake word (openWakeWord) — **[CODE COMPLETE / PARKED 2026-06-20]** `assistant.py --hands-free`; model-agnostic loop (`WAKE_MODEL` = pretrained name *or* custom `.onnx` path). Integration verified, but the `hey_jarvis` pretrained stand-in does **not** reliably trigger on live mic (scores stay near noise floor). Parked until the custom "Hey Alfred" model is trained in Colab and dropped into `models/`. Push-to-talk remains the working default. |

### Mac Gotchas
- Grant microphone permission in System Settings > Privacy & Security > Microphone.
- Audio capture libraries (sounddevice/pyaudio) need PortAudio — install via Homebrew first.
- Use a dedicated Python venv (`venv` or `uv`).
- Use earbuds during testing to avoid the assistant hearing its own voice.

---

## Phase 0 — Runbook

> Activate the venv before each milestone: `source ~/assistant-env/bin/activate`

### One-Time Setup

```bash
brew install ollama portaudio espeak-ng
brew services start ollama
# Kokoro TTS (v1.0, the bm_george voice) requires Python <3.13 — use 3.12.
python3.12 -m venv ~/assistant-env
source ~/assistant-env/bin/activate
pip install --upgrade pip
pip install sounddevice soundfile numpy mlx-whisper ollama "kokoro>=0.9.4"
```

> **Python version:** the venv is built on **Python 3.12**, not 3.14. Kokoro v1.0 (and its `misaki` G2P dep) cap at Python <3.13, and the wider ML stack has the most reliable wheels on 3.12. `espeak-ng` is Kokoro's phoneme fallback and must be installed at the OS level.

### M0.1 — Brain in the Terminal

```bash
ollama pull llama3.2:3b
ollama run llama3.2:3b
# Alternates: gemma3:4b, qwen3:4b, phi4-mini
# Type /bye to exit
```

### M0.2 — Voice Out

Use the Ollama HTTP API (not `ollama run | say`) — `ollama run` injects word-wrap control codes that get spoken as garbage, and the API lets you pass the Alfred persona as a proper `system` prompt:

```bash
reply="$(curl -s http://localhost:11434/api/generate -d '{
  "model": "llama3.2:3b",
  "system": "You are Alfred, a refined English butler modeled on Alfred Pennyworth. Address the user as sir, with dry wit and unfailing courtesy. Keep replies short and easily spoken aloud.",
  "prompt": "Introduce yourself in one short sentence.",
  "stream": false
}' | python3 -c "import sys,json;print(json.load(sys.stdin)['response'].strip())")"
echo "$reply"
say -v Daniel "$reply"   # British placeholder voice for the Alfred timbre
```

### M0.3 — Voice In

The script lives in the project as [`record_test.py`](record_test.py) (records 5s, transcribes with `mlx-whisper`, falls back from `whisper-small-mlx` to `whisper-base-mlx` automatically). Run it with the venv's Python:

```bash
cd "/Users/mattnero/Documents/Matts stuff/Unnamed AI Assistant"
~/assistant-env/bin/python record_test.py
```

- **First real run:** grant microphone access when macOS prompts (or System Settings → Privacy & Security → Microphone → enable your terminal), then speak during the 5-second window.
- Transcribing **silence** makes Whisper hallucinate random text — that's normal, not a bug. Speak and it transcribes correctly.
- The model is already cached from setup, so runs start immediately. (An unauthenticated-HF-Hub warning on first download is harmless.)

### M0.4 — Closed Loop Push-to-Talk

The full push-to-talk assistant lives in the project as [`assistant.py`](assistant.py). It wires the whole Phase 0 loop together: **press Enter to start speaking, Enter again to stop** → mlx-whisper transcribes → `llama3.2:3b` answers in Alfred's voice (conversation history preserved across turns) → macOS `say -v Daniel` speaks the reply. Ctrl+C to quit.

```bash
cd "/Users/mattnero/Documents/Matts stuff/Unnamed AI Assistant"
~/assistant-env/bin/python assistant.py
```

- **Use earbuds** so Alfred's spoken reply doesn't bleed into the mic (matters once we add hands-free wake-word in a later milestone).
- Same mic-permission and silence-hallucination notes as M0.3 apply.
- TTS is now the locked Kokoro `bm_lewis`+`bm_george` blend (see M0.5), not macOS `say`.

### M0.5 — Lock the Alfred Voice (Kokoro)

[`voice_audition.py`](voice_audition.py) speaks the same Alfred line in each British male Kokoro voice (`bm_george`, `bm_daniel`, `bm_lewis`, `bm_fable`) so you can compare them back-to-back and pick the one that sounds most like Alfred. First run downloads the Kokoro model + voice tensors (internet once).

```bash
cd "/Users/mattnero/Documents/Matts stuff/Unnamed AI Assistant"
~/assistant-env/bin/python voice_audition.py
# Re-listen any time:  afplay /tmp/alfred_bm_george.wav
```

#### Cross-session memory (added 2026-06-19)

Alfred remembers durable facts about you between runs via **`~/.alfred/profile.md`** (human-editable; one fact per line starting with `- `). At startup the facts are injected into the system prompt. When you tell him something lasting ("call me Master Nero", "I take my tea with milk"), the model appends a hidden `[REMEMBER: ...]` tag to its reply; `assistant.py` extracts the fact, saves it (deduped), strips the tag before speaking, and applies it immediately in-session. This is a durable-facts store, **not** a full transcript — ordinary back-and-forth is still only kept for the current run.

#### Voice

**Locked voice (2026-06-19):** a **70/30 blend of `bm_lewis` + `bm_george` at speed 0.9** — chosen in a second audition round ([`voice_audition_lewis.py`](voice_audition_lewis.py)) that compared `bm_lewis` speed variants and lewis-heavy blends. Kokoro accepts a voice *tensor*, so `assistant.py` averages the two voices' tensors at startup (`KOKORO_BLEND` / `KOKORO_SPEED`). Re-run `voice_audition_lewis.py` to retune. Kokoro runs on PyTorch (CPU/MPS) at 24 kHz; heavier than `say` but far more natural. Voice cloning (Tier B/C) stays out of scope.

### M0.6 — Hands-free Wake Word (openWakeWord)

`assistant.py` has two modes. Default is push-to-talk (M0.4); add `--hands-free` to listen for a wake word instead:

```bash
cd "/Users/mattnero/Documents/Matts stuff/Unnamed AI Assistant"
pip install openwakeword onnxruntime          # one-time, into ~/assistant-env
~/assistant-env/bin/python assistant.py --hands-free
# Say the wake word ("hey jarvis" with the stand-in model), then speak your command.
```

How it works: an always-on 16 kHz int16 stream is fed to openWakeWord in 80 ms (1280-sample) frames. On a detection above `WAKE_THRESHOLD`, the script calibrates against ambient noise once, then records your command with RMS-based endpointing (`CMD_SILENCE_HANG` of trailing silence ends the turn; `CMD_START_TIMEOUT` / `CMD_MAX_SECONDS` are the guard rails). The command audio then runs the normal Whisper → LLM → Kokoro loop.

- **Use earbuds.** Alfred's own voice bleeding into the mic can re-trigger the wake word.
- **The wake model is config, not code.** `WAKE_MODEL` is either a pretrained name (`hey_jarvis`, `alexa`, `hey_mycroft`, …) **or** a path to a custom `.onnx` file. Shipping default is `hey_jarvis` so the feature works today.
- **Heads-up on synthetic testing:** openWakeWord is tuned for real human speech and under-triggers on clean neural TTS, so the **only** authoritative check is a live mic test by a human — speak the phrase yourself.

#### Custom "Hey Alfred" wake word (the proper trigger)

openWakeWord has no pretrained "Alfred" model; the custom phrase is trained off-machine and dropped in. Local training on the Mac is impractical (the toolchain assumes Linux/CUDA and large negative-audio datasets), so use the sanctioned Colab path:

1. Open openWakeWord's **`automatic_model_training.ipynb`** notebook in Google Colab (GPU runtime). It synthesizes positive samples with Piper TTS, pulls pre-computed negative feature sets, and trains — ~1 hour, no local GPU needed.
2. Set the target phrase to **`hey alfred`** and run all cells.
3. Download the exported **`hey_alfred.onnx`** and place it in this project's **`models/`** directory:
   ```
   models/hey_alfred.onnx
   ```
4. Point the assistant at it — edit `assistant.py`:
   ```python
   WAKE_MODEL = "models/hey_alfred.onnx"
   ```
   (A relative path is resolved against the script's directory; an absolute path also works.) No other code changes — the loop is model-agnostic. If the file is missing, `assistant.py` raises a clear error telling you to train it or fall back to a pretrained name.
5. Live-mic test and tune `WAKE_THRESHOLD` (lower = more sensitive / more false triggers).

---

## Phase 1 — Deep Dive

> **Hardware plan (revised 2026-06-22 after voice training was dropped).** Two facts set the sizing:
> 1. **Reliable Home Assistant control needs a 7B tool-calling model** (Qwen2.5-7B class; validated 21/21 — see Step 3). 3–4B models are unreliable past a single tool.
> 2. **The brain only does inference now** — 7B LLM + Whisper + Kokoro. No voice training, so **no 24GB GPU and no cloud-rental step.** A 7B at Q4 is ~5–6GB; with 16GB you can keep the LLM, Whisper, and Kokoro all resident.
>
> **NOW — prototype on the existing gaming PC ($0, purchase deferred).** Hardware: **RTX 3060 Ti (8GB VRAM), i7, 32GB RAM, Windows 11**. Qwen2.5-7B Q4 fits in 8GB; run Whisper/Kokoro on CPU (32GB RAM is ample) or time-share VRAM. This proves the whole stack — HA control, brain-service, satellites — before spending anything. (Full setup in [Phase 1 — Windows 11 Setup Runbook](#phase-1--windows-11-setup-runbook).)
>
> **NEAR FUTURE — move to a Jetson Orin (battery-capable embeddable brain).** The user's chosen direction: a low-power ARM board with a CUDA GPU that runs natively off a battery. Two tiers:
> | Option | ~Cost | Notes |
> |---|---|---|
> | **Jetson Orin Nano Super 8GB dev kit** | **$249** + ~$50 NVMe | 67 TOPS, USB-C PD powered (a power bank *is* the battery). 7B Q4 fits but is tight alongside Whisper+Kokoro — great cheap proof of the Jetson path. |
> | **Jetson Orin NX 16GB** (e.g. Seeed reComputer J4012) | **~$899** (incl. 128GB NVMe) | ~100 TOPS, 16GB holds 7B + Whisper + Kokoro resident — the permanent-brain pick. Powered by DC barrel, so battery needs a USB-C-PD→12V cable or small power station. |
>
> **Battery backup.** Jetson draws ~7–15W typical (~25W under load): a ~74Wh USB-C power bank ≈ 4–5 hrs, a 99Wh ≈ 6+ hrs. For a mini-PC/desktop brain instead, a standard **UPS** works but runtime tracks wattage (a gaming PC at ~300W gets only ~10–20 min). Want true UPS behaviour? Confirm the power bank does clean **pass-through charging**.
>
> **Recommendation:** validate on the PC now → buy the **$249 Orin Nano Super** as a low-risk proof of the Jetson/battery path → step up to the **16GB NX (~$900)** if it earns the always-on role (or go straight to the NX to avoid buying twice). Neither includes a mic/speaker — that belongs to the satellite/robot, not the brain box.

### Success Criteria
- Autostarts on power-up with no keyboard or monitor attached.
- Wake word triggers reliably from ~2–3 metres away in a normal room.
- End-to-end latency under ~2.5 seconds for a short answer (with token streaming).
- Runs a 3–4B model with answer quality genuinely useful.
- Survives a power-cycle and "just works" again.
- Does not trigger on or transcribe its own voice (echo handled).

### Recommended Hardware Build

| Item | Est. Cost | Notes |
|------|-----------|-------|
| NVIDIA Jetson Orin Nano Super Dev Kit | ~$249 | 8GB LPDDR5, ~67 TOPS; the brain |
| NVMe SSD 500GB M.2 2280 + heatsink | ~$50 | Boot from NVMe, not microSD |
| USB speakerphone (mic + speaker + hardware AEC) | ~$80–130 | e.g. Anker PowerConf class. Solves mic, speaker, and echo in one device. |
| Quality USB-C PD power supply | ~$20–40 | Under-powering causes brownouts |
| USB-C PD power bank (74–99Wh) — *optional* | ~$60–150 | Native battery for the Orin Nano Super (~15W → 4–6 hrs). The battery-backup goal. |

> **Alternative audio:** ReSpeaker USB Mic Array v2.0 (~$80, onboard DSP/AEC) + small USB speaker (~$25). More flexible, more wiring.
>
> **Headroom build:** swap the Orin Nano Super for an **Orin NX 16GB** (~$899, e.g. Seeed reComputer J4012 with NVMe + case) when you want the 7B, Whisper, and Kokoro all GPU-resident as the permanent brain. It's DC-barrel powered, so battery means a USB-C-PD→12V cable or a small power station.

### Audio I/O — the Part Most People Underestimate
- The Orin Nano dev kit has **no 3.5mm analog jack** — all audio is USB or HDMI.
- **Acoustic echo cancellation (AEC) is not optional.** Without it, TTS bleeds into the mic. Hardware AEC (USB speakerphone or ReSpeaker DSP) handles this far better than software.
- This is the single biggest UX factor in Phase 1 — don't cheap out on the mic/audio.

### Software Stack (Phase 1)

| Component | Pick | Notes |
|-----------|------|-------|
| LLM runtime | Ollama (ARM64 + CUDA) | TensorRT-LLM/MLC for squeeze later |
| Model | Qwen3 4B, Gemma 3 4B, Phi-4-mini, or Llama 3.2 3B | Q4 quantized; stay 3–4B for snappy replies on 8GB |
| STT | faster-whisper (CTranslate2, CUDA) + Silero VAD | `small` model at int8 is a good quality/speed balance |
| Wake word | openWakeWord (CPU, ONNX) | Train a custom phrase in ~20 min |
| TTS | Kokoro `bm_george` (primary) + Piper `en_GB-alan`/`vctk` RP (fallback) | British male voices for the Alfred persona; Kokoro ~4.4 MOS, ~10x real-time on CPU |
| Orchestrator | Python state machine service | |

### Pipeline State Machine

```
IDLE → (wake word) → LISTENING → (VAD end-of-speech) → THINKING → SPEAKING → IDLE
```

Key design choices:
- **Token streaming:** feed LLM output into TTS sentence-by-sentence — biggest perceived-latency win. **[DONE 2026-06-22]** `Brain.chat_stream()` yields complete sentences as the model emits them (stripping `[REMEMBER:]` tags); `assistant.py`'s `speak_stream()` synthesises the next sentence while the current one plays, so Alfred starts speaking after sentence one instead of the whole reply. The tool-calling path resolves all HA calls first, then streams the final reply.
- **Barge-in (optional):** let the wake word interrupt SPEAKING so you can cut it off mid-answer.

### Latency Budget (Orin Nano Super, 3–4B model)

| Stage | Time |
|-------|------|
| Wake word detection | <200ms |
| STT (whisper small, GPU, few seconds audio) | ~0.3–1s |
| LLM time-to-first-token | ~0.3–0.8s |
| TTS first audio (Kokoro) | ~0.2–0.5s |
| **Total (starts talking after you stop)** | **~1.5–2.5s** |

> Jumping to a 7–8B model pushes all of this up and can break the conversational feel.

### Build Milestones

| Milestone | Goal |
|-----------|------|
| M1 | Boot off NVMe, enable high-perf power mode, run Ollama, benchmark model candidates |
| M2 | Voice out: Kokoro speaks text through USB speaker |
| M3 | Voice in: faster-whisper transcribes mic at conversational distance |
| M4 | Closed loop (push-to-talk): STT → LLM → TTS, triggered by Enter |
| M5 | Hands-free: openWakeWord triggers; confirm no self-trigger with AEC |
| M6 | Headless autostart: systemd service, auto-recovers on crash, survives power-cycle |

### Risks and Gotchas
- No analog audio jack — commit to USB audio from the start.
- Self-trigger / echo without hardware AEC — fix with the speakerphone or ReSpeaker DSP.
- microSD is slow and wears out — boot from NVMe.
- Enable the high-performance ("Super") power mode — leaving it off wastes significant performance.
- Under-spec power supply causes brownouts under high-performance load.
- ARM64 Python wheels mostly work, but occasionally need building from source.

### Phase 1 Cost Summary

| Item | Cost |
|------|------|
| Jetson Orin Nano Super | ~$249 |
| NVMe SSD 500GB + heatsink | ~$50 |
| USB speakerphone | ~$80–130 |
| Power supply and cables | ~$20–40 |
| **Total** | **~$400–470** |

---

## Phase 1 — Windows 11 Setup Runbook (interim brain, existing gaming PC)

> **What this is:** the actual, ordered build steps for standing up Phase 1 on the existing gaming PC — **Windows 11, RTX 3060 Ti (8GB VRAM), i7, 32GB RAM** — instead of buying a Jetson now. This supersedes the Jetson runbook above *for the interim*. The hardware tables above remain the reference for the eventual dedicated brain.
>
> **Execution note:** you run all of this **yourself on the personal PC** — Claude is only available on the work Mac and has no access to the PC. So these steps are written to be self-contained; copy them over and work through them in order. Paste back any errors on the Mac and we debug from there.

### Why native Windows, not WSL2

Develop **natively on Windows**, not inside WSL2. WSL2's Linux kernel has no clean path to the host microphone and speakers — USB/audio passthrough is the exact kind of pain we're avoiding. Ollama and Python both have first-class native Windows builds, so there's no reason to add a Linux layer. (Home Assistant is the one exception — it runs in Docker, which uses the WSL2 backend under the hood, but we never touch audio from there.)

**Role mapping:** the **Windows PC is the brain** (Ollama + the Alfred voice service + Home Assistant). The **Mac stays the working reference / a satellite client.** This is the "central brain + thin satellites" topology from the [Architecture Decision](#architecture-decision--hybrid-locked-2026-06-20), just with the brain on the gaming PC for now.

### De-risk order (do NOT skip step 4 before step 5)

The single make-or-break question for this whole project is **"can a local 7B model reliably drive Home Assistant?"** Prove that with *text only* before touching the voice pipeline. If 7B tool-calling is unreliable even by text, no amount of voice work matters yet.

1. Brain: Ollama + Qwen2.5-7B answering by text.
2. Devices: Home Assistant running with a token and a couple of test entities.
3. **Make-or-break:** wire the model to HA and confirm "turn on the light" works **by text**.
4. Only then: port the voice loop (faster-whisper + Kokoro) onto Windows.

### Step 1 — Ollama + the 7B brain

1. Install **Ollama for Windows** (native installer from ollama.com — *not* the WSL/Linux build). It auto-detects the RTX 3060 Ti and uses CUDA.
2. Pull the tool-calling model (this is the reliability floor for HA control — 3–4B models are not dependable past one tool):
   ```powershell
   ollama pull qwen2.5:7b
   ollama run qwen2.5:7b
   # confirm it answers, then /bye
   ```
   At Q4 this sits ~5–6GB in VRAM (with context) and fits the 8GB card. Keep it the *only* GPU-resident model — Whisper/Kokoro go on CPU (see Step 4).

### Step 2 — Home Assistant (Docker) + access token

1. Install **Docker Desktop** for Windows (WSL2 backend — accept its prompt to install/enable WSL2; this is only for the container runtime, not for our audio code).
2. Run Home Assistant Container:
   ```powershell
   docker run -d --name homeassistant --restart=unless-stopped `
     -p 8123:8123 `
     -v ${PWD}\ha-config:/config `
     ghcr.io/home-assistant/home-assistant:stable
   ```
3. Open `http://localhost:8123`, complete onboarding, and add **a couple of test devices** — even HA's built-in demo/helper entities or a single smart bulb integration are enough to test control.
4. Create a **long-lived access token**: HA profile (bottom-left user) → Security → *Long-lived access tokens* → Create. Copy it somewhere safe — you'll paste it into the tool layer next.

### Step 3 — Make-or-break: 7B drives HA, by text

Goal: confirm the model reliably turns devices on/off and reports state, by text. **This is the gate** — if it works, Phase 1 is proven; if it's flaky, the fix is model/prompt/tool-schema work, *not* voice.

The hand-rolled tool layer is already built and lives in the repo (no `ha-mcp` needed): `ha_tools.py` exposes `get_states` + `call_service` over HA's **WebSocket API** (REST is frozen). It has two backends — a `MockHAClient` (no HA required) and the real `WebSocketHAClient`.

1. **Prove the model behaviour first, against the mock** (this needs no HA at all — can be done anywhere):
   ```powershell
   ollama serve   # if not already running
   python validate_tools.py --model qwen2.5:7b --runs 5 --verbose
   ```
   This fires a battery of natural commands ("turn on the office light", "it's too dark in the living room", "turn everything off", "is the office light on?") and reports a pass-rate. **Validated on the Mac 2026-06-22: 21/21 (100%) across all runs.** Single-device + implicit-intent control was solid immediately; the one early gap — open-ended "turn everything off" (the model invented a wildcard) — was closed by adding the `turn_off_all` semantic tool, which the model now uses correctly (and scopes by domain, e.g. "turn off all the lights" leaves the kettle alone). **No bigger hardware needed.**
2. **Then wire the real HA** — set the env vars and run the voice app (or the brain server); it constructs `WebSocketHAClient` automatically:
   ```powershell
   $env:HA_URL="http://localhost:8123"; $env:HA_TOKEN="<long-lived-token>"
   python validate_tools.py --model qwen2.5:7b   # still uses the mock for the battery
   # real control happens through assistant.py / brain_server.py once HA_URL+HA_TOKEN are set
   ```
   Confirm a real device reacts to "turn on the office light".

> Alternative: the community **`ha-mcp`** MCP server (`homeassistant-ai/ha-mcp`, ~86 tools) is a heavier drop-in if you'd rather not maintain the minimal schema. The built-in `ha_tools.py` is the lighter, dependency-free path.

### Step 4 — Port the voice loop to Windows

`assistant.py` is now **cross-platform** — `STT_BACKEND="auto"` picks `mlx-whisper` on the Mac and **faster-whisper** on Windows/Linux automatically, so there's no code edit for STT. You just install the deps and one OS-level package:

1. Python venv (3.12 — Kokoro v1.0 caps at <3.13, same as the Mac):
   ```powershell
   py -3.12 -m venv $HOME\assistant-env
   $HOME\assistant-env\Scripts\Activate.ps1
   pip install --upgrade pip
   pip install sounddevice soundfile numpy ollama "kokoro>=0.9.4" faster-whisper websocket-client
   ```
2. Install **eSpeak-NG** via its **Windows installer** (the `.msi` from the espeak-ng GitHub releases) — Kokoro uses it for phoneme fallback. (On the Mac this came from Homebrew; Windows has no equivalent, so use the installer.)
3. faster-whisper defaults to **CPU int8** (`FASTER_WHISPER_DEVICE="cpu"` in `assistant.py`) so all 8GB of VRAM stays free for the 7B model. 32GB of system RAM is ample; latency is higher than GPU but fine for development. `sounddevice` works natively on Windows — mic capture and playback need no changes.
4. Set `MODEL = "qwen2.5:7b"` in `assistant.py` (it ships defaulting to `llama3.2:3b`) so the voice path uses the tool-calling-capable brain from Step 3.
5. Enable home control by setting the env vars before launch — the app builds the real HA client automatically:
   ```powershell
   $env:HA_URL="http://localhost:8123"; $env:HA_TOKEN="<long-lived-token>"
   python assistant.py            # push-to-talk, now with home control
   ```

### Repo scaffolding built for Phase 1 (2026-06-22)

The Mac-side work that de-risks this runbook is already in the repo:

| File | Role |
|------|------|
| `ha_tools.py` | HA tool layer: `get_states` + `call_service` over the WebSocket API; `WebSocketHAClient` (real, with auto-reconnect + re-auth on a dropped socket) and `MockHAClient` (no-HA testing). |
| `google_tools.py` | Calendar/Tasks tool layer (the one feature that reaches the internet): `list_events`/`create_event`/`list_tasks`/`add_task`/`complete_task`/`show_calendar` as LLM tools. `GoogleAPIClient` (real Calendar v3 + Tasks v1, OAuth token on the brain, Google libs imported lazily) and `MockGoogleClient` (no-account testing). Also renders the satellite calendar view (`render_calendar_html`). |
| `authorize_google.py` | One-time PC-side OAuth consent — opens a browser, you grant access, and the token is written for `GoogleAPIClient`. Run once on the brain (see Step 6). |
| `validate_tools.py` | The Step 3 make-or-break harness — runs the tool battery against the mock and reports a pass-rate. |
| `brain.py` | `Brain` class — Alfred's reusable core: persona + cross-session memory + the LLM turn + the HA **and** Google tool loops. `chat()` returns the whole reply; `chat_stream()` yields spoken-clean sentences for low-latency TTS. Bounded history so an always-on Alfred can't overflow context. `last_display` surfaces a screen view (e.g. the calendar) per turn. Shared by the voice app and satellites. |
| `brain_server.py` | Thin HTTP front door (`POST /chat`, `POST /chat/stream`, `POST /chat/audio`, `GET /health`, `GET /display/calendar`) so satellites reach the central brain and render its visual output — the "central brain + thin satellites" seam. `/chat/audio` is the voice-satellite seam: it does Whisper STT + Kokoro TTS so the satellite stays thin (see Step 8). |
| `voice.py` | Shared audio toolkit — capture (push-to-talk + wake-word), STT (`make_transcriber`, auto mlx/faster-whisper), TTS (`make_synthesizer`, Kokoro), and the WAV wire (`encode_wav`/`decode_wav`). Factored out of `assistant.py` so the voice app, the brain's audio endpoint, and the voice satellite share one implementation. Every heavy dep is imported lazily *inside* its function, so importing the module costs nothing — a thin satellite never pulls in Whisper/Kokoro. |
| `satellite.py` | A thin remote front-end: `SatelliteClient` (stdlib `urllib`) + a text REPL that talks to a `brain_server` over the LAN, consuming the streamed reply sentence-by-sentence. `ask_audio(wav)` is the voice seam — POSTs a WAV to `/chat/audio` and yields typed events (`transcript` / `audio` / `display_url`). Advances the locked multi-device goal. Run: `~/assistant-env/bin/python satellite.py --server http://<pc-ip>:8200`. |
| `voice_satellite.py` | The thin **voice** satellite — mic in, speaker out, brain does the rest. Captures audio, ships the WAV to `/chat/audio`, and plays back the per-sentence audio the brain returns; no Whisper/Kokoro/LLM locally (light deps: `sounddevice`, `numpy`, and for hands-free `openwakeword`/`onnxruntime`). Push-to-talk + `--hands-free` modes, reusing `voice.py` capture. Drives a `status.py` indicator (`--indicator console|oled|none`) so a headless Pi shows Listening/Thinking/Speaking. This is the exact program the Pi robot runs; the Mac is the first device to run it. Run: `~/assistant-env/bin/python voice_satellite.py --server http://<pc-ip>:8200`. |
| `status.py` | The satellite's state readout — a `StatusIndicator` abstraction with `make_indicator()` factory and three backends: `ConsoleIndicator` (default, prints state), `NullIndicator` (silent), and `OledIndicator` (SSD1306 I2C via `luma.oled`, lazily imported). Lets a headless Pi show Listening/Thinking(+transcript)/Speaking. All hardware imports are lazy, so it imports cleanly on the Mac (see Step 8 "Status display"). |
| `web_app.py` | The browser satellite — one self-contained HTML/CSS/JS page (no build step, no deps) served by `brain_server` at `GET /`. Streams Alfred's reply into a chat log and embeds `/display/calendar` in a pane on `display_url`. A stopgap satellite: open it on any phone/tablet on the LAN. Text + show-calendar only (the real voice satellite is `voice_satellite.py`). |
| `assistant.py` | The voice app, now cross-platform and delegating its brain to `brain.py` and its audio to `voice.py`. Speaks via `speak_stream()` (sentence-by-sentence, synthesis overlapped with playback). |
| `test_ha_tools.py`, `test_brain.py`, `test_brain_server.py`, `test_google_tools.py`, `test_voice.py`, `test_status.py`, `test_voice_satellite.py` | pytest regression suite (95 tests, no HA/Google/LLM/network/mic/speaker/display deps — `ollama.chat` is monkeypatched, Google services are injected fakes, the HTTP seam runs on a loopback port, `/chat/audio` is tested with injected fake STT/TTS over real WAV bytes, and the satellite's indicator wiring is driven with a fake client + fake indicator). Run: `~/assistant-env/bin/python -m pytest -q`. |

### Step 5 — Where this leaves the architecture

- **Windows PC = brain:** Ollama (Qwen2.5-7B) + Alfred voice service + Home Assistant, all on one box for now.
- **Mac = satellite / reference:** keep the working Mac build as a second front-end and as the place we iterate on code together.
- **Deferred to a real purchase:** the StyleTTS2 voice *fine-tune* (needs a 24GB GPU — rent cloud for that one-time job; the 3060 Ti can *run* the finished ~2GB model but can't *train* it) and the eventual dedicated/embeddable brain (3090 / 4060 Ti 16GB / Jetson Orin NX 16GB — see the Phase 1 Deep Dive options table).

### Step 6 — Personal Google Calendar & Tasks (optional capability)

> **Scope/principle note.** This is the **one** feature that reaches the internet — the LLM, voice, and home control stay 100% local. It does **not** break "no subscription": Google's Calendar/Tasks API is free for personal use. Auth is a **personal** OAuth token stored on the brain (the PC), entirely separate from any work Google account.

Alfred reads and manages your personal calendar and to-do list as tools (`list_events`, `create_event`, `list_tasks`, `add_task`, `complete_task`) and can render the calendar on a screen (`show_calendar` → `GET /display/calendar`). Writes are **confirmed first** — the persona asks before creating/modifying anything and waits for your "yes". Until you do this step, set `GOOGLE_MOCK=1` to exercise everything against the in-memory mock (demo events/tasks, no account needed).

**A. Create a personal Google OAuth client (one-time, in a browser):**
1. Go to the [Google Cloud Console](https://console.cloud.google.com/), create a new project (e.g. "Alfred").
2. **APIs & Services → Enable APIs** → enable **Google Calendar API** and **Google Tasks API**.
3. **APIs & Services → OAuth consent screen** → User type **External**, fill the minimal app name/email, and add **your own Google account** as a **Test user** (keeps the app in testing mode — no Google verification needed for personal use).
4. **APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app.** Download the JSON.
5. Save that JSON on the PC as `~/.alfred/google_credentials.json` (or anywhere; pass `--credentials` if elsewhere).

**B. Install the Google client libraries and run consent (on the PC):**
```powershell
$HOME\assistant-env\Scripts\Activate.ps1
pip install google-api-python-client google-auth-oauthlib
python authorize_google.py
# A browser opens → pick your account → grant Calendar + Tasks → "authorized" prints.
# Token is written to ~/.alfred/google_token.json
```

**C. Start the brain with calendar enabled:**
```powershell
# real Google: brain_server auto-detects the stored token and uses GoogleAPIClient
python brain_server.py --model qwen2.5:7b --port 8200
# or force the mock (no account/token): set GOOGLE_MOCK=1 first
$env:GOOGLE_MOCK="1"; python brain_server.py --model qwen2.5:7b --port 8200
```
`GET /health` reports `"calendar": true` once it's wired. Ask "what's on my calendar today?" or "show me my calendar"; a satellite opens `http://<pc-ip>:8200/display/calendar?range=week` to display it.

- **Token storage:** `~/.alfred/google_token.json` holds a refresh token — treat it like a password; it's how Alfred stays authorized without re-consent. `GoogleAPIClient` refreshes it automatically when it expires. Override paths with `GOOGLE_TOKEN` / `GOOGLE_CREDENTIALS` env vars.
- **Re-auth:** delete the token and re-run `authorize_google.py` if you change scopes or revoke access.

### Step 7 — The first satellite: a phone/tablet web client

The cheapest satellite is one you already own. `brain_server` serves a self-contained web app (`web_app.py`) at `GET /` — no install on the device, just a browser.

1. Make sure `brain_server` is reachable on the LAN. It already binds `0.0.0.0`, so just start it and note the PC's LAN IP (`ipconfig` → IPv4 address, e.g. `192.168.1.50`):
   ```powershell
   python brain_server.py --model qwen2.5:7b --port 8200
   # (add HA_URL/HA_TOKEN and the Google token from Steps 2–6 for full capability)
   ```
   If Windows Firewall prompts, allow Python on **private networks** (or add an inbound rule for TCP 8200).
2. On the phone/tablet (same Wi-Fi), open **`http://<pc-ip>:8200/`**. You'll see the Alfred chat page; the status line shows the model and enabled capabilities (home control / calendar).
3. Type a message — the reply streams in sentence by sentence. Ask "show me my calendar" and the calendar view opens in a pane beside (or below) the chat.

- **It's a thin satellite by design:** the page only ferries text and shows what the brain renders. All logic — persona, memory, tools, the calendar HTML — stays on the brain, so every satellite stays identical and disposable, and the same `/display/calendar` URL is what a projector would show later.
- **Add to a home screen** (iOS/Android "Add to Home Screen") for an app-like launcher.
- **The web client was always a stopgap.** It proves the satellite seam, but the goal (decided 2026-06-22) is a **real physical voice satellite robot** — see Step 8. The web app's deliberately-deferred "voice in the browser" idea is superseded: instead of browser dictation (which routes audio to the cloud), a real device captures the mic and ships a WAV to the brain over the LAN, keeping STT/TTS local on the brain.

### Step 8 — The voice satellite (a real robot; brain stays on the PC)

> **Direction (decided 2026-06-22).** The phone/tablet web client was a stopgap. The next focus is a **real physical satellite robot** — a thin Raspberry Pi 5 with a mic + speaker (projector later) — talking to the existing PC brain over the LAN. The brain stays on the PC "for a while". This is the locked "central brain + thin satellites" architecture made physical. **Decisions:** board = **Raspberry Pi 5**; sequence = **voice first, projector later**; STT+TTS run **on the brain** (the satellite ships audio up and plays audio back, staying truly thin).

**The whole software path is built and testable on the Mac today** — the Mac is just the first mic+speaker device to run the satellite program. The Pi is identical code on cheaper, embeddable hardware. No purchase is needed to prove the end-to-end voice-over-LAN loop.

#### The `/chat/audio` wire contract

`POST /chat/audio` on `brain_server` — request body is **WAV bytes** (mono 16 kHz, `Content-Type: audio/wav`). The brain transcribes it (Whisper), runs the turn, synthesises each sentence (Kokoro), and streams **NDJSON** back so the satellite can speak incrementally:

```
{"transcript": "what whisper heard"}                              # first line
{"sentence": "Good evening, sir.", "audio": "<base64 24kHz wav>"} # one per sentence
{"sentence": "...", "audio": "..."}
{"display_url": "/display/calendar?range=week"}                   # trailing, when a view was shown
{"error": "..."}                                                  # trailing, on mid-stream failure
```

Empty speech is a normal `200` with a transcript-only line and no sentences. STT runs before the `200` is committed, so a malformed/empty body still gets a clean `400`. `SatelliteClient.ask_audio(wav)` decodes this stream into typed events (`("transcript", txt)`, `("audio", sentence, wav_bytes)`, `("display_url", url)`) and raises `SatelliteError` on `{"error"}` or a connection failure.

#### Run the Mac as the first voice satellite (now, no Pi)

1. **On the PC brain** — start the server with voice enabled (it preloads Whisper + Kokoro; `/health` voice line shows `ready`). Add `HA_URL`/`HA_TOKEN` and the Google token from Steps 2–6 for full capability:
   ```powershell
   python brain_server.py --model qwen2.5:7b --port 8200
   # text-only box? pass --no-voice to skip Whisper/Kokoro (disables /chat/audio)
   ```
2. **On the Mac** (same LAN) — run the thin voice satellite, pointed at the PC's LAN IP:
   ```bash
   ~/assistant-env/bin/python voice_satellite.py --server http://<pc-ip>:8200
   # push-to-talk: Enter to start, Enter to stop. Add --hands-free for wake-word.
   ```
   Speak; you'll hear Alfred reply. This proves the whole audio-over-LAN path before buying any hardware. (Use earbuds so Alfred's voice doesn't bleed into the mic.)

- **Why STT+TTS on the brain:** the satellite needs only `sounddevice` + `numpy` (+ `openwakeword`/`onnxruntime` for hands-free) — no Whisper, no Kokoro, no LLM. The locked Kokoro voice lives once on the brain, so every satellite sounds identical and stays disposable.
- **The projector is nearly free later** (Increment below): the satellite already receives `display_url` events; a Pi kiosk browser pointed at the brain's `/display/*` pages is the whole projector path.

#### The Raspberry Pi 5 robot (you run this on the hardware)

> Same code as the Mac satellite — the Pi is just the embeddable copy. Claude has no access to the Pi, so these steps are self-contained; run them on the Pi and paste back any errors on the Mac.

**Shopping list (voice satellite):**

| Item | Purpose | Est. Cost (USD) | Notes |
|------|---------|-----------------|-------|
| Raspberry Pi 5 (4GB is plenty; 8GB fine) | The satellite compute | ~$60–80 | It's *thin* — no LLM/STT/TTS runs here, so RAM/GPU don't matter much. |
| Active cooler + 27W USB-C PD PSU | Thermal + power | ~$25 | Pi 5 needs active cooling and the official 27W supply. |
| microSD (32GB+) or NVMe HAT + SSD | OS/storage | ~$10–60 | SD is fine for a thin client; NVMe is nicer but optional. |
| **USB speakerphone with hardware AEC** (Anker PowerConf class) | Mic + speaker + echo cancel in one | ~$80–130 | Same lesson as Phase 1: hardware AEC stops Alfred's voice re-triggering the mic. The single most important part. |
| **SSD1306 0.96" I2C OLED** + 4 female-female jumper wires | Headless state readout (Listening/Thinking/Speaking) | ~$5–8 | Wires to 3V3/GND/SDA/SCL; driven by `status.py` via `--indicator oled`. See "Status display (OLED)" below. |
| Small case / enclosure | Body | ~$10–30 | The "robot" shell; expand in Phase 2. |

Projector is **deferred** — not on this list.

**Runbook:**
1. Flash **Raspberry Pi OS (64-bit)**, boot, connect to the same LAN/Wi-Fi as the PC brain.
2. System audio deps + a Python venv:
   ```bash
   sudo apt update && sudo apt install -y python3-venv portaudio19-dev
   python3 -m venv ~/assistant-env
   source ~/assistant-env/bin/activate
   pip install --upgrade pip
   pip install sounddevice numpy soundfile openwakeword onnxruntime   # light deps only
   ```
   (No `kokoro`, no `mlx-whisper`/`faster-whisper`, no `ollama` — those live on the brain.)
3. Copy `voice.py`, `satellite.py`, `status.py`, and `voice_satellite.py` to the Pi (the only files it needs).
4. Run it, pointed at the brain:
   ```bash
   ~/assistant-env/bin/python voice_satellite.py --server http://<pc-ip>:8200 --hands-free
   ```
5. **Autostart on boot** — a `systemd` user service (mirrors the Phase 1 M6 headless pattern):
   ```ini
   # ~/.config/systemd/user/alfred-satellite.service
   [Unit]
   Description=Alfred voice satellite
   After=network-online.target sound.target

   [Service]
   ExecStart=%h/assistant-env/bin/python %h/alfred/voice_satellite.py --server http://<pc-ip>:8200 --hands-free
   Restart=on-failure

   [Install]
   WantedBy=default.target
   ```
   ```bash
   systemctl --user enable --now alfred-satellite
   loginctl enable-linger "$USER"   # start at boot without a login session
   ```

#### Status display (OLED) — see what Alfred is doing on a headless Pi

The Mac satellite prints its state to the terminal, but the Pi is headless — when you talk to it you can't tell whether it heard you, is thinking, is speaking, or has crashed. A tiny **0.96" SSD1306 I2C OLED** fixes that: it shows the live state (and the transcript of what was heard) in words.

`status.py` provides the abstraction with three backends, chosen by `--indicator`:

| `--indicator` | Backend | Use |
|---|---|---|
| `console` (default) | prints `[STATE] detail` | Mac, debugging, no hardware |
| `oled` | SSD1306 over I2C via `luma.oled` | the Pi robot |
| `none` | silent no-op | headless with no readout wanted |

States shown: **Alfred/ready** (idle) → **Listening** (recording you) → **Thinking** + your transcript (brain working) → **Speaking** (playing the reply) → back to idle; **Error** on a failure. All `luma.*` imports are lazy, so `status.py` and the satellite import cleanly on the Mac; if the panel can't be opened the OLED backend degrades to console rather than crashing the loop.

*Why an OLED and not an LED strip:* the Pi 5's RP1 I/O chip breaks the RPi.GPIO / WS2812 (NeoPixel) timing libraries, so addressable LEDs are fiddly there. **I2C is unaffected**, and an I2C OLED can show words, not just a colour.

**Wiring (4 jumper wires):** OLED `VCC → Pi 3V3 (pin 1)`, `GND → GND (pin 6)`, `SDA → GPIO2 (pin 3)`, `SCL → GPIO3 (pin 5)`. Default I2C bus `1`, address `0x3C`.

**Enable + run:**
```bash
sudo raspi-config       # Interface Options → I2C → enable, then reboot
source ~/assistant-env/bin/activate
pip install luma.oled   # Pi only; not needed on the Mac
~/assistant-env/bin/python voice_satellite.py --server http://<pc-ip>:8200 --hands-free --indicator oled
```
(Add `--indicator oled` to the systemd `ExecStart` above to keep the display on at boot.)

#### Increment — the projector (deferred)

Once the voice satellite works, the projector is mostly wiring: a small DLP/pico projector on the Pi's HDMI, plus a kiosk browser (`chromium-browser --kiosk`) pointed at the brain's `/display/*` pages. The satellite already gets `display_url` events from `/chat/audio` — on one, it drives the local kiosk to that page (e.g. a tiny local redirect page the kiosk polls, or `xdotool`/Chromium remote-debugging to navigate). Pico projectors are dim, so expect a dim-room requirement. Not built now — voice first.
