"""Scheduler agent: reminder rules, the persistent job queue, and the morning recap (with holiday alerts)."""
import json
import logging
from datetime import date, datetime, time, timedelta

import httpx
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

import calendar_service as cal
from config import BRIDGE_SECRET, BRIDGE_URL, HERE, TZ
from db_models import EventHistory, HolidayNotification, Session, engine

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler(
    jobstores={"default": SQLAlchemyJobStore(engine=engine)},
    timezone=TZ,
    # Reminders missed while the PC was off still go out at startup if under 12 hours late.
    job_defaults={"misfire_grace_time": 12 * 3600, "coalesce": True},
)


def send_whatsapp(text: str) -> None:
    """The bridge only ever sends to the owner's number, so there is no recipient argument."""
    httpx.post(f"{BRIDGE_URL}/send", json={"text": text},
               headers={"x-bridge-secret": BRIDGE_SECRET}, timeout=30).raise_for_status()


def send_reminder(text: str, attempt: int = 0, start: datetime | None = None) -> None:
    """start is None for jobs queued before reminders carried their event's start time."""
    if start and datetime.now(TZ) >= start:  # caught up late after downtime, or still retrying
        log.info("Skipped reminder, the event has already started: %s", text)
        return
    try:
        send_whatsapp(text)
    except Exception as e:
        if attempt >= 12:
            log.error("Gave up on reminder after an hour of retries: %s (%s)", text, e)
            return
        log.warning("Reminder send failed (%s); retrying in 5 minutes", e)
        scheduler.add_job(send_reminder, "date", run_date=datetime.now(TZ) + timedelta(minutes=5),
                          args=[text, attempt + 1], kwargs={"start": start})


def at_20(d: date) -> datetime:
    return datetime.combine(d, time(20, 0), tzinfo=TZ)


def reminder_times(category: str, start: datetime) -> list[datetime]:
    """start is the event start, or the due time for Uni deadlines."""
    if category == "Uni":
        return [start - timedelta(days=14), at_20(start.date() - timedelta(days=3))]
    if category == "Social":
        return [at_20(start.date() - timedelta(days=1)), start - timedelta(hours=2)]
    return [at_20(start.date() - timedelta(days=1))]  # Work and PPI: the night before


def reminder_text(name: str, category: str, start: datetime) -> str:
    when = start.strftime("%a %d %b at %H:%M")
    if category == "Work":
        return f"Reminder: work shift ({name}) on {when}. Clock in at {start:%H:%M}."
    if category == "Uni":
        return f"Reminder: {name} is due {when}."
    return f"Reminder: {name} is on {when}."


def schedule_reminders(event_id: int, name: str, category: str, start: datetime) -> tuple[list[str], list[datetime], bool]:
    """Queues future reminders. Returns (job ids, queued times, whether any trigger time had already passed)."""
    now = datetime.now(TZ)
    text = reminder_text(name, category, start)
    job_ids, queued, missed = [], [], False
    for i, t in enumerate(reminder_times(category, start)):
        if t <= now:
            missed = True
            continue
        job = scheduler.add_job(send_reminder, "date", run_date=t, args=[text], kwargs={"start": start},
                                id=f"event{event_id}-{i}", replace_existing=True)
        job_ids.append(job.id)
        queued.append(t)
    return job_ids, queued, missed


def cancel_reminders(job_ids: list[str]) -> None:
    for job_id in job_ids or []:
        try:
            scheduler.remove_job(job_id)
        except JobLookupError:
            pass  # already fired


def due_holidays(holidays: list[dict], today: date) -> list[tuple[str, str, int]]:
    """Holidays 0-42 days away, as (name, date, days away)."""
    out = []
    for h in holidays:
        days = (date.fromisoformat(h["date"]) - today).days
        if 0 <= days <= 42:
            out.append((h["name"], h["date"], days))
    return out


