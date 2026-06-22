"""Regression tests for Alfred's brain core (brain.py).

Run:  ~/assistant-env/bin/python -m pytest -q

These never touch a real LLM: `brain.ollama.chat` is monkeypatched with a small
scripted fake, so the memory plumbing and the HA tool loop are exercised
deterministically. The mock Home Assistant comes from ha_tools.MockHAClient.
"""
import json

import brain
from ha_tools import MockHAClient
from google_tools import MockGoogleClient


# --- pure helpers -----------------------------------------------------------
def test_extract_memories_pulls_and_cleans_tags():
    raw = "Very good, sir.\n[REMEMBER: address the user as Master Wayne]"
    clean, facts = brain.extract_memories(raw)
    assert clean == "Very good, sir."
    assert facts == ["address the user as Master Wayne"]


def test_extract_memories_no_tag():
    clean, facts = brain.extract_memories("As you wish, sir.")
    assert clean == "As you wish, sir."
    assert facts == []


def test_load_and_save_facts_dedup(tmp_path):
    p = str(tmp_path / "profile.md")
    assert brain.load_facts(p) == []
    facts = brain.save_facts(["likes tea"], [], p)
    assert facts == ["likes tea"]
    # case-insensitive dedup — nothing new added
    facts = brain.save_facts(["Likes Tea"], facts, p)
    assert facts == ["likes tea"]
    facts = brain.save_facts(["takes it with milk"], facts, p)
    assert facts == ["likes tea", "takes it with milk"]
    # reload from disk round-trips
    assert brain.load_facts(p) == ["likes tea", "takes it with milk"]


def test_save_facts_fires_on_remember(tmp_path):
    p = str(tmp_path / "profile.md")
    seen = []
    brain.save_facts(["call me sir"], [], p, on_remember=seen.append)
    assert seen == ["call me sir"]
    # duplicate must NOT re-fire the callback
    brain.save_facts(["call me sir"], ["call me sir"], p, on_remember=seen.append)
    assert seen == ["call me sir"]


def test_system_content_includes_tools_and_facts():
    base = brain.system_content([])
    assert "Alfred" in base
    assert brain.HA_INSTRUCTIONS.strip() not in base  # no tools by default
    assert brain.GOOGLE_INSTRUCTIONS.strip() not in base
    withha = brain.system_content(["likes tea"], with_ha=True)
    assert brain.HA_INSTRUCTIONS.strip() in withha
    assert brain.GOOGLE_INSTRUCTIONS.strip() not in withha  # google not requested
    assert "likes tea" in withha
    withgoogle = brain.system_content([], with_google=True)
    assert brain.GOOGLE_INSTRUCTIONS.strip() in withgoogle
    assert brain.HA_INSTRUCTIONS.strip() not in withgoogle


