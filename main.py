"""FastAPI backend: receives the owner's WhatsApp messages from whatsapp_bridge.js and returns the reply."""
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

import agents
import calendar_service as cal
import scheduler as sched
from config import BRIDGE_SECRET, TZ
from db_models import EventHistory, PendingDraft, Session

log = logging.getLogger("assistant")

FIELDS = ["Event_Name", "Date", "Time", "End_Time", "Category", "Is_Deadline"]
EDITABLE = ["Event_Name", "Date", "Time", "End_Time", "Category"]
ASK = {"Event_Name": "a name for it", "Date": "the date", "Time": "the time (start, or due time)",
       "End_Time": "the end time", "Category": "the category (Uni, Work or PPI)"}
DROP = {"never mind", "nevermind", "forget it", "stop", "no"}
YES = {"yes", "y", "yeah", "yep", "ok", "okay", "confirm"}


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)  # the DB stores London wall-clock time


def aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=TZ)


def fmt(dt: datetime) -> str:
    return dt.strftime("%a %d %b %H:%M")


def line(e: EventHistory) -> str:
    return f"#{e.id} [{e.category}] {e.name} - {fmt(e.start)}"


def to_dt(d, t) -> datetime | None:
    try:
        return datetime.combine(date.fromisoformat(d), time.fromisoformat(t))
    except (TypeError, ValueError):
        return None


def to_time(t) -> time | None:
    try:
        return time.fromisoformat(t)
    except (TypeError, ValueError):
        return None


def is_deadline(f: dict) -> bool:
    return bool(f.get("Is_Deadline")) and f.get("Category") == "Uni"


def missing(f: dict) -> list[str]:
    m = [k for k in ("Event_Name", "Category") if not f.get(k)]
    if to_dt(f.get("Date"), f.get("Time")) is None:
        m += ["Date", "Time"] if not f.get("Date") or to_dt(f["Date"], "00:00") is None else ["Time"]
    if not is_deadline(f) and to_time(f.get("End_Time")) is None:
        m.append("End_Time")
    return m


def span(f: dict) -> tuple[datetime, datetime]:
    start = to_dt(f["Date"], f["Time"])
    if is_deadline(f):
        return start, start
    end = datetime.combine(start.date(), to_time(f["End_Time"]))
    if end <= start:
        end += timedelta(days=1)  # overnight shift
    return start, end


def set_draft(kind: str, data: dict) -> None:
    with Session() as s:
        s.merge(PendingDraft(id=1, kind=kind, data=data))
        s.commit()


def clear_draft() -> None:
    with Session() as s:
        s.query(PendingDraft).delete()
        s.commit()


def reschedule(event_id: int, name: str, category: str, start: datetime) -> str:
    job_ids, queued, missed = sched.schedule_reminders(event_id, name, category, aware(start))
    with Session() as s:
        s.get(EventHistory, event_id).apscheduler_job_ids = job_ids
        s.commit()
    out = "Reminders: " + (", ".join(map(fmt, queued)) or "none left to schedule")
    if missed:  # trigger times already passed: send one combined reminder now
        out += "\n\n" + sched.reminder_text(name, category, aware(start))
    return out


def create(f: dict, raw: str) -> str:
    if m := missing(f):
        set_draft("create", {**f, "_raw": raw})
        known = ", ".join(f"{k.replace('_', ' ').lower()}: {f[k]}" for k in EDITABLE if f.get(k))
        return (f"Got it ({known}). " if known else "") + "What's " + " and ".join(ASK[k] for k in m) + "?"
    start, end = span(f)
    if start <= now_local():
        set_draft("create", {**f, "Date": None, "Time": None, "_raw": raw})
        return f"{fmt(start)} is in the past. What's the correct date and time?"
    try:
        cal_id, url = cal.create_event(f["Event_Name"], f["Category"], aware(start), aware(end))
    except cal.CalendarError as e:
        set_draft("create", {**f, "_raw": raw})  # keep it so "try again" works
        return f"{e} Nothing was saved; reply 'try again' once that's fixed."
    clear_draft()
    with Session() as s:
        ev = EventHistory(raw_message=raw, parsed_json=f, name=f["Event_Name"], category=f["Category"],
                          start=start, end=end, google_calendar_event_id=cal_id, event_url=url)
        s.add(ev)
        s.commit()  # commit before scheduling: the job store writes to the same SQLite file
    return f"Added {line(ev)}\n{url}\n" + reschedule(ev.id, ev.name, ev.category, start)


def list_events(days: int) -> str:
    with Session() as s:
        events = (s.query(EventHistory)
                  .filter(EventHistory.status == "active", EventHistory.start >= now_local(),
                          EventHistory.start <= now_local() + timedelta(days=days))
                  .order_by(EventHistory.start).all())
    if not events:
        return f"Nothing scheduled in the next {days} days."
    return f"Next {days} days:\n" + "\n".join(map(line, events))