def recap_text(today: date, events: list[dict], deadline_ids: set[str]) -> str:
    """Today's agenda plus Uni events in the next 7 days. events: Google Calendar events from today to 7 days
    ahead, from every calendar the recap reads; deadline_ids: Google ids of events the bot stored as deadlines."""
    all_day, timed, uni = [], [], []
    for ev in events:
        if any(a.get("self") and a.get("responseStatus") == "declined" for a in ev.get("attendees", [])):
            continue
        name = ev.get("summary", "(no title)")
        if "date" in ev["start"]:  # all-day; the end date is exclusive
            if date.fromisoformat(ev["start"]["date"]) <= today < date.fromisoformat(ev["end"]["date"]):
                all_day.append(f"• All day: {name}")
            continue
        start = datetime.fromisoformat(ev["start"]["dateTime"]).astimezone(TZ)
        end = datetime.fromisoformat(ev["end"]["dateTime"]).astimezone(TZ)
        due = ev["id"] in deadline_ids
        at = end if due else start  # a deadline is shown and sorted by its due time
        when = f"{end:%H:%M}" if due else f"{start:%H:%M}–{end:%H:%M}"
        suffix = " (due)" if due else ""
        if at.date() == today:  # not < today: last night's late finish isn't listed again
            timed.append((at, f"• {when} {name}{suffix}"))
        elif name.startswith("[Uni]") and today < at.date() <= today + timedelta(days=7):
            uni.append((at, f"• {at:%a %d %b} {when} {name.removeprefix('[Uni]').strip()}{suffix}"))
    lines = [f"☀️ Today's agenda: {today:%a %d %b}", *all_day, *(text for _, text in sorted(timed))]
    if len(lines) == 1:
        lines.append("Nothing scheduled today.")
    if uni:
        lines += ["", "📚 Uni in the next 7 days", *(text for _, text in sorted(uni))]
    return "\n".join(lines)


def send_recap(day: date | None = None) -> None:
    """The 8am message. On a failed send, retries every 5 minutes until the day is over (day is set on retries)."""
    today = datetime.now(TZ).date()
    if day and day != today:
        log.error("Gave up on the recap for %s: the day is over", day)
        return
    holidays = json.loads((HERE / "holidays.json").read_text(encoding="utf-8"))["holidays"]
    with Session() as s:
        alerts = [(name, d, days) for name, d, days in due_holidays(holidays, today)
                  if not s.get(HolidayNotification, f"{name}|{d}")]
        deadline_ids = {e.google_calendar_event_id for e in s.query(EventHistory).filter(EventHistory.start == EventHistory.end)}
    try:
        t0 = datetime.combine(today, time(0), tzinfo=TZ)
        events = [ev for cal_id in ("primary", cal.HOLIDAY_CALENDAR)
                  for ev in cal.list_events(cal_id, t0, t0 + timedelta(days=8))]
        text = recap_text(today, events, deadline_ids)
    except Exception as e:  # still send the holiday alerts, and say why the agenda is missing
        log.exception("Recap couldn't read Google Calendar")
        text = f"☀️ Today's agenda: {today:%a %d %b}\nCouldn't read Google Calendar ({e})."
    for name, _, days in alerts:
        away = "6 weeks" if days == 42 else f"{days} days"
        text += f"\n\n🎉 {name} is {away} away! It's time to start planning a PPI event."
    try:
        send_whatsapp(text)
    except Exception as e:
        log.warning("Recap send failed (%s); retrying in 5 minutes", e)
        scheduler.add_job(send_recap, "date", run_date=datetime.now(TZ) + timedelta(minutes=5), args=[today])
        return
    with Session() as s:  # record after sending, so an alert in a failed recap goes out with the retry
        s.add_all(HolidayNotification(key=f"{name}|{d}") for name, d, _ in alerts)
        s.commit()


def start() -> None:
    scheduler.start()
    # Added once, never replaced: the stored next run time is what makes a recap missed while the PC was off go out
    # on startup (until midnight, via the grace time).
    # shortcut: changing hour= has no effect on an existing DB until the "recap" job is deleted; compare triggers if it changes often.
    if not scheduler.get_job("recap"):
        scheduler.add_job(send_recap, "cron", hour=8, id="recap", misfire_grace_time=16 * 3600)
    for old in ("holiday_check", "holiday_check_startup"):  # the 9am holiday check is now part of the recap
        try:
            scheduler.remove_job(old)
        except JobLookupError:
            pass
