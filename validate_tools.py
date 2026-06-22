"""Phase 1 make-or-break gate: can a local 7B reliably drive Home Assistant?

Runs the README "Step 3" test on the Mac with a MOCKED Home Assistant, so it
needs no HA instance and no smart devices. For each natural-language command it:
  1. sends the command to the model with the HA tool schema,
  2. executes whatever tool calls the model makes against the mock,
  3. checks the resulting device state (and that read-only questions did NOT
     mutate anything).

Because the real question is *reliability*, each case is run several times and a
pass-rate is reported. A model that drives the mock dependably here is the green
light to wire the real WebSocket client to a live HA on the PC.

    ~/assistant-env/bin/python validate_tools.py
    ~/assistant-env/bin/python validate_tools.py --model qwen2.5:7b --runs 5
    ~/assistant-env/bin/python validate_tools.py --model llama3.2:3b   # baseline
"""
from __future__ import annotations

import argparse
import json

import ollama

from ha_tools import TOOL_SCHEMA, MockHAClient, dispatch

SYSTEM_PROMPT = (
    "You are the home-control brain for a smart home running Home Assistant. "
    "You have two tools: get_states (read entity states) and call_service "
    "(control devices). When the user asks you to do something physical, call "
    "the appropriate service. When they ask about the state of something, call "
    "get_states first and answer from the result. Entity ids look like "
    "'light.office' or 'switch.kettle'. If you are unsure which entity a request "
    "refers to, call get_states to discover the available entities. Do not "
    "invent entity ids. To turn off many or all devices at once (e.g. 'turn "
    "everything off'), use the turn_off_all tool — not a wildcard and not many "
    "separate calls. Keep spoken replies short."
)

MAX_STEPS = 6  # tool-call rounds before we give up on a single turn


def run_turn(model: str, client: MockHAClient, user_msg: str) -> tuple[str, list]:
    """Drive one user message to completion; return (final_text, tool_calls)."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    made: list[tuple[str, dict]] = []
    for _ in range(MAX_STEPS):
        resp = ollama.chat(model=model, messages=messages, tools=TOOL_SCHEMA)
        msg = resp["message"]
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            return (msg.get("content") or "").strip(), made
        for call in calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            made.append((name, args))
            result = dispatch(client, name, args)
            messages.append({"role": "tool", "content": json.dumps(result)})
    return "(max tool-call rounds reached)", made


# Each case: a prompt and a check(client, calls) -> bool over the post-run state.
def _all_off(c: MockHAClient) -> bool:
    return all(
        c.state_of(e) == "off"
        for e in ("light.office", "light.living_room", "light.bedroom", "switch.kettle")
    )


CASES = [
    {
        "name": "explicit on",
        "prompt": "Turn on the office light.",
        "check": lambda c, calls: c.state_of("light.office") == "on",
    },
    {
        "name": "explicit off",
        "prompt": "Switch off the bedroom light.",
        "setup": lambda c: c.call_service("light", "turn_on", "light.bedroom"),
        "check": lambda c, calls: c.state_of("light.bedroom") == "off",
    },
    {
        "name": "implicit intent",
        "prompt": "It's too dark in the living room.",
        "check": lambda c, calls: c.state_of("light.living_room") == "on",
    },
    {
        "name": "natural phrasing",
        "prompt": "Put the kettle on, please.",
        "check": lambda c, calls: c.state_of("switch.kettle") == "on",
    },
    {
        "name": "multi-entity",
        "prompt": "Turn everything off for the night.",
        "setup": lambda c: [
            c.call_service("light", "turn_on", e)
            for e in ("light.office", "light.living_room", "light.bedroom")
        ],
        "check": lambda c, calls: _all_off(c),
    },
    {
        "name": "scoped all-off",
        "prompt": "Turn off all the lights.",
        "setup": lambda c: [
            c.call_service("light", "turn_on", e)
            for e in ("light.office", "light.living_room", "light.bedroom")
        ]
        + [c.call_service("switch", "turn_on", "switch.kettle")],
        # all lights off, but the kettle (a switch) must be left alone
        "check": lambda c, calls: (
            all(c.state_of(e) == "off" for e in ("light.office", "light.living_room", "light.bedroom"))
            and c.state_of("switch.kettle") == "on"
        ),
    },
    {
        "name": "read-only (no mutation)",
        "prompt": "Is the office light on?",
        "setup": lambda c: c.call_service("light", "turn_on", "light.office"),
        # must NOT call_service; should answer from get_states only
        "check": lambda c, calls: not any(n == "call_service" for n, _ in calls),
    },
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate local LLM HA tool-calling")
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--runs", type=int, default=3, help="repeats per case (reliability)")
    ap.add_argument("--verbose", action="store_true", help="print tool calls per run")
    args = ap.parse_args()

    print(f"Model: {args.model}   Runs per case: {args.runs}\n" + "=" * 56)
    grand_pass = grand_total = 0
    for case in CASES:
        passes = 0
        for i in range(args.runs):
            client = MockHAClient()
            if "setup" in case:
                case["setup"](client)
            text, calls = run_turn(args.model, client, case["prompt"])
            ok = bool(case["check"](client, calls))
            passes += ok
            if args.verbose:
                pretty = ", ".join(f"{n}({a})" for n, a in calls) or "(no tool calls)"
                print(f"  [{case['name']}] run {i+1}: {'PASS' if ok else 'FAIL'} | {pretty}")
                if text:
                    print(f"      reply: {text}")
        grand_pass += passes
        grand_total += args.runs
        rate = passes / args.runs * 100
        bar = "PASS" if passes == args.runs else ("WARN" if passes else "FAIL")
        print(f"[{bar}] {case['name']:<22} {passes}/{args.runs}  ({rate:.0f}%)")

    print("=" * 56)
    print(f"Overall: {grand_pass}/{grand_total} ({grand_pass / grand_total * 100:.0f}%)")
    if grand_pass == grand_total:
        print("Green light: wire the real WebSocket client to a live HA on the PC.")
    else:
        print("Some cases flaky — try a larger model, or add semantic tool wrappers.")


if __name__ == "__main__":
    main()
