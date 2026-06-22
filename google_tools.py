"""Google Calendar + Google Tasks tool layer for the Alfred brain.

This is the calendar/tasks sibling of `ha_tools.py`: it exposes the user's
*personal* Google Calendar and Tasks to the LLM as callable tools, so Alfred can
read the schedule, add events, and manage a to-do list. It is the one feature
that reaches the internet — the LLM, voice, and home control stay fully local;
only these tools talk to Google (free personal API, the user's own data).

Two backends share one interface (see the `GoogleClient` Protocol):
  * GoogleAPIClient — the real client; talks to Google's Calendar v3 / Tasks v1
    APIs using a personal OAuth token stored on the brain (the PC). Written in a
    later step, behind the same interface this mock proves.
  * MockGoogleClient — an in-memory fake with a few demo events/tasks, so model
    tool-calling can be validated on a machine with no Google credentials (the
    Mac), exactly as we validated Home Assistant with MockHAClient.

`TOOL_SCHEMA` is the function-calling spec handed to Ollama; `dispatch()` routes
a model tool call to whichever client is active and returns a JSON-friendly dict.

Write tools (create_event, add_task, complete_task) perform the action when
called; Alfred is expected to *confirm with the user before calling them* — that
guard lives in the brain's persona instructions, not here.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import os
from typing import Any, Optional, Protocol


# --- Tool schema handed to the model -----------------------------------------

TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": (
                "Read upcoming events from the user's Google Calendar. Call this "
                "before answering any question about their schedule, what's on "
                "today, when something is, or whether they're free."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "time_min": {
                        "type": "string",
                        "description": (
                            "ISO 8601 start of the window, e.g. "
                            "'2026-06-22T00:00:00'. Omit for 'from now'."
                        ),
                    },
                    "time_max": {
                        "type": "string",
                        "description": "ISO 8601 end of the window. Omit for no upper bound.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of events to return (default 10).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": (
                "Create a new event on the user's Google Calendar. This changes "
                "their calendar — confirm the details with the user before calling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Event title."},
                    "start": {
                        "type": "string",
                        "description": "ISO 8601 start, e.g. '2026-06-23T14:00:00'.",
                    },
                    "end": {
                        "type": "string",
                        "description": "ISO 8601 end, e.g. '2026-06-23T15:00:00'.",
                    },
                    "description": {"type": "string", "description": "Optional event details."},
                    "location": {"type": "string", "description": "Optional location."},
                },
                "required": ["summary", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": (
                "Read the user's Google Tasks to-do list. Call this before "
                "answering questions about what they need to do, and to find a "
                "task's id before completing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "show_completed": {
                        "type": "boolean",
                        "description": "Include already-completed tasks (default false).",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": (
                "Add a new task to the user's Google Tasks list. This changes "
                "their list — confirm with the user before calling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "What the task is."},
                    "due": {
                        "type": "string",
                        "description": "Optional ISO 8601 due date, e.g. '2026-06-25'.",
                    },
                    "notes": {"type": "string", "description": "Optional extra notes."},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": (
                "Mark a task complete by its id. Call list_tasks first to find "
                "the id; never invent one. Confirm with the user before calling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task's id, from list_tasks."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_calendar",
            "description": (
                "Display the user's calendar visually on a screen (or projector). "
                "Use this when the user asks to *see* or *show* their calendar, "
                "not just hear it. Returns a display_url the satellite opens."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "range": {
                        "type": "string",
                        "description": "What to show: 'today', 'week', or 'month' (default 'week').",
                    }
                },
            },
        },
    },
]


_VALID_RANGES = ("today", "week", "month")


class GoogleClient(Protocol):
    def list_events(
        self,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 10,
    ) -> list[dict]: ...
    def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        description: Optional[str] = None,
        location: Optional[str] = None,
    ) -> dict: ...
    def list_tasks(self, show_completed: bool = False) -> list[dict]: ...
    def add_task(
        self, title: str, due: Optional[str] = None, notes: Optional[str] = None
    ) -> dict: ...
    def complete_task(self, task_id: str) -> dict: ...
    def show_calendar(self, range: str = "week") -> dict: ...


def dispatch(client: GoogleClient, name: str, arguments: dict) -> Any:
    """Route one model tool call to the active client. Never raises — failures
    come back as {'error': ...} so the model can read and recover."""
    try:
        if name == "list_events":
            return client.list_events(
                time_min=arguments.get("time_min"),
                time_max=arguments.get("time_max"),
                max_results=int(arguments.get("max_results", 10)),
            )
        if name == "create_event":
            return client.create_event(
                summary=arguments["summary"],
                start=arguments["start"],
                end=arguments["end"],
                description=arguments.get("description"),
                location=arguments.get("location"),
            )
        if name == "list_tasks":
            return client.list_tasks(show_completed=bool(arguments.get("show_completed", False)))
        if name == "add_task":
            return client.add_task(
                title=arguments["title"],
                due=arguments.get("due"),
                notes=arguments.get("notes"),
            )
        if name == "complete_task":
            return client.complete_task(task_id=arguments["task_id"])
        if name == "show_calendar":
            return client.show_calendar(range=arguments.get("range", "week"))
        return {"error": f"unknown tool: {name}"}
    except KeyError as e:
        return {"error": f"missing required argument: {e}"}
    except Exception as e:  # surface transport/API errors to the model
        return {"error": str(e)}


# --- Mock client (no Google account needed) ----------------------------------

_DEMO_EVENTS = [
    {
        "id": "evt_1",
        "summary": "Dentist appointment",
        "start": "2026-06-23T09:30:00",
        "end": "2026-06-23T10:15:00",
        "location": "12 High Street",
    },
    {
        "id": "evt_2",
        "summary": "Team standup",
        "start": "2026-06-23T14:00:00",
        "end": "2026-06-23T14:30:00",
    },
    {
        "id": "evt_3",
        "summary": "Dinner with Alice",
        "start": "2026-06-25T19:00:00",
        "end": "2026-06-25T21:00:00",
        "location": "The Ivy",
    },
]

_DEMO_TASKS = [
    {"id": "task_1", "title": "Buy groceries", "status": "needsAction", "due": "2026-06-23"},
    {"id": "task_2", "title": "Call the bank", "status": "needsAction", "due": None},
    {"id": "task_3", "title": "Renew passport", "status": "completed", "due": None},
]


class MockGoogleClient:
    """In-memory Google Calendar + Tasks stand-in with demo data and recorded
    calls. Used to validate model tool-calling on a machine with no credentials."""

    def __init__(self) -> None:
        self._events = [dict(e) for e in _DEMO_EVENTS]
        self._tasks = [dict(t) for t in _DEMO_TASKS]
        self.calls: list[dict] = []
        self._next_id = 100

    def _new_id(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}_{self._next_id}"

    def list_events(
        self,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 10,
    ) -> list[dict]:
        self.calls.append({"tool": "list_events", "time_min": time_min, "time_max": time_max})
        out = []
        for e in sorted(self._events, key=lambda e: e["start"]):
            if time_min and e["end"] < time_min:
                continue
            if time_max and e["start"] > time_max:
                continue
            out.append(dict(e))
        return out[:max_results]

    def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        description: Optional[str] = None,
        location: Optional[str] = None,
    ) -> dict:
        event = {"id": self._new_id("evt"), "summary": summary, "start": start, "end": end}
        if description:
            event["description"] = description
        if location:
            event["location"] = location
        self._events.append(event)
        self.calls.append({"tool": "create_event", "summary": summary, "start": start})
        return {"success": True, "event": event}

    def list_tasks(self, show_completed: bool = False) -> list[dict]:
        self.calls.append({"tool": "list_tasks", "show_completed": show_completed})
        return [
            dict(t)
            for t in self._tasks
            if show_completed or t["status"] != "completed"
        ]

    def add_task(
        self, title: str, due: Optional[str] = None, notes: Optional[str] = None
    ) -> dict:
        task = {"id": self._new_id("task"), "title": title, "status": "needsAction", "due": due}
        if notes:
            task["notes"] = notes
        self._tasks.append(task)
        self.calls.append({"tool": "add_task", "title": title})
        return {"success": True, "task": task}

    def complete_task(self, task_id: str) -> dict:
        self.calls.append({"tool": "complete_task", "task_id": task_id})
        for t in self._tasks:
            if t["id"] == task_id:
                t["status"] = "completed"
                return {"success": True, "task": dict(t)}
        return {"error": f"no such task: {task_id}"}

    def show_calendar(self, range: str = "week") -> dict:
        if range not in _VALID_RANGES:
            range = "week"
        self.calls.append({"tool": "show_calendar", "range": range})
        return {
            "success": True,
            "display_url": f"/display/calendar?range={range}",
            "range": range,
        }


# --- Real client over Google Calendar v3 + Tasks v1 --------------------------
# Personal Google account, OAuth token stored on the brain (the PC). The Google
# client libraries are imported lazily so this module still loads on a machine
# without them (the Mac only ever needs the mock). Service objects may be
# injected for testing; otherwise they're built on first use from the token.

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]

DEFAULT_CREDENTIALS_PATH = os.path.expanduser("~/.alfred/google_credentials.json")
DEFAULT_TOKEN_PATH = os.path.expanduser("~/.alfred/google_token.json")


def _local_tz() -> _dt.tzinfo:
    return _dt.datetime.now().astimezone().tzinfo


def _to_rfc3339(iso: Optional[str]) -> Optional[str]:
    """Coerce a possibly-naive ISO 8601 string to an RFC3339 timestamp carrying a
    timezone offset (Google's APIs require one). Naive inputs are assumed to be
    in the brain's local timezone; offset-bearing inputs pass through unchanged.
    None passes through; an unparseable string is handed to Google as-is."""
    if not iso:
        return None
    try:
        dt = _dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_tz())
    return dt.isoformat()


def _event_dt(node: dict) -> str:
    """Pull the timestamp from a Calendar start/end node, which is either
    {'dateTime': ...} (timed) or {'date': ...} (all-day)."""
    return node.get("dateTime") or node.get("date") or ""


def _normalize_event(item: dict) -> dict:
    """Map a Calendar v3 event resource to our flat internal shape."""
    out = {
        "id": item.get("id"),
        "summary": item.get("summary", "(untitled)"),
        "start": _event_dt(item.get("start", {})),
        "end": _event_dt(item.get("end", {})),
    }
    if item.get("location"):
        out["location"] = item["location"]
    if item.get("description"):
        out["description"] = item["description"]
    return out


def _normalize_task(item: dict) -> dict:
    """Map a Tasks v1 task resource to our flat internal shape."""
    out = {
        "id": item.get("id"),
        "title": item.get("title", ""),
        "status": item.get("status", "needsAction"),
        "due": item.get("due"),
    }
    if item.get("notes"):
        out["notes"] = item["notes"]
    return out


class GoogleAPIClient:
    """Real Google Calendar v3 + Tasks v1 client, behind the same interface the
    mock proves. show_calendar needs no network — it only returns the display URL
    the satellite opens, identical to the mock."""

    def __init__(
        self,
        credentials_path: str = DEFAULT_CREDENTIALS_PATH,
        token_path: str = DEFAULT_TOKEN_PATH,
        calendar_id: str = "primary",
        tasklist_id: str = "@default",
        calendar_service=None,
        tasks_service=None,
    ) -> None:
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.calendar_id = calendar_id
        self.tasklist_id = tasklist_id
        self._cal = calendar_service
        self._tasks = tasks_service

    # -- auth + lazily-built services -------------------------------------
    def _creds(self):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        if not os.path.exists(self.token_path):
            raise RuntimeError(
                f"no Google token at {self.token_path}; run authorize_google.py first"
            )
        creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(self.token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
            else:
                raise RuntimeError("Google token invalid; re-run authorize_google.py")
        return creds

    def _calendar(self):
        if self._cal is None:
            from googleapiclient.discovery import build

            self._cal = build(
                "calendar", "v3", credentials=self._creds(), cache_discovery=False
            )
        return self._cal

    def _tasks_svc(self):
        if self._tasks is None:
            from googleapiclient.discovery import build

            self._tasks = build(
                "tasks", "v1", credentials=self._creds(), cache_discovery=False
            )
        return self._tasks

    # -- calendar ---------------------------------------------------------
    def list_events(
        self,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 10,
    ) -> list[dict]:
        params = {
            "calendarId": self.calendar_id,
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": max_results,
            "timeMin": _to_rfc3339(time_min)
            or _dt.datetime.now(_local_tz()).isoformat(),
        }
        if time_max:
            params["timeMax"] = _to_rfc3339(time_max)
        resp = self._calendar().events().list(**params).execute()
        return [_normalize_event(e) for e in resp.get("items", [])]

    def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        description: Optional[str] = None,
        location: Optional[str] = None,
    ) -> dict:
        body = {
            "summary": summary,
            "start": {"dateTime": _to_rfc3339(start)},
            "end": {"dateTime": _to_rfc3339(end)},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        created = (
            self._calendar().events().insert(calendarId=self.calendar_id, body=body).execute()
        )
        return {"success": True, "event": _normalize_event(created)}

    # -- tasks ------------------------------------------------------------
    def list_tasks(self, show_completed: bool = False) -> list[dict]:
        params = {"tasklist": self.tasklist_id, "showCompleted": show_completed}
        if show_completed:
            params["showHidden"] = True
        resp = self._tasks_svc().tasks().list(**params).execute()
        return [_normalize_task(t) for t in resp.get("items", [])]

    def add_task(
        self, title: str, due: Optional[str] = None, notes: Optional[str] = None
    ) -> dict:
        body: dict[str, Any] = {"title": title}
        if due:
            body["due"] = _to_rfc3339(due)
        if notes:
            body["notes"] = notes
        created = self._tasks_svc().tasks().insert(tasklist=self.tasklist_id, body=body).execute()
        return {"success": True, "task": _normalize_task(created)}

    def complete_task(self, task_id: str) -> dict:
        updated = (
            self._tasks_svc()
            .tasks()
            .patch(tasklist=self.tasklist_id, task=task_id, body={"status": "completed"})
            .execute()
        )
        return {"success": True, "task": _normalize_task(updated)}

    def show_calendar(self, range: str = "week") -> dict:
        if range not in _VALID_RANGES:
            range = "week"
        return {
            "success": True,
            "display_url": f"/display/calendar?range={range}",
            "range": range,
        }


# --- Helpers shared with the real client -------------------------------------

def _now_iso() -> str:
    """Current local time as a naive ISO 8601 string (matches demo data)."""
    return _dt.datetime.now().replace(microsecond=0).isoformat()


# --- Calendar display rendering ----------------------------------------------
# A self-contained HTML view for a satellite screen or projector. Sizing is in
# `vw` units so the same page reads well on a phone, a small panel, or a wall.

_RANGE_TITLES = {"today": "Today", "week": "This Week", "month": "This Month"}

_CALENDAR_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alfred — __TITLE__</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font-family: Georgia, 'Times New Roman', serif;
         background: #0e0e10; color: #ece6d8; padding: 4vw; }
  h1 { font-size: 6vw; margin: 0 0 4vw; font-weight: normal;
       border-bottom: 1px solid #333; padding-bottom: 2vw; }
  section { margin-bottom: 4vw; }
  h2 { font-size: 3.2vw; color: #c9a227; margin: 0 0 1.5vw; }
  ul { list-style: none; margin: 0; padding: 0; }
  li { display: flex; align-items: baseline; gap: 2vw; font-size: 3vw;
       padding: 1.2vw 0; border-bottom: 1px solid #1d1d20; }
  .time { flex: 0 0 20vw; color: #9fb4c7; }
  .summary { flex: 1 1 auto; }
  .loc { color: #8a8a8a; font-style: italic; font-size: 2.2vw; }
  .empty { font-size: 3vw; color: #8a8a8a; }
</style>
</head>
<body>
  <h1>__TITLE__</h1>
  __BODY__
</body>
</html>
"""


def _fmt_time(iso: Optional[str]) -> str:
    """'2026-06-23T09:30:00' -> '9:30 AM'. Portable (no %-I / %#I)."""
    if not iso:
        return ""
    try:
        dt = _dt.datetime.fromisoformat(iso)
    except ValueError:
        return ""
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _fmt_day(day: str) -> str:
    """'2026-06-23' -> 'Tuesday, 23 June'."""
    try:
        d = _dt.date.fromisoformat(day)
    except ValueError:
        return day or "Unknown"
    return d.strftime("%A, %d %B")


def render_calendar_html(events: list[dict], range_label: str = "week") -> str:
    """Render a self-contained HTML calendar from event dicts, grouped by day.
    Pure function (no I/O) so it can be unit-tested directly. User-supplied text
    is HTML-escaped."""
    title = _RANGE_TITLES.get(range_label, "Calendar")

    by_day: dict[str, list[dict]] = {}
    for e in sorted(events, key=lambda e: e.get("start", "")):
        day = (e.get("start") or "")[:10]
        by_day.setdefault(day, []).append(e)

    sections: list[str] = []
    if not by_day:
        sections.append('<p class="empty">Nothing scheduled, sir.</p>')
    for day, items in by_day.items():
        rows = []
        for e in items:
            time = _fmt_time(e.get("start"))
            summary = _html.escape(e.get("summary", "(untitled)"))
            loc = e.get("location")
            loc_html = f'<span class="loc">{_html.escape(loc)}</span>' if loc else ""
            rows.append(
                f'<li><span class="time">{time}</span>'
                f'<span class="summary">{summary}</span>{loc_html}</li>'
            )
        sections.append(
            f"<section><h2>{_html.escape(_fmt_day(day))}</h2>"
            f'<ul>{"".join(rows)}</ul></section>'
        )

    return _CALENDAR_PAGE.replace("__TITLE__", _html.escape(title)).replace(
        "__BODY__", "".join(sections)
    )
