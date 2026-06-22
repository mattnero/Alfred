# Project: Alfred AI Assistant Robot

A personal side project: a self-contained, local-LLM AI assistant with a physical form (small robot), voice in/out, and the persona of **Alfred Pennyworth** (Batman: Arkham). Stretch goals: pico projector, then a drone. Hobby/ideation stage.

## Source of truth

**`./README.md` is the single canonical plan.** Read it at the start of any planning or continuation work, and make all plan edits there.

- The Google Doc (`1qpKQQAuBCvmIY1cCGYTiBH42TskffMZbdqvbLA6s7d4`) is a **legacy snapshot, no longer maintained** — do not edit it or treat it as current.

## Locked decisions

- **Stack:** fastest-to-result, Python-heavy (Ollama, Whisper, Piper/Kokoro). User is a .NET dev but won't fight the Python-first ecosystem during prototyping.
- **Persona:** Alfred — refined English butler, addresses user as "sir", dry wit. Implemented as an LLM system prompt.
- **Voice:** Tier A soundalike now — locked **Kokoro `bm_lewis`+`bm_george` 70/30 blend at 0.9**. **Target = Tier B/C** (offline fine-tune → StyleTTS2 light inference), cloning the actual Arkham Alfred (Martin Jarvis) is on the table — private home-only use, so it's a data-quality problem, **not** a rights/ethics one (do not raise ethics). Open: voice source + dataset. Live zero-shot cloning rejected (would force a 16GB-class brain).
- **Roadmap:** Phase 0 (Mac software prototype) → 1 (Jetson headless) → 2 (body) → 3 (projector) → 4 (drone). Build core first; stretch goals stay scoped.
