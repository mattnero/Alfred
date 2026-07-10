# Alfred Satellite (Android)

A thin, hands-free voice satellite for the Alfred brain — the native-Android
sibling of the Mac CLI `voice_satellite.py`. It listens for a wake word, records
your command, ships the audio to the brain's `/chat/audio` endpoint, and plays
Alfred's spoken reply. No STT/LLM/TTS runs on the phone — the brain does all of
it. This is the same "central brain + thin satellites" contract the future Pi
will use.

## What it does

```
IDLE ──"Jarvis"──▶ LISTENING ──you stop talking──▶ THINKING ──▶ SPEAKING ──▶ IDLE
```

- Wake word: **Porcupine**, on the built-in **"jarvis"** keyword (free, no training).
- Capture: `AudioRecord` → mono 16 kHz WAV, ended by an energy-based silence gate.
- Reply: streamed sentence-by-sentence from the brain and played via `AudioTrack`.
- Runs as a foreground (microphone) service so it keeps listening with the screen off.

## Prerequisite (do this first — the app needs it)

1. Create a free account at the **Picovoice Console** (<https://console.picovoice.ai>).
2. Copy your **AccessKey**. You'll paste it into the app — no rebuild needed.
3. *(Optional, later)* Train a custom **"Alfred"** wake word in the Console,
   download `Alfred.ppn`, drop it in `app/src/main/assets/`, and in
   `WakeListener.kt` swap `setKeyword(Porcupine.BuiltInKeyword.JARVIS)` for
   `setKeywordPath("Alfred.ppn")`.

## Build & install

### Option A — Android Studio (recommended)

1. Install **Android Studio** (it bundles the SDK and generates the Gradle wrapper).
2. **Open** the `android-satellite/` folder. Let it sync — it downloads the SDK
   packages and writes `local.properties` automatically.
3. Plug in the phone with **USB debugging** enabled (Settings → Developer options),
   then press **Run** to install and launch. Or **Build → Build Bundle(s)/APK(s) →
   Build APK(s)** and copy `app/build/outputs/apk/debug/app-debug.apk` to the phone.

### Option B — command line

Requires JDK 17 + the Android SDK, with `local.properties` pointing at it
(`sdk.dir=/path/to/Android/sdk`).

```bash
cd android-satellite
gradle wrapper        # once, to generate ./gradlew (or: brew install gradle)
./gradlew assembleDebug
# → app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

To sideload manually: transfer the APK to the phone and tap it (enable
**Install unknown apps** for your file manager when prompted).

## Using it

1. Open the app. Enter the **brain URL** (e.g. `http://192.168.2.35:8200`) and your
   **Picovoice AccessKey**. Tap **Start listening**.
2. Grant the **microphone** and **notification** permissions when asked.
3. The status line should show the model + capabilities (this confirms the phone
   can reach the brain — same LAN caveats as the PC: correct IP, firewall on the
   *private* profile).
4. Say **"Jarvis"**, then speak your request. Alfred replies out loud.

## Known limitations (by design, for now)

- **Echo / self-trigger:** no hardware echo cancellation — the phone speaker can
  bleed into its mic. Use **earbuds or an external speaker** for reliable hands-free.
- **Not fully headless:** Android 14+ forbids starting a microphone foreground
  service from boot, so after a reboot you must open the app once and tap Start.
  Also disable battery optimization for the app and keep the phone charging.
- **minSdk = 26 (Android 8.0).** If your phone is older, tell me and we'll lower
  it (and add a raster launcher icon).
- `display_url` (projector) and a custom "Alfred" keyword are out of scope for v1.

## Layout

| File | Role |
|------|------|
| `MainActivity.kt` | Config UI (URL + AccessKey), Start/Stop, status line |
| `SatelliteService.kt` | Foreground service; the IDLE→LISTENING→THINKING→SPEAKING loop |
| `WakeListener.kt` | Porcupine wrapper (built-in "jarvis") |
| `AudioRecorder.kt` | Mic → 16 kHz WAV with an energy VAD end-of-speech |
| `AudioPlayer.kt` | Streams the reply's 24 kHz WAV sentences to `AudioTrack` |
| `BrainClient.kt` | `POST /chat/audio` (NDJSON) + `GET /health` via OkHttp |
| `Prefs.kt`, `SatelliteState.kt` | Settings storage + UI state bus |
