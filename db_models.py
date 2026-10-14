from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config import DB_URL

# One file for app state and the APScheduler job store; timeout waits out the scheduler's writes.
engine = create_engine(DB_URL, connect_args={"timeout": 30})
Session = sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()


class EventHistory(Base):
    __tablename__ = "events_history"
    id = Column(Integer, primary_key=True)
    raw_message = Column(Text)
    parsed_json = Column(JSON)
    name = Column(String)
    category = Column(String)
    start = Column(DateTime)  # Europe/London wall-clock time; for deadlines, the due time
    end = Column(DateTime)
    google_calendar_event_id = Column(String)
    event_url = Column(String)
    apscheduler_job_ids = Column(JSON, default=list)
    status = Column(String, default="active")  # active | cancelled
    created_at = Column(DateTime, default=datetime.now)


class PendingDraft(Base):
    """At most one row (id=1): an event waiting for missing details, or an edit/cancel waiting for confirmation."""
    __tablename__ = "pending_draft"
    id = Column(Integer, primary_key=True)
    kind = Column(String)  # create | confirm
    data = Column(JSON)


class HolidayNotification(Base):
    __tablename__ = "holiday_notifications"
    key = Column(String, primary_key=True)  # "<name>|<date>"
    sent_at = Column(DateTime, default=datetime.now)


Base.metadata.create_all(engine)
