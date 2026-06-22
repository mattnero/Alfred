# Setting up the Alfred brain on the PC

> **Portable runbook.** This is a self-contained, copy-paste extract of the brain-setup
> path so you can work through it directly on the gaming PC (Claude has no access to that
> machine). The **canonical, always-current plan is `README.md`** — see its *"Phase 1 —
> Windows 11 Setup Runbook"* and *Step 8*. If anything here disagrees with the README, the
> README wins.

**Target machine:** Windows 11, RTX 3060 Ti (8GB VRAM), i7, 32GB RAM.
**Role:** this PC is the **brain** — Ollama (LLM) + Whisper (STT) + Kokoro (TTS) + Home
Assistant. Mic/speaker belong to the *satellites* (the Mac today, a Pi later), not here.

Work top to bottom. The make-or-break gate is Step 4 (a 7B model reliably driving Home
Assistant *by text*) — prove that before caring about voice.

---

## Step 0 — Get the code onto the PC

If you make a GitHub repo (recommended — see the bottom of this file):

```powershell
cd $HOME
git clone https://github.com/<you>/alfred.git alfred
cd alfred
```

Otherwise just copy the whole project folder over. Either way, `cd` into it for the rest.

---

## Step 1 — Ollama + the 7B brain

1. Install **Ollama for Windows** (native installer from ollama.com — *not* the WSL/Linux
   build). It auto-detects the RTX 3060 Ti and uses CUDA.
2. Pull and smoke-test the tool-calling model (3–4B models are not reliable past one tool):
   ```powershell
   ollama pull qwen2.5:7b
   ollama run qwen2.5:7b
   # confirm it answers, then /bye
   ```
   At Q4 this sits ~5–6GB in VRAM and fits the 8GB card. Keep it the **only** GPU-resident
   model — Whisper/Kokoro run on CPU (Step 5).

---

## Step 2 — Python venv + brain dependencies

Kokoro v1.0 caps at Python <3.13, so use **3.12**.

```powershell
py -3.12 -m venv $HOME\assistant-env
$HOME\assistant-env\Scripts\Activate.ps1
pip install --upgrade pip
pip install sounddevice soundfile numpy ollama "kokoro>=0.9.4" faster-whisper websocket-client
```

Then install **eSpeak-NG** from its Windows installer (the `.msi` on the espeak-ng GitHub
releases) — Kokoro uses it for phoneme fallback. (No Homebrew on Windows.)

> `faster-whisper` runs on **CPU int8** by default (`FASTER_WHISPER_DEVICE="cpu"` in
> `voice.py`) so all 8GB of VRAM stays free for the 7B model. 32GB RAM is ample.

---

## Step 3 — Run the brain and verify

```powershell
python brain_server.py --model qwen2.5:7b --port 8200
```

On startup it preloads Whisper + Kokoro (so the first spoken request isn't slow) and prints
a voice-status line. Then, from the same PC:

```powershell
# health check — should report the model and "voice satellite ... ready"
curl http://localhost:8200/health

# a text turn
curl http://localhost:8200/chat -d '{\"text\":\"Good evening, Alfred.\"}'
```

- Text-only box / skipping audio? Add `--no-voice` (disables `/chat/audio`, keeps text
  routes).
- `GET /` serves the browser satellite; open `http://localhost:8200/` to chat from a phone
  on the LAN.

---

## Step 4 — Make-or-break: 7B drives Home Assistant (by text)

This is the gate for the whole project. Prove it with text before any voice work.

1. **Prove model behaviour against the mock** (needs no HA):
   ```powershell
   python validate_tools.py --model qwen2.5:7b --runs 5 --verbose
   ```
   This fires a battery of natural commands and reports a pass-rate. (Validated on the Mac
   2026-06-22: 21/21.)
2. **Stand up Home Assistant** (Docker Desktop, WSL2 backend — only for the container, not
   our audio):
   ```powershell
   docker run -d --name homeassistant --restart=unless-stopped `
     -p 8123:8123 `
     -v ${PWD}\ha-config:/config `
     ghcr.io/home-assistant/home-assistant:stable
   ```
   Open `http://localhost:8123`, finish onboarding, add a couple of test entities, then
   create a **long-lived access token** (profile → Security → Long-lived access tokens).
3. **Wire HA into the brain** — set env vars before launching:
   ```powershell
   $env:HA_URL="http://localhost:8123"; $env:HA_TOKEN="<long-lived-token>"
   python brain_server.py --model qwen2.5:7b --port 8200
   ```
   Confirm a real device reacts to "turn on the office light". `GET /health` shows
   `"home_control": true`.

---

## Step 5 — (Optional) Personal Google Calendar & Tasks

The one feature that reaches the internet (free personal API, personal OAuth token on the
PC — separate from any work account). Full walkthrough is in README **Step 6**. Short form:

```powershell
$HOME\assistant-env\Scripts\Activate.ps1
pip install google-api-python-client google-auth-oauthlib
python authorize_google.py   # browser opens → grant Calendar + Tasks → token saved to ~/.alfred
```

Then start the brain normally — it auto-detects the stored token. No account?
`$env:GOOGLE_MOCK="1"` wires an in-memory mock instead. `GET /health` shows
`"calendar": true` when live.

---

## Step 6 — Connect a satellite (prove voice over the LAN)

With the brain running and voice enabled:

1. Find the PC's LAN IP: `ipconfig` → IPv4 (e.g. `192.168.1.50`). If Windows Firewall
   prompts, allow Python on **private networks** (or add an inbound rule for TCP 8200).
2. On the **Mac** (same Wi-Fi), run the thin voice satellite:
   ```bash
   ~/assistant-env/bin/python voice_satellite.py --server http://<pc-ip>:8200
   ```
   Speak (Enter to start/stop; `--hands-free` for wake-word). You should hear Alfred reply.
   Use earbuds so his voice doesn't bleed into the mic. This proves the whole
   audio-over-LAN path — the Pi later runs identical code.

---

## Autostart (optional, once it's stable)

To run the brain headless on boot, register `brain_server.py` as a service. The simplest
Windows path is **Task Scheduler** → "At startup" → run
`%USERPROFILE%\assistant-env\Scripts\python.exe %USERPROFILE%\alfred\brain_server.py
--model qwen2.5:7b --port 8200` (set `HA_URL`/`HA_TOKEN` as system env vars first so the
task inherits them). NSSM is a tidier alternative if you want proper service semantics.

---

## Quick reference

| Action | Command |
|---|---|
| Start brain (full) | `python brain_server.py --model qwen2.5:7b --port 8200` |
| Start brain (text only) | `python brain_server.py --model qwen2.5:7b --port 8200 --no-voice` |
| Health | `curl http://localhost:8200/health` |
| Validate HA tool-calling | `python validate_tools.py --model qwen2.5:7b --runs 5 --verbose` |
| Run the test suite | `python -m pytest -q` |
| Enable HA | set `HA_URL` + `HA_TOKEN` env vars before launch |
| Force Google mock | set `GOOGLE_MOCK=1` before launch |
