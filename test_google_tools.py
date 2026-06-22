"""Regression tests for the Google Calendar + Tasks tool layer (google_tools.py).

Run:  ~/assistant-env/bin/python -m pytest -q
These cover the mock client and the dispatch router — no real Google account,
credentials, or network needed.
"""
from google_tools import (
    GoogleAPIClient,
    MockGoogleClient,
    _normalize_event,
    _normalize_task,
    _to_rfc3339,
    dispatch,
    render_calendar_html,
)


# --- Calendar ----------------------------------------------------------------
def test_list_events_sorted_and_windowed():
    c = MockGoogleClient()
    events = c.list_events()
    assert [e["id"] for e in events] == ["evt_1", "evt_2", "evt_3"]  # sorted by start
    # window that only catches the dinner on the 25th
    later = c.list_events(time_min="2026-06-24T00:00:00")
    assert [e["id"] for e in later] == ["evt_3"]


def test_list_events_respects_max_results():
    c = MockGoogleClient()
    assert len(c.list_events(max_results=1)) == 1


def test_create_event_appends_and_is_listable():
    c = MockGoogleClient()
    out = c.create_event(
        summary="Haircut", start="2026-06-23T11:00:00", end="2026-06-23T11:30:00"
    )
    assert out["success"] is True
    new_id = out["event"]["id"]
    assert any(call["tool"] == "create_event" for call in c.calls)
    assert any(e["id"] == new_id for e in c.list_events())


# --- Tasks -------------------------------------------------------------------
def test_list_tasks_hides_completed_by_default():
    c = MockGoogleClient()
    ids = {t["id"] for t in c.list_tasks()}
    assert ids == {"task_1", "task_2"}  # task_3 is completed, hidden
    assert {t["id"] for t in c.list_tasks(show_completed=True)} == {"task_1", "task_2", "task_3"}


def test_add_task_then_complete_it():
    c = MockGoogleClient()
    added = c.add_task(title="Water the plants", due="2026-06-24")
    tid = added["task"]["id"]
    assert any(t["id"] == tid for t in c.list_tasks())
    done = c.complete_task(tid)
    assert done["success"] is True
    assert tid not in {t["id"] for t in c.list_tasks()}  # now hidden as completed


def test_complete_unknown_task_returns_error():
    c = MockGoogleClient()
    assert "error" in c.complete_task("task_nope")


# --- Display -----------------------------------------------------------------
def test_show_calendar_returns_display_url():
    c = MockGoogleClient()
    out = c.show_calendar(range="today")
    assert out["display_url"] == "/display/calendar?range=today"
    assert out["range"] == "today"


def test_show_calendar_defaults_bad_range_to_week():
    c = MockGoogleClient()
    assert c.show_calendar(range="decade")["range"] == "week"


# --- dispatch router ---------------------------------------------------------
def test_dispatch_routes_each_tool():
    c = MockGoogleClient()
    assert isinstance(dispatch(c, "list_events", {}), list)
    assert isinstance(dispatch(c, "list_tasks", {}), list)
    created = dispatch(
        c,
        "create_event",
        {"summary": "X", "start": "2026-06-23T08:00:00", "end": "2026-06-23T09:00:00"},
    )
    assert created["success"] is True
    added = dispatch(c, "add_task", {"title": "Y"})
    assert added["success"] is True
    assert dispatch(c, "complete_task", {"task_id": added["task"]["id"]})["success"] is True
    assert dispatch(c, "show_calendar", {"range": "month"})["range"] == "month"


def test_dispatch_unknown_tool_returns_error():
    c = MockGoogleClient()
    assert "error" in dispatch(c, "frobnicate", {})


def test_dispatch_missing_required_arg_returns_error():
    c = MockGoogleClient()
    # create_event requires summary/start/end; omit end
    out = dispatch(c, "create_event", {"summary": "X", "start": "2026-06-23T08:00:00"})
    assert "error" in out
    # add_task requires title
    assert "error" in dispatch(c, "add_task", {})
    # complete_task requires task_id
    assert "error" in dispatch(c, "complete_task", {})


