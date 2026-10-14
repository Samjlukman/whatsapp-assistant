"""Calendar agent: creates, updates and deletes events in your primary Google Calendar.

First-time setup: put credentials.json (OAuth client, type "Desktop app") next to this file and run
    python calendar_service.py
once to sign in in the browser; it saves token.json.
"""
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import HERE

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CREDENTIALS = HERE / "credentials.json"
TOKEN = HERE / "token.json"
COLORS = {"Uni": "9", "Work": "6", "PPI": "10"}  # blueberry, tangerine, basil


class CalendarError(Exception):
    pass


def _service():
    if not TOKEN.exists():
        raise CalendarError("Google Calendar isn't connected. Run `python calendar_service.py` once.")
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except Exception as e:
            raise CalendarError(f"Google sign-in expired ({e}). Run `python calendar_service.py` again.") from e
        TOKEN.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _body(name: str, category: str, start: datetime, end: datetime) -> dict:
    if start == end:  # deadlines are stored as a single instant; show a 30-minute block ending at the due time
        start = end - timedelta(minutes=30)
    return {
        "summary": f"[{category}] {name}",
        "colorId": COLORS[category],
        "start": {"dateTime": start.isoformat(), "timeZone": "Europe/London"},  # aware isoformat = RFC 3339
        "end": {"dateTime": end.isoformat(), "timeZone": "Europe/London"},
    }


def create_event(name, category, start, end) -> tuple[str, str]:
    """Returns (event id, event URL)."""
    try:
        ev = _service().events().insert(calendarId="primary", body=_body(name, category, start, end)).execute()
    except HttpError as e:
        raise CalendarError(f"Google Calendar refused the event: {e.reason}") from e
    return ev["id"], ev["htmlLink"]


def update_event(event_id, name, category, start, end) -> str:
    try:
        ev = _service().events().patch(calendarId="primary", eventId=event_id,
                                       body=_body(name, category, start, end)).execute()
    except HttpError as e:
        raise CalendarError(f"Google Calendar refused the update: {e.reason}") from e
    return ev["htmlLink"]


def delete_event(event_id) -> None:
    try:
        _service().events().delete(calendarId="primary", eventId=event_id).execute()
    except HttpError as e:
        if e.resp.status not in (404, 410):  # already deleted by hand is fine
            raise CalendarError(f"Google Calendar refused the delete: {e.reason}") from e


if __name__ == "__main__":
    if not CREDENTIALS.exists():
        raise SystemExit(f"Put your OAuth client file at {CREDENTIALS} first.")
    creds = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES).run_local_server(port=0)
    TOKEN.write_text(creds.to_json())
    print("Google Calendar connected.")