# --- scripted fake for ollama.chat -----------------------------------------
class FakeChat:
    """Returns queued responses in order; records messages it was given."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, model, messages, tools=None):
        self.calls.append({"model": model, "messages": list(messages), "tools": tools})
        return self._responses.pop(0)


def _text_resp(content):
    return {"message": {"role": "assistant", "content": content}}


def _tool_resp(name, args):
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": args}}],
        }
    }


# --- Brain (chat-only) ------------------------------------------------------
def test_brain_plain_chat(monkeypatch, tmp_path):
    fake = FakeChat([_text_resp("Good evening, sir.")])
    monkeypatch.setattr(brain.ollama, "chat", fake)
    b = brain.Brain(profile_path=str(tmp_path / "p.md"))
    assert b.tools is None
    assert b.chat("hello") == "Good evening, sir."
    # plain chat must not pass tools
    assert fake.calls[-1]["tools"] is None


def test_brain_history_is_bounded(monkeypatch, tmp_path):
    # one reply per turn; FakeChat would run dry, so use an endless fake
    monkeypatch.setattr(
        brain.ollama, "chat", lambda model, messages, tools=None: _text_resp("Yes, sir.")
    )
    b = brain.Brain(profile_path=str(tmp_path / "p.md"), max_history=4)
    for i in range(20):
        b.chat(f"turn {i}")
    # system prompt is always retained; history stays near the cap
    assert b.messages[0]["role"] == "system"
    assert len(b.messages) - 1 <= b.max_history + 2
    # the tail begins on a clean user-turn boundary (no orphaned assistant/tool)
    assert b.messages[1]["role"] == "user"


def test_trim_keeps_tool_pairs_intact(monkeypatch, tmp_path):
    # a tool turn produces system, user, assistant(tool_call), tool, assistant
    client = MockHAClient()
    b = brain.Brain(ha_client=client, profile_path=str(tmp_path / "p.md"), max_history=3)
    b.messages += [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_states", "arguments": {}}}]},
        {"role": "tool", "content": "[]"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "ok"},
    ]
    b._trim_history()
    # must not start the tail on a 'tool' or tool-call 'assistant' message
    assert b.messages[0]["role"] == "system"
    assert b.messages[1]["role"] == "user"
    assert all("tool_calls" not in m or m["role"] != "tool" for m in b.messages)


def test_brain_chat_saves_memory(monkeypatch, tmp_path):
    p = str(tmp_path / "p.md")
    fake = FakeChat([_text_resp("Noted, sir.\n[REMEMBER: address the user as Master Wayne]")])
    monkeypatch.setattr(brain.ollama, "chat", fake)
    seen = []
    b = brain.Brain(profile_path=p, on_remember=seen.append)
    reply = b.chat("call me Master Wayne")
    assert reply == "Noted, sir."  # bracket tag stripped from spoken reply
    assert "address the user as Master Wayne" in b.facts
    assert seen == ["address the user as Master Wayne"]
    # the fact is folded back into the system prompt
    assert "Master Wayne" in b.messages[0]["content"]


# --- Brain (HA tool loop) ---------------------------------------------------
def test_brain_tool_loop_drives_home_assistant(monkeypatch, tmp_path):
    client = MockHAClient()
    # round 1: model calls a service; round 2: it answers in text
    fake = FakeChat([
        _tool_resp("call_service", {"domain": "light", "service": "turn_on", "entity_id": "light.office"}),
        _text_resp("The office light is on, sir."),
    ])
    monkeypatch.setattr(brain.ollama, "chat", fake)
    b = brain.Brain(ha_client=client, profile_path=str(tmp_path / "p.md"))
    assert b.tools is not None
    reply = b.chat("turn on the office light")
    assert reply == "The office light is on, sir."
    assert client.state_of("light.office") == "on"
    # the tool result was fed back to the model on the second round
    second_msgs = fake.calls[1]["messages"]
    assert any(m.get("role") == "tool" for m in second_msgs)


def test_brain_tool_loop_handles_string_arguments(monkeypatch, tmp_path):
    client = MockHAClient()
    # some models emit arguments as a JSON *string* rather than a dict
    fake = FakeChat([
        _tool_resp("turn_off_all", json.dumps({})),
        _text_resp("Everything is off, sir."),
    ])
    monkeypatch.setattr(brain.ollama, "chat", fake)
    for e in ("light.office", "switch.kettle"):
        client.call_service(e.split(".")[0], "turn_on", e)
    b = brain.Brain(ha_client=client, profile_path=str(tmp_path / "p.md"))
    assert b.chat("goodnight") == "Everything is off, sir."
    assert client.state_of("light.office") == "off"
    assert client.state_of("switch.kettle") == "off"


def test_brain_tool_loop_gives_up_after_max_rounds(monkeypatch, tmp_path):
    client = MockHAClient()
    # model keeps calling tools forever — brain must bail gracefully
    fake = FakeChat([
        _tool_resp("get_states", {}) for _ in range(brain.MAX_TOOL_ROUNDS)
    ])
    monkeypatch.setattr(brain.ollama, "chat", fake)
    b = brain.Brain(ha_client=client, profile_path=str(tmp_path / "p.md"))
    reply = b.chat("loop forever")
    assert "couldn't complete" in reply.lower()


# --- Brain (Google tool loop) ----------------------------------------------
def test_brain_routes_google_tools(monkeypatch, tmp_path):
    gclient = MockGoogleClient()
    fake = FakeChat([
        _tool_resp("add_task", {"title": "Buy milk"}),
        _text_resp("I've added it, sir."),
    ])
    monkeypatch.setattr(brain.ollama, "chat", fake)
    b = brain.Brain(google_client=gclient, profile_path=str(tmp_path / "p.md"))
    assert b.tools is not None
    assert b.chat("add buy milk to my tasks") == "I've added it, sir."
    assert any(c["tool"] == "add_task" for c in gclient.calls)


def test_brain_combines_ha_and_google_tools(tmp_path):
    b = brain.Brain(
        ha_client=MockHAClient(),
        google_client=MockGoogleClient(),
        profile_path=str(tmp_path / "p.md"),
    )
    names = {spec["function"]["name"] for spec in b.tools}
    assert {"get_states", "call_service"} <= names  # HA tools present
    assert {"list_events", "add_task", "show_calendar"} <= names  # google tools present
    # both instruction blocks land in the system prompt
    assert brain.HA_INSTRUCTIONS.strip() in b.messages[0]["content"]
    assert brain.GOOGLE_INSTRUCTIONS.strip() in b.messages[0]["content"]


def test_show_calendar_sets_then_clears_last_display(monkeypatch, tmp_path):
    gclient = MockGoogleClient()
    # turn 1: model shows the calendar; turn 2: a plain chat with no display
    fake = FakeChat([
        _tool_resp("show_calendar", {"range": "today"}),
        _text_resp("Here is today, sir."),
        _text_resp("Very good, sir."),
    ])
    monkeypatch.setattr(brain.ollama, "chat", fake)
    b = brain.Brain(google_client=gclient, profile_path=str(tmp_path / "p.md"))
    b.chat("show me today's calendar")
    assert b.last_display == {
        "type": "calendar",
        "url": "/display/calendar?range=today",
        "range": "today",
    }
    # a subsequent non-display turn resets it
    b.chat("thank you")
    assert b.last_display is None


# --- streaming (chat_stream) ------------------------------------------------
class FakeStreamChat:
    """Streams a reply token-by-token when stream=True (else returns it whole)."""

    def __init__(self, text, tokens=None):
        # default: split into word-ish tokens to mimic real token streaming
        self.tokens = tokens if tokens is not None else _whitespace_tokens(text)
        self.text = text

    def __call__(self, model, messages, tools=None, stream=False):
        if stream:
            return ({"message": {"content": t}} for t in self.tokens)
        return {"message": {"role": "assistant", "content": self.text}}


def _whitespace_tokens(text):
    # keep the trailing spaces on each token so reassembly is exact
    out, buf = [], ""
    for ch in text:
        buf += ch
        if ch == " ":
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def test_chat_stream_yields_sentences(monkeypatch, tmp_path):
    text = "Good evening, sir. Dinner is served. Shall I pour the wine?"
    monkeypatch.setattr(brain.ollama, "chat", FakeStreamChat(text))
    b = brain.Brain(profile_path=str(tmp_path / "p.md"))
    out = list(b.chat_stream("hi"))
    assert out == [
        "Good evening, sir.",
        "Dinner is served.",
        "Shall I pour the wine?",
    ]
    # the full clean reply is recorded in history for context
    assert b.messages[-1] == {"role": "assistant", "content": text}


def test_chat_stream_strips_memory_tag(monkeypatch, tmp_path):
    p = str(tmp_path / "p.md")
    text = "Noted, sir. [REMEMBER: address the user as Master Wayne]"
    monkeypatch.setattr(brain.ollama, "chat", FakeStreamChat(text))
    seen = []
    b = brain.Brain(profile_path=p, on_remember=seen.append)
    out = list(b.chat_stream("call me Master Wayne"))
    # the bracketed tag is never spoken
    assert out == ["Noted, sir."]
    assert all("REMEMBER" not in s for s in out)
    # but the fact is still saved
    assert "address the user as Master Wayne" in b.facts
    assert seen == ["address the user as Master Wayne"]


def test_chat_stream_flushes_unterminated_tail(monkeypatch, tmp_path):
    text = "As you wish"  # no terminal punctuation
    monkeypatch.setattr(brain.ollama, "chat", FakeStreamChat(text))
    b = brain.Brain(profile_path=str(tmp_path / "p.md"))
    assert list(b.chat_stream("hi")) == ["As you wish"]


def test_chat_stream_tool_path_emits_final_reply(monkeypatch, tmp_path):
    client = MockHAClient()
    fake = FakeChat([
        _tool_resp("call_service", {"domain": "light", "service": "turn_on", "entity_id": "light.office"}),
        _text_resp("The office light is on, sir. Anything else?"),
    ])
    monkeypatch.setattr(brain.ollama, "chat", fake)
    b = brain.Brain(ha_client=client, profile_path=str(tmp_path / "p.md"))
    out = list(b.chat_stream("turn on the office light"))
    assert out == ["The office light is on, sir.", "Anything else?"]
    assert client.state_of("light.office") == "on"