# --- render_calendar_html ----------------------------------------------------
def test_render_calendar_shows_events_grouped_by_day():
    html = render_calendar_html(MockGoogleClient().list_events())
    # the range title for the default 'week'
    assert "This Week" in html
    # each demo event's summary appears
    assert "Dentist appointment" in html
    assert "Team standup" in html
    assert "Dinner with Alice" in html
    # times are rendered in 12-hour form
    assert "9:30 AM" in html
    assert "7:00 PM" in html
    # locations render when present
    assert "The Ivy" in html
    # grouped by day: two day headings (23rd and 25th)
    assert html.count("<section>") == 2
    assert "Tuesday, 23 June" in html
    assert "Thursday, 25 June" in html


def test_render_calendar_empty_shows_nothing_scheduled():
    html = render_calendar_html([])
    assert "Nothing scheduled, sir." in html
    assert "<section>" not in html


def test_render_calendar_escapes_user_text():
    events = [{
        "id": "x", "summary": "<script>alert(1)</script>",
        "start": "2026-06-23T10:00:00", "end": "2026-06-23T11:00:00",
        "location": "A & B <b>",
    }]
    html = render_calendar_html(events)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "A &amp; B &lt;b&gt;" in html


def test_render_calendar_range_titles():
    assert "Today" in render_calendar_html([], "today")
    assert "This Month" in render_calendar_html([], "month")
    # an unknown range falls back to the generic "Calendar" title
    assert "Calendar" in render_calendar_html([], "decade")


# --- real client normalizers (pure) ------------------------------------------
def test_to_rfc3339_adds_offset_to_naive_and_preserves_aware():
    out = _to_rfc3339("2026-06-23T09:30:00")
    assert out.startswith("2026-06-23T09:30:00")
    assert out != "2026-06-23T09:30:00"  # a tz offset was appended
    # already-offset input is preserved verbatim
    assert _to_rfc3339("2026-06-23T09:30:00+01:00") == "2026-06-23T09:30:00+01:00"
    assert _to_rfc3339(None) is None


def test_normalize_event_handles_timed_and_all_day():
    timed = _normalize_event({
        "id": "g1", "summary": "Standup",
        "start": {"dateTime": "2026-06-23T14:00:00-04:00"},
        "end": {"dateTime": "2026-06-23T14:30:00-04:00"},
        "location": "Zoom",
    })
    assert timed == {
        "id": "g1", "summary": "Standup",
        "start": "2026-06-23T14:00:00-04:00", "end": "2026-06-23T14:30:00-04:00",
        "location": "Zoom",
    }
    all_day = _normalize_event({
        "id": "g2", "start": {"date": "2026-06-24"}, "end": {"date": "2026-06-25"}
    })
    assert all_day["start"] == "2026-06-24"
    assert all_day["summary"] == "(untitled)"  # missing summary gets a placeholder


def test_normalize_task_defaults_status():
    t = _normalize_task({"id": "t1", "title": "Pay rent", "due": "2026-06-25T00:00:00.000Z"})
    assert t == {"id": "t1", "title": "Pay rent", "status": "needsAction",
                 "due": "2026-06-25T00:00:00.000Z"}


# --- real client over injected fake services (no network, no creds) ----------
class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeCalendarService:
    def __init__(self, items=None, created=None):
        self._items = items or []
        self._created = created
        self.list_kwargs = None
        self.insert_body = None

    def events(self):
        return self

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        return _Exec({"items": self._items})

    def insert(self, calendarId=None, body=None):
        self.insert_body = body
        return _Exec(self._created or {**body, "id": "evt_new"})


class FakeTasksService:
    def __init__(self, items=None, created=None, patched=None):
        self._items = items or []
        self._created = created
        self._patched = patched
        self.list_kwargs = None
        self.insert_body = None
        self.patch_kwargs = None

    def tasks(self):
        return self

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        return _Exec({"items": self._items})

    def insert(self, tasklist=None, body=None):
        self.insert_body = body
        return _Exec(self._created or {**body, "id": "t_new"})

    def patch(self, **kwargs):
        self.patch_kwargs = kwargs
        return _Exec(self._patched or {"id": kwargs.get("task"), "status": "completed"})


