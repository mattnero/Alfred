"""Alfred's brain — the reusable core, independent of voice/audio.

This is the seam the locked architecture calls for: one "brain" (persona +
cross-session memory + LLM turn + Home Assistant tool-calling) that both the
local voice app (`assistant.py`) and remote satellites (`brain_server.py`) drive
through the same `Brain.chat(text) -> reply` call.

A Brain holds the running conversation, the durable user facts, and — when given
an HA client — the home-control tool loop. Pass an `ha_client` from `ha_tools`
(WebSocketHAClient on the real brain, MockHAClient for testing) to enable device
control; omit it for a pure chat brain (the current Mac voice setup).
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable, Optional

import ollama

from ha_tools import TOOL_SCHEMA as HA_TOOL_SCHEMA, dispatch as ha_dispatch
from google_tools import TOOL_SCHEMA as GOOGLE_TOOL_SCHEMA, dispatch as google_dispatch

# ---------------------------------------------------------------------------
# Domain knowledge — injected as context when the user's query matches a topic
# ---------------------------------------------------------------------------

KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge")

# Each topic maps to (set-of-trigger-keywords, filename).
# Keywords are matched as substrings of the lowercased user text.
TOPIC_REGISTRY: dict[str, tuple[frozenset[str], str]] = {
    "survival": (
        frozenset({
            "survival", "disaster", "emergency", "grid down", "power out",
            "power outage", "blackout", "earthquake", "hurricane", "flood",
            "tornado", "wildfire", "stranded", "lost in the woods", "forage",
            "edible plant", "fire starting", "purify water", "water purification",
            "first aid", "wound", "bleeding", "bleed", "burn", "fracture",
            "hypothermia", "hyperthermia", "heat stroke", "choking", "cpr",
            "signal for help", "signal for rescue", "bug out", "shelter in place",
        }),
        "survival.md",
    ),
    "cooking": (
        frozenset({
            "recipe", "cook", "bake", "roast", "fry", "sauté", "saute",
            "braise", "grill", "grilling", "poach", "sear", "simmer",
            "ingredient", "substitute", "substitution", "meal prep",
            "dinner", "lunch", "breakfast", "flavour", "flavor",
            "seasoning", "spice", "sauce", "marinade", "food safety",
            "internal temperature", "knife skills", "chop", "dice", "mince",
            "dough", "batter", "caramelise", "caramelize", "deglaze",
            "al dente", "blanch", "reduction", "pantry meal", "pantry cooking",
        }),
        "cooking.md",
    ),
    "gardening": (
        frozenset({
            "garden", "vegetable garden", "raised bed", "in the ground",
            "plant out", "transplant", "seedling", "seed starting",
            "tomato", "pepper", "cucumber", "zucchini", "courgette",
            "squash", "lettuce", "kale", "spinach", "carrot", "beet",
            "broccoli", "cauliflower", "pea", "bean", "basil", "harvest",
            "compost", "fertilizer", "fertiliser", "fertilize", "fertilise",
            "pest", "aphid", "slug", "snail", "caterpillar", "hornworm",
            "mildew", "blight", "prune", "companion planting", "crop rotation",
            "mulch", "hardening off", "succession planting", "soil amendment",
            "soil ph", "bolting",
        }),
        "gardening.md",
    ),
}


def _load_knowledge_context(text: str) -> str:
    """Return the concatenated content of any knowledge files whose topic
    keywords appear in *text*.  Returns an empty string if no match."""
    text_lower = text.lower()
    sections: list[str] = []
    for _topic, (keywords, filename) in TOPIC_REGISTRY.items():
        if any(kw in text_lower for kw in keywords):
            path = os.path.join(KNOWLEDGE_DIR, filename)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    sections.append(f.read())
    return "\n\n---\n\n".join(sections)

# The HA-capable default. The Mac voice app passes its own lighter model.
DEFAULT_MODEL = "qwen2.5:7b"
MAX_TOOL_ROUNDS = 6  # tool-call rounds before giving up on a single turn
# Cap on retained messages (excluding the system prompt). An always-on Alfred
# would otherwise grow self.messages until it overflows the model's context.
# Durable facts survive trimming — they live in the system prompt, not history.
MAX_HISTORY_MESSAGES = 40

PROFILE_PATH = os.path.expanduser("~/.alfred/profile.md")
REMEMBER_RE = re.compile(r"\[REMEMBER:\s*(.+?)\]", re.IGNORECASE | re.DOTALL)
# A complete sentence: text up to a terminator, then whitespace or end-of-text.
SENTENCE_RE = re.compile(r"(.+?[.!?])(?:\s+|$)", re.DOTALL)

SYSTEM_PROMPT = (
    "You are Alfred, a personal AI assistant modeled on Alfred Pennyworth — "
    "a refined English butler. Address the user as 'sir' by default, unless you "
    "have been told to address them otherwise. Speak in precise, "
    "formal British English with dry, understated wit and unfailing courtesy. "
    "Offer gentle, tactful counsel ('If I may, sir...'). Be loyal, calm, and "
    "concise — never verbose, never slangy. Keep replies short and easily "
    "spoken aloud, as they will be read by a text-to-speech voice. "
    "You have extensive practical knowledge of cookery and culinary technique, "
    "vegetable gardening, and emergency preparedness and survival skills — "
    "answer questions in these areas with the same authority and care you "
    "bring to all matters."
)

MEMORY_INSTRUCTIONS = (
    " When the user tells you something durable to remember about themselves "
    "(how to address them, preferences, important personal facts), acknowledge "
    "it naturally in your reply, then on a new line append exactly: "
    "[REMEMBER: <the fact as a short instruction>]. Use this only for lasting "
    "facts, never for one-off tasks, and never read the bracketed text aloud."
)

HA_INSTRUCTIONS = (
    " You control the home via two tools: get_states (read entity states) and "
    "call_service (control devices, e.g. light.turn_on). When the user asks you "
    "to do something physical, call the right service; when they ask about a "
    "device's state, call get_states first and answer from the result. Entity "
    "ids look like 'light.office'. Never invent entity ids — call get_states to "
    "discover them. To turn off many or all devices at once (e.g. 'turn "
    "everything off', 'goodnight'), use the turn_off_all tool — not a wildcard "
    "and not many separate calls."
)

GOOGLE_INSTRUCTIONS = (
    " You manage the user's Google Calendar and Tasks with these tools: "
    "list_events and list_tasks (read), create_event, add_task and complete_task "
    "(write), and show_calendar (display the calendar on a screen). For any "
    "question about their schedule or to-do list, call the matching read tool "
    "first and answer from the result. IMPORTANT: before any write — creating an "
    "event, adding a task, or completing one — briefly confirm the details with "
    "the user and wait for their agreement; do not call the write tool until they "
    "say yes. To complete a task, call list_tasks first to find its id; never "
    "invent ids. When the user asks to *see* or *show* their calendar (rather "
    "than just hear it), call show_calendar."
)


def load_facts(profile_path: str = PROFILE_PATH) -> list[str]:
    if not os.path.exists(profile_path):
        return []
    with open(profile_path, encoding="utf-8") as f:
        return [
            line[2:].strip()
            for line in f
            if line.startswith("- ") and line[2:].strip()
        ]


def save_facts(
    new_facts: list[str],
    existing: list[str],
    profile_path: str = PROFILE_PATH,
    on_remember: Optional[Callable[[str], None]] = None,
) -> list[str]:
    """Append genuinely new facts to the profile; return the updated list."""
    seen = {f.lower() for f in existing}
    added = [f for f in new_facts if f.lower() not in seen]
    if not added:
        return existing
    os.makedirs(os.path.dirname(profile_path), exist_ok=True)
    if not os.path.exists(profile_path):
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write("# Alfred's notebook — durable facts about the user\n")
            f.write("# (edit freely; one fact per line starting with '- ')\n")
    with open(profile_path, "a", encoding="utf-8") as f:
        for fact in added:
            f.write(f"- {fact}\n")
    if on_remember:
        for fact in added:
            on_remember(fact)
    return existing + added


def system_content(
    facts: list[str], with_ha: bool = False, with_google: bool = False
) -> str:
    content = SYSTEM_PROMPT + MEMORY_INSTRUCTIONS
    if with_ha:
        content += HA_INSTRUCTIONS
    if with_google:
        content += GOOGLE_INSTRUCTIONS
    if facts:
        content += "\n\nThings you already know about the user (always honour these):\n"
        content += "\n".join(f"- {f}" for f in facts)
    return content


def extract_memories(reply: str) -> tuple[str, list[str]]:
    """Pull [REMEMBER: ...] tags out of a reply; return (clean_reply, facts)."""
    facts = [m.strip() for m in REMEMBER_RE.findall(reply)]
    clean = REMEMBER_RE.sub("", reply).strip()
    return clean, facts


class Brain:
    """One Alfred conversation: persona, memory, and optional home control."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        ha_client=None,
        google_client=None,
        profile_path: str = PROFILE_PATH,
        on_remember: Optional[Callable[[str], None]] = None,
        max_history: int = MAX_HISTORY_MESSAGES,
    ) -> None:
        self.model = model
        self.ha_client = ha_client
        self.google_client = google_client
        self.profile_path = profile_path
        self.on_remember = on_remember
        self.max_history = max_history
        # Build the combined tool schema and a name -> (client, dispatch) router
        # from whichever backends are wired in.
        self._tool_routes: dict[str, tuple] = {}
        tools: list[dict] = []
        if ha_client:
            tools += HA_TOOL_SCHEMA
            for spec in HA_TOOL_SCHEMA:
                self._tool_routes[spec["function"]["name"]] = (ha_client, ha_dispatch)
        if google_client:
            tools += GOOGLE_TOOL_SCHEMA
            for spec in GOOGLE_TOOL_SCHEMA:
                self._tool_routes[spec["function"]["name"]] = (google_client, google_dispatch)
        self.tools = tools or None
        self.facts = load_facts(profile_path)
        # Set when a turn produces something to show on a screen (e.g. the
        # calendar). Reset at the start of each turn; read by the server after
        # the turn to tell the satellite what to display.
        self.last_display: Optional[dict] = None
        self.messages = [{"role": "system", "content": self._system_content()}]

    def _system_content(self) -> str:
        return system_content(
            self.facts,
            with_ha=bool(self.ha_client),
            with_google=bool(self.google_client),
        )

    def _trim_history(self) -> None:
        """Drop the oldest turns so history stays bounded, keeping the system
        prompt and a tail that begins on a clean user-turn boundary (so a tool
        result is never left without its preceding tool-call message)."""
        if len(self.messages) - 1 <= self.max_history:
            return
        start = len(self.messages) - self.max_history
        while start < len(self.messages) and self.messages[start].get("role") != "user":
            start += 1
        if start < len(self.messages):
            self.messages = [self.messages[0]] + self.messages[start:]

    def _make_messages_for_llm(self) -> list[dict]:
        """Return a messages list for the next LLM call.

        If the last message is a user turn whose text matches a domain topic,
        the relevant knowledge file(s) are prepended as reference material.
        ``self.messages`` is never modified — the injection lives only in the
        copy returned here.
        """
        if not self.messages or self.messages[-1].get("role") != "user":
            return list(self.messages)
        user_text = self.messages[-1]["content"]
        context = _load_knowledge_context(user_text)
        if not context:
            return list(self.messages)
        enriched = list(self.messages)
        enriched[-1] = {
            "role": "user",
            "content": (
                "[Reference material for this query — draw on it as needed, "
                "but do not recite it verbatim or announce that you have it]:\n\n"
                f"{context}\n\n---\n\n{user_text}"
            ),
        }
        return enriched

    def chat(self, text: str) -> str:
        """Run one user turn; update memory; return the spoken-clean reply."""
        self.last_display = None
        self._trim_history()
        self.messages.append({"role": "user", "content": text})
        raw = self._run_llm()
        reply, new_facts = extract_memories(raw)
        self.messages.append({"role": "assistant", "content": reply})
        if new_facts:
            self.facts = save_facts(
                new_facts, self.facts, self.profile_path, self.on_remember
            )
            self.messages[0]["content"] = self._system_content()
        return reply

    def chat_stream(self, text: str):
        """Run one user turn, yielding spoken-clean sentences as they form.

        Same memory/tool behaviour as chat(), but emits each complete sentence
        the moment it's ready so a TTS front-end can start speaking before the
        whole reply exists. [REMEMBER: ...] tags are never yielded; durable
        facts are saved once the turn completes.
        """
        self.last_display = None
        self._trim_history()
        self.messages.append({"role": "user", "content": text})
        raw = ""
        pos = 0  # index in raw up to which we've already yielded
        for token in self._stream_llm():
            raw += token
            # never speak from a possible [REMEMBER ...] tag onward
            cut = raw.find("[", pos)
            region_end = cut if cut != -1 else len(raw)
            for m in SENTENCE_RE.finditer(raw, pos, region_end):
                sentence = m.group(1).strip()
                if sentence:
                    yield sentence
                pos = m.end()

        reply, new_facts = extract_memories(raw)
        self.messages.append({"role": "assistant", "content": reply})
        if new_facts:
            self.facts = save_facts(
                new_facts, self.facts, self.profile_path, self.on_remember
            )
            self.messages[0]["content"] = self._system_content()
        # flush any trailing clean text that never ended in a terminator
        tail = reply[pos:].strip()
        if tail:
            yield tail

    def _stream_llm(self):
        """Yield reply text in chunks. Plain chat streams token-by-token; the
        tool path resolves all calls first, then emits the final reply whole."""
        if not self.tools:
            messages = self._make_messages_for_llm()
            for part in ollama.chat(model=self.model, messages=messages, stream=True):
                chunk = part["message"].get("content") or ""
                if chunk:
                    yield chunk
            return
        yield self._run_llm()

    def _run_llm(self) -> str:
        """Get the model's final reply, resolving any HA tool calls first.

        Knowledge context is injected into the first LLM call only (via
        ``_make_messages_for_llm``).  Subsequent tool-loop rounds use
        ``self.messages`` directly so tool results remain in the context.
        """
        llm_messages = self._make_messages_for_llm()

        if not self.tools:
            resp = ollama.chat(model=self.model, messages=llm_messages)
            return (resp["message"].get("content") or "").strip()

        for round_num in range(MAX_TOOL_ROUNDS):
            # First round uses the knowledge-injected copy; subsequent rounds
            # use self.messages (which now carries the tool-call history).
            msgs = llm_messages if round_num == 0 else self.messages
            resp = ollama.chat(model=self.model, messages=msgs, tools=self.tools)
            msg = resp["message"]
            calls = msg.get("tool_calls") or []
            if not calls:
                return (msg.get("content") or "").strip()
            self.messages.append(msg)  # record the tool-call turn for context
            for call in calls:
                name = call["function"]["name"]
                args = call["function"]["arguments"]
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                client, fn = self._tool_routes.get(name, (None, None))
                result = fn(client, name, args) if fn else {"error": f"unknown tool: {name}"}
                if isinstance(result, dict) and result.get("display_url"):
                    self.last_display = {
                        "type": "calendar",
                        "url": result["display_url"],
                        "range": result.get("range"),
                    }
                self.messages.append({"role": "tool", "content": json.dumps(result)})
        return "I'm afraid I couldn't complete that, sir."