def confirm(p: agents.Intake, upcoming: list[EventHistory]) -> str:
    by_id = {e.id: e for e in upcoming}
    ids = [i for i in p.Target_Ids if i in by_id]
    if not ids:
        return "I couldn't tell which event you mean. Send \"what's on\" to see your events."
    changes = {k: getattr(p, k) for k in EDITABLE if getattr(p, k) is not None}
    if p.Intent == "edit" and not changes:
        return "What should I change it to?"
    set_draft("confirm", {"action": p.Intent, "ids": ids, "changes": changes})
    verb = "Cancel" if p.Intent == "cancel" else "Change (" + ", ".join(f"{k}: {v}" for k, v in changes.items()) + ")"
    if len(ids) == 1:
        return f"{verb} {line(by_id[ids[0]])}? Reply yes."
    return f"{verb} which one?\n" + "\n".join(f"{n}. {line(by_id[i])}" for n, i in enumerate(ids, 1)) + "\nReply with the number."


def apply(data: dict, event_id: int) -> str:
    with Session() as s:
        ev = s.get(EventHistory, event_id)
    if not ev or ev.status != "active":
        return "That event no longer exists."
    if data["action"] == "cancel":
        try:
            cal.delete_event(ev.google_calendar_event_id)
        except cal.CalendarError as e:
            return f"{e} Nothing was changed."
        sched.cancel_reminders(ev.apscheduler_job_ids)
        with Session() as s:
            row = s.get(EventHistory, event_id)
            row.status, row.apscheduler_job_ids = "cancelled", []
            s.commit()
        return f"Cancelled {line(ev)}."

    f = {**ev.parsed_json, **data["changes"]}
    start = to_dt(f["Date"], f["Time"])
    if start is None:
        return "I couldn't read the new date or time. Nothing was changed."
    if "End_Time" in data["changes"]:
        end_t = to_time(f["End_Time"])
        if end_t is None:
            return "I couldn't read the new end time. Nothing was changed."
        end = datetime.combine(start.date(), end_t)
        end += timedelta(days=1) if end <= start else timedelta()
    else:
        end = start + (ev.end - ev.start)  # keep the old length
    if start <= now_local():
        return f"{fmt(start)} is in the past. Nothing was changed."
    try:
        url = cal.update_event(ev.google_calendar_event_id, f["Event_Name"], f["Category"], aware(start), aware(end))
    except cal.CalendarError as e:
        return f"{e} Nothing was changed."
    sched.cancel_reminders(ev.apscheduler_job_ids)
    with Session() as s:
        row = s.get(EventHistory, event_id)
        row.parsed_json, row.name, row.category = f, f["Event_Name"], f["Category"]
        row.start, row.end, row.event_url, row.apscheduler_job_ids = start, end, url, []
        s.commit()
    return f"Updated {line(row)}\n{url}\n" + reschedule(event_id, row.name, row.category, start)


def handle(text: str) -> str:
    t = text.strip().lower().rstrip(".!")
    if t in DROP:
        clear_draft()
        return "Okay, dropped it."
    with Session() as s:
        draft = s.get(PendingDraft, 1)
        if draft and draft.kind == "confirm":
            s.delete(draft)
            s.commit()
            ids = draft.data["ids"]
            pick = (ids[0] if len(ids) == 1 and t in YES
                    else ids[int(t) - 1] if t.isdigit() and 1 <= int(t) <= len(ids) else None)
            if pick:
                return apply(draft.data, pick)
            draft = None  # not an answer to the question: treat it as a new message
        upcoming = (s.query(EventHistory)
                    .filter(EventHistory.status == "active", EventHistory.start >= now_local())
                    .order_by(EventHistory.start).limit(50).all())
    pending = draft.data if draft else None

    try:
        p = agents.parse(text, [line(e) for e in upcoming], pending)
    except agents.IntakeError as e:
        log.warning("Intake failed: %s", e)
        return f"Sorry, I couldn't process that ({e}). Is Ollama running?"

    # A follow-up answers the pending question unless it asks for a list or names an existing event.
    if pending and p.Intent != "list" and not p.Target_Ids:
        merged = {k: v if (v := getattr(p, k)) not in (None, False) else pending.get(k) for k in FIELDS}
        return create(merged, f"{pending.get('_raw', '')}\n{text}".strip())
    clear_draft()
    if p.Intent == "create":
        return create(p.model_dump(include=set(FIELDS)), text)
    if p.Intent == "list":
        return list_events(p.List_Days or 7)
    if p.Intent in ("edit", "cancel"):
        return confirm(p, upcoming)
    return "I can add, change, cancel or list events. Try: \"Shift at Tesco Friday 9:00 to 17:00\"."


class Incoming(BaseModel):
    text: str


@asynccontextmanager
async def lifespan(app):
    sched.start()
    yield
    sched.scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.post("/incoming")
def incoming(msg: Incoming, x_bridge_secret: str = Header("")):
    if not secrets.compare_digest(x_bridge_secret.encode(), BRIDGE_SECRET.encode()):
        raise HTTPException(403)
    try:
        return {"reply": handle(msg.text)}
    except Exception:
        log.exception("Failed to handle message")
        return {"reply": "Something went wrong on my side; check the server log."}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="127.0.0.1", port=8000)
