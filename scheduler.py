"""Scheduler agent: reminder rules, the persistent job queue, and the daily holiday check."""
import json
import logging
from datetime import date, datetime, time, timedelta

import httpx
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from config import BRIDGE_SECRET, BRIDGE_URL, HERE, TZ
from db_models import HolidayNotification, Session, engine

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


def check_holidays() -> None:
    holidays = json.loads((HERE / "holidays.json").read_text(encoding="utf-8"))["holidays"]
    for name, day, days in due_holidays(holidays, datetime.now(TZ).date()):
        key = f"{name}|{day}"
        with Session() as s:
            if s.get(HolidayNotification, key):
                continue
            away = "6 weeks" if days == 42 else f"{days} days"
            # Send first, record after: a failed send is retried on the next check.
            send_whatsapp(f"Reminder: {name} is {away} away! It's time to start planning a PPI event.")
            s.add(HolidayNotification(key=key))
            s.commit()


def start() -> None:
    scheduler.start()
    scheduler.add_job(check_holidays, "cron", hour=9, id="holiday_check", replace_existing=True)
    # Also check shortly after startup (gives the WhatsApp bridge time to connect) to catch days the PC was off.
    scheduler.add_job(check_holidays, "date", run_date=datetime.now(TZ) + timedelta(minutes=2),
                      id="holiday_check_startup", replace_existing=True)