def test_api_client_list_events_normalizes_and_windows():
    items = [
        {"id": "g1", "summary": "Standup",
         "start": {"dateTime": "2026-06-23T14:00:00-04:00"},
         "end": {"dateTime": "2026-06-23T14:30:00-04:00"}},
        {"id": "g2", "summary": "Holiday",
         "start": {"date": "2026-06-24"}, "end": {"date": "2026-06-25"}},
    ]
    cal = FakeCalendarService(items=items)
    client = GoogleAPIClient(calendar_service=cal)
    events = client.list_events(time_min="2026-06-23T00:00:00", max_results=5)
    assert [e["id"] for e in events] == ["g1", "g2"]
    assert events[1]["start"] == "2026-06-24"  # all-day uses the date node
    # the request carried our params and coerced the naive window to an offset
    assert cal.list_kwargs["maxResults"] == 5
    assert cal.list_kwargs["singleEvents"] is True
    assert cal.list_kwargs["timeMin"].startswith("2026-06-23T00:00:00")
    assert cal.list_kwargs["timeMin"] != "2026-06-23T00:00:00"


def test_api_client_list_events_defaults_timemin_to_now():
    cal = FakeCalendarService()
    GoogleAPIClient(calendar_service=cal).list_events()
    assert cal.list_kwargs["timeMin"]  # a default 'now' was supplied
    assert "timeMax" not in cal.list_kwargs


def test_api_client_create_event_builds_body():
    cal = FakeCalendarService(created={
        "id": "evt_x", "summary": "Lunch",
        "start": {"dateTime": "2026-06-23T12:00:00-04:00"},
        "end": {"dateTime": "2026-06-23T13:00:00-04:00"},
    })
    client = GoogleAPIClient(calendar_service=cal)
    out = client.create_event(
        "Lunch", "2026-06-23T12:00:00", "2026-06-23T13:00:00", location="Cafe"
    )
    assert out["success"] is True
    assert out["event"]["id"] == "evt_x"
    assert cal.insert_body["summary"] == "Lunch"
    assert cal.insert_body["location"] == "Cafe"
    assert "dateTime" in cal.insert_body["start"]


def test_api_client_list_tasks_passes_show_flags():
    items = [
        {"id": "t1", "title": "Pay rent", "status": "needsAction"},
        {"id": "t2", "title": "Done", "status": "completed"},
    ]
    tsvc = FakeTasksService(items=items)
    client = GoogleAPIClient(tasks_service=tsvc)
    tasks = client.list_tasks(show_completed=True)
    assert [t["id"] for t in tasks] == ["t1", "t2"]
    assert tsvc.list_kwargs["showCompleted"] is True
    assert tsvc.list_kwargs["showHidden"] is True
    # default hides completed and does not ask for hidden
    GoogleAPIClient(tasks_service=FakeTasksService()).list_tasks()


def test_api_client_add_and_complete_task():
    tsvc = FakeTasksService(
        created={"id": "t9", "title": "New", "status": "needsAction"},
        patched={"id": "t9", "title": "New", "status": "completed"},
    )
    client = GoogleAPIClient(tasks_service=tsvc)
    added = client.add_task("New", due="2026-06-25")
    assert added["task"]["id"] == "t9"
    assert "due" in tsvc.insert_body  # due coerced and included
    done = client.complete_task("t9")
    assert done["task"]["status"] == "completed"
    assert tsvc.patch_kwargs["task"] == "t9"
    assert tsvc.patch_kwargs["body"] == {"status": "completed"}


def test_api_client_show_calendar_needs_no_network():
    # no services and no token wired in — show_calendar must not touch either
    client = GoogleAPIClient()
    out = client.show_calendar(range="today")
    assert out["display_url"] == "/display/calendar?range=today"
    assert client.show_calendar(range="decade")["range"] == "week"  # bad range -> week
